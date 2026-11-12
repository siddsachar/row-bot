"""X (Twitter) tool — read, post, and engage on X via the X API v2, as one of several accounts.

Uses native httpx against the X API v2 with OAuth 2.0 PKCE authentication.
No external dependencies (no tweepy, no xurl CLI). Each connected account has its own sign-in
(``x_accounts``); every call names the account it acts as, and posting asks the person
(``x_posting``).
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import List, Literal, Optional
from urllib.parse import urlencode, urlparse, parse_qs

from langchain_core.tools import StructuredTool
from langgraph.errors import GraphBubbleUp
from pydantic import BaseModel, Field

from row_bot import account_tokens, x_accounts
from row_bot.data_paths import get_row_bot_data_dir
from row_bot.tools.base import BaseTool
from row_bot.tools import registry, x_posting
from row_bot.x_accounts import XAccount, XAccountChoice

logger = logging.getLogger(__name__)

# ── Data paths ───────────────────────────────────────────────────────────────
_DATA_DIR = get_row_bot_data_dir()
_X_DIR = _DATA_DIR / "x"
_X_DIR.mkdir(parents=True, exist_ok=True)
_TIER_PATH = _X_DIR / "tier_info.json"  # The sign-ins themselves are in the system keychain (x_accounts).

# ── X API v2 base URL ───────────────────────────────────────────────────────
_API_BASE = x_accounts.API_BASE
_AUTH_URL = "https://x.com/i/oauth2/authorize"
_TOKEN_URL = x_accounts.TOKEN_URL

# ── OAuth 2.0 scopes ────────────────────────────────────────────────────────
_SCOPES = (
    "tweet.read tweet.write users.read like.read like.write "
    "bookmark.read bookmark.write media.write offline.access"
)

# Fixed port for OAuth callback — must match what user registers in X Developer Portal
_OAUTH_CALLBACK_PORT = 17638
_OAUTH_REDIRECT_URI = f"http://127.0.0.1:{_OAUTH_CALLBACK_PORT}/callback"

# ── Operations grouped by risk tier ─────────────────────────────────────────
_READ_OPS = ["x_search", "x_read_tweet", "x_timeline", "x_mentions", "x_user_info"]
_POST_OPS = ["x_post_tweet", "x_reply", "x_quote", "x_delete_tweet"]
_ENGAGE_OPS = ["x_like", "x_unlike", "x_repost", "x_unrepost",
               "x_bookmark", "x_unbookmark"]
ALL_OPERATIONS = _READ_OPS + _POST_OPS + _ENGAGE_OPS
DEFAULT_OPERATIONS = list(ALL_OPERATIONS)  # All enabled by default

# ── Tweet fields to request ─────────────────────────────────────────────────
_TWEET_FIELDS = "created_at,author_id,public_metrics,conversation_id,in_reply_to_user_id"
_USER_FIELDS = "name,username,description,public_metrics,verified,created_at,profile_image_url"


# ── Sign-ins ────────────────────────────────────────────────────────────────

# Whether a saved sign-in may bind the X tools, decided once per sign-in (WP A5). Building a chat's
# tools used to ask X (GET /users/me) on every turn, and a timeout or a 5xx dropped the X tools for
# that turn: a different tool list, so a cold prompt cache, and the X tools gone for no reason.
_SIGN_IN_VERDICTS: dict[str, bool] = {}
_SIGN_IN_VERDICTS_MAX = 16


def _sign_in_key(token: dict) -> str:
    raw = f"{token.get('access_token', '')}\n{token.get('refresh_token', '')}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ── Rate limit tracking ─────────────────────────────────────────────────────

class _RateLimiter:
    """Track X API rate limits from response headers."""

    def __init__(self):
        self._limits: dict[str, dict] = {}  # endpoint → {remaining, reset}

    def update(self, endpoint: str, headers: dict):
        remaining = headers.get("x-rate-limit-remaining")
        reset = headers.get("x-rate-limit-reset")
        if remaining is not None and reset is not None:
            self._limits[endpoint] = {
                "remaining": int(remaining),
                "reset": int(reset),
            }

    def check(self, endpoint: str) -> str | None:
        """Return an error message if rate-limited, else None."""
        info = self._limits.get(endpoint)
        if info and info["remaining"] <= 0:
            wait = max(0, info["reset"] - int(time.time()))
            if wait > 0:
                return (
                    f"Rate limit reached for this endpoint. "
                    f"Resets in {wait} seconds (at {time.strftime('%H:%M:%S', time.localtime(info['reset']))}). "
                    f"Please try again later."
                )
        return None


# ── Tier discovery ───────────────────────────────────────────────────────────

def _load_tier_info() -> dict:
    if _TIER_PATH.is_file():
        try:
            with open(_TIER_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_tier_info(info: dict):
    with open(_TIER_PATH, "w") as f:
        json.dump(info, f, indent=2)


# ── OAuth 2.0 PKCE callback server ──────────────────────────────────────────

class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    """Handle the OAuth 2.0 callback from the browser."""

    auth_code: str | None = None
    error: str | None = None

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if "code" in params:
            _OAuthCallbackHandler.auth_code = params["code"][0]
            self._respond("Authentication successful! You can close this tab.")
        elif "error" in params:
            _OAuthCallbackHandler.error = params.get("error_description",
                                                      params["error"])[0]
            self._respond(f"Authentication failed: {html.escape(_OAuthCallbackHandler.error)}")
        else:
            self._respond("Invalid callback — missing code parameter.")

    def _respond(self, message: str):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        body = (
            f"<html><body style='font-family:system-ui;text-align:center;"
            f"padding:60px'><h2>{message}</h2></body></html>"
        )
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, format, *args):
        pass  # Suppress HTTP access log noise


def _authorization_url(client_id: str, redirect_uri: str, state: str, code_challenge: str) -> str:
    """X's sign-in page for one PKCE sign-in that returns to ``redirect_uri``."""
    auth_params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": _SCOPES,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{_AUTH_URL}?{urlencode(auth_params)}"


