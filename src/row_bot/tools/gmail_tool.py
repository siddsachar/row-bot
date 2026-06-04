"""Gmail tool — search, read, draft, and send emails via the Gmail API.

Custom send/draft implementations replace the LangChain defaults to add
file attachment support.
"""

from __future__ import annotations

import base64
import email.encoders
import email.mime.base
import email.mime.multipart
import email.mime.text
import logging
import mimetypes
import os
import pathlib
from pathlib import Path
from typing import List, Optional, Union

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from row_bot.data_paths import get_row_bot_data_dir
from row_bot.tools.base import BaseTool
from row_bot.tools import registry

logger = logging.getLogger(__name__)

# Credential / token files live in the Row-Bot data directory.
_DATA_DIR = get_row_bot_data_dir()
_GMAIL_DIR = _DATA_DIR / "gmail"
_GMAIL_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_CREDENTIALS_PATH = str(_GMAIL_DIR / "credentials.json")
DEFAULT_TOKEN_PATH = str(_GMAIL_DIR / "token.json")

# Gmail operations — grouped by risk level
_READ_OPS = ["search_gmail", "get_gmail_message", "get_gmail_thread"]
_COMPOSE_OPS = ["create_gmail_draft"]
_SEND_OPS = ["send_gmail_message"]
ALL_OPERATIONS = _READ_OPS + _COMPOSE_OPS + _SEND_OPS
DEFAULT_OPERATIONS = _READ_OPS + _COMPOSE_OPS  # send disabled by default

# Full access scope
GMAIL_SCOPES = ["https://mail.google.com/"]


def _check_google_token(token_path: str) -> tuple[str, str]:
    """Probe a Google OAuth *token_path* and attempt silent refresh.

    Returns ``(status, detail)`` — see ``GmailTool.check_token_health``.
    """
    if not os.path.isfile(token_path):
        return ("missing", "No token file found")
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request

        creds = Credentials.from_authorized_user_file(token_path)
        if creds.valid:
            return ("valid", "Token is valid")
        # Access token expired — try silent refresh
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                # Persist the refreshed token
                pathlib.Path(token_path).write_text(creds.to_json())
                return ("refreshed", "Token refreshed successfully")
            except Exception as exc:
                err = str(exc).lower()
                if "invalid_grant" in err or "revoked" in err:
                    return ("expired", "Refresh token expired or revoked — re-authenticate in Settings")
                return ("error", f"Refresh failed: {exc}")
        return ("expired", "Token expired and no refresh token available")
    except Exception as exc:
        return ("error", f"Token check failed: {exc}")


# ── Path resolution (shared with Telegram tool) ─────────────────────────

def _resolve_file_path(file_path: str) -> str:
    """Resolve relative paths against workspace root, tracker exports, cwd."""
    p = Path(file_path)
    if p.is_absolute() and p.is_file():
        return str(p)
    try:
        from row_bot.tools import registry as _reg
        fs_tool = _reg.get_tool("filesystem")
        if fs_tool:
            ws_root = fs_tool.get_config("workspace_root", "")
            if ws_root:
                candidate = Path(ws_root) / p
                if candidate.is_file():
                    return str(candidate.resolve())
    except Exception:
        pass
    try:
        tracker_exports = _DATA_DIR / "tracker" / "exports"
        candidate = tracker_exports / p
        if candidate.is_file():
            return str(candidate.resolve())
    except Exception:
        pass
    candidate = Path.cwd() / p
    if candidate.is_file():
        return str(candidate.resolve())
    return file_path


# ── Custom send / draft with attachment support ──────────────────────────

class _SendMessageInput(BaseModel):
    message: str = Field(description="The email body text.")
    to: Union[str, List[str]] = Field(description="Recipient email address(es).")
    subject: str = Field(description="The email subject line.")
    cc: Optional[Union[str, List[str]]] = Field(
        default=None, description="CC recipients.",
    )
    bcc: Optional[Union[str, List[str]]] = Field(
        default=None, description="BCC recipients.",
    )
    attachments: Optional[List[str]] = Field(
        default=None,
        description=(
            "Optional list of file paths to attach. Accepts workspace-relative "
            "paths (e.g. 'report.pdf') or absolute paths."
        ),
    )


class _CreateDraftInput(BaseModel):
    message: str = Field(description="The draft email body text.")
    to: Union[str, List[str]] = Field(description="Recipient email address(es).")
    subject: str = Field(description="The email subject line.")
    cc: Optional[Union[str, List[str]]] = Field(
        default=None, description="CC recipients.",
    )
    bcc: Optional[Union[str, List[str]]] = Field(
        default=None, description="BCC recipients.",
    )
    attachments: Optional[List[str]] = Field(
        default=None,
        description=(
            "Optional list of file paths to attach. Accepts workspace-relative "
            "paths (e.g. 'report.pdf') or absolute paths."
        ),
    )


