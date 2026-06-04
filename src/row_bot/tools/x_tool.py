"""X (Twitter) tool — read, post, and engage on X via the X API v2.

Uses native httpx against the X API v2 with OAuth 2.0 PKCE authentication.
No external dependencies (no tweepy, no xurl CLI).
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import pathlib
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import List, Optional
from urllib.parse import urlencode, urlparse, parse_qs

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from row_bot.data_paths import get_row_bot_data_dir
from row_bot.tools.base import BaseTool
from row_bot.tools import registry

logger = logging.getLogger(__name__)

# ── Data paths ───────────────────────────────────────────────────────────────
_DATA_DIR = get_row_bot_data_dir()
_X_DIR = _DATA_DIR / "x"
_X_DIR.mkdir(parents=True, exist_ok=True)
_TOKEN_PATH = _X_DIR / "token.json"
_TIER_PATH = _X_DIR / "tier_info.json"

# ── X API v2 base URL ───────────────────────────────────────────────────────
_API_BASE = "https://api.x.com/2"
_AUTH_URL = "https://x.com/i/oauth2/authorize"
_TOKEN_URL = "https://api.x.com/2/oauth2/token"

# ── OAuth 2.0 scopes ────────────────────────────────────────────────────────
_SCOPES = (
    "tweet.read tweet.write users.read like.read like.write "
    "bookmark.read bookmark.write offline.access"
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


# ── Token management ────────────────────────────────────────────────────────

def _load_token() -> dict | None:
    """Load persisted OAuth token from disk."""
    if _TOKEN_PATH.is_file():
        try:
            with open(_TOKEN_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            logger.warning("Failed to load X token from %s", _TOKEN_PATH)
    return None


def _save_token(token: dict):
    """Persist OAuth token to disk."""
    _TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_TOKEN_PATH, "w") as f:
        json.dump(token, f, indent=2)


def _token_expired(token: dict) -> bool:
    """Check if the access token has expired (with 60s buffer)."""
    expires_at = token.get("expires_at", 0)
    return time.time() >= (expires_at - 60)


def _refresh_token(token: dict, client_id: str, client_secret: str) -> dict | None:
    """Attempt to refresh the access token using the refresh token."""
    import httpx

    refresh_tok = token.get("refresh_token")
    if not refresh_tok:
        return None
    try:
        resp = httpx.post(
            _TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_tok,
                "client_id": client_id,
            },
            auth=(client_id, client_secret),
            timeout=15,
        )
        resp.raise_for_status()
        new_token = resp.json()
        new_token["expires_at"] = time.time() + new_token.get("expires_in", 7200)
        _save_token(new_token)
        return new_token
    except Exception as exc:
        logger.error("X token refresh failed: %s", exc)
        return None


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


def _run_oauth_flow(client_id: str, client_secret: str) -> dict:
    """Run the full OAuth 2.0 PKCE flow.

    Opens the user's browser to X's authorization page, starts a local
    HTTP server on a fixed port to receive the callback, and exchanges
    the code for tokens.

    **Important**: This function blocks until the user completes the flow
    or the 120-second timeout expires.  Call from a background thread
    (e.g. ``await run.io_bound(_run_oauth_flow, ...)`` in NiceGUI).

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
    import httpx
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

    # Build the authorization URL
    auth_params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": _OAUTH_REDIRECT_URI,
        "scope": _SCOPES,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    auth_url = f"{_AUTH_URL}?{urlencode(auth_params)}"

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

    # Exchange code for tokens
    resp = httpx.post(
        _TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": _OAuthCallbackHandler.auth_code,
            "redirect_uri": _OAUTH_REDIRECT_URI,
            "client_id": client_id,
            "code_verifier": code_verifier,
        },
        auth=(client_id, client_secret),
        timeout=15,
    )
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
    _save_token(token)
    return token


# ── Pydantic input schemas ──────────────────────────────────────────────────