def _exchange_code(client_id: str, client_secret: str, code: str, redirect_uri: str,
                   code_verifier: str, *, http=None) -> dict:
    """Exchange a sign-in's code for X's tokens (``http``: an httpx client; default, ``x_accounts.send``)."""
    request = {"data": {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
                        "client_id": client_id, "code_verifier": code_verifier},
               "auth": (client_id, client_secret), "timeout": 15}
    resp = http.post(_TOKEN_URL, **request) if http is not None else x_accounts.send("POST", _TOKEN_URL, **request)
    if resp.status_code != 200:
        body = resp.text
        logger.error("X token exchange failed (HTTP %d): %s", resp.status_code, body)
        # Provide a human-friendly error for common cases
        if resp.status_code == 400:
            raise RuntimeError(
                "X token exchange failed (400 Bad Request). "
                "This usually means the Client ID or Client Secret is wrong, "
                "or the callback URL doesn't match. Check your X Developer Portal settings."
            )
        raise RuntimeError(f"X token exchange failed (HTTP {resp.status_code}): {body}")
    token = resp.json()
    token["expires_at"] = time.time() + token.get("expires_in", 7200)
    return token


def _run_oauth_flow(client_id: str, client_secret: str) -> dict:
    """Run the full OAuth 2.0 PKCE flow; the caller saves the sign-in (``x_accounts.add``).

    Opens the user's browser to X's authorization page, starts a local
    HTTP server on a fixed port to receive the callback, and exchanges
    the code for tokens.

    **Important**: This function blocks until the user completes the flow
    or the 120-second timeout expires.  Call from a background thread.

    Parameters
    ----------
    client_id : str
        X app Client ID.
    client_secret : str
        X app Client Secret.

    Returns
    -------
    dict
        Token dictionary with access_token, refresh_token, expires_at.
    """
    import webbrowser
    import base64

    # Reset handler state
    _OAuthCallbackHandler.auth_code = None
    _OAuthCallbackHandler.error = None

    # PKCE code verifier + challenge
    code_verifier = secrets.token_urlsafe(64)[:128]
    code_challenge = (
        base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode("ascii")).digest()
        )
        .rstrip(b"=")
        .decode("ascii")
    )

    state = secrets.token_urlsafe(32)

    # Start the callback server on the fixed port
    try:
        server = HTTPServer(("127.0.0.1", _OAUTH_CALLBACK_PORT), _OAuthCallbackHandler)
    except OSError as exc:
        raise RuntimeError(
            f"Could not start OAuth callback server on port {_OAUTH_CALLBACK_PORT}. "
            f"Is another process using it? Error: {exc}"
        ) from exc

    auth_url = _authorization_url(client_id, _OAUTH_REDIRECT_URI, state, code_challenge)

    # Open browser
    logger.info("Opening browser for X authentication...")
    webbrowser.open(auth_url)

    # Wait for callback (with timeout)
    server.timeout = 120
    try:
        while _OAuthCallbackHandler.auth_code is None and _OAuthCallbackHandler.error is None:
            server.handle_request()
    finally:
        server.server_close()

    if _OAuthCallbackHandler.error:
        raise RuntimeError(f"X authentication failed: {_OAuthCallbackHandler.error}")
    if not _OAuthCallbackHandler.auth_code:
        raise RuntimeError("X authentication timed out — no callback received.")

    return _exchange_code(client_id, client_secret, _OAuthCallbackHandler.auth_code,
                          _OAUTH_REDIRECT_URI, code_verifier)


# ── Pydantic input schemas ──────────────────────────────────────────────────

def _account_field(writes: bool) -> object:
    return Field(default=None, description=(
        "The X account to act as: its @handle or numeric ID, as x_accounts lists them. "
        + ("Required when more than one account is connected; never guess one."
           if writes else "Needed for 'mentions' and your own 'timeline' when several are connected.")))


class _XReadInput(BaseModel):
    action: Literal["search", "read_tweet", "timeline", "mentions", "user_info"] = Field(
        description=(
            "search: recent tweets; read_tweet: one tweet by ID; timeline: a user's recent tweets; "
            "mentions: your recent mentions; user_info: a user's profile."
        )
    )
    query: Optional[str] = Field(
        default=None,
        description="Search query string. Required for 'search' action. "
        "Use plain keywords only — do NOT include operators like "
        "'since:', 'until:', or 'within_time:' (those are unsupported "
        "by the API). Use start_time/end_time parameters instead for "
        "time filtering.",
    )
    tweet_id: Optional[str] = Field(
        default=None,
        description="Tweet ID. Required for 'read_tweet' action.",
    )
    username: Optional[str] = Field(
        default=None,
        description=(
            "Twitter/X username (without @). "
            "Used for 'timeline' and 'user_info'. "
            "Defaults to authenticated user for 'timeline'."
        ),
    )
    max_results: Optional[int] = Field(
        default=10,
        description="Maximum number of results (default 10, max 100).",
    )
    start_time: Optional[str] = Field(
        default=None,
        description=(
            "Oldest date/time for results. Accepts ISO 8601 "
            "(e.g. '2026-04-14T00:00:00Z') or relative like "
            "'1h', '24h', '7d'. Only used with 'search' action."
        ),
    )
    end_time: Optional[str] = Field(
        default=None,
        description=(
            "Newest date/time for results. Same formats as "
            "start_time. Only used with 'search' action."
        ),
    )
    account: Optional[str] = _account_field(False)


