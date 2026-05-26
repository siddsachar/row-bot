# Chat Only Runtime Implementation Plan

## Status

Planning note created during the model-provider overhaul follow-up.

This plan adds a normal chat experience for models that can generate text but
do not support native structured tool calling. It should be implemented as a
first-class runtime lane beside Agent Mode, not as a hidden downgrade inside the
ReAct graph.

The non-negotiable constraint is that current Agent Mode behavior must not
regress. Tool-capable models, background workflows, Developer Studio, Designer
Studio, and agentic channel workflows must keep using the existing Agent Mode
contract and readiness gate.

## Product Goal

Users should be able to select chat-capable non-tool models, including local or
custom DeepSeek-style models, and receive a normal streaming answer without
needing to understand provider capability details.

The app should auto-detect runtime capability:

- Agent-ready model -> full Agent Mode with tools.
- Chat-capable but non-tool model -> Chat Only mode.
- Unknown custom/local model -> probe first, classify, then route.
- Unavailable/non-chat model -> block with actionable setup guidance.

The switch must be visible but not disruptive. The user should see a clear mode
indicator such as:

`Chat Only: this model does not support tool calling. Tools and actions are off.`

There should also be an easy way to re-probe or switch back to an Agent-ready
model without losing the conversation.

## Surface Policy

### Normal Chat

Normal user chat is the only initial surface that should auto-route to Chat
Only.

Allowed:

- Compact assistant prompt.
- Streaming text responses.
- Reasoning/thinking display when the provider emits reasoning tokens.
- Attachments as passive context when already processed by existing file/vision
  helpers.
- Read-only lightweight memory/context injection if it can be done without
  tools and without writing memory.

Not allowed:

- Tool calls.
- Plugin tools.
- Channel tools.
- Destructive approvals.
- Background actions.
- Memory writes via tools.
- Designer/Developer tool mutations.

### Background Workflows

Agent Mode only.

Workflows are explicitly action-oriented and rely on tools, approval gates,
recursion budgets, channel delivery, and resumable LangGraph checkpoints. A
chat-only model selected as a workflow or task model should be rejected before
execution with a message that explains the model is Chat Only and that workflows
require an Agent-ready model.

### Developer Studio

Agent Mode only.

Developer Studio depends on workspace context, shell/filesystem tools, approval
gates, progress tracking, detached runs, and checkpoint repair. Chat Only would
make it look like coding work is possible while silently disabling the actions
that make the workspace useful, so it should be blocked.

### Designer Studio

Agent Mode only.

Designer Studio depends on the `designer` tool family for reading, mutating,
exporting, publishing, and managing project state. Chat Only could explain
design ideas, but it cannot safely create or edit the project. Initial support
should block Chat Only inside an active Designer project and ask the user to
switch to an Agent-ready model.

### Channels

Split channels into two cases:

1. User-initiated external chat in a running channel, such as Discord, Slack,
   Telegram, SMS, or WhatsApp.
2. Workflow/task delivery and approval messages sent to channels.

Initial recommendation:

- Workflow/task channel delivery remains Agent Mode only because the agentic
  work has already happened in the workflow runtime.
- Approval/resume flows remain Agent Mode only.
- Free-form channel conversations may use the same auto-routing as normal chat
  only when no channel-specific tool execution is expected.

Practical implementation detail: channel adapters currently call
`stream_agent()`, so the runtime router can support Chat Only for ordinary
messages. However, any channel resume/approval path, background task path, or
channel tool injection path must force Agent Mode and block Chat Only models.

## Runtime Architecture

Add a runtime capability layer above Agent Mode:

```text
Runtime mode:
  auto
  agent
  chat_only
```

`auto` is the default for normal chat. It resolves capability as:

1. Resolve active provider/model through `providers.resolution`.
2. Evaluate Agent Mode readiness.
3. If Agent-ready, use the existing ReAct graph unchanged.
4. If Agent is not ready but chat readiness passes, use Chat Only.
5. If capability is unknown and provider is custom/local, run a probe and cache
   the result.
