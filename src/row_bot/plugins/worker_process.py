"""Trusted stdlib bootstrap for one prepared plugin process.

Invoked by worker.py with -I -S -B. No application package path is installed.
The public SDK definitions are loaded explicitly; internal imports remain absent.
This process boundary is not an operating-system filesystem/network sandbox.
"""
from __future__ import annotations

import asyncio
import importlib.machinery
import importlib.util
import inspect
import os
from pathlib import Path
import sys
import threading
import types
import contextvars
from dataclasses import asdict, fields, is_dataclass
from uuid import uuid4



def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    parent, _, child = name.rpartition(".")
    if parent in sys.modules:
        setattr(sys.modules[parent], child, module)
    return module


def _source_only(loader, fullname):
    if {"__pycache__", ".git"}.intersection(Path(loader.path).parts):
        raise ImportError("worker_ignored_source_unavailable")
    source = loader.get_data(loader.path)
    return loader.source_to_code(source, loader.path)


def _no_bytecode(loader, fullname):
    raise ImportError("worker_sourceless_bytecode_unavailable")


def _source_imports(environment):
    # -S prevents .pth execution before this hook. -B alone does not prevent
    # consuming mutable ignored bytecode, so source imports always compile .py.
    importlib.machinery.SourceFileLoader.get_code = _source_only
    importlib.machinery.SourcelessFileLoader.get_code = _no_bytecode
    original_extension = importlib.machinery.ExtensionFileLoader.create_module
    def source_owned_extension(loader, spec):
        if {"__pycache__", ".git"}.intersection(Path(loader.path).parts):
            raise ImportError("worker_ignored_source_unavailable")
        return original_extension(loader, spec)
    importlib.machinery.ExtensionFileLoader.create_module = source_owned_extension
    site = (environment / "Lib/site-packages" if os.name == "nt" else
            environment / f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages")
    sys.path.append(str(site))


def _bootstrap(environment, sdk_path, channel_path):
    _source_imports(environment)
    for name in ("row_bot", "row_bot.plugins", "row_bot.channels"):
        module = types.ModuleType(name)
        module.__path__ = []
        sys.modules[name] = module
        parent, _, child = name.rpartition(".")
        if parent in sys.modules:
            setattr(sys.modules[parent], child, module)
    _load("row_bot.channels.base", channel_path)
    sdk = _load("row_bot.plugins.api", sdk_path)
    sys.modules["plugins"] = sys.modules["row_bot.plugins"]
    sys.modules["plugins.api"] = sdk
    return sdk


def _structured_descriptor(child, tools):
    if child.name in tools:
        raise ValueError("worker_duplicate_tool")
    tools[child.name] = ("structured", child)
    if len(tools) > 1024:
        raise ValueError("worker_contribution_limit")
    schema = child.args_schema
    if not isinstance(schema, dict):
        schema = child.get_input_schema().model_json_schema()
    return {"name": child.name, "description": child.description, "schema": schema,
            "return_direct": child.return_direct, "response_format": child.response_format}


def _tool_descriptor(tool, sdk, tools):
    name = tool.name
    children = []
    basic = (type(tool).as_langchain_tools is sdk.PluginTool.as_langchain_tools
             and type(tool).as_langchain_tool is sdk.PluginTool.as_langchain_tool)
    if basic:
        if name in tools:
            raise ValueError("worker_duplicate_tool")
        tools[name] = ("basic", tool)
        children.append({"name": name, "description": f"{tool.display_name}: {tool.description}",
            "schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
            "return_direct": False, "response_format": "content"})
    else:
        for child in tool.as_langchain_tools():
            children.append(_structured_descriptor(child, tools))
    return {"name": name, "display_name": tool.display_name, "description": tool.description,
            "destructive": sorted(tool.destructive_tool_names),
            "background_allowed": sorted(tool.background_allowed_tool_names), "children": children}


def main():
    _input = os.fdopen(os.dup(sys.stdin.fileno()), "rb", buffering=0)
    _output = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)
    # Native prints cannot become control frames. Only duplicated private pipes
    # reach the shared protocol, which is loaded by an exact trusted source path.
    sys.stdout = sys.stderr = open(os.devnull, "w", encoding="utf-8")
    with open(os.devnull, "r+b") as discarded:
        for descriptor in (0, 1, 2):
            os.dup2(discarded.fileno(), descriptor)
    environment, sdk_path, channel_path = map(Path, sys.argv[1:4])
    sdk = _bootstrap(environment, sdk_path, channel_path)
    protocol = _load("_row_bot_worker_protocol", Path(__file__).with_name("worker_protocol.py"))
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    context = contextvars.ContextVar("plugin_call_context", default={})
    ready = threading.Event()
    callback_lock = threading.Lock()
    callbacks = {}
    api = None
    tools = {}
    channels = {}
    webhooks = {}

    def complete(value):
        if inspect.isawaitable(value):
            async def wait():
                return await value
            return asyncio.run_coroutine_threadsafe(wait(), loop).result()
        return value

    def remote(method, *args, timeout=60.0, **kwargs):
        ready.wait()
        return protocol.unpack(peer.request("api", {"method": method,
            "args": protocol.pack(list(args)), "kwargs": protocol.pack(kwargs)}, timeout=timeout))

    def public(value):
        return asdict(value) if is_dataclass(value) else value

    class State:
        def is_plugin_enabled(self, plugin_id):
            return True  # Each host admission checks the actual registration.

        def get_plugin_config(self, plugin_id, key, default=None):
            return remote("get_config", key, default)

        def set_plugin_config(self, plugin_id, key, value):
            return remote("set_config", key, value)

        def get_plugin_secret(self, plugin_id, key):
            return remote("get_secret", key)

        def set_plugin_secret(self, plugin_id, key, value):
            return remote("set_secret", key, value)

    class API(sdk.PluginAPI):
        def register_webhook_route(self, name, handler, *, methods=None, max_body_bytes=1048576):
            with self._registration_lock:
                self._check_active(registration=True)
                if not callable(handler) or name in webhooks or len(webhooks) >= 64:
                    raise ValueError("worker_arguments_invalid")
                # The host's existing validator is authoritative at publication;
                # path validation is passive and can also be used during setup.
                path = remote("get_webhook_path", name)
                webhooks[name] = (handler, methods, max_body_bytes)
                return path

        def register_skill(self, skill_info):
            if type(skill_info) is dict and isinstance(skill_info.get("root"), Path):
                skill_info = {**skill_info, "root": str(skill_info["root"])}
            super().register_skill(skill_info)

        def is_background_workflow(self):
            return context.get().get("background", False)

        def get_allowed_recipients(self):
            return list(context.get().get("recipients", []))

        def _publish_webhooks(self):
            self._registration_sealed = True
            self._staged = False

        async def _turn(self, method, outbound, args, kwargs):
            self._check_dispatch()
            identity = uuid4().hex
            methods = {field.name: getattr(outbound, field.name) for field in fields(sdk.ChannelOutboundCallbacks)
                       if getattr(outbound, field.name) is not None}
            if not all(callable(value) for value in methods.values()) or "send_text" not in methods:
                raise ValueError("worker_arguments_invalid")
            with callback_lock:
                if len(callbacks) >= 4:
                    raise RuntimeError("worker_busy")
                callbacks[identity] = {"methods": methods, "handles": {}}
            descriptor = {"id": identity, "methods": list(methods)}
            if method == "handle_channel_message":
                args.append(descriptor)
            else:
                kwargs["callbacks"] = descriptor
            try:
                # The existing Goal owner controls turn duration; revocation
                # closes this finite-slot request instead of imposing a tool TTL.
                value = await asyncio.to_thread(remote, method, *args, timeout=None, **kwargs)
                return sdk.ChannelRunResult(**value)
            except asyncio.CancelledError:
                await asyncio.shield(asyncio.to_thread(remote, "cancel_channel_turn", identity))
                raise
            finally:
                with callback_lock:
                    callbacks.pop(identity, None)

        async def handle_channel_message(self, message, callbacks, *, channel=None,
                                         enabled_tool_names=None, stream=None, approval_context=None):
            return await self._turn("handle_channel_message", callbacks, [asdict(message)], {
                "channel": channel.name if channel is not None else None,
                "enabled_tool_names": enabled_tool_names, "stream": stream,
                "approval_context": approval_context})

        async def handle_channel_approval(self, *, channel_name, thread_id, approved, callbacks,
                                          interrupt_ids=None, source=""):
            return await self._turn("handle_channel_approval", callbacks, [], {
                "channel_name": channel_name, "thread_id": thread_id, "approved": approved,
                "interrupt_ids": interrupt_ids, "source": source})

        def process_channel_attachment(self, attachment, *, question="", max_chars=80000):
            value = remote("process_channel_attachment", asdict(attachment), question=question, max_chars=max_chars)
            return sdk.ChannelAttachmentResult(**value)

        def verify_bot_framework_jwt(self, authorization_header, **kwargs):
            return sdk.BotFrameworkAuthResult(**remote("verify_bot_framework_jwt", authorization_header, **kwargs))

    def forward(method):
        def invoke(self, *args, **kwargs):
            return remote(method, *args, **kwargs)
        return invoke

    for name in ("record_channel_activity", "generate_channel_pairing_code", "verify_channel_pairing_code",
                 "is_channel_user_approved", "get_channel_approved_users", "revoke_channel_user",
                 "get_webhook_path", "get_webhook_url"):
        setattr(API, name, forward(name))

    # The public module-level JWT helper follows the same explicit host owner.
    sdk.verify_bot_framework_jwt = lambda authorization_header, **kwargs: sdk.BotFrameworkAuthResult(
        **remote("verify_bot_framework_jwt", authorization_header, **kwargs))

    def handle(method, args):
        nonlocal api
        ready.wait()
        if method == "register" and api is None:
            root = Path(args["plugin_dir"])
            api = API(args["plugin_id"], root, State(), staged=True)
            sys.path.insert(0, str(root))  # Only the child sees the plugin root.
            module = _load("_row_bot_plugin", root / "plugin_main.py")
            complete(module.register(api))
            if len(api._registered_tools) > 256 or len(api._registered_skills) > 256 or len(api._registered_channels) > 32:
                raise ValueError("worker_contribution_limit")
            descriptors = []
            for channel in api._registered_channels:
                if channel.name in channels:
                    raise ValueError("worker_arguments_invalid")
                channels[channel.name] = channel
                descriptors.append({"name": channel.name, "display_name": channel.display_name,
                    "icon": channel.icon, "setup_guide": channel.setup_guide,
                    "webhook_port": channel.webhook_port, "needs_tunnel": channel.needs_tunnel,
                    "capabilities": asdict(channel.capabilities),
                    "config_fields": [asdict(item) for item in channel.config_fields],
                    "extra_tools": [_structured_descriptor(item, tools) for item in channel.extra_tools()]})
            return {"tools": [_tool_descriptor(tool, sdk, tools) for tool in api._registered_tools],
                    "skills": api._registered_skills, "channels": descriptors,
                    "webhooks": [{"name": name, "methods": item[1], "max_body_bytes": item[2]}
                                 for name, item in webhooks.items()]}
        if api is None:
            raise ValueError("worker_method_unavailable")
        if method == "publish":
            api._publish_webhooks()
            return None
        api._check_dispatch()
        if method == "invoke":
            token = context.set(args.get("context", {}))
            try:
                kind, tool = tools[args["name"]]
                values = protocol.unpack(args["values"])
                if kind == "basic":
                    if type(values) is not dict or set(values) != {"query"} or type(values["query"]) is not str:
                        raise ValueError("worker_arguments_invalid")
                    value = tool.execute(values["query"])
                elif getattr(tool, "func", None) is None and getattr(tool, "coroutine", None) is not None:
                    value = tool.ainvoke(values)
                else:
                    value = tool.invoke(values)
                return protocol.pack(complete(value))
            finally:
                context.reset(token)
        if method == "channel":
            permitted = {"start", "stop", "is_configured", "is_running", "send_message", "send_photo",
                         "send_document", "send_approval_request", "update_approval_message",
                         "get_default_target", "make_thread_id"}
            if set(args) != {"name", "method", "args", "kwargs"} or args["method"] not in permitted:
                raise ValueError("worker_method_unavailable")
            channel = channels[args["name"]]
            value = getattr(channel, args["method"])(*protocol.unpack(args["args"]), **protocol.unpack(args["kwargs"]))
            return protocol.pack(complete(value))
        if method == "webhook":
            request = sdk.PluginWebhookRequest(**protocol.unpack(args["request"]))
            return protocol.pack(public(complete(webhooks[args["name"]][0](request))))
        if method == "outbound_callback":
            if set(args) != {"id", "method", "args"}:
                raise ValueError("worker_arguments_invalid")
            with callback_lock:
                group = callbacks.get(args["id"])
                if group is None or args["method"] not in group["methods"]:
                    raise ValueError("worker_callback_denied")
                function = group["methods"][args["method"]]
            values = protocol.unpack(args["args"])
            if args["method"] in {"update_stream", "finish_stream"}:
                with callback_lock:
                    values[0] = group["handles"][values[0]]
            value = complete(function(*values))
            if args["method"] == "start_stream":
                with callback_lock:
                    if len(group["handles"]) >= 256:
                        raise ValueError("worker_contribution_limit")
                    identity = uuid4().hex
                    group["handles"][identity] = value
                return identity
            if args["method"] == "send_approval_request":
                return protocol.pack(value)
            return None  # Other public callback return values are not consumed.
        raise ValueError("worker_method_unavailable")

    peer = protocol.Peer(_input, _output, role="worker", handler=handle)
    ready.set()
    def observe_close():
        peer.closed.wait()
        loop.call_soon_threadsafe(loop.stop)
    threading.Thread(target=observe_close, name="plugin-control-lifetime", daemon=True).start()
    loop.run_forever()


if __name__ == "__main__":
    main()