class _XPostInput(BaseModel):
    action: Literal["post", "reply", "quote", "delete"] = Field(
        description="post a new tweet, reply to or quote a tweet, or delete one."
    )
    text: Optional[str] = Field(
        default=None,
        description="The tweet text content. Required for post/reply/quote.",
    )
    tweet_id: Optional[str] = Field(
        default=None,
        description="Tweet ID to reply to, quote, or delete.",
    )
    media_paths: Optional[List[str]] = Field(
        default=None,
        description=(
            "Optional media to attach, each a file in this chat by its name, a Library › Media item by its "
            "reference (from media_find), or a file path. "
            "Supports pictures (JPEG, PNG, WebP, GIF) and videos (MP4, MOV). "
            "Max 4 pictures, or 1 video or GIF, per post."
        ),
    )
    account: Optional[str] = _account_field(True)
    post_at: Optional[str] = Field(
        default=None,
        description=("When it goes out, if not now: a local time like '13:00' or '2026-10-29T09:30', up to 7 days "
                     "ahead. Its card waits for the person in Home › Review (several posts made in one step wait "
                     "there together); approved, Row-Bot posts it then."),
    )


class _XEngageInput(BaseModel):
    action: Literal["like", "unlike", "repost", "unrepost", "bookmark", "unbookmark"] = Field(
        description="The engagement action."
    )
    tweet_id: str = Field(
        description="The tweet ID to engage with.",
    )
    account: Optional[str] = _account_field(True)


class _XAccountsInput(BaseModel):
    pass


# ── Formatting helpers ──────────────────────────────────────────────────────

def _format_tweet(tweet: dict, includes: dict | None = None) -> str:
    """Format a tweet dict into a readable string."""
    text = tweet.get("text", "")
    tweet_id = tweet.get("id", "?")
    created = tweet.get("created_at", "")
    metrics = tweet.get("public_metrics", {})
    author_id = tweet.get("author_id", "")

    # Try to resolve author username from includes
    author_name = author_id
    if includes and "users" in includes:
        for u in includes["users"]:
            if u.get("id") == author_id:
                author_name = f"@{u['username']}"
                break

    parts = [f"Tweet ID: {tweet_id}"]
    if author_name:
        parts.append(f"Author: {author_name}")
    if created:
        parts.append(f"Posted: {created}")
    parts.append(f"\n{text}")
    if metrics:
        parts.append("\n" + _numbers(metrics))
    return "\n".join(parts)


_METRICS = (("impression_count", "view", "views"), ("like_count", "like", "likes"),
            ("retweet_count", "repost", "reposts"), ("reply_count", "reply", "replies"),
            ("quote_count", "quote", "quotes"), ("bookmark_count", "bookmark", "bookmarks"))


def _numbers(metrics: dict) -> str:
    """"1,204 views · 31 likes · 4 reposts · 2 replies · 1 quote · 6 bookmarks", what X said (G7)."""
    shown = []
    for key, one, many in _METRICS:
        if key in metrics:
            count = int(metrics.get(key) or 0)
            shown.append(f"{count:,} {one if count == 1 else many}")
    return " · ".join(shown)


def _totals(tweets: list[dict]) -> str:
    """An account's recent posts in one line: each number added up, and the most viewed post (G7)."""
    totals: dict[str, int] = {}
    for tweet in tweets:
        for key, *_ in _METRICS:
            if key in (tweet.get("public_metrics") or {}):
                totals[key] = totals.get(key, 0) + int(tweet["public_metrics"].get(key) or 0)
    if not totals:
        return ""
    best = max(tweets, key=lambda tweet: int((tweet.get("public_metrics") or {}).get("impression_count") or 0))
    return (f"Together, these {len(tweets)} posts: {_numbers(totals)}. Most viewed: {best.get('id')} "
            f"({int((best.get('public_metrics') or {}).get('impression_count') or 0):,} views).\n\n")


def _format_user(user: dict) -> str:
    """Format a user dict into a readable string."""
    metrics = user.get("public_metrics", {})
    parts = [
        f"@{user.get('username', '?')} — {user.get('name', '')}",
        f"Bio: {user.get('description', 'N/A')}",
        f"Followers: {metrics.get('followers_count', 0):,}  "
        f"Following: {metrics.get('following_count', 0):,}  "
        f"Tweets: {metrics.get('tweet_count', 0):,}",
    ]
    if user.get("verified"):
        parts.append("✅ Verified")
    if user.get("created_at"):
        parts.append(f"Joined: {user['created_at']}")
    return "\n".join(parts)


# ── Time parsing & query sanitisation ────────────────────────────────────────

# Regex for relative time strings like "1h", "24h", "7d", "30m"
_RELATIVE_TIME_RE = re.compile(r"^(\d+)\s*([mhdw])$", re.IGNORECASE)

# Operators that only work in the Twitter web UI, not the API v2
_UNSUPPORTED_OPS_RE = re.compile(
    r"\b(?:since|until|within_time|near|geocode):[^\s)]+",
    re.IGNORECASE,
)