6. If a tool-capable request later fails with a tool-support error, classify the
   model as Chat Only and retry the same user turn once through Chat Only.

Agent-only surfaces should call an explicit Agent readiness function and should
not accept `auto` downgrade.

## Readiness Contracts

Keep `AgentReadinessResult` strict. Add a sibling result:

```python
ChatReadinessResult:
    ready: bool
    provider_id: str
    model_id: str
    runtime_model: str
    selection_ref: str
    transport: TransportMode
    context_window: int | None
    required_context: int = 16_384
    streaming: bool | None
    credential_status: str
    capability_source: str
    confidence: str
    errors: list[str]
    warnings: list[str]
    actions: list[str]
```

Then add an aggregate:

```python
ModelRuntimeReadiness:
    agent: AgentReadinessResult
    chat: ChatReadinessResult
    selected_mode: "agent" | "chat_only" | "blocked"
    selection_reason: str
```

Chat Only requirements:

- Chat/responses task.
- Text output.
- Supported transport.
- Provider credentials/runtime configured.
- Effective context of at least 16K when known, with a local/custom warning if
  only a fallback context is available.

Agent Mode requirements remain unchanged:

- Structured tool calling.
- Tool-result round trip.
- 32K context.
- Full message/tool transport compatibility.

## Probing

Custom/local OpenAI-compatible probing should produce both agent and chat
classification:

- `/models` fetch.
- Simple non-stream chat request.
- Structured tool-call request.
- Tool-result continuation request.
- Streaming probe.
- Context metadata extraction.

Result fields should distinguish:

- `chat_ok`
- `agent_ok`
- `tool_calling`
- `tool_round_trip`
- `streaming_ok`
- `context_window`
- `classification: agent_ready | chat_only | unavailable`

The probe must never treat plain text that describes a tool call as tool support.
Structured tool calling must remain native and parseable.

## Chat Only Runtime

Add a dedicated chat-only streaming function rather than building an empty-tool
ReAct graph.

Responsibilities:

- Use `get_llm_for(model)` or provider runtime resolution.
- Build a compact message list from the thread transcript.
- Use a compact chat-only system prompt.
- Apply the 16K chat context policy.
- Stream tokens and reasoning using the same event tuple shape consumed by the
  UI: `thinking`, `thinking_token`, `token`, `done`, `error`.
- Persist assistant responses in the same visible thread history.

It should not:

- Build `create_react_agent`.
- Inject tools.
- Inject plugin/channel tools.
- Use approval interrupts.
- Repair tool calls.

## Transcript And Checkpoint Safety

Current message hydration uses `get_agent_graph()` in `ui.helpers.load_thread_messages`.
That is unsafe once a default/current model can be Chat Only because graph
construction may fail Agent readiness.

Before enabling Chat Only selection:

- Add model-independent transcript loading from checkpoints.
- Ensure thread hydration does not require Agent readiness.
- Ensure chat-only responses can be saved in a way existing thread rendering can
  read.
- Keep Agent Mode checkpoints compatible.

This is a critical regression-prevention step.

## Transcript Performance And Long-Thread Stability

This work should also be treated as a performance and stability improvement.
It is not only related to Chat Only.

Current behavior:

- `ui.helpers.load_thread_messages()` calls `get_agent_graph()` just to read a
  thread transcript.
- `get_agent_graph()` can resolve providers, run Agent readiness, rebuild tool
  wrappers, touch plugin/channel tools, and construct a LangGraph runtime.
- Long or dirty threads then pay that cost before any message rendering starts.
- The UI conversion path walks the full checkpoint message list and then the
  chat renderer progressively mounts all message DOM nodes.

Why this matters:

- Cold-loading a long thread should be a storage/read operation, not a
  model/provider/tool operation.
