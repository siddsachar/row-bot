"""Phase 3 real-service browser fixture with synthetic provider boundaries only."""
from __future__ import annotations

import json
import base64
import hashlib
import logging
from pathlib import Path
import runpy
import traceback
import struct
import zlib
import queue
import threading
from dataclasses import replace

from fastapi import Header, HTTPException
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from nicegui import app

from tests.browser.client_platform import fixture_app as predecessor
from tests.helpers.client_platform_fakes import fixture_id


_large_library: dict | None = None
_orchestrations: dict[str, dict] = {}
_token_feeds: dict[str, queue.Queue[str]] = {}
_parent_releases: dict[str, threading.Event] = {}
_parent_threads: dict[str, threading.Thread] = {}
_expiry_starts: dict[str, threading.Event] = {}


class SyntheticRequestDiagnostics:
    """Observe lifecycle only; never read or serialize authentication/content."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        disconnected = False
        started = False
        exception_type = None

        async def observed_receive():
            nonlocal disconnected
            message = await receive()
            if message["type"] == "http.disconnect":
                disconnected = True
            return message

        async def observed_send(message):
            nonlocal started
            await send(message)
            if message["type"] == "http.response.start":
                started = True

        try:
            await self.app(scope, observed_receive, observed_send)
        except BaseException as error:
            exception_type = type(error).__name__
            raise
        finally:
            path = scope.get("path", "")
            if exception_type is not None or path == "/api/v1/events":
                logging.getLogger(__name__).info("Synthetic request lifecycle: %s", json.dumps({
                    "path": path, "disconnectSeen": disconnected,
                    "responseStarted": started, "exceptionType": exception_type,
                }, separators=(",", ":")))
def _synthetic_image() -> str:
    """A visible deterministic RGB chart fixture, with no external image data."""
    width, height = 240, 120
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data))
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for x in range(width):
            rows.extend((32, 112, 184) if x < width // 2 else (32, 144, 112) if y < height // 2 else (224, 160, 48))
    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!2I5B", width, height, 8, 2, 0, 0, 0))
    data += chunk(b"IDAT", zlib.compress(bytes(rows))) + chunk(b"IEND", b"")
    return base64.b64encode(data).decode("ascii")


_IMAGE = _synthetic_image()


@app.get("/__p3_fixture/resources")
def resources(x_fixture_token: str = Header(default="")) -> dict:
    """Expose synthetic identities, never host paths or fixture credentials."""
    predecessor._authorize(x_fixture_token)
    state = json.loads((predecessor.DATA / "docs_real_ui_demo_state.json").read_text(encoding="utf-8"))
    folder = predecessor.DATA / "fixture-workspace"
    return {"workspace_id": state["developer"]["workspace_id"], "artifact_id": state["designer"]["project_id"],
            "fixture_file_sha256": hashlib.sha256((folder / "fixture.txt").read_bytes()).hexdigest(),
            "git_present": (folder / ".git").exists()}


def stream(text: str, enabled_tools: list[str], config: dict, *, stop_event=None):
    """Script real tools/media projection and a durable final behind a barrier."""
    if any(marker in text for marker in ("rich fixture", "steering fixture", "burst fixture", "cadence fixture", "exhaustion fixture", "target fixture", "fail first draft")):
        from row_bot.threads import append_checkpoint_messages, get_latest_checkpoint_messages
        settings = config["configurable"]
        thread = str(settings["thread_id"])
        identity = str(settings["platform_submission_id"])
        if not any(str(message.id) in {identity, "user:submission:" + identity}
                   for message in get_latest_checkpoint_messages(thread)):
            append_checkpoint_messages(thread, [HumanMessage(id=identity, content=text)])
    if any(marker in text for marker in ("exhaustion fixture", "target fixture", "fail first draft")):
        from row_bot.threads import append_checkpoint_messages, get_latest_checkpoint_revision
        from row_bot.conversation_resources import current_execution_context
        case = "exhaustion" if "exhaustion fixture" in text else "target" if "target fixture" in text else "first-draft-failure"
        call = predecessor._record("submit", config, case)
        context = current_execution_context()
        call["accepted_binding_ids"] = [binding.binding_id for binding in context.bindings] if context else []
        try:
            if case == "first-draft-failure":
                yield "error", "Synthetic first-draft provider failure"
                return
            if case == "exhaustion":
                if "timed exhaustion fixture" in text:
                    start = threading.Event()
                    _expiry_starts[call["barrier_id"]] = start
                    if not start.wait(180):
                        raise TimeoutError("Timed expiry observers were not prepared")
                chunks = [f"Tick {index:04d}. " for index in range(5000)]
                call["emitted_tokens"] = 0
                for chunk in chunks:
                    yield "token", chunk
                    call["emitted_tokens"] += 1
                partial = "".join(chunks)
            else:
                partial = "Captured target is active."
                yield "token", partial
            if not predecessor._barriers[call["barrier_id"]].wait(180):
                raise TimeoutError("Phase 3 bounded execution fixture was not released")
            if stop_event is not None and stop_event.is_set():
                return
            context = current_execution_context()
            call["final_binding_ids"] = [binding.binding_id for binding in context.bindings] if context else []
            suffix = " Exhaustion settled." if case == "exhaustion" else " Captured target settled."
            final = partial + suffix
            native_id = fixture_id("phase3:" + call["generation_id"] + ":final")
            append_checkpoint_messages(call["conversation_id"], [AIMessage(id=native_id, content=final)])
            yield "token", suffix
            yield "output_binding", {"native_message_id": native_id,
                "checkpoint_revision": get_latest_checkpoint_revision(call["conversation_id"])}
            yield "done", final
        finally:
            _expiry_starts.pop(call["barrier_id"], None)
            call["quiesced"] = True
        return
    if "steering fixture" in text:
        yield from steering_stream(config, stop_event=stop_event)
        return
    if "cadence fixture" in text:
        yield from cadence_stream(config, stop_event=stop_event)
        return
    if "burst fixture" in text:
        call = predecessor._record("submit", config, "burst")
        try:
            yield "thinking", None
            for index in range(160):
                yield "token", f"Burst {index:03d}. "
            yield "token", "Burst complete; waiting for Stop."
            if stop_event is None or not stop_event.wait(180):
                raise TimeoutError("Burst producer must be stopped by its owner")
        finally:
            call["quiesced"] = True
        return
    if "rich fixture" not in text:
        yield from predecessor.stream(text, enabled_tools, config, stop_event=stop_event)
        return
    from row_bot.application.attachment_context import current_caches
    from row_bot.application.generated_media import capture_generated_media
    from row_bot.threads import append_checkpoint_messages, get_latest_checkpoint_revision

    call = predecessor._record("submit", config, "tools-media")
    thread_id = call["conversation_id"]
    identity = f"phase3:{thread_id}:{call['sequence']}"
    tool_id, tool_message = fixture_id(identity + ":tool"), fixture_id(identity + ":result")
    try:
        yield "thinking", None
        append_checkpoint_messages(thread_id, [AIMessage(id=fixture_id(identity + ":tool-call"), content="",
            tool_calls=[{"id": tool_id, "name": "fixture_image", "args": {}}])])
        yield "tool_call", {"tool_call_id": tool_id, "message_id": tool_message}
        caches = current_caches()
        if caches is None or caches.conversation_id != thread_id:
            raise AssertionError("Phase 3 media fixture must use the real execution attachment scope")
        caches.pending_image = _IMAGE
        media = capture_generated_media(thread_id, caches)
        append_checkpoint_messages(thread_id, [ToolMessage(id=tool_message, tool_call_id=tool_id,
                                                          content="Synthetic image created.")])
        yield "tool_done", {"tool_call_id": tool_id, "message_id": tool_message, "media": media}
        yield "token", "Synthetic tools and media are ready."
        if not predecessor._barriers[call["barrier_id"]].wait(180):
            raise TimeoutError("Phase 3 tools/media producer was not released")
        if stop_event is not None and stop_event.is_set():
            return
        final = "Synthetic tools and media are ready.\n\n```python\nprint('local fixture')\n```"
        yield "token", "\n\n```python\nprint('local fixture')\n```"
        native_id = fixture_id(identity + ":final")
        append_checkpoint_messages(thread_id, [AIMessage(content=final, id=native_id)])
        yield "output_binding", {"native_message_id": native_id,
                                 "checkpoint_revision": get_latest_checkpoint_revision(thread_id)}
        yield "done", final
    finally:
        call["quiesced"] = True


def cadence_stream(config: dict, *, stop_event=None):
    """Advance actual streamed output only on explicit synthetic test input."""
    from row_bot.threads import append_checkpoint_messages, get_latest_checkpoint_revision
    call = predecessor._record("submit", config, "cadence")
    feed: queue.Queue[str] = queue.Queue(maxsize=256)
    _token_feeds[call["barrier_id"]] = feed
    text = "Cadence stream ready."
    try:
        yield "token", text
        while not predecessor._barriers[call["barrier_id"]].is_set():
            if stop_event is not None and stop_event.is_set():
                return
            try:
                token = feed.get(timeout=0.05)
            except queue.Empty:
                continue
            text += token
            yield "token", token
        native = fixture_id("cadence-final:" + call["generation_id"])
        append_checkpoint_messages(call["conversation_id"], [AIMessage(id=native, content=text)])
        yield "output_binding", {"native_message_id": native,
                                 "checkpoint_revision": get_latest_checkpoint_revision(call["conversation_id"])}
        yield "done", text
    finally:
        _token_feeds.pop(call["barrier_id"], None)
        call["quiesced"] = True


@app.post("/__p3_fixture/cadence/{barrier_id}/{index}")
def advance_cadence(barrier_id: str, index: int, x_fixture_token: str = Header(default="")) -> dict:
    predecessor._authorize(x_fixture_token)
    feed = _token_feeds.get(barrier_id)
    if feed is None or index < 0 or index > 1000:
        raise HTTPException(status_code=404)
    try:
        feed.put_nowait(f" Token {index:03d}.")
    except queue.Full:
        raise HTTPException(status_code=429) from None
    return {"accepted": index}


def steering_stream(config: dict, *, stop_event=None):
    """Real orchestration/run persistence and receipts; parent execution is fake."""
    from row_bot import agent_orchestrator as owner, agent_runs
    from row_bot.threads import append_checkpoint_messages, get_latest_checkpoint_revision, create_thread
    call = predecessor._record("submit", config, "steering")
    thread = call["conversation_id"]
    run_id = fixture_id("phase3-child:" + call["generation_id"])
    row = owner.create_or_get_orchestration(parent_thread_id=thread,
        parent_generation_id=call["generation_id"], root_objective="Synthetic bounded child work",
        model_ref="ollama:fixture", approval_mode="block", runtime_surface="normal_chat",
        orchestration_version=2)
    child_thread = fixture_id("phase3-child-thread:" + run_id)
    create_thread(thread_id=child_thread, name="Synthetic child conversation",
                  name_source="manual", seed_default_skills=False)
    append_checkpoint_messages(child_thread, [HumanMessage(
        id=fixture_id("phase3-child-prompt:" + run_id), content="Synthetic delegated objective")])
    child = agent_runs.create_agent_run(run_id=run_id, status="running", parent_thread_id=thread,
        thread_id=child_thread, prompt="Synthetic child work",
        display_name="Synthetic child", model_override="ollama:fixture")
    owner.register_member(row["id"], child["id"], required=True)
    _orchestrations[thread] = {"orchestration_id": row["id"], "child_id": child["id"],
                             "child_conversation_id": child_thread, "batches": []}
    owner.complete_parent_pass(row["id"], "Synthetic child is working.", foreground=True)
    try:
        yield "thinking", None
        yield "token", "Synthetic child is working."
        if not predecessor._barriers[call["barrier_id"]].wait(180):
            raise TimeoutError("Steering fixture producer was not released")
        if stop_event is not None and stop_event.is_set():
            return
        final = "Synthetic child is working. Synthetic joined work complete."
        native_id = fixture_id("phase3-child-final:" + call["generation_id"])
        append_checkpoint_messages(thread, [AIMessage(id=native_id, content=final)])
        yield "token", " Synthetic joined work complete."
        yield "output_binding", {"native_message_id": native_id,
                                 "checkpoint_revision": get_latest_checkpoint_revision(thread)}
        yield "done", final
    finally:
        call["quiesced"] = True


@app.post("/__p3_fixture/orchestration/{conversation_id}/{action}")
def advance_orchestration(conversation_id: str, action: str, x_fixture_token: str = Header(default="")) -> dict:
    """Release one real parent pass or finish the synthetic execution boundary."""
    predecessor._authorize(x_fixture_token)
    from row_bot import agent_orchestrator as owner, agent_runs
    from dataclasses import asdict
    record = _orchestrations.get(conversation_id)
    if record is None or action not in {"state", "pass", "begin-pass", "release-pass", "finish-child"}:
        raise HTTPException(status_code=404)
    if action == "begin-pass":
        if conversation_id in _parent_threads:
            raise HTTPException(status_code=409)
        _parent_releases[conversation_id] = threading.Event()
        worker = threading.Thread(target=owner._run_parent_thread, args=(record["orchestration_id"],), daemon=True)
        _parent_threads[conversation_id] = worker
        worker.start()
    elif action == "release-pass":
        release = _parent_releases.get(conversation_id)
        if release is not None:
            release.set()
        worker = _parent_threads.get(conversation_id)
        if worker is not None:
            worker.join(timeout=10)
            if worker.is_alive():
                raise HTTPException(status_code=503)
    elif action == "pass":
        owner._run_parent_thread(record["orchestration_id"])
    elif action == "finish-child":
        agent_runs.finish_agent_run(record["child_id"], "completed", summary="Synthetic child result")
        owner._run_parent_thread(record["orchestration_id"])
    row = owner.get_orchestration(record["orchestration_id"])
    return {**record, "parent_state": row["parent_state"],
            "child_status": agent_runs.get_agent_run(record["child_id"])["status"],
            "steering": asdict(owner.read_parent_steering(conversation_id))}


@app.post("/__p3_fixture/large-library")
def large_library(x_fixture_token: str = Header(default="")) -> dict:
    """Seed 1,001 ordinary conversations and 10,001 durable messages once."""
    predecessor._authorize(x_fixture_token)
    global _large_library
    if _large_library is not None:
        return _large_library
    from row_bot import threads

    identifiers = []
    for index in range(1001):
        identifier = fixture_id(f"phase3-library:{index}")
        threads.create_thread(thread_id=identifier, name=f"Library conversation {index:04d}",
                              name_source="manual", seed_default_skills=False)
        identifiers.append(identifier)
    target = identifiers[0]
    def mixed_history(prefix: str, count: int, short: bool = False):
        messages = []
        for index in range(count):
            identity = fixture_id(f"{prefix}:{index}")
            text = (f"Short history row {index:04d}" if short else
                    f"History row {index:05d}" + (" archival needle" if index in {7, 9999} else ""))
            shape = (count - 1 - index) % 20
            if shape == 5:
                messages.append(AIMessage(id=identity, content=text, tool_calls=[{
                    "id": fixture_id(f"{prefix}:tool:{index}"), "name": "fixture_inspect", "args": {}}]))
            elif shape == 4:
                messages.append(ToolMessage(id=identity, content=text,
                    tool_call_id=fixture_id(f"{prefix}:tool:{index - 1}")))
            elif shape == 3:
                messages.append(AIMessage(id=identity, content=[{"type": "text", "text": text},
                    {"type": "text", "text": "[Synthetic media placeholder: bounded image output]"}]))
            else:
                messages.append((HumanMessage if index % 2 == 0 else AIMessage)(id=identity, content=text))
        return messages
    messages = mixed_history("phase3-history", 10001)
    if not threads.append_checkpoint_messages(target, messages):
        raise HTTPException(status_code=500, detail="Synthetic checkpoint seed rejected")
    short = identifiers[1]
    short_messages = mixed_history("phase3-short", 1000, True)
    if not threads.append_checkpoint_messages(short, short_messages):
        raise HTTPException(status_code=500, detail="Synthetic short checkpoint seed rejected")
    oversized = identifiers[2]
    oversized_id = fixture_id("phase3-oversized:0")
    oversized_text = "FIRST synthetic public text | " + "\u00e9\U0001f9ea\u6f22\u5b57|" * 6000 + " | MIDDLE marker | " + "\u03a9\U0001f642\u754c\u00e9|" * 6000 + " | END synthetic public text"
    if not threads.append_checkpoint_messages(oversized, [AIMessage(id=oversized_id, content=oversized_text)]):
        raise HTTPException(status_code=500, detail="Synthetic oversized checkpoint seed rejected")
    _large_library = {"conversation_count": len(identifiers), "message_count": len(messages),
                      "conversation_id": target, "message_id": fixture_id("phase3-history:7"),
                      "short_conversation_id": short, "short_last_message_id": fixture_id("phase3-short:999"),
                      "query": "archival needle",
                      "oversized_conversation_id": oversized, "oversized_message_id": oversized_id,
                      "oversized_public_sha256": hashlib.sha256((oversized_text + "\n").encode()).hexdigest(),
                      "oversized_public_characters": len(oversized_text + "\n")}
    return _large_library


@app.post("/__p3_fixture/expiry/{barrier_id}/start")
def start_expiry(barrier_id: str, x_fixture_token: str = Header(default="")) -> dict:
    predecessor._authorize(x_fixture_token)
    start = _expiry_starts.get(barrier_id)
    if start is None:
        raise HTTPException(status_code=404)
    start.set()
    return {"started": True}


@app.get("/__p3_fixture/conversation/{conversation_id}")
def conversation_state(conversation_id: str, x_fixture_token: str = Header(default="")) -> dict:
    """Observe only test-owned IDs; credentials and private paths never returned."""
    predecessor._authorize(x_fixture_token)
    from row_bot.application.client_platform import client_platform_service
    from row_bot.application.workspace_setup import conversation_workspace

    return {"conversation": client_platform_service.get_conversation(conversation_id),
            "snapshot": client_platform_service.snapshot(conversation_id),
            "workspace": conversation_workspace(client_platform_service, conversation_id)}


def main() -> None:
    from row_bot import notifications
    from row_bot.providers import readiness
    from row_bot.providers.models import TransportMode

    # NiceGUI adds its BaseHTTPMiddleware instances later inside ui.run().
    # Wrap the completed stack so their own exceptions are observed too.
    build_middleware_stack = app.build_middleware_stack
    app.build_middleware_stack = lambda: SyntheticRequestDiagnostics(build_middleware_stack())

    notifications._desktop_notify = predecessor._suppress_desktop_notification
    notifications._play_sound = predecessor._suppress_notification_sound
    from row_bot.developer import storage
    original_save = storage.save_workspace

    def save_with_real_identity(workspace):
        if workspace.id == "p1-browser-workspace":
            workspace = replace(workspace, id=storage._workspace_id_for_path(Path(workspace.path)))
        return original_save(workspace)

    storage.save_workspace = save_with_real_identity
    try:
        predecessor.seed()
    finally:
        storage.save_workspace = original_save
    state_path = predecessor.DATA / "docs_real_ui_demo_state.json"
    seeded = json.loads(state_path.read_text(encoding="utf-8"))
    seeded["developer"]["workspace_id"] = storage._workspace_id_for_path(predecessor.DATA / "fixture-workspace")
    state_path.write_text(json.dumps(seeded), encoding="utf-8")
    import row_bot.agent as agent
    from row_bot import models
    from row_bot.providers import reasoning
    from row_bot.providers.selection import model_choice_value, parse_model_ref
    from row_bot.providers.runtime import _reasoning_constructor_kwargs

    # Exactly one isolated synthetic model supplies Thinking controls. These
    # fixture capabilities perform no discovery and never reach a provider.
    reasoning_model = model_choice_value(models.get_current_model())
    reasoning_provider = (parse_model_ref(reasoning_model) or ("ollama", ""))[0]
    reasoning_caps = reasoning.ReasoningCapabilities(
        supported_efforts=("low", "high"), request_style="ollama" if reasoning_provider == "ollama" else "openai",
        source="isolated_visual_alignment_fixture", revision="visual-alignment-1")
    reasoning.resolve_reasoning_capabilities_for_ref = lambda value: reasoning_caps if value == reasoning_model else None
    original_record = predecessor._record

    def record_reasoning(kind, config, case):
        call = original_record(kind, config, case)
        snapshot = config["configurable"].get("reasoning_snapshot") or {}
        plan = reasoning.ReasoningRequestPlan(
            str(snapshot.get("model_ref") or ""),
            reasoning.ReasoningSelection.from_json(snapshot.get("selection")),
            reasoning.ReasoningCapabilities.from_json(snapshot.get("capabilities")))
        call["reasoning_selection"] = plan.selection.to_json()
        call["effective_reasoning_kwargs"] = _reasoning_constructor_kwargs(plan, provider=reasoning_provider)
        return call

    predecessor._record = record_reasoning

    def fake_agent_readiness(model_ref, **kwargs):
        return readiness.AgentReadinessResult(
            ready=True, provider_id="fixture", model_id="scripted", runtime_model="scripted",
            selection_ref=str(model_ref), transport=TransportMode.OLLAMA_CHAT, context_window=131072,
            tool_calling=True, tool_round_trip=True, streaming=True, credential_status="fixture",
            capability_source="fixture", confidence="high")

    def fake_chat_readiness(model_ref, **kwargs):
        return readiness.ChatReadinessResult(
            ready=True, provider_id="fixture", model_id="scripted", runtime_model="scripted",
            selection_ref=str(model_ref), transport=TransportMode.OLLAMA_CHAT, context_window=131072,
            streaming=True, credential_status="fixture", capability_source="fixture", confidence="high")

    agent.stream_agent = stream
    agent.resume_stream_agent = predecessor.resume
    readiness.evaluate_agent_readiness = fake_agent_readiness
    readiness.evaluate_chat_readiness = fake_chat_readiness
    from row_bot.api.v1 import routes
    from row_bot.application.client_platform import client_platform_service
    from row_bot.application.folder_selections import FolderSelections

    client_platform_service.readiness_factory = lambda _: True
    client_platform_service.stream_factory = stream
    client_platform_service.resume_factory = predecessor.resume
    from row_bot import agent_orchestrator as orchestration
    # The explicit fixture control is the scheduler barrier. Every pass still
    # uses the real owner lease, batch selection, acknowledgement and projection.
    orchestration._schedule_parent_runner = lambda _: None

    def parent_executor(row, context, enabled_tools, config):
        record = _orchestrations[row["parent_thread_id"]]
        messages = config["configurable"]["thread_event_messages"]
        record["batches"].append([item["content"] for item in messages if item["role"] == "human"])
        release = _parent_releases.get(row["parent_thread_id"])
        if len(record["batches"]) == 1 and release is not None and not release.wait(180):
            raise TimeoutError("Synthetic captured parent invocation was not released")
        return "Synthetic parent applied captured guidance."

    orchestration.set_test_executors(parent=parent_executor, delivery=lambda *_: True)
    install = routes.install_client_platform

    def install_with_synthetic_picker(*args, **kwargs):
        kwargs["folder_selections"] = FolderSelections(picker=lambda: predecessor.DATA / "fixture-workspace")
        return install(*args, **kwargs)

    routes.install_client_platform = install_with_synthetic_picker
    problem = routes.problem

    def record_private_failure(error, *args, **kwargs):
        frames = [(Path(frame.filename).name, frame.lineno) for frame in traceback.extract_tb(error.__traceback__)]
        logging.getLogger(__name__).error("Synthetic API failure: %s code=%s frames=%s",
                                         type(error).__name__, getattr(error, "code", ""), frames)
        return problem(error, *args, **kwargs)

    routes.problem = record_private_failure
    runpy.run_module("row_bot.app", run_name="__main__")


if __name__ == "__main__":
    main()