def _parse_time_param(value: str | None) -> str | None:
    """Convert a time parameter to ISO 8601 UTC string for the X API.

    Accepts:
    - None → None
    - ISO 8601 string (pass through if already valid)
    - Relative: "30m", "1h", "24h", "7d", "2w"
    """
    if not value:
        return None

    value = value.strip()

    # Relative time: "1h", "24h", "7d", "30m", "2w"
    m = _RELATIVE_TIME_RE.match(value)
    if m:
        amount = int(m.group(1))
        unit = m.group(2).lower()
        if unit == "m":
            delta = timedelta(minutes=amount)
        elif unit == "h":
            delta = timedelta(hours=amount)
        elif unit == "d":
            delta = timedelta(days=amount)
        elif unit == "w":
            delta = timedelta(weeks=amount)
        else:
            return None
        dt = datetime.now(timezone.utc) - delta
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Already ISO 8601 — pass through
    if "T" in value or len(value) == 10:  # "2026-04-14" or "2026-04-14T..."
        # Normalise: ensure it ends with Z if no timezone
        if value.endswith("Z") or "+" in value:
            return value
        return value + "Z" if "T" in value else value + "T00:00:00Z"

    return None


def _strip_unsupported_operators(query: str) -> str:
    """Remove Twitter web-UI-only operators from a search query.

    Operators like since:, until:, within_time:, near:, geocode: are NOT
    supported by the X API v2 search endpoint and cause 400 errors.
    """
    cleaned = _UNSUPPORTED_OPS_RE.sub("", query)
    # Collapse extra whitespace
    return " ".join(cleaned.split())


# ═════════════════════════════════════════════════════════════════════════════
# ── XTool ────────────────────────────────────────────────────────────────────
# ═════════════════════════════════════════════════════════════════════════════