- Building model runtimes during thread load can block the UI event loop or
  contend with background timers.
- Large transcript hydration and rendering can contribute to NiceGUI/WebSocket
  disconnects because the client receives a burst of DOM work while periodic
  timers and socket pings are also active.
- Provider stream disconnects during generation are a separate category, but
  long-thread UI disconnects and load-time stalls are directly related to
  transcript hydration/rendering.

Add a `TranscriptService` style boundary:

```text
threads/checkpoint storage -> raw LangChain messages
raw LangChain messages -> compact UI message dicts
UI message dicts -> virtualized/progressive render
runtime execution -> Agent Mode or Chat Only
```

The service should support:

- Direct checkpoint reads without constructing `get_agent_graph()`.
- Latest-checkpoint lookup by `thread_id`.
- Conversion of raw LangChain messages to existing UI message dictionaries.
- Optional pagination/windowing for long threads, such as latest 50 visible
  turns plus "Load earlier" support.
- Cheap metadata reads: message count, last updated, last assistant/user
  preview, attachment/media presence.
- Cache invalidation when detached runs, background workflows, summarization,
  or media persistence modify a thread.

Performance goals:

- Opening a long thread should not create an LLM client or agent graph.
- Opening a long thread should render recent messages first and defer older
  messages.
- Token counter refresh should not force agent graph construction.
- Detached finalization should append or patch the cached transcript where
  possible instead of reloading the entire checkpoint immediately.
- Thread switching should remain responsive while hydration runs off the UI
  event loop.

Suggested implementation stages:

1. Extract the current message conversion logic out of `ui.helpers` into a pure
   converter function that accepts raw LangChain messages.
2. Add direct checkpoint read helpers in `threads.py`.
3. Replace `load_thread_messages()` internals so it reads via the checkpoint
   helper and never calls `get_agent_graph()`.
4. Add perf logging around checkpoint read, conversion, media hydration, and DOM
   render.
5. Add bounded initial rendering for long threads, with an older-message loader.
6. Audit token counter and detached media paths so they do not accidentally
   rebuild the agent graph for read-only work.

This should reduce random disconnects related to long-thread loading. It will
not by itself fix provider/network stream disconnects during generation, but it
will make those easier to diagnose by removing UI hydration stalls from the
same failure envelope.

## UI/UX Plan

### Model Picker

The inline picker should allow Chat Only models for normal chat and mark them
clearly.

Suggested labels:

- `Agent-ready`
- `Chat only`
- `Probe required`
- `Unavailable`

For Agent-only surfaces, the picker should either filter to Agent-ready models
or show Chat Only models disabled with:

`Chat Only models cannot run tools here.`

### Chat Banner

Extend the current cloud/local banner with runtime mode:

- Agent-ready cloud:
  `Using gpt-4o via OpenAI - Agent Mode - data is sent to the cloud`
- Agent-ready local:
  `Using qwen via Ollama - Agent Mode - local/private`
- Chat-only local:
  `Using deepseek via LM Studio - Chat Only - tools and actions are off`
- Probe required:
  `Capability check needed before using this model`

Provide `Re-probe` and `Switch model` actions when useful.

### Composer State

In Chat Only mode:

- Keep text input normal.
- Keep attachments if they can be converted into passive context.
- Do not show tool/skill controls as active.
- Show a small disabled tools indicator rather than hiding all evidence.

### Settings / Model Catalog

Catalog rows should be visible, not hidden:

- Agent-ready models can be set as default for all agentic surfaces.
- Chat-only models can be default for normal chat only, unless the app later
  adds per-surface defaults.
- Blocked models show remediation: connect provider, re-probe, increase context,
  choose Agent-ready model.

## Guardrails