def _build_mime_message(
    body: str,
    to: Union[str, List[str]],
    subject: str,
    cc: Optional[Union[str, List[str]]] = None,
    bcc: Optional[Union[str, List[str]]] = None,
    attachments: Optional[List[str]] = None,
) -> email.mime.multipart.MIMEMultipart:
    """Build a MIME message with optional file attachments."""
    mime = email.mime.multipart.MIMEMultipart()
    mime.attach(email.mime.text.MIMEText(body, "plain", "utf-8"))
    mime["To"] = ", ".join(to) if isinstance(to, list) else to
    mime["Subject"] = subject
    if cc:
        mime["Cc"] = ", ".join(cc) if isinstance(cc, list) else cc
    if bcc:
        mime["Bcc"] = ", ".join(bcc) if isinstance(bcc, list) else bcc

    for fp in (attachments or []):
        resolved = _resolve_file_path(fp)
        if not os.path.isfile(resolved):
            logger.warning("Attachment not found, skipping: %s", fp)
            continue
        content_type, _ = mimetypes.guess_type(resolved)
        if content_type is None:
            content_type = "application/octet-stream"
        main_type, sub_type = content_type.split("/", 1)
        with open(resolved, "rb") as f:
            part = email.mime.base.MIMEBase(main_type, sub_type)
            part.set_payload(f.read())
        email.encoders.encode_base64(part)
        part.add_header(
            "Content-Disposition", "attachment",
            filename=os.path.basename(resolved),
        )
        mime.attach(part)

    return mime


def _make_custom_send(api_resource):
    """Return a send function bound to *api_resource*."""
    def _send_gmail_message(
        message: str,
        to: Union[str, List[str]],
        subject: str,
        cc: Optional[Union[str, List[str]]] = None,
        bcc: Optional[Union[str, List[str]]] = None,
        attachments: Optional[List[str]] = None,
    ) -> str:
        try:
            mime = _build_mime_message(message, to, subject, cc, bcc, attachments)
            raw = base64.urlsafe_b64encode(mime.as_bytes()).decode("ascii")
            sent = (
                api_resource.users()
                .messages()
                .send(userId="me", body={"raw": raw})
                .execute()
            )
            att_note = ""
            if attachments:
                resolved = [_resolve_file_path(a) for a in attachments if os.path.isfile(_resolve_file_path(a))]
                if resolved:
                    att_note = f" with {len(resolved)} attachment(s)"
            return f"Message sent{att_note}. Message Id: {sent['id']}"
        except Exception as exc:
            return f"Error sending email: {exc}"
    return _send_gmail_message


def _make_custom_draft(api_resource):
    """Return a draft-creation function bound to *api_resource*."""
    def _create_gmail_draft(
        message: str,
        to: Union[str, List[str]],
        subject: str,
        cc: Optional[Union[str, List[str]]] = None,
        bcc: Optional[Union[str, List[str]]] = None,
        attachments: Optional[List[str]] = None,
    ) -> str:
        try:
            mime = _build_mime_message(message, to, subject, cc, bcc, attachments)
            raw = base64.urlsafe_b64encode(mime.as_bytes()).decode("ascii")
            draft = (
                api_resource.users()
                .drafts()
                .create(userId="me", body={"message": {"raw": raw}})
                .execute()
            )
            att_note = ""
            if attachments:
                resolved = [_resolve_file_path(a) for a in attachments if os.path.isfile(_resolve_file_path(a))]
                if resolved:
                    att_note = f" with {len(resolved)} attachment(s)"
            return f"Draft created{att_note}. Draft Id: {draft['id']}"
        except Exception as exc:
            return f"Error creating draft: {exc}"
    return _create_gmail_draft