class XTool(BaseTool):

    def __init__(self):
        self._rate_limiter = _RateLimiter()
        self._tier_info: dict = _load_tier_info()

    # ── Identity ─────────────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "x"

    @property
    def display_name(self) -> str:
        return "𝕏 X (Twitter)"

    @property
    def description(self) -> str:
        return (
            "Read, post, and engage on X (Twitter), as one of the person's connected accounts. "
            "Use this when the user asks about tweets, X timelines, "
            "posting on X, liking, reposting, or bookmarking tweets."
        )

    @property
    def destructive_tool_names(self) -> set[str]:
        return {"x_post"}

    @property
    def untrusted_tool_names(self) -> set[str]:
        return {"x_read"}

    @property
    def enabled_by_default(self) -> bool:
        return False  # Must set up OAuth credentials first

    @property
    def required_api_keys(self) -> dict[str, str]:
        return {
            "X Client ID": "X_CLIENT_ID",
            "X Client Secret": "X_CLIENT_SECRET",
        }

    @property
    def config_schema(self) -> dict[str, dict]:
        return {
            "read_operations": {
                "label": "Read Operations",
                "type": "multicheck",
                "default": _READ_OPS,
                "options": _READ_OPS,
            },
            "post_operations": {
                "label": "Post Operations (⚠️ requires approval)",
                "type": "multicheck",
                "default": _POST_OPS,
                "options": _POST_OPS,
            },
            "engage_operations": {
                "label": "Engage Operations",
                "type": "multicheck",
                "default": _ENGAGE_OPS,
                "options": _ENGAGE_OPS,
            },
        }

    # ── Auth helpers ─────────────────────────────────────────────────────────

    def _get_client_id(self) -> str:
        from row_bot.api_keys import get_key

        return get_key("X_CLIENT_ID")

    def _get_client_secret(self) -> str:
        from row_bot.api_keys import get_key

        return get_key("X_CLIENT_SECRET")

    def has_credentials(self) -> bool:
        """Check if Client ID and Secret are configured."""
        return bool(self._get_client_id() and self._get_client_secret())

    def is_authenticated(self) -> bool:
        """Whether any X account is signed in (or the one sign-in from before 5.3 waits to become one)."""
        return bool(x_accounts.listed()) or x_accounts.legacy_pending()

    def check_token_health(self) -> tuple[str, str]:
        """Check every connected account, refreshing a sign-in whose access ran out.

        Returns (status, detail) where status is one of:
        - "valid"     — every account answered X
        - "refreshed" — every account works; one or more was silently refreshed
        - "expired"   — an account needs signing in again
        - "missing"   — no account is connected
        - "error"     — X couldn't be asked (or the sign-in from before 5.3 couldn't be set up yet)

        Each account's verdict is remembered for Settings › Apps › X (B263). The sign-in from
        before 5.3 becomes the first account here (x_accounts.migrate).
        """
        try:
            x_accounts.migrate()
        except x_accounts.XAccountError as exc:
            return ("error", f"Couldn't finish setting up the X account from before: {exc}")
        accounts = x_accounts.listed()
        if not accounts:
            return ("missing", "No X account is connected")
        results = [(account, self._check_account(account)) for account in accounts]
        expired = [account.handle for account, (status, _) in results if status in ("expired", "missing")]
        failed = [detail for _, (status, detail) in results if status == "error"]
        if expired:
            return ("expired", f"{x_accounts.names([a for a in accounts if a.handle in expired])} needs "
                               "signing in again — reconnect in Settings")
        if failed:
            return ("error", failed[0])
        if any(status == "refreshed" for _, (status, _) in results):
            return ("refreshed", "Token refreshed successfully")
        return ("valid", "Token is valid")

    def _check_account(self, account: XAccount) -> tuple[str, str]:
        result = self._probe_account(account)
        account_tokens.record_check(account.slot, result[0])
        return result

    def _probe_account(self, account: XAccount) -> tuple[str, str]:
        token = x_accounts.token(account)
        if not token:
            return ("missing", f"{account.handle} has no saved sign-in")
        from row_bot.docs_capture import is_docs_real_data_capture

        if x_accounts.expired(token):
            if is_docs_real_data_capture():
                return ("expired", "Token refresh suppressed during authorized capture")
            if x_accounts.valid_token(account) is not None:
                return ("refreshed", "Token refreshed successfully")
            return ("expired", f"{account.handle}'s sign-in expired — re-authenticate in Settings")
        if is_docs_real_data_capture():
            return ("valid", "Saved token present; live verification suppressed during capture")
        try:
            resp = x_accounts.send("GET", f"{_API_BASE}/users/me", headers=x_accounts.bearer(token),
                                   params={"user.fields": "name,username"}, timeout=10)
        except Exception as exc:
            logger.warning("Checking %s on X failed (%s)", account.handle, type(exc).__name__)
            return ("error", f"Could not verify token: {type(exc).__name__}")
        if resp.status_code == 200:
            data = resp.json().get("data") or {}
            if str(data.get("id") or "") != account.id:
                return ("expired", f"The sign-in kept for {account.handle} is another account's")
            # A handle or name changed on X since: the list follows.
            if data.get("username") and (data.get("username"), data.get("name") or "") != (
                    account.username, account.name):
                try:
                    x_accounts.rename(account, str(data["username"]), str(data.get("name") or ""))
                except ValueError:
                    pass
            return ("valid", "Token is valid")
        if resp.status_code == 429:
            # Rate limiting is transient and does not indicate an invalid token.
            return ("valid", "Token is valid (rate limited by X API)")
        if resp.status_code == 401:
            return ("expired", f"X turned down {account.handle}'s sign-in — re-authenticate in Settings")
        return ("error", f"X API returned {resp.status_code}")

    def _signed_in(self, account: str | None, *, required: bool) -> tuple[XAccount, dict]:
        """The account a call names and its usable sign-in; raises XAccountChoice with what to do."""
        chosen = x_accounts.resolve(account, required=required)
        token = x_accounts.valid_token(chosen)
        if token is None:
            raise XAccountChoice(f"The sign-in of {chosen.handle} has run out or was removed. The person can "
                                 "reconnect it in Settings › Apps › X.")
        return chosen, token

    # ── API request helper ───────────────────────────────────────────────────

    def _api_request(
        self,
        method: str,
        endpoint: str,
        token: dict,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
    ) -> dict:
        """Make an authenticated API request to X API v2.

        Handles rate limiting, tier detection, and common errors.
        Returns the parsed JSON response or raises RuntimeError.
        """
        # Check rate limit before calling
        rate_msg = self._rate_limiter.check(endpoint)
        if rate_msg:
            raise RuntimeError(rate_msg)

        url = f"{_API_BASE}/{endpoint.lstrip('/')}"
        resp = x_accounts.send(method, url, headers=x_accounts.bearer(token), params=params, json=json_body)

        # Update rate limit tracking
        self._rate_limiter.update(endpoint, dict(resp.headers))

        # Handle common errors
        if resp.status_code == 429:
            reset = resp.headers.get("x-rate-limit-reset", "")
            wait = max(0, int(reset) - int(time.time())) if reset else 60
            raise RuntimeError(
                f"Rate limited by X API. Resets in {wait} seconds. "
                f"Please try again later."
            )

        if resp.status_code == 403:
            body = resp.json() if resp.content else {}
            detail = body.get("detail", "")
            if "client-not-enrolled" in detail.lower() or "not-enrolled" in detail.lower():
                # Mark this operation as unavailable for the user's tier
                self._tier_info[endpoint] = "unavailable"
                _save_tier_info(self._tier_info)
                raise RuntimeError(
                    "This operation is not available on your X API tier. "
                    "Upgrade at developer.x.com to access this feature."
                )
            raise RuntimeError(f"X API access denied: {detail or resp.text}")

        if resp.status_code == 401:
            raise RuntimeError(
                "X authentication expired. Please re-authenticate in Settings."
            )

        if resp.status_code >= 400:
            body = resp.json() if resp.content else {}
            detail = body.get("detail", body.get("title", resp.text))
            raise RuntimeError(f"X API error ({resp.status_code}): {detail}")

        if resp.status_code == 204:
            return {}
        return resp.json()

    # ── Operation check ──────────────────────────────────────────────────────

    def _is_op_enabled(self, op_name: str) -> bool:
        """Check whether a specific operation is enabled in config."""
        for config_key in ("read_operations", "post_operations", "engage_operations"):
            ops = self.get_config(config_key, None)
            if ops is None:
                # Not yet configured — check defaults
                schema = self.config_schema.get(config_key, {})
                ops = schema.get("default", [])
            if op_name in ops:
                return True
        return False

    def _check_tier(self, endpoint: str) -> str | None:
        """Return an error message if the endpoint is known to be unavailable."""
        if self._tier_info.get(endpoint) == "unavailable":
            return (
                "This operation is not available on your X API tier. "
                "Upgrade at developer.x.com to access this feature."
            )
        return None

    # ── Accounts ─────────────────────────────────────────────────────────────

    def _x_accounts(self) -> str:
        """The connected accounts: handle, name, numeric ID and whether each is signed in."""
        words = {"connected": "signed in", "invalid": "needs reconnecting", "expired": "needs reconnecting",
                 "saved_unchecked": "signed in, not checked yet", "not_authenticated": "signed out"}
        rows = x_accounts.summaries()
        if not rows:
            return "No X account is connected. The person can connect one in Settings › Apps › X."
        lines = []
        for row in rows:
            state = words.get(row["state"], "status unknown")
            if not row["id"]:
                lines.append(f"- The X account from before (its handle shows once Row-Bot reaches X): {state}")
            else:
                name = f" · {row['name']}" if row["name"] else ""
                limit = x_posting.text_limit(XAccount(row["id"], row["username"], row["name"])) \
                    if row["state"] in {"connected", "saved_unchecked"} else None
                posts = f"; posts up to {limit:,} characters" if limit else ""
                lines.append(f"- @{row['username']}{name} (ID {row['id']}): {state}{posts}")
        more = ("" if len(rows) < 2 else
                "\nWith several accounts, every post, reply, quote, delete, like, repost or bookmark must say "
                "which with account; ask the person if they haven't said.")
        return (f"Connected X accounts ({len(rows)}):\n" + "\n".join(lines) + more
                + f"\nMedia: {x_posting.MEDIA_LIMITS} per post. Links count {x_posting.LINK_LENGTH} characters.")

    # ── Read operations ──────────────────────────────────────────────────────

    def _x_read(self, action: str, query: str | None = None,
                tweet_id: str | None = None, username: str | None = None,
                max_results: int = 10,
                start_time: str | None = None,
                end_time: str | None = None, *, account: str | None = None) -> str:
        """Execute a read operation on X, as the account named (or the first one, for a read about others)."""
        # Mentions and your own timeline are about one account: with several, the call must say which.
        about_you = action == "mentions" or (action == "timeline" and not username)
        try:
            chosen, token = self._signed_in(account, required=about_you)
        except XAccountChoice as exc:
            return str(exc)

        # A model sends null for "the default": that was a TypeError on every read (R7).
        max_results = max(1, min(int(max_results or 10), 100))

        if action == "search":
            if not self._is_op_enabled("x_search"):
                return "Search is disabled in X tool settings."
            if not query:
                return "Please provide a search query."

            tier_msg = self._check_tier("tweets/search/recent")
            if tier_msg:
                return tier_msg

            # Strip unsupported Twitter web UI operators from the query
            clean_query = _strip_unsupported_operators(query)
            if not clean_query.strip():
                return "Search query is empty after removing unsupported operators. Please provide keywords."

            params: dict = {
                "query": clean_query,
                "max_results": max_results,
                "tweet.fields": _TWEET_FIELDS,
                "expansions": "author_id",
                "user.fields": "username",
            }

            # Add time params if provided
            parsed_start = _parse_time_param(start_time)
            parsed_end = _parse_time_param(end_time)
            if parsed_start:
                params["start_time"] = parsed_start
            if parsed_end:
                params["end_time"] = parsed_end

            try:
                data = self._api_request(
                    "GET", "tweets/search/recent", token,
                    params=params,
                )
                tweets = data.get("data", [])
                if not tweets:
                    return f"No tweets found for: {query}"
                includes = data.get("includes", {})
                results = [_format_tweet(t, includes) for t in tweets]
                return f"Found {len(tweets)} tweet(s) for '{query}':\n\n" + "\n\n---\n\n".join(results)
            except RuntimeError as e:
                return str(e)

        elif action == "read_tweet":
            if not self._is_op_enabled("x_read_tweet"):
                return "Read tweet is disabled in X tool settings."
            if not tweet_id:
                return "Please provide a tweet_id."
            try:
                data = self._api_request(
                    "GET", f"tweets/{tweet_id}", token,
                    params={
                        "tweet.fields": _TWEET_FIELDS,
                        "expansions": "author_id",
                        "user.fields": "username",
                    },
                )
                tweet = data.get("data")
                if not tweet:
                    return f"Tweet {tweet_id} not found."
                return _format_tweet(tweet, data.get("includes"))
            except RuntimeError as e:
                return str(e)

        elif action == "timeline":
            if not self._is_op_enabled("x_timeline"):
                return "Timeline is disabled in X tool settings."

            try:
                if username:
                    # Look up user ID by username
                    user_data = self._api_request(
                        "GET", f"users/by/username/{username}", token,
                    )
                    uid = user_data.get("data", {}).get("id")
                    if not uid:
                        return f"User @{username} not found."
                else:
                    uid = chosen.id

                data = self._api_request(
                    "GET", f"users/{uid}/tweets", token,
                    params={
                        "max_results": max_results,
                        "tweet.fields": _TWEET_FIELDS,
                        "expansions": "author_id",
                        "user.fields": "username",
                    },
                )
                tweets = data.get("data", [])
                who = f"@{username}" if username else chosen.handle
                if not tweets:
                    return f"No tweets found in {who}'s timeline."
                includes = data.get("includes", {})
                results = [_format_tweet(t, includes) for t in tweets]
                return (f"{who}'s recent tweets ({len(tweets)}):\n\n" + _totals(tweets)
                        + "\n\n---\n\n".join(results))
            except RuntimeError as e:
                return str(e)

        elif action == "mentions":
            if not self._is_op_enabled("x_mentions"):
                return "Mentions is disabled in X tool settings."
            try:
                data = self._api_request(
                    "GET", f"users/{chosen.id}/mentions", token,
                    params={
                        "max_results": max_results,
                        "tweet.fields": _TWEET_FIELDS,
                        "expansions": "author_id",
                        "user.fields": "username",
                    },
                )
                tweets = data.get("data", [])
                if not tweets:
                    return f"No recent mentions of {chosen.handle} found."
                includes = data.get("includes", {})
                results = [_format_tweet(t, includes) for t in tweets]
                return (f"{chosen.handle}'s recent mentions ({len(tweets)}):\n\n"
                        + "\n\n---\n\n".join(results))
            except RuntimeError as e:
                return str(e)

        elif action == "user_info":
            if not self._is_op_enabled("x_user_info"):
                return "User info is disabled in X tool settings."
            if not username:
                return "Please provide a username."
            try:
                data = self._api_request(
                    "GET", f"users/by/username/{username}", token,
                    params={"user.fields": _USER_FIELDS},
                )
                user = data.get("data")
                if not user:
                    return f"User @{username} not found."
                return _format_user(user)
            except RuntimeError as e:
                return str(e)

        else:
            return f"Unknown read action: {action}. Use: search, read_tweet, timeline, mentions, user_info"

    # ── Post operations ──────────────────────────────────────────────────────

    def _x_post(self, action: str, text: str | None = None,
                tweet_id: str | None = None,
                media_paths: list[str] | None = None, *, account: str | None = None,
                post_at: str | None = None) -> str:
        """Post, reply, quote or delete as the named account, once the person approves exactly that; at its
        time slot (post_at) when it has one."""
        operation = {"post": "x_post_tweet", "reply": "x_reply", "quote": "x_quote",
                     "delete": "x_delete_tweet"}.get(action)
        if operation is None:
            return f"Unknown post action: {action}. Use: post, reply, quote, delete"
        if not self._is_op_enabled(operation):
            return f"{action.capitalize()} is turned off in Settings › Apps › X."
        try:
            words, target = x_posting.check_text(action, text, tweet_id)
            chosen = x_accounts.resolve(account, required=True)
            x_posting.check_length(chosen, action, words)
            from row_bot.tool_outputs import current_thread_id

            media = (x_posting.media_facts(media_paths, str(current_thread_id() or "")) if action != "delete"
                     else [])
            if media and not x_accounts.can_post_media(chosen):
                # Before any card: the person reconnects once, then the post with its pictures can go.
                return (f"{x_accounts.media_reconnect(chosen)} Ask the person (ask_person) to do that, with Done as "
                        "the recommended option, then make the post again. Nothing was sent to X.")
            if post_at and action == "delete":
                raise x_posting.XPostError("A delete happens at once; leave post_at out.")
            when = x_posting.slot(post_at)
        except (x_posting.XPostError, XAccountChoice) as exc:
            return str(exc)
        if action in ("reply", "quote") and x_posting.summoned(chosen, target) is False:
            # X's reply rule would refuse it after approval: no card, the draft for the person instead.
            return (x_posting.by_hand(chosen, action, words, target)
                    + "\n\nGive the person the draft and the link. Don't post it another way.")
        target_text = x_posting.read_text(chosen, target) if action == "delete" else ""
        edited = False
        while True:
            try:
                refusal, line = x_posting.approve(chosen, action, words, target, media, target_text=target_text,
                                                  post_at=when)
                break
            except x_posting.PostEdited as edit:
                # The person changed the post on its card (G2): a new card, bound to the edited post.
                try:
                    words, _ = x_posting.check_text(action, edit.text, tweet_id)
                    x_posting.check_length(chosen, action, words)
                except x_posting.XPostError as exc:
                    return f"The person edited the post, but it can't go out as edited: {exc} Nothing was sent to X."
                media, edited = [item for item in media if item.short in edit.kept], True
        if refusal is not None:
            return refusal
        from row_bot.tools.approval_gate import with_approval

        if when is not None:
            kept = x_posting.schedule(chosen, action, words, target, media, when)
            sent = (f"Approved for its time: it goes out as {chosen.handle} at {when:%H:%M} on {when:%a %d %b} "
                    f"(scheduled post {kept['id']}). Nothing is on X yet; the person can cancel it in Home › "
                    "Review until then.")
        else:
            try:
                sent = x_posting.post_as(chosen, action, words, target, media).text
            except x_posting.XPostError as exc:
                sent = str(exc)
        if edited:
            sent = f"The person edited the post on its card, then approved it as edited:\n{words}\n\n{sent}"
        return with_approval(line, sent) if line else sent

    # ── Engage operations ────────────────────────────────────────────────────

    def _x_engage(self, action: str, tweet_id: str, *, account: str | None = None) -> str:
        """Like, repost or bookmark (or undo one) as the named account."""
        try:
            chosen, token = self._signed_in(account, required=True)
        except XAccountChoice as exc:
            return str(exc)
        uid = chosen.id
        who = chosen.handle

        action_map = {
            "like":       ("x_like",      "POST",   f"users/{uid}/likes",
                           {"tweet_id": tweet_id}, f"Liked tweet {tweet_id} as {who}."),
            "unlike":     ("x_unlike",    "DELETE", f"users/{uid}/likes/{tweet_id}",
                           None, f"Unliked tweet {tweet_id} as {who}."),
            "repost":     ("x_repost",    "POST",   f"users/{uid}/retweets",
                           {"tweet_id": tweet_id}, f"Reposted tweet {tweet_id} as {who}."),
            "unrepost":   ("x_unrepost",  "DELETE", f"users/{uid}/retweets/{tweet_id}",
                           None, f"Removed {who}'s repost of tweet {tweet_id}."),
            "bookmark":   ("x_bookmark",  "POST",   f"users/{uid}/bookmarks",
                           {"tweet_id": tweet_id}, f"Bookmarked tweet {tweet_id} as {who}."),
            "unbookmark": ("x_unbookmark", "DELETE", f"users/{uid}/bookmarks/{tweet_id}",
                           None, f"Removed {who}'s bookmark of tweet {tweet_id}."),
        }

        if action not in action_map:
            return f"Unknown engage action: {action}. Use: like, unlike, repost, unrepost, bookmark, unbookmark"

        op_name, method, endpoint, body, success_msg = action_map[action]

        if not self._is_op_enabled(op_name):
            return f"{action.capitalize()} is disabled in X tool settings."

        try:
            self._api_request(method, endpoint, token, json_body=body)
            return success_msg
        except RuntimeError as e:
            return str(e)

    # ── LangChain tool wrappers ──────────────────────────────────────────────

    def _sign_in_usable(self) -> bool:
        """Whether any saved sign-in binds the X tools.

        X is asked once per sign-in and the answer is kept, so a chat's tools never wait on X. An
        access token past its time is refreshed by the call that needs it, which also says if X
        refuses. A check that could not reach X keeps the tools bound: the call reports the error.
        Only X refusing every sign-in leaves them out (until a new sign-in).
        """
        sign_ins = [(account, x_accounts.token(account)) for account in x_accounts.listed()]
        sign_ins = [(account, token) for account, token in sign_ins if token]
        if not sign_ins:
            # The sign-in from before 5.3 binds them too; the first call makes it an account.
            return x_accounts.legacy_pending()
        for account, token in sign_ins:
            key = _sign_in_key(token)
            known = _SIGN_IN_VERDICTS.get(key)
            if known is None:
                if x_accounts.expired(token):
                    return True
                status, _ = self._check_account(account)
                known = status not in ("missing", "expired")
                current = x_accounts.token(account) or token  # A refresh during the check saved a new one.
                for sign_in in {key, _sign_in_key(current)}:
                    _SIGN_IN_VERDICTS[sign_in] = known
                while len(_SIGN_IN_VERDICTS) > _SIGN_IN_VERDICTS_MAX:
                    _SIGN_IN_VERDICTS.pop(next(iter(_SIGN_IN_VERDICTS)))
            if known:
                return True
        return False

    def as_langchain_tools(self) -> list:
        """Return the X tools based on configured operations."""
        if not self.has_credentials():
            return []
        if not self._sign_in_usable():
            return []

        tools = []
        tool_instance = self

        def _run_accounts() -> str:
            return tool_instance._x_accounts()

        tools.append(StructuredTool.from_function(
            func=_run_accounts,
            name="x_accounts",
            description=(
                "List the connected X accounts: handle, name, numeric ID and whether each is signed in. "
                "Read-only. Use it to know which account to name in x_post, x_engage and x_read."
            ),
            args_schema=_XAccountsInput,
        ))

        # ── x_read ───────────────────────────────────────────────────────
        read_ops = self.get_config("read_operations", _READ_OPS)
        if any(op in read_ops for op in _READ_OPS):

            def _run_read(action: str, query: str | None = None,
                          tweet_id: str | None = None,
                          username: str | None = None,
                          max_results: int = 10,
                          start_time: str | None = None,
                          end_time: str | None = None,
                          account: str | None = None) -> str:
                try:
                    return tool_instance._x_read(
                        action, query, tweet_id, username, max_results,
                        start_time, end_time, account=account
                    )
                except Exception as exc:
                    logger.error("x_read error: %s", type(exc).__name__, exc_info=True)
                    return f"Error in X read: {type(exc).__name__}"

            tools.append(StructuredTool.from_function(
                func=_run_read,
                name="x_read",
                description=(
                    "Read from X (Twitter). Actions: "
                    "'search' (query required; use start_time/end_time for "
                    "date filtering — do NOT put 'since:' or 'until:' in the query), "
                    "'read_tweet' (tweet_id required), "
                    "'timeline' (optional username, defaults to the account's own posts), "
                    "'mentions' (the account's recent mentions), "
                    "'user_info' (username required)."
                ),
                args_schema=_XReadInput,
            ))

        # ── x_post ───────────────────────────────────────────────────────
        post_ops = self.get_config("post_operations", _POST_OPS)
        if any(op in post_ops for op in _POST_OPS):

            def _run_post(action: str, text: str | None = None,
                          tweet_id: str | None = None,
                          media_paths: list[str] | None = None,
                          account: str | None = None, post_at: str | None = None) -> str:
                try:
                    return tool_instance._x_post(
                        action, text, tweet_id, media_paths, account=account, post_at=post_at
                    )
                except GraphBubbleUp:
                    raise  # The approval card: the conversation waits for the person.
                except Exception as exc:
                    logger.error("x_post error: %s", type(exc).__name__, exc_info=True)
                    return f"Error in X post: {type(exc).__name__}"

            tools.append(StructuredTool.from_function(
                func=_run_post,
                name="x_post",
                description=(
                    "Post on X (Twitter) as one connected account. Actions: "
                    "'post' (text required, optional media_paths), "
                    "'reply' (text + tweet_id required), "
                    "'quote' (text + tweet_id required), "
                    "'delete' (tweet_id required). "
                    "Name the account; with several connected a call without one is refused. "
                    "Every call asks the person on a card with the account, the full text and the media; "
                    "the result gives the post's link."
                ),
                args_schema=_XPostInput,
                # It asks for its own approval, bound to the account, text and media it sends.
                metadata={"row_bot_gates_itself": True},
            ))

        # ── x_engage ─────────────────────────────────────────────────────
        engage_ops = self.get_config("engage_operations", _ENGAGE_OPS)
        if any(op in engage_ops for op in _ENGAGE_OPS):

            def _run_engage(action: str, tweet_id: str, account: str | None = None) -> str:
                try:
                    return tool_instance._x_engage(action, tweet_id, account=account)
                except Exception as exc:
                    logger.error("x_engage error: %s", type(exc).__name__, exc_info=True)
                    return f"Error in X engage: {type(exc).__name__}"

            tools.append(StructuredTool.from_function(
                func=_run_engage,
                name="x_engage",
                description=(
                    "Engage with tweets on X (Twitter) as one connected account. Actions: "
                    "'like', 'unlike', 'repost', 'unrepost', "
                    "'bookmark', 'unbookmark'. "
                    "Requires tweet_id; name the account when several are connected."
                ),
                args_schema=_XEngageInput,
            ))

        return tools

    def execute(self, query: str) -> str:
        return "Use the individual X operations (x_accounts, x_read, x_post, x_engage) instead."


registry.register(XTool())
