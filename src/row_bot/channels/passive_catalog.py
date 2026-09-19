"""Static metadata for rendering core channels before adapters are loaded.

The real adapters remain the lifecycle and mutation owners.  This catalog is
only a value-free description of the five bundled Settings panels so a local
client can render saved credential state without importing optional SDKs or
starting a channel.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from row_bot.channels.base import ChannelCapabilities, ConfigField


@dataclass(frozen=True)
class PassiveCoreChannel:
    name: str
    display_name: str
    config_fields: tuple[ConfigField, ...]
    capabilities: ChannelCapabilities
    required_fields: tuple[str, ...]
    packaged_configuration: Path | None = None
    passive_only: bool = True

    def is_configured(self) -> None:
        """The redacted control owner derives this from field statuses."""

        return None

    def is_running(self) -> bool:
        """An unloaded adapter is stopped in the current application process."""

        return False


def _secret(
    key: str,
    label: str,
    env_key: str,
    help_text: str,
    *,
    password: bool = True,
) -> ConfigField:
    return ConfigField(
        key=key,
        label=label,
        field_type="password" if password else "text",
        storage="env",
        env_key=env_key,
        help_text=help_text,
    )


def passive_core_channels() -> list[PassiveCoreChannel]:
    """Return the bundled channel descriptions in NiceGUI navigation order."""

    from row_bot.runtime_paths import PACKAGE_DIR

    return [
        PassiveCoreChannel(
            "telegram",
            "Telegram",
            (
                _secret(
                    "bot_token",
                    "Bot Token",
                    "TELEGRAM_BOT_TOKEN",
                    "Bot token from @BotFather",
                ),
                _secret(
                    "user_id",
                    "Your Telegram User ID",
                    "TELEGRAM_USER_ID",
                    "Your numeric user ID (access control)",
                    password=False,
                ),
            ),
            ChannelCapabilities(
                photo_in=True,
                voice_in=True,
                document_in=True,
                photo_out=True,
                document_out=True,
                buttons=True,
                streaming=True,
                typing=True,
                reactions=True,
            ),
            ("bot_token", "user_id"),
        ),
        PassiveCoreChannel(
            "slack",
            "Slack",
            (
                _secret(
                    "bot_token",
                    "Bot Token (xoxb-…)",
                    "SLACK_BOT_TOKEN",
                    "Bot user OAuth token from Slack app settings",
                ),
                _secret(
                    "app_token",
                    "App Token (xapp-…)",
                    "SLACK_APP_TOKEN",
                    "App-level token for Socket Mode",
                ),
                _secret(
                    "user_id",
                    "Your Slack User ID (optional)",
                    "SLACK_USER_ID",
                    "Your Slack user ID for auto-auth (or use DM pairing)",
                    password=False,
                ),
            ),
            ChannelCapabilities(
                photo_in=True,
                voice_in=True,
                document_in=True,
                photo_out=True,
                document_out=True,
                buttons=True,
                streaming=True,
                reactions=True,
                slash_commands=True,
            ),
            ("bot_token", "app_token"),
        ),
        PassiveCoreChannel(
            "sms",
            "SMS",
            (
                _secret(
                    "account_sid",
                    "Account SID",
                    "TWILIO_ACCOUNT_SID",
                    "Twilio Account SID from console",
                ),
                _secret(
                    "auth_token",
                    "Auth Token",
                    "TWILIO_AUTH_TOKEN",
                    "Twilio Auth Token from console",
                ),
                _secret(
                    "phone_number",
                    "Twilio Phone Number",
                    "TWILIO_PHONE_NUMBER",
                    "Your Twilio number in E.164 format (e.g. +1234567890)",
                    password=False,
                ),
                _secret(
                    "user_phone",
                    "Your Phone Number",
                    "SMS_USER_PHONE",
                    "Your phone number for auto-auth (E.164 format)",
                    password=False,
                ),
            ),
            ChannelCapabilities(slash_commands=True),
            ("account_sid", "auth_token", "phone_number"),
        ),
        PassiveCoreChannel(
            "discord",
            "Discord",
            (
                _secret(
                    "bot_token",
                    "Bot Token",
                    "DISCORD_BOT_TOKEN",
                    "Bot token from Discord Developer Portal",
                ),
                _secret(
                    "user_id",
                    "Your Discord User ID (optional)",
                    "DISCORD_USER_ID",
                    "Your Discord user ID for auto-auth (or use DM pairing)",
                    password=False,
                ),
            ),
            ChannelCapabilities(
                photo_in=True,
                voice_in=True,
                document_in=True,
                photo_out=True,
                document_out=True,
                buttons=True,
                streaming=True,
                typing=True,
                reactions=True,
                slash_commands=True,
            ),
            ("bot_token",),
        ),
        PassiveCoreChannel(
            "whatsapp",
            "WhatsApp",
            (
                _secret(
                    "user_phone",
                    "Your Phone Number (optional)",
                    "WHATSAPP_USER_PHONE",
                    "Your phone in E.164 format for auto-auth (or use DM pairing)",
                    password=False,
                ),
            ),
            ChannelCapabilities(
                photo_in=True,
                voice_in=True,
                document_in=True,
                photo_out=True,
                document_out=True,
                streaming=True,
                typing=True,
                reactions=True,
                slash_commands=True,
            ),
            (),
            PACKAGE_DIR / "channels" / "whatsapp_bridge" / "bridge.js",
        ),
    ]


def core_channel_secret_names() -> frozenset[str]:
    return frozenset(
        field.env_key
        for channel in passive_core_channels()
        for field in channel.config_fields
        if field.env_key
    )


__all__ = [
    "PassiveCoreChannel",
    "core_channel_secret_names",
    "passive_core_channels",
]
