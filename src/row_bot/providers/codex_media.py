"""Images with a ChatGPT plan.

The ChatGPT sign-in that runs chat also reaches the Codex backend's own image
endpoints (``images/generations`` and ``images/edits``), which the Codex CLI
uses for its image tool. The plan's limits apply, used faster than by messages,
and a Free plan has none.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from row_bot.providers import codex as codex_auth
from row_bot.providers.transports.codex_responses import (
    CODEX_ORIGINATOR,
    CODEX_RESPONSES_BASE_URL,
    CODEX_USER_AGENT,
)

CODEX_IMAGE_MODEL = "gpt-image-2"
# An image can take minutes; the Codex CLI and Hermes wait this long.
CODEX_IMAGE_TIMEOUT = 300.0
_QUALITIES = {"low", "medium", "high"}
# A plan that can't make images (Codex offers none on Free).
NO_IMAGES_PLANS = {"free"}


def codex_images_plan_note() -> str:
    """Why the signed-in ChatGPT plan can't make images, or empty when it can."""
    plan = (codex_auth.codex_runtime_credentials(refresh_if_needed=False).plan_type or "").lower()
    return "Images need a paid ChatGPT plan." if plan in NO_IMAGES_PLANS else ""


def codex_image(model: str, prompt: str, *, size: str = "auto", quality: str = "auto",
                source: str = "", http_client: Any | None = None) -> str:
    """Make (or, with a source data URL, edit) one image; returns its base64."""
    note = codex_images_plan_note()
    if note:
        raise RuntimeError(note)
    body: dict[str, Any] = {
        "model": model or CODEX_IMAGE_MODEL,
        "prompt": prompt,
        "n": 1,
        "background": "opaque",
        "quality": quality if quality in _QUALITIES else "auto",
        "size": size or "auto",
    }
    if source:
        body["images"] = [{"image_url": source}]
    response = _post("images/edits" if source else "images/generations", body, http_client)
    items = response.json().get("data") or []
    return str(items[0].get("b64_json") or "") if items and isinstance(items[0], dict) else ""


def _post(path: str, body: dict[str, Any], http_client: Any | None) -> Any:
    from row_bot.providers.transports.codex_responses import _new_http_client

    client = http_client or _new_http_client(CODEX_IMAGE_TIMEOUT)
    try:
        url = f"{CODEX_RESPONSES_BASE_URL}/{path}"
        response = client.post(url, json=body, headers=_headers(), timeout=CODEX_IMAGE_TIMEOUT)
        if response.status_code == 401:
            credentials = codex_auth.codex_runtime_credentials(refresh_if_needed=False)
            if credentials.refresh_token:
                codex_auth.save_codex_oauth_tokens(codex_auth.refresh_codex_token(credentials.refresh_token))
                response = client.post(url, json=body, headers=_headers(), timeout=CODEX_IMAGE_TIMEOUT)
        if not 200 <= response.status_code < 300:
            raise RuntimeError(_failure(response))
        return response
    finally:
        if http_client is None:
            client.close()


def _headers() -> dict[str, str]:
    from row_bot.providers.media_auth import current_media_auth

    credentials = codex_auth.codex_runtime_credentials(refresh_if_needed=True)
    if not credentials.access_token or not credentials.account_id:
        raise RuntimeError(codex_auth.codex_reconnect_message())
    # A Buddy Hatch was approved for one ChatGPT account (its captured credential).
    captured = current_media_auth("codex")
    if captured is not None and captured.credential != credentials.account_id:
        raise ValueError("media_auth_unavailable")
    return {
        "Authorization": f"Bearer {credentials.access_token}",
        "ChatGPT-Account-ID": credentials.account_id,
        "originator": CODEX_ORIGINATOR,
        "User-Agent": CODEX_USER_AGENT,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _failure(response: Any) -> str:
    try:
        payload = response.json()
    except Exception:
        payload = {}
    error = payload.get("error") if isinstance(payload, dict) and isinstance(payload.get("error"), dict) else {}
    if response.status_code == 429 or error.get("type") == "usage_limit_reached":
        resets = error.get("resets_at")
        when = ""
        if isinstance(resets, (int, float)) and resets > 0:
            when = f" It resets at {datetime.fromtimestamp(resets).strftime('%H:%M on %d %b')}."
        return ("Your ChatGPT plan's image limit is reached." + when
                + " Choose another image model in Settings › Models to keep going.")
    detail = str(error.get("message") or (payload.get("detail") if isinstance(payload, dict) else "") or "")
    return f"ChatGPT couldn't make the image (HTTP {response.status_code}){': ' + detail[:300] if detail else ''}."