- Do not change existing Agent Mode graph behavior for Agent-ready models.
- Do not make `AgentReadinessResult.ready` pass for chat-only models.
- Do not let manual capability overrides fake Agent Mode tool support.
- Do not allow workflows, Developer Studio, or Designer Studio to silently
  downgrade.
- Do not call tools from Chat Only, even if the model writes tool-like text.
- Do not let channel approval/resume flows run in Chat Only.

## Implementation Steps

1. Add runtime-mode data types and chat readiness tests.
2. Extend custom endpoint probe classification.
3. Add aggregate runtime readiness resolver.
4. Add model-independent transcript hydration.
5. Add Chat Only streaming runtime.
6. Route normal chat through `auto` runtime selection.
7. Force Agent Mode for workflows, Developer Studio, Designer Studio, and
   approval/resume paths.
8. Decide and implement channel policy:
   ordinary channel messages may use auto; channel resume/approval/task paths
   force Agent Mode.
9. Update inline picker, chat banner, and model catalog badges.
10. Add runtime failure retry: tool-support error -> classify Chat Only -> retry
    once for normal chat only.
11. Add regression tests around all Agent-only surfaces.
12. Run targeted provider/runtime/UI tests before broader suite.

## Test Plan

- Agent-ready model still builds the existing ReAct graph with full tools.
- Known cloud provider behavior remains unchanged.
- Custom endpoint with chat OK and tool failure becomes Chat Only.
- Tool call probe success remains Agent-ready.
- OpenRouter unknown tool metadata can become Chat Only for normal chat but not
  Agent-ready.
- Normal chat with a Chat Only model streams text and persists transcript.
- Normal chat tool-support runtime failure retries once through Chat Only.
- BG workflow with Chat Only model blocks before execution.
- Developer Studio with Chat Only model blocks before execution.
- Designer Studio with Chat Only model blocks before execution.
- Channel approval/resume with Chat Only model blocks.
- Ordinary channel message with Chat Only model either routes to Chat Only or,
  if final product decision changes, blocks with a clear message.
- `load_thread_messages()` works when the current/default model is Chat Only.

## End-To-End Implementation Handoff

This section is intended as the implementation plan for a fresh coding thread.

### Primary Objective

Implement Chat Only as a first-class runtime lane for normal chat while
preserving the current Agent Mode contract everywhere agentic behavior is
required.

At the same time, refactor transcript loading so reading a thread no longer
constructs the agent graph. This is both a prerequisite for Chat Only models and
a performance/stability improvement for long-thread loading.

### Current Architecture To Preserve

Important existing pieces:

- `providers.readiness.AgentReadinessResult`
- `providers.readiness.ensure_agent_ready`
- `providers.custom.probe_custom_endpoint`
- `providers.runtime.create_chat_model`
- `providers.resolution.resolve_provider_config`
- `providers.model_catalog.CatalogModelRow`
- `agent.get_agent_graph`
- `agent.stream_agent`
- `agent.invoke_agent`
- `ui.streaming.send_message`
- `ui.streaming.consume_generation`
- `ui.helpers.load_thread_messages`
- `threads.checkpointer`
- `ui.state.AppState.message_cache`

Agent Mode must remain strict:

- Minimum context: 32K.
- Structured tool calling required.
- Tool-result round trip required for custom/local OpenAI-compatible endpoints.
- Unknown tool support does not pass Agent Mode.
- No manual override should fake Agent Mode tool support.

### Files To Read First

Read these before editing:

- `docs/chat-only-runtime-implementation-plan.md`
- `docs/agent-mode-provider-overhaul-implementation-plan.md`
- `providers/readiness.py`
- `providers/custom.py`
- `providers/runtime.py`
- `providers/resolution.py`
- `providers/transports/openai_compatible.py`
- `providers/model_catalog.py`
- `providers/capabilities.py`
- `providers/models.py`
- `models.py`
- `agent.py`
- `ui/helpers.py`
- `ui/streaming.py`
- `ui/chat.py`
- `ui/chat_components.py`
- `ui/model_catalog.py`
- `threads.py`
- channel adapters under `channels/`
- `tasks.py`
- Designer and Developer entry points: `designer/editor.py`, `developer/ui.py`
- relevant tests under `tests/test_agent_readiness.py`,
  `tests/test_provider_custom.py`, `tests/test_provider_catalog.py`,
  `tests/test_model_picker_regressions.py`,
  `tests/test_context_policy.py`, `tests/test_openai_compatible_transport.py`