class _XReadInput(BaseModel):
    action: str = Field(
        description=(
            "The read action to perform. One of: "
            "'search' (search recent tweets), "
            "'read_tweet' (get a specific tweet by ID), "
            "'timeline' (get a user's recent tweets), "
            "'mentions' (get your recent mentions), "
            "'user_info' (get info about a user)."
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


class _XPostInput(BaseModel):
    action: str = Field(
        description=(
            "The post action to perform. One of: "
            "'post' (create a new tweet), "
            "'reply' (reply to a tweet), "
            "'quote' (quote a tweet), "
            "'delete' (delete a tweet)."
        )
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
            "Optional list of file paths for media to attach. "
            "Supports images (JPEG, PNG, GIF) and videos (MP4). "
            "Max 4 images or 1 video per tweet."
        ),
    )


class _XEngageInput(BaseModel):
    action: str = Field(
        description=(
            "The engagement action to perform. One of: "
            "'like', 'unlike', 'repost', 'unrepost', "
            "'bookmark', 'unbookmark'."
        )
    )
    tweet_id: str = Field(
        description="The tweet ID to engage with.",
    )


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
        parts.append(
            f"\n❤️ {metrics.get('like_count', 0)}  "
            f"🔁 {metrics.get('retweet_count', 0)}  "
            f"💬 {metrics.get('reply_count', 0)}  "
            f"👁️ {metrics.get('impression_count', 0)}"
        )
    return "\n".join(parts)


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


# ── Media upload (v1.1 chunked — required for v2 tweet creation) ─────────
# X API v2 does not have a media upload endpoint; media must be uploaded
# via the v1.1 media/upload endpoint and then attached to v2 tweets.

_MEDIA_UPLOAD_URL = "https://upload.twitter.com/1.1/media/upload.json"


def _upload_media(file_path: str, token: dict) -> str | None:
    """Upload a media file to X and return the media_id string.

    Uses the v1.1 chunked media upload endpoint.
    Note: Media upload requires OAuth 1.0a or OAuth 2.0 user token with
    appropriate scopes. For OAuth 2.0 PKCE, the media upload may require
    additional permissions depending on the app tier.
    """
    import httpx

    path = pathlib.Path(file_path)
    if not path.is_file():
        logger.warning("Media file not found: %s", file_path)
        return None

    mime_type, _ = __import__("mimetypes").guess_type(str(path))
    if not mime_type:
        mime_type = "application/octet-stream"

    headers = {"Authorization": f"Bearer {token['access_token']}"}
    file_size = path.stat().st_size

    # INIT
    init_resp = httpx.post(
        _MEDIA_UPLOAD_URL,
        data={
            "command": "INIT",
            "total_bytes": str(file_size),
            "media_type": mime_type,
        },
        headers=headers,
        timeout=30,
    )
    if init_resp.status_code != 202 and init_resp.status_code != 200:
        logger.error("Media upload INIT failed: %s %s",
                      init_resp.status_code, init_resp.text)
        return None
    media_id = init_resp.json().get("media_id_string")

    # APPEND (chunked — 5MB per chunk)
    chunk_size = 5 * 1024 * 1024
    with open(path, "rb") as f:
        segment = 0
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            append_resp = httpx.post(
                _MEDIA_UPLOAD_URL,
                data={"command": "APPEND", "media_id": media_id,
                      "segment_index": str(segment)},
                files={"media_data": chunk},
                headers=headers,
                timeout=60,
            )
            if append_resp.status_code not in (200, 202, 204):
                logger.error("Media upload APPEND failed: %s %s",
                              append_resp.status_code, append_resp.text)
                return None
            segment += 1

    # FINALIZE
    finalize_resp = httpx.post(
        _MEDIA_UPLOAD_URL,
        data={"command": "FINALIZE", "media_id": media_id},
        headers=headers,
        timeout=30,
    )
    if finalize_resp.status_code not in (200, 201):
        logger.error("Media upload FINALIZE failed: %s %s",
                      finalize_resp.status_code, finalize_resp.text)
        return None

    return media_id


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
        self._user_id: str | None = None
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
            "Read, post, and engage on X (Twitter). "
            "Use this when the user asks about tweets, X timelines, "
            "posting on X, liking, reposting, or bookmarking tweets."
        )

    @property
    def destructive_tool_names(self) -> set[str]:
        return {"x_post"}

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

    @property
    def inference_keywords(self) -> list[str]:
        return ["tweet", "twitter", "x.com", "timeline", "retweet", "repost"]

    # ── Auth helpers ─────────────────────────────────────────────────────────

    def _get_client_id(self) -> str:
        return os.environ.get("X_CLIENT_ID", "")

    def _get_client_secret(self) -> str:
        return os.environ.get("X_CLIENT_SECRET", "")

    def has_credentials(self) -> bool:
        """Check if Client ID and Secret are configured."""
        return bool(self._get_client_id() and self._get_client_secret())

    def is_authenticated(self) -> bool:
        """Check if a token file exists."""
        return _TOKEN_PATH.is_file()

    def check_token_health(self) -> tuple[str, str]:
        """Probe the OAuth token and attempt silent refresh if needed.

        Returns (status, detail) where status is one of:
        - "valid"     — token is fresh and verified against API
        - "refreshed" — token was expired, silently refreshed
        - "expired"   — refresh token failed; re-authenticate
        - "missing"   — no token file
        - "error"     — unexpected error (e.g. bad credentials)
        """
        token = _load_token()
        if not token:
            return ("missing", "No token file found")

        if _token_expired(token):
            # Try silent refresh
            new_token = _refresh_token(
                token, self._get_client_id(), self._get_client_secret()
            )
            if new_token:
                return ("refreshed", "Token refreshed successfully")
            return ("expired", "Token expired — re-authenticate in Settings")

        # Token not expired — verify it actually works
        try:
            import httpx
            resp = httpx.get(
                f"{_API_BASE}/users/me",
                headers={"Authorization": f"Bearer {token['access_token']}"},
                params={"user.fields": "username"},
                timeout=10,
            )
            if resp.status_code == 200:
                return ("valid", "Token is valid")
            if resp.status_code == 429:
                # Rate limiting is transient and does not indicate an invalid token.
                return ("valid", "Token is valid (rate limited by X API)")
            if resp.status_code == 401:
                return ("expired", "Token rejected by X API — re-authenticate in Settings")
            return ("error", f"X API returned {resp.status_code}")
        except Exception as exc:
            logger.warning("X token health check failed: %s", exc)
            return ("error", f"Could not verify token: {exc}")

    def authenticate(self):
        """Run the OAuth 2.0 PKCE flow (opens browser)."""
        token = _run_oauth_flow(self._get_client_id(), self._get_client_secret())
        # Fetch and cache the authenticated user's ID
        self._fetch_user_id(token)
        return token

    def get_authenticated_username(self) -> str | None:
        """Return the authenticated user's username, or None."""
        token = self._get_valid_token()
        if not token:
            return None
        try:
            import httpx
            resp = httpx.get(
                f"{_API_BASE}/users/me",
                headers={"Authorization": f"Bearer {token['access_token']}"},
                params={"user.fields": "username"},
                timeout=10,
            )
            if resp.status_code == 200:
                return resp.json().get("data", {}).get("username")
        except Exception:
            pass
        return None

    def _get_valid_token(self) -> dict | None:
        """Return a valid token, refreshing if needed."""
        token = _load_token()
        if not token:
            return None
        if _token_expired(token):
            token = _refresh_token(
                token, self._get_client_id(), self._get_client_secret()
            )
        return token

    def _fetch_user_id(self, token: dict) -> str | None:
        """Fetch and cache the authenticated user's ID."""
        import httpx
        try:
            resp = httpx.get(
                f"{_API_BASE}/users/me",
                headers={"Authorization": f"Bearer {token['access_token']}"},
                params={"user.fields": "id,username"},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                self._user_id = data.get("id")
                return self._user_id
        except Exception as exc:
            logger.warning("Failed to fetch X user ID: %s", exc)
        return None

    def _get_user_id(self, token: dict) -> str | None:
        """Get cached user ID or fetch it."""
        if self._user_id:
            return self._user_id
        return self._fetch_user_id(token)

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
        import httpx

        # Check rate limit before calling
        rate_msg = self._rate_limiter.check(endpoint)
        if rate_msg:
            raise RuntimeError(rate_msg)

        url = f"{_API_BASE}/{endpoint.lstrip('/')}"
        headers = {"Authorization": f"Bearer {token['access_token']}"}

        resp = httpx.request(
            method, url,
            headers=headers,
            params=params,
            json=json_body,
            timeout=15,
        )

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
                    f"This operation is not available on your X API tier. "
                    f"Upgrade at developer.x.com to access this feature."
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

    # ── Read operations ──────────────────────────────────────────────────────

    def _x_read(self, action: str, query: str | None = None,
                tweet_id: str | None = None, username: str | None = None,
                max_results: int = 10,
                start_time: str | None = None,
                end_time: str | None = None) -> str:
        """Execute a read operation on X."""
        token = self._get_valid_token()
        if not token:
            return "Not authenticated with X. Please authenticate in Settings → Accounts."

        max_results = max(1, min(max_results, 100))

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
                    uid = self._get_user_id(token)
                    if not uid:
                        return "Could not determine your user ID."

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
                if not tweets:
                    who = f"@{username}" if username else "your"
                    return f"No tweets found in {who} timeline."
                includes = data.get("includes", {})
                results = [_format_tweet(t, includes) for t in tweets]
                who = f"@{username}'s" if username else "Your"
                return f"{who} recent tweets ({len(tweets)}):\n\n" + "\n\n---\n\n".join(results)
            except RuntimeError as e:
                return str(e)

        elif action == "mentions":
            if not self._is_op_enabled("x_mentions"):
                return "Mentions is disabled in X tool settings."
            try:
                uid = self._get_user_id(token)
                if not uid:
                    return "Could not determine your user ID."
                data = self._api_request(
                    "GET", f"users/{uid}/mentions", token,
                    params={
                        "max_results": max_results,
                        "tweet.fields": _TWEET_FIELDS,
                        "expansions": "author_id",
                        "user.fields": "username",
                    },
                )
                tweets = data.get("data", [])
                if not tweets:
                    return "No recent mentions found."
                includes = data.get("includes", {})
                results = [_format_tweet(t, includes) for t in tweets]
                return f"Your recent mentions ({len(tweets)}):\n\n" + "\n\n---\n\n".join(results)
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
                media_paths: list[str] | None = None) -> str:
        """Execute a post/write operation on X."""
        token = self._get_valid_token()
        if not token:
            return "Not authenticated with X. Please authenticate in Settings → Accounts."

        if action == "post":
            if not self._is_op_enabled("x_post_tweet"):
                return "Post is disabled in X tool settings."
            if not text:
                return "Please provide text for the tweet."

            body: dict = {"text": text}
            if media_paths:
                media_ids = self._upload_media_files(media_paths, token)
                if media_ids:
                    body["media"] = {"media_ids": media_ids}

            try:
                data = self._api_request("POST", "tweets", token, json_body=body)
                tid = data.get("data", {}).get("id", "?")
                return f"Tweet posted successfully! Tweet ID: {tid}"
            except RuntimeError as e:
                return str(e)

        elif action == "reply":
            if not self._is_op_enabled("x_reply"):
                return "Reply is disabled in X tool settings."
            if not text:
                return "Please provide text for the reply."
            if not tweet_id:
                return "Please provide the tweet_id to reply to."

            body = {
                "text": text,
                "reply": {"in_reply_to_tweet_id": tweet_id},
            }
            if media_paths:
                media_ids = self._upload_media_files(media_paths, token)
                if media_ids:
                    body["media"] = {"media_ids": media_ids}

            try:
                data = self._api_request("POST", "tweets", token, json_body=body)
                tid = data.get("data", {}).get("id", "?")
                return f"Reply posted successfully! Tweet ID: {tid}"
            except RuntimeError as e:
                return str(e)

        elif action == "quote":
            if not self._is_op_enabled("x_quote"):
                return "Quote is disabled in X tool settings."
            if not text:
                return "Please provide text for the quote tweet."
            if not tweet_id:
                return "Please provide the tweet_id to quote."

            body = {
                "text": text,
                "quote_tweet_id": tweet_id,
            }
            if media_paths:
                media_ids = self._upload_media_files(media_paths, token)
                if media_ids:
                    body["media"] = {"media_ids": media_ids}

            try:
                data = self._api_request("POST", "tweets", token, json_body=body)
                tid = data.get("data", {}).get("id", "?")
                return f"Quote tweet posted successfully! Tweet ID: {tid}"
            except RuntimeError as e:
                return str(e)

        elif action == "delete":
            if not self._is_op_enabled("x_delete_tweet"):
                return "Delete is disabled in X tool settings."
            if not tweet_id:
                return "Please provide the tweet_id to delete."

            try:
                self._api_request("DELETE", f"tweets/{tweet_id}", token)
                return f"Tweet {tweet_id} deleted successfully."
            except RuntimeError as e:
                return str(e)

        else:
            return f"Unknown post action: {action}. Use: post, reply, quote, delete"

    # ── Engage operations ────────────────────────────────────────────────────

    def _x_engage(self, action: str, tweet_id: str) -> str:
        """Execute an engagement operation on X."""
        token = self._get_valid_token()
        if not token:
            return "Not authenticated with X. Please authenticate in Settings → Accounts."

        uid = self._get_user_id(token)
        if not uid:
            return "Could not determine your user ID."

        action_map = {
            "like":       ("x_like",      "POST",   f"users/{uid}/likes",
                           {"tweet_id": tweet_id}, f"Liked tweet {tweet_id}."),
            "unlike":     ("x_unlike",    "DELETE", f"users/{uid}/likes/{tweet_id}",
                           None, f"Unliked tweet {tweet_id}."),
            "repost":     ("x_repost",    "POST",   f"users/{uid}/retweets",
                           {"tweet_id": tweet_id}, f"Reposted tweet {tweet_id}."),
            "unrepost":   ("x_unrepost",  "DELETE", f"users/{uid}/retweets/{tweet_id}",
                           None, f"Removed repost of tweet {tweet_id}."),
            "bookmark":   ("x_bookmark",  "POST",   f"users/{uid}/bookmarks",
                           {"tweet_id": tweet_id}, f"Bookmarked tweet {tweet_id}."),
            "unbookmark": ("x_unbookmark", "DELETE", f"users/{uid}/bookmarks/{tweet_id}",
                           None, f"Removed bookmark of tweet {tweet_id}."),
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

    # ── Media helpers ────────────────────────────────────────────────────────

    def _upload_media_files(self, paths: list[str], token: dict) -> list[str]:
        """Upload multiple media files and return their media IDs."""
        media_ids = []
        for p in paths[:4]:  # Max 4 media per tweet
            mid = _upload_media(p, token)
            if mid:
                media_ids.append(mid)
            else:
                logger.warning("Failed to upload media: %s", p)
        return media_ids

    # ── LangChain tool wrappers ──────────────────────────────────────────────

    def as_langchain_tools(self) -> list:
        """Return the X tools based on configured operations."""
        if not self.has_credentials():
            return []
        if not self.is_authenticated():
            return []

        # Verify token health
        status, _ = self.check_token_health()
        if status in ("missing", "expired", "error"):
            return []

        tools = []

        # ── x_read ───────────────────────────────────────────────────────
        read_ops = self.get_config("read_operations", _READ_OPS)
        if any(op in read_ops for op in _READ_OPS):
            tool_instance = self

            def _run_read(action: str, query: str | None = None,
                          tweet_id: str | None = None,
                          username: str | None = None,
                          max_results: int = 10,
                          start_time: str | None = None,
                          end_time: str | None = None) -> str:
                try:
                    return tool_instance._x_read(
                        action, query, tweet_id, username, max_results,
                        start_time, end_time
                    )
                except Exception as exc:
                    logger.error("x_read error: %s", exc, exc_info=True)
                    return f"Error in X read: {exc}"

            tools.append(StructuredTool.from_function(
                func=_run_read,
                name="x_read",
                description=(
                    "Read from X (Twitter). Actions: "
                    "'search' (query required; use start_time/end_time for "
                    "date filtering — do NOT put 'since:' or 'until:' in the query), "
                    "'read_tweet' (tweet_id required), "
                    "'timeline' (optional username, defaults to your feed), "
                    "'mentions' (your recent mentions), "
                    "'user_info' (username required)."
                ),
                args_schema=_XReadInput,
            ))

        # ── x_post ───────────────────────────────────────────────────────
        post_ops = self.get_config("post_operations", _POST_OPS)
        if any(op in post_ops for op in _POST_OPS):
            tool_instance = self

            def _run_post(action: str, text: str | None = None,
                          tweet_id: str | None = None,
                          media_paths: list[str] | None = None) -> str:
                try:
                    return tool_instance._x_post(
                        action, text, tweet_id, media_paths
                    )
                except Exception as exc:
                    logger.error("x_post error: %s", exc, exc_info=True)
                    return f"Error in X post: {exc}"

            tools.append(StructuredTool.from_function(
                func=_run_post,
                name="x_post",
                description=(
                    "Post on X (Twitter). Actions: "
                    "'post' (text required, optional media_paths), "
                    "'reply' (text + tweet_id required), "
                    "'quote' (text + tweet_id required), "
                    "'delete' (tweet_id required). "
                    "⚠️ Post operations require approval."
                ),
                args_schema=_XPostInput,
            ))

        # ── x_engage ─────────────────────────────────────────────────────
        engage_ops = self.get_config("engage_operations", _ENGAGE_OPS)
        if any(op in engage_ops for op in _ENGAGE_OPS):
            tool_instance = self

            def _run_engage(action: str, tweet_id: str) -> str:
                try:
                    return tool_instance._x_engage(action, tweet_id)
                except Exception as exc:
                    logger.error("x_engage error: %s", exc, exc_info=True)
                    return f"Error in X engage: {exc}"

            tools.append(StructuredTool.from_function(
                func=_run_engage,
                name="x_engage",
                description=(
                    "Engage with tweets on X (Twitter). Actions: "
                    "'like', 'unlike', 'repost', 'unrepost', "
                    "'bookmark', 'unbookmark'. "
                    "Requires tweet_id."
                ),
                args_schema=_XEngageInput,
            ))

        return tools

    def execute(self, query: str) -> str:
        return "Use the individual X operations (x_read, x_post, x_engage) instead."


registry.register(XTool())
