"""Host proxies for the public channel/webhook API of one plugin worker.

Only explicit public methods and dataclasses cross this boundary. Core channel,
approval, media and webhook owners remain authoritative; callables remain remote.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, fields, is_dataclass
import inspect
import types
import re
from typing import Any, Union, get_args, get_origin, get_type_hints

from row_bot.channels.base import Channel, ChannelCapabilities, ConfigField
from row_bot.plugins import api as sdk
from row_bot.plugins.worker_protocol import ProtocolError, pack, unpack

_CALLBACKS = frozenset(field.name for field in fields(sdk.ChannelOutboundCallbacks))
_CHANNEL_METHODS = frozenset({"start", "stop", "is_configured", "is_running", "send_message",
    "send_photo", "send_document", "send_approval_request", "update_approval_message",
    "get_default_target", "make_thread_id"})
_API_METHODS = frozenset({"process_channel_attachment", "record_channel_activity",
    "generate_channel_pairing_code", "verify_channel_pairing_code", "is_channel_user_approved",
    "get_channel_approved_users", "revoke_channel_user", "get_webhook_path", "get_webhook_url",
    "verify_bot_framework_jwt"})


def _typed(annotation, value):
    if annotation is Any:
        return value
    origin = get_origin(annotation)
    if origin in (Union, types.UnionType):
        for choice in get_args(annotation):
            try:
                return _typed(choice, value)
            except (ValueError, TypeError, ProtocolError):
                pass
        raise ProtocolError("worker_arguments_invalid")
    if annotation is type(None):
        if value is not None:
            raise ProtocolError("worker_arguments_invalid")
        return None
    if origin is list:
        if type(value) is not list or len(value) > 10000:
            raise ProtocolError("worker_arguments_invalid")
        return [_typed(get_args(annotation)[0], item) for item in value]
    if origin is dict:
        if type(value) is not dict or len(value) > 10000:
            raise ProtocolError("worker_arguments_invalid")
        key_type, value_type = get_args(annotation)
        return {_typed(key_type, key): _typed(value_type, item) for key, item in value.items()}
    if isinstance(annotation, type) and is_dataclass(annotation):
        return public_dataclass(annotation, value)
    if annotation in (str, bool, int, float, bytes):
        if type(value) is not annotation:
            raise ProtocolError("worker_arguments_invalid")
        return value
    raise ProtocolError("worker_arguments_invalid")


def public_dataclass(cls, value):
    if type(value) is not dict or set(value) - {field.name for field in fields(cls)}:
        raise ProtocolError("worker_arguments_invalid")
    hints = get_type_hints(cls)
    try:
        result = cls(**{key: _typed(hints[key], item) for key, item in value.items()})
    except (TypeError, ValueError):
        raise ProtocolError("worker_arguments_invalid") from None
    if isinstance(result, sdk.ChannelAttachment):
        if not 0 <= result.size_bytes <= 50 * 1024 * 1024 or (result.data is not None and len(result.data) > 50 * 1024 * 1024):
            raise ProtocolError("worker_payload_invalid")
    return result


def _channel(api, name):
    if type(name) is not str or not 1 <= len(name) <= 128:
        raise ProtocolError("worker_callback_denied")
    for channel in api._registered_channels:
        if channel.name == name:
            return channel
    raise ProtocolError("worker_callback_denied")


def _reply(value):
    return pack(asdict(value) if is_dataclass(value) else value)


class WorkerChannel(Channel):
    name = ""
    display_name = ""
    icon = "chat"
    setup_guide = ""
    webhook_port = None
    needs_tunnel = False

    def __init__(self, api, value):
        self._api = api
        for key, maximum in (("name", 128), ("display_name", 256), ("icon", 128), ("setup_guide", 32768)):
            text = value.get(key)
            if type(text) is not str or len(text) > maximum or (key == "name" and not text):
                raise ProtocolError("worker_arguments_invalid")
            setattr(self, key, text)
        port = value.get("webhook_port")
        if port is not None and (type(port) is not int or not 1 <= port <= 65535):
            raise ProtocolError("worker_arguments_invalid")
        self.webhook_port = port
        if type(value.get("needs_tunnel")) is not bool:
            raise ProtocolError("worker_arguments_invalid")
        self.needs_tunnel = value["needs_tunnel"]
        self._capabilities = public_dataclass(ChannelCapabilities, value.get("capabilities"))
        if type(value.get("config_fields")) is not list or len(value["config_fields"]) > 128:
            raise ProtocolError("worker_arguments_invalid")
        self._config_fields = [public_dataclass(ConfigField, item) for item in value["config_fields"]]
        extras = value.get("extra_tools")
        if type(extras) is not list or len(extras) > 1024:
            raise ProtocolError("worker_contribution_limit")
        for child in extras:
            if (type(child) is not dict or set(child) != {"name", "description", "schema", "return_direct", "response_format"}
                    or type(child["name"]) is not str or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", child["name"])
                    or type(child["description"]) is not str or len(child["description"]) > 16384
                    or type(child["schema"]) is not dict or type(child["return_direct"]) is not bool
                    or child["response_format"] not in {"content", "content_and_artifact"}):
                raise ProtocolError("worker_arguments_invalid")
        self._extra_descriptors = extras

    def extra_tools(self):
        from row_bot.plugins.worker import WorkerTool
        tool = WorkerTool(self._api, {"name": self.name, "display_name": self.display_name,
            "description": "", "children": self._extra_descriptors,
            "destructive": [], "background_allowed": []})
        return tool.as_langchain_tools()


    @property
    def capabilities(self):
        return self._capabilities

    @property
    def config_fields(self):
        return list(self._config_fields)

    def _call(self, method, *args, **kwargs):
        self._api._check_dispatch()
        if method not in _CHANNEL_METHODS:
            raise ProtocolError("worker_method_unavailable")
        return unpack(self._api._worker.call("channel", {"name": self.name, "method": method,
            "args": pack(list(args)), "kwargs": pack(kwargs)}, control=True))

    async def start(self):
        return _typed(bool, await asyncio.to_thread(self._call, "start"))

    async def stop(self):
        await asyncio.to_thread(self._call, "stop")

    def is_configured(self):
        return _typed(bool, self._call("is_configured"))

    def is_running(self):
        return _typed(bool, self._call("is_running"))

    def send_message(self, target, text):
        return self._call("send_message", target, text)

    def send_photo(self, target, file_path, caption=None):
        return self._call("send_photo", target, file_path, caption)

    def send_document(self, target, file_path, caption=None):
        return self._call("send_document", target, file_path, caption)

    def send_approval_request(self, target, interrupt_data, config):
        return _typed(str | None, self._call("send_approval_request", target, interrupt_data, config))

    def update_approval_message(self, message_ref, status, source=""):
        return self._call("update_approval_message", message_ref, status, source)

    def get_default_target(self):
        return _typed(str | int, self._call("get_default_target"))

    def make_thread_id(self, external_id):
        return _typed(str, self._call("make_thread_id", external_id))


def _callbacks(api, value):
    if type(value) is not dict or set(value) != {"id", "methods"}:
        raise ProtocolError("worker_callback_denied")
    identity, names = value["id"], value["methods"]
    if type(identity) is not str or not re.fullmatch(r"[a-f0-9]{32}", identity) or type(names) is not list or any(type(name) is not str for name in names) or len(names) != len(set(names)) or not set(names) <= _CALLBACKS or "send_text" not in names:
        raise ProtocolError("worker_callback_denied")
    def method(name):
        async def call(*args):
            api._check_dispatch()
            value = await asyncio.to_thread(api._worker.call, "outbound_callback", {
                "id": identity, "method": name, "args": pack(list(args))}, control=True)
            api._check_dispatch()
            return unpack(value)
        return call
    return sdk.ChannelOutboundCallbacks(**{name: method(name) for name in names})


def _attachment_path(api, attachment):
    if not attachment.local_path:
        return
    from pathlib import Path
    from row_bot.plugins.sandbox import _checked_path
    path = Path(attachment.local_path)
    if ".." in path.parts or not path.is_absolute():
        raise ProtocolError("worker_callback_denied")
    try:
        _checked_path(api._worker.directory, path)
    except Exception:
        raise ProtocolError("worker_callback_denied") from None


def _run_turn(api, identity, factory):
    from row_bot.cancellation import CancellationScope, use_cancellation_scope
    scope = CancellationScope()
    async def run():
        loop, task = asyncio.get_running_loop(), asyncio.current_task()
        def cancel():
            scope.cancel("plugin_revoked")
            loop.call_soon_threadsafe(task.cancel)
        worker = api._worker
        worker.admit_channel_turn(identity, cancel)
        try:
            with use_cancellation_scope(scope):
                return await factory()
        finally:
            worker.release_channel_turn(identity, cancel)
    return asyncio.run(run())


def handle_callback(api, method, args, kwargs):
    """Explicit, source-bound public SDK callbacks; never arbitrary host RPC."""
    api._check_active()
    args, kwargs = unpack(args), unpack(kwargs)
    if method not in {"get_webhook_path", "get_webhook_url"} or kwargs.get("start_tunnel"):
        api._check_dispatch()
    if type(args) is not list or type(kwargs) is not dict:
        raise ProtocolError("worker_callback_denied")
    if method == "cancel_channel_turn":
        if len(args) != 1 or kwargs or type(args[0]) is not str or not re.fullmatch(r"[a-f0-9]{32}", args[0]):
            raise ProtocolError("worker_callback_denied")
        api._worker.cancel_channel_turn(args[0])
        return None
    if method == "handle_channel_message":
        if len(args) != 2 or set(kwargs) - {"channel", "enabled_tool_names", "stream", "approval_context"}:
            raise ProtocolError("worker_arguments_invalid")
        message = public_dataclass(sdk.ChannelInboundMessage, args[0])
        channel = _channel(api, message.channel_name)
        for attachment in message.attachments:
            _attachment_path(api, attachment)
        if kwargs.get("channel") not in (None, channel.name):
            raise ProtocolError("worker_callback_denied")
        callbacks = _callbacks(api, args[1])
        values = {"channel": channel,
            "enabled_tool_names": _typed(list[str] | None, kwargs.get("enabled_tool_names")),
            "stream": _typed(bool | None, kwargs.get("stream")),
            "approval_context": _typed(dict[str, Any] | None, kwargs.get("approval_context"))}
        return _reply(_run_turn(api, args[1]["id"], lambda: api.handle_channel_message(message, callbacks, **values)))
    if method == "handle_channel_approval":
        if args or set(kwargs) - {"channel_name", "thread_id", "approved", "callbacks", "interrupt_ids", "source"}:
            raise ProtocolError("worker_arguments_invalid")
        _channel(api, kwargs.get("channel_name"))
        values = {"channel_name": _typed(str, kwargs.get("channel_name")),
            "thread_id": _typed(str, kwargs.get("thread_id")), "approved": _typed(bool, kwargs.get("approved")),
            "callbacks": _callbacks(api, kwargs.get("callbacks")),
            "interrupt_ids": _typed(list[str] | None, kwargs.get("interrupt_ids")),
            "source": _typed(str, kwargs.get("source", ""))}
        return _reply(_run_turn(api, kwargs["callbacks"]["id"], lambda: api.handle_channel_approval(**values)))
    if method not in _API_METHODS:
        raise ProtocolError("worker_callback_denied")
    function = getattr(api, method)
    try:
        bound = inspect.signature(function).bind(*args, **kwargs)
        hints = get_type_hints(function)
        values = {key: _typed(hints[key], item) for key, item in bound.arguments.items()}
    except (TypeError, ValueError, KeyError):
        raise ProtocolError("worker_arguments_invalid") from None
    if "channel_name" in values:
        _channel(api, values["channel_name"])
    if method == "process_channel_attachment":
        _attachment_path(api, values["attachment"])
    return _reply(function(**values))


def register_contributions(api, value):
    channels = value.get("channels")
    webhooks = value.get("webhooks")
    if type(channels) is not list or len(channels) > 32 or type(webhooks) is not list or len(webhooks) > 64:
        raise ProtocolError("worker_contribution_limit")
    for channel in channels:
        if type(channel) is not dict:
            raise ProtocolError("worker_arguments_invalid")
        api.register_channel(WorkerChannel(api, channel))
    for webhook in webhooks:
        if type(webhook) is not dict or set(webhook) != {"name", "methods", "max_body_bytes"}:
            raise ProtocolError("worker_arguments_invalid")
        def make_handler(name):
            async def handler(request):
                result = await asyncio.to_thread(api._worker.call, "webhook", {
                    "name": name, "request": pack(asdict(request))}, control=True)
                return public_dataclass(sdk.PluginWebhookResponse, unpack(result))
            return handler
        api.register_webhook_route(webhook["name"], make_handler(webhook["name"]),
                                  methods=webhook["methods"], max_body_bytes=webhook["max_body_bytes"])