### Phase 1: Transcript Service Boundary

Implement this before exposing Chat Only in the picker.

Goal: loading a transcript must not construct an agent graph or touch provider
readiness.

Tasks:

1. Extract the message-conversion loop from `ui.helpers.load_thread_messages`
   into a pure function, for example:

   ```python
   langchain_messages_to_ui_messages(thread_id: str, messages: list) -> list[dict]
   ```

2. Add a direct checkpoint read helper, likely in `threads.py`, for example:

   ```python
   get_latest_checkpoint_messages(thread_id: str) -> list
   ```

   It should read the latest LangGraph checkpoint for the thread without
   calling `agent.get_agent_graph()`.

3. Update `ui.helpers.load_thread_messages(thread_id)` to:

   - call the direct checkpoint helper;
   - convert messages through the pure converter;
   - hydrate thread media;
   - never call `get_agent_graph()`.

4. Add perf logging:

   - checkpoint read time;
   - message conversion time;
   - media hydration time;
   - total `load_thread_messages` time.

5. Keep existing UI message shape exactly compatible:

   - `{"role": "user", "content": ...}`
   - `{"role": "assistant", "content": ...}`
   - `thinking`
   - `tool_results`
   - `charts`
   - `images`
   - `videos`

6. Audit read-only call sites:

   - `ui.helpers.persist_detached_thread_media`
   - token counter refresh in `app.py`
   - thread switch paths in `ui/sidebar.py`, `ui/home.py`,
     `ui/command_center.py`

   Do not broaden the refactor beyond what is needed, but ensure transcript
   loading itself no longer builds an agent.

Tests:

- Add a test proving `load_thread_messages()` does not call
  `agent.get_agent_graph()`.
- Add a test proving transcript loading still reconstructs tool results,
  charts, thinking blocks, and user image payloads from fake LangChain messages.
- Add a test proving transcript loading works when `get_agent_graph()` would
  raise Agent readiness errors.

### Phase 2: Chat Readiness And Runtime Classification

Goal: readiness can answer both "Can this model run Agent Mode?" and "Can this
model at least run normal chat?"

Tasks:

1. Keep `AgentReadinessResult` unchanged in semantics.

2. Add:

   ```python
   CHAT_ONLY_MIN_CONTEXT = 16_384
   ChatReadinessResult
   ModelRuntimeReadiness
   RuntimeMode = Literal["agent", "chat_only", "blocked"]
   ```

3. Add `evaluate_chat_readiness(...)`.

   Requirements:

   - chat/responses task;
   - text output;
   - supported transport;
   - provider/runtime configured;
   - target context 16K, with warnings when custom/local context is inferred.

4. Add `evaluate_runtime_readiness(...)`.

   Behavior:

   - if Agent-ready -> `selected_mode="agent"`;
   - else if Chat-ready -> `selected_mode="chat_only"`;
   - else -> `selected_mode="blocked"`.

5. Keep known Agent-ready providers passing as they do today.

6. Let OpenRouter models with missing tool metadata become Chat Only for normal
   chat if their chat/text readiness passes, but never Agent-ready.

Tests:

- Known cloud Agent-ready remains Agent-ready.
- Context under 32K but over 16K can be Chat Only, not Agent-ready.
- OpenRouter missing tool metadata is Chat Only or blocked depending chat
  metadata, but not Agent-ready.