class GmailTool(BaseTool):

    @property
    def name(self) -> str:
        return "gmail"

    @property
    def display_name(self) -> str:
        return "📧 Gmail"

    @property
    def description(self) -> str:
        return (
            "Search, read, draft, and send emails via Gmail. "
            "Use this when the user asks about emails, wants to search "
            "their inbox, read messages, draft, or send emails."
        )

    @property
    def destructive_tool_names(self) -> set[str]:
        return {"send_gmail_message"}

    @property
    def enabled_by_default(self) -> bool:
        return False  # Must set up OAuth credentials first

    @property
    def required_api_keys(self) -> dict[str, str]:
        return {}

    @property
    def config_schema(self) -> dict[str, dict]:
        return {
            "credentials_path": {
                "label": "credentials.json path",
                "type": "text",
                "default": DEFAULT_CREDENTIALS_PATH,
            },
            "selected_operations": {
                "label": "Allowed operations",
                "type": "multicheck",
                "default": DEFAULT_OPERATIONS,
                "options": ALL_OPERATIONS,
            },
        }

    # ── Auth helpers ─────────────────────────────────────────────────────────
    def _get_credentials_path(self) -> str:
        return self.get_config("credentials_path", DEFAULT_CREDENTIALS_PATH)

    def _get_token_path(self) -> str:
        return DEFAULT_TOKEN_PATH

    def has_credentials_file(self) -> bool:
        return os.path.isfile(self._get_credentials_path())

    def is_authenticated(self) -> bool:
        return os.path.isfile(self._get_token_path())

    def check_token_health(self) -> tuple[str, str]:
        """Probe the OAuth token and attempt silent refresh if needed.

        Returns
        -------
        (status, detail) where status is one of:
        - ``"valid"``   — token is fresh, no action needed
        - ``"refreshed"`` — access token was expired, silently refreshed
        - ``"expired"`` — refresh token is revoked; user must re-authenticate
        - ``"missing"`` — no token.json found
        - ``"error"``   — unexpected error during check
        """
        return _check_google_token(self._get_token_path())

    def authenticate(self):
        """Run the OAuth consent flow (opens browser).  Must be called
        when ``credentials.json`` exists but ``token.json`` does not."""
        from langchain_google_community.gmail.utils import get_gmail_credentials

        get_gmail_credentials(
            token_file=self._get_token_path(),
            scopes=GMAIL_SCOPES,
            client_sercret_file=self._get_credentials_path(),
        )

    def _build_api_resource(self):
        from langchain_google_community.gmail.utils import (
            build_resource_service,
            get_gmail_credentials,
        )

        credentials = get_gmail_credentials(
            token_file=self._get_token_path(),
            scopes=GMAIL_SCOPES,
            client_sercret_file=self._get_credentials_path(),
        )
        return build_resource_service(credentials=credentials)

    # ── Build toolkit tools ──────────────────────────────────────────────────
    def _get_selected_operations(self) -> list[str]:
        ops = self.get_config("selected_operations", DEFAULT_OPERATIONS)
        return [op for op in ops if op in ALL_OPERATIONS]

    def as_langchain_tools(self) -> list:
        """Return the selected Gmail tools using stored OAuth credentials."""
        if not self.has_credentials_file():
            return []
        if not self.is_authenticated():
            return []

        try:
            api_resource = self._build_api_resource()
        except Exception as exc:
            logger.warning("Gmail tools unavailable — %s", exc)
            return []

        selected = self._get_selected_operations()
        tools = []

        # Read tools from LangChain toolkit
        if any(op in selected for op in _READ_OPS):
            from langchain_google_community import GmailToolkit
            toolkit = GmailToolkit(api_resource=api_resource)
            all_lc_tools = toolkit.get_tools()
            for t in all_lc_tools:
                if t.name in selected and t.name in _READ_OPS:
                    tools.append(_wrap_gmail_tool_empty_guard(t))

        # Custom draft with attachments
        if "create_gmail_draft" in selected:
            tools.append(StructuredTool.from_function(
                func=_make_custom_draft(api_resource),
                name="create_gmail_draft",
                description=(
                    "Create a Gmail draft email. Supports file attachments — "
                    "pass workspace-relative or absolute file paths in the "
                    "attachments list. Use this when the user wants to draft "
                    "an email, optionally with files attached."
                ),
                args_schema=_CreateDraftInput,
            ))

        # Custom send with attachments
        if "send_gmail_message" in selected:
            tools.append(StructuredTool.from_function(
                func=_make_custom_send(api_resource),
                name="send_gmail_message",
                description=(
                    "Send an email via Gmail. Supports file attachments — "
                    "pass workspace-relative or absolute file paths in the "
                    "attachments list. Use this when the user wants to send "
                    "an email, optionally with files attached."
                ),
                args_schema=_SendMessageInput,
            ))

        return tools

    def execute(self, query: str) -> str:
        return "Use the individual Gmail operations instead."


# ── Empty-result guard ───────────────────────────────────────────────────

_EMPTY_MESSAGES: dict[str, str] = {
    "search_gmail": (
        "No emails were found matching that query. "
        "The inbox search returned zero results."
    ),
    "get_gmail_message": "No message content was returned.",
    "get_gmail_thread": "No thread content was returned.",
}
_DEFAULT_EMPTY_MSG = "The Gmail tool returned no results."


def _wrap_gmail_tool_empty_guard(tool):
    """Wrap a LangChain Gmail tool so that empty / blank results are
    replaced with an explicit 'no results' message.  This prevents the
    LLM from hallucinating fake emails when the API returns nothing."""
    from langchain_core.tools import StructuredTool

    original_func = tool.func if hasattr(tool, "func") else None
    if original_func is None:
        return tool

    empty_msg = _EMPTY_MESSAGES.get(tool.name, _DEFAULT_EMPTY_MSG)

    def _guarded(*args, **kwargs):
        result = original_func(*args, **kwargs)
        # Treat None, empty string, empty list, or whitespace-only as empty
        if not result or (isinstance(result, str) and not result.strip()):
            return empty_msg
        # Also catch list-like results that stringified to '[]'
        if isinstance(result, str) and result.strip() in ("[]", "[]\n", ""):
            return empty_msg
        return result

    return StructuredTool.from_function(
        func=_guarded,
        name=tool.name,
        description=tool.description,
    )


registry.register(GmailTool())