- Custom endpoint with chat OK and tool probe failure is Chat Only.
- Non-chat media/embedding models are blocked.

### Phase 3: Custom Endpoint Probe Classification

Goal: probes classify endpoints as `agent_ready`, `chat_only`, or
`unavailable`.

Tasks:

1. Extend `probe_custom_endpoint()` output with:

   - `agent_ok`
   - `chat_only_ok`
   - `classification`

2. Keep existing fields:

   - `chat_ok`
   - `tool_calling`
   - `tool_round_trip`
   - `streaming_ok`
   - `context_window`
   - `errors`

3. Classification rules:

   - `agent_ready`: chat OK, structured tool calling true, tool round trip true.
   - `chat_only`: chat OK, but tool calling or round trip failed/missing.
   - `unavailable`: chat failed or no model available.

4. Continue to reject plain-text descriptions of tool calls as tool support.

5. Store probe classification in endpoint `last_probe`.

Tests:

- Probe with chat success and tool failure stores `classification="chat_only"`.
- Probe with full tool round trip stores `classification="agent_ready"`.
- Probe with chat failure stores `classification="unavailable"`.
- Existing streaming fallback tests still pass.

### Phase 4: Dedicated Chat Only Runtime

Goal: normal chat can stream a response without building a ReAct agent.

Tasks:

1. Add a compact chat-only system prompt, probably in `prompts.py`.

   It should be much shorter than Agent Mode and must not advertise tools,
   memory writes, workflows, actions, Designer, or Developer capabilities.

2. Add a chat-only message builder.

   Inputs:

   - thread id;
   - user input;
   - model override;
   - optional developer flag should block before this path;
   - optional passive file context.

   Behavior:

   - load recent thread messages through the transcript service;
   - convert to provider/LangChain messages;
   - include system prompt;
   - trim to 16K chat policy;
   - do not include ToolMessages as native tool messages. Historical tool
     results may be summarized or flattened as ordinary text if needed.

3. Add `stream_chat_only(...)` in `agent.py` or a small new module such as
   `chat_runtime.py`.

   It should emit the same event tuple shapes the UI already consumes:

   - `thinking`
   - `thinking_token`
   - `token`
   - `done`
   - `error`

4. Persist chat-only user/assistant turns compatibly.

   Prefer using the same checkpoint/message storage format where feasible, but
   do not require graph construction to write or read simple chat turns. If
   direct checkpoint writes are risky in the first pass, keep the UI thread
   cache plus existing persistence shape minimal and add tests around reload.

5. Preserve reasoning support:

   - `additional_kwargs["reasoning_content"]`
   - `<think>...</think>` parsing behavior should match the existing stream
     path where practical.

Tests:

- Chat Only runtime streams tokens and final `done`.
- Chat Only runtime does not call `create_react_agent`.
- Chat Only runtime does not bind tools.
- Chat Only runtime handles reasoning chunks.
- Chat Only turns reload correctly in the UI transcript.

### Phase 5: Runtime Router For Normal Chat

Goal: `stream_agent()` can auto-route only for normal chat.

Tasks:

1. Add runtime context flags in `config["configurable"]`, for example:

   - `runtime_surface`: `"normal_chat" | "workflow" | "developer" |
     "designer" | "channel" | "approval"`
   - `runtime_mode`: `"auto" | "agent" | "chat_only"`

2. In `ui.streaming.send_message`, set:

   - normal chat: `runtime_surface="normal_chat"`, `runtime_mode="auto"`;
   - Developer active: `runtime_surface="developer"`,
     `runtime_mode="agent"`;
   - Designer active/project chat: `runtime_surface="designer"`,
     `runtime_mode="agent"`.

3. In tasks/workflows, set:

   - `runtime_surface="workflow"`, `runtime_mode="agent"`.

4. In approval/resume paths, force:

   - `runtime_surface="approval"`, `runtime_mode="agent"`.

5. In ordinary channel adapters:

   - normal incoming user messages: `runtime_surface="channel"`,
     `runtime_mode="auto"`;
   - resume/approval paths: `runtime_surface="approval"`,
     `runtime_mode="agent"`.

6. In `stream_agent()`:

   - if forced Agent Mode, call existing Agent path and preserve behavior;
   - if normal/chat/channel auto, evaluate runtime readiness;
   - route to Agent Mode or Chat Only;
   - if blocked, yield clear error.

7. Runtime failure fallback:

   For normal chat/channel auto only, if Agent path fails with a clear
   tool-support rejection, classify as Chat Only and retry the same user turn
   once through Chat Only. Emit a user-visible mode notice.

Tests:

- Normal chat auto routes Agent-ready to existing graph.
- Normal chat auto routes chat-only to Chat Only.
- Workflow forces Agent and blocks Chat Only.
- Developer forces Agent and blocks Chat Only.
- Designer forces Agent and blocks Chat Only.
- Approval/resume forces Agent and blocks Chat Only.
- Ordinary channel message can route Chat Only.
- Channel approval cannot route Chat Only.

### Phase 6: UI/UX Integration

Goal: the user understands what mode is active without needing to manage it.

Tasks:

1. Chat banner:

   Add runtime mode to `ui.chat._model_surface()`:

   - Agent Mode;
   - Chat Only;
   - Probe required;
   - Blocked/unavailable.

2. Inline model picker:

   - Allow Chat Only selections in normal chat.
   - Stop using the old local-only tool-support block as a hard rejection for
     normal chat.
   - For Agent-only surfaces, disable or reject Chat Only selections.

3. Model catalog:

   - Show badges: `Agent-ready`, `Chat only`, `Probe required`, `Unavailable`.
   - Keep models visible.
   - Chat-only models should be selectable for chat but not for Agent-only
     defaults unless per-surface defaults exist.

4. Notifications:

   - On first auto-switch to Chat Only, show a concise notification.
   - The persistent banner is the durable indicator.

5. Re-probe action:

   - In custom endpoint UI/model catalog, expose re-probe where existing probe
     actions already live.
   - Do not probe automatically on every render.

Tests:

- Banner renders Chat Only copy for chat-only readiness.
- Model picker includes chat-only models for normal chat.
- Agent-only surface does not accept chat-only override.
- Catalog row status reason is actionable.

### Phase 7: Long-Thread Rendering Performance

Goal: reduce long-thread UI stalls and random client disconnects during loading.

Tasks:

1. Keep existing chunked render in `ui.chat`, but add perf logging around:

   - initial render batch;
   - chunk render batches;
   - finalization.

2. Consider lowering initial render for very long threads:

   - latest 15 messages today is fine;
   - for extremely long threads, avoid pre-building expensive media/tool
     expansions for older messages until needed.

3. Add a follow-up-friendly boundary for pagination:

   - initial pass can still load all messages into `state.messages`;
   - render only recent window first;
   - add "Load earlier" later if needed.

4. Avoid token-counter work during active hydration and generation.

5. Ensure timers are deactivated on disconnect and do not race hydration.

Tests:

- Existing chat render tests still pass.
- New perf logging should not assert exact timings.
- Long fake transcript can load without invoking model/runtime functions.

### Phase 8: Documentation And Release Notes

Update:

- `docs/ARCHITECTURE.md`
- `README.md` if model selection behavior is described there.
- `RELEASE_NOTES.md`

Document:

- Agent Mode vs Chat Only.
- Which surfaces require Agent Mode.
- Custom endpoint probe classification.
- Long-thread transcript loading improvement.

### Acceptance Criteria

The implementation is complete when:

- Agent-ready models still use the existing ReAct graph and full tool set.
- Chat-capable non-tool models can answer in normal chat.
- Chat Only never runs tools.
- BG workflows are Agent Mode only.
- Developer Studio is Agent Mode only.
- Designer Studio is Agent Mode only.
- Approval/resume flows are Agent Mode only.
- Ordinary channel chat can use Chat Only if policy remains accepted.
- Transcript loading does not build the agent graph.
- Long-thread cold load is measurably lighter through logging.
- Model catalog and chat banner clearly indicate runtime mode.
- Targeted tests pass.

### Suggested Targeted Verification

Run a focused suite before broader tests:

```powershell
pytest tests/test_agent_readiness.py tests/test_provider_custom.py tests/test_provider_catalog.py tests/test_model_picker_regressions.py tests/test_context_policy.py tests/test_openai_compatible_transport.py tests/test_provider_runtime.py tests/test_provider_resolution.py -q --basetemp D:\Code\Thoth\.tmp\pytest -p no:cacheprovider
```

Add any new tests for transcript loading and chat-only routing to that command.

Also run:

```powershell
python -m compileall agent.py models.py providers ui channels designer developer tasks.py threads.py tests -q
```

## Starting Prompt For Implementation Thread

Use this prompt in a fresh thread:

```text
We are working in D:\Code\Thoth on branch codex/local-provider-compatibility.

Implement the Chat Only runtime and transcript-performance plan described in:

- docs/chat-only-runtime-implementation-plan.md
- docs/agent-mode-provider-overhaul-implementation-plan.md

Important constraints:
- Do not regress current Agent Mode behavior.
- Agent Mode remains strict: 32K context, structured tool calling, and tool-result round trip required.
- Chat Only is a first-class sibling runtime for normal chat, not a hidden downgrade inside the ReAct graph.
- Normal chat should auto-route: Agent-ready models use Agent Mode; chat-capable non-tool models use Chat Only.
- BG workflows are Agent Mode only.
- Developer Studio is Agent Mode only.
- Designer Studio is Agent Mode only.
- Approval/resume flows are Agent Mode only.
- Ordinary channel user messages may use auto routing, but channel approval/resume/workflow paths must force Agent Mode.
- Transcript loading must not call get_agent_graph() or require provider/model readiness.
- Treat transcript loading as a performance/stability improvement for long threads.

Start by reading:
- docs/chat-only-runtime-implementation-plan.md
- docs/agent-mode-provider-overhaul-implementation-plan.md
- providers/readiness.py
- providers/custom.py
- providers/runtime.py
- providers/resolution.py
- providers/transports/openai_compatible.py
- providers/model_catalog.py
- providers/capabilities.py
- providers/models.py
- models.py
- agent.py
- ui/helpers.py
- ui/streaming.py
- ui/chat.py
- ui/chat_components.py
- ui/model_catalog.py
- threads.py
- tasks.py
- channels/discord_channel.py, channels/slack.py, channels/telegram.py, channels/sms.py, channels/whatsapp.py, channels/approval.py
- designer/editor.py
- developer/ui.py
- tests around provider readiness/custom/catalog/runtime/model picker/context/openai-compatible transport

Implementation order:
1. Refactor transcript loading so load_thread_messages() reads checkpoint messages without get_agent_graph().
2. Add transcript conversion tests and regression tests proving no graph/model construction is needed to load a thread.
3. Add ChatReadinessResult and aggregate runtime readiness while preserving AgentReadinessResult semantics.
4. Extend custom endpoint probes with agent_ready/chat_only/unavailable classification.
5. Add a dedicated Chat Only streaming runtime with compact prompt and no tools.
6. Add runtime routing for normal chat/channel auto mode and forced Agent Mode for workflows, Developer, Designer, and approvals.
7. Update chat banner, inline model picker, and model catalog badges/status.
8. Add tests for all forced-Agent surfaces and normal-chat Chat Only behavior.
9. Run targeted tests and compile checks.

Keep changes scoped and preserve existing cloud provider constructors, transport behavior, media flows, and Agent Mode graph behavior.
```
