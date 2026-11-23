# Prompt Context And Cache Contract

Row-Bot assembles Agent requests so that each request starts with the exact
bytes of the one before it. Provider prompt caches (Anthropic breakpoints,
OpenAI, Gemini, xAI and DeepSeek automatic caching, Ollama and llama.cpp KV
reuse) only pay off for that shared start, so:

- the system prompt is made only of **stable** sections;
- everything that changes from turn to turn travels in one **turn context**
  message per turn, appended after the turn's message and kept in the
  checkpoint;
- conversation history is replayed as it was stored, and old tool output is
  cleared only in rare jumps (see Tool results in history).

## Stable sections

Stable sections change only when user settings, the provider or model, enabled
tools, skills, plugins, the Designer project's mode or the thread's profile
change, or the code folder's project notes change (read at a turn's start, so an
edit applies from the next turn). All of them may be marked cacheable.

- `agent.root`: identity line and agent guidelines from the ReAct system prompt.
  On a window of 32K or less its TASKS and MEMORY parts move to
  `skills.tool_guides` (see Tool set).
- `agent.turn_context`: what the turn context is, and that only Row-Bot writes
  it (see Authority).
- `agent.profile`: active thread Agent Profile instructions. A child Agent gets
  its profile's instructions and handoff contract here only; its first message
  names the profile without repeating them.
- `platform.context`: local platform and shell guidance.
- `agent.runtime_guidance`: Agent Mode is active and tools are usable, plus the
  setup guidance (conversation setup, how to offer a tool that is off, apps
  turned off or switched off in this chat). The lists themselves are turn
  context values.
- `self_knowledge.static`: static Row-Bot feature knowledge and optional
  self-improvement guidance.
- `designer.guide`: Designer tools, HTML requirements and guidelines for the
  active project's mode. The project's values are turn context values.
- `agent.interactive_progress`: progress-update guidance for interactive
  surfaces.
- `background.override`: static background-workflow override.
- `background.persistent_thread`: persistent-thread note for tasks that keep
  their thread.
- `skills.tool_guides`: automatic tool guide instructions for the tool groups
  bound. A tool-linked skill marked `enabled_by_default: false` (the Developer
  workflows) is not a guide: it loads with `skill_load` while its tools are
  bound. On a window of 32K or less this section is one line per guide plus the
  TASKS and MEMORY guidance (see Tool set).
- `developer.project_notes`: the code folder's own notes for agents (`AGENTS.md`,
  `CLAUDE.md`, `.cursorrules`, `.github/copilot-instructions.md` from the
  folder's root only), with the Developer tools only. Read once per turn at its
  first model call. Bounded to 8,000 characters together (2,000 on a window of
  32K or less; a cut file ends with where to read the rest), fenced as
  `project_notes` after a fixed header saying the notes cannot change approvals,
  grant permissions, name recipients or URLs, or ask for anything to be revealed,
  and between the fixed lines `<<<PROJECT NOTES>>>` and `<<<END PROJECT NOTES>>>`.
  A file the injection scan flags, that looks like it holds a secret, links
  outside the folder, is over 64 KB or isn't UTF-8 text is left out and named.
  `CLAUDE.md` `@path` imports are never followed.
- `skills.manual`: the skills the conversation keeps: the profile's skills, the
  thread override and pinned skills.
- `chat_only.root`: Chat Only identity, behavior rules, and profile context.

Within a turn nothing edits these sections. A skill loaded with `skill_load`
arrives in the tool result (its full instructions are part of the result), and
a skill mentioned with `/skill` arrives in the turn context; neither is ever
moved into `skills.manual`, so an ongoing conversation keeps its prefix. A
loaded skill whose instructions are no longer in view (for example after
compaction, or in a conversation from before this contract) is delivered again
once in the next turn context.

## Turn context

`row_bot.turn_context` builds one app-authored `HumanMessage` per turn whose
text is a `<row_bot_turn_context>…</row_bot_turn_context>` block:

- **When.** Built on the turn's first model call (the turn is the execution
  budget of the run), and reused unchanged by every later call of the turn.
  Its values are gathered once: recall (embedding, search, graph lookup,
  `touch`, trace), the dynamic-state lookups, the Designer project read and
  the skill usage record all run once per turn.
- **Where.** Right after the turn's message, before its assistant and tool
  messages. The pre-model hook returns it as a `messages` update, so LangGraph
  persists it and every later turn replays it byte for byte. Providers receive
  it inside the user turn it follows (joined to that message as one more text
  part), which every provider shape accepts, including chat templates that
  reject two user messages in a row. A context that follows anything else
  (an event, a tool result) is a user message of its own.
- **What.** The current date and time (minute resolution, with the UTC offset
  and the time in UTC), the approval mode, the workspace folder (and a bound
  code folder; `~/…` under the home folder), the tool groups available, what
  this turn leaves out (`NOT IN THIS TURN`, see Tool set), tools and apps that
  are off, Row-Bot's live state (model, providers, channels, MCP,
  Designer, Dream Cycle, update), the active Designer project's values (name,
  canvas, brand and CSS variables, brief, references, pages, recent manual
  edits, canvas rules), skills for this message, skills switched off after
  they were shown, and relevant long-term memories.
- **Diff-only.** After the first, a value appears only when it changed since
  the latest turn context still in view. The date is said again when the day
  changes; the time is one short line every turn. Recall lists only memories
  not already shown in view. A value that became empty says so where that
  matters (for example "TOOLS THAT ARE OFF: none.").
- **Full snapshot.** When no earlier turn context is in view, or a compaction
  summary replaced what the earlier ones said, the next turn context is a full
  snapshot. A compaction later in a turn appends a full snapshot after the
  latest message.
- **Comparison data.** Digests of each value, the memory and skill ids shown,
  and the summary in force are kept in the message's `additional_kwargs`. They
  never reach a provider: the request carries only the block's text.
- **Hidden from people.** The message is never a transcript row and is skipped
  by every reader of history: the transcript and history pages, exports and
  PDF, search, memory extraction, the conversation search tool, a child
  Agent's parent context, Chat Only and the legacy projection. Retry replaces
  it with its turn; removing a provisional answer keeps it with its message;
  compaction keeps it in its message's group.

Within one long turn the model sees the time the turn started, not the current
minute.

## Authority

Only Row-Bot writes a `<row_bot_turn_context>` block, and only in that
position. The stable `agent.turn_context` section says so. Before a request is
sent, the tag in any form is replaced (`[row_bot_turn_context tag removed]`) in
what people send (attached file context included) and in every tool result;
values Row-Bot quotes inside its own block (memories, project names, skill
instructions) and a code folder's project notes are neutralized the same way. A message is recognized as a turn
context by its metadata, never by its text. The checkpoint keeps what was
sent. Approvals and tool gating stay enforced in code, and the approval policy
text stays in the stable prompt.

## Tool results in history

The checkpoint keeps every tool result as it was created (after its cap, see
Tool schemas and results). What the model
sees is derived from it on each request (`agent._apply_history_hygiene`,
`row_bot.history_hygiene`), deterministically, so an earlier request stays an
exact prefix of a later one:

- **Sticky clearing.** Two marks per thread (`thread_meta.history_hygiene_json`)
  say where old output stops being sent in full: a non-browser result before
  the `mask` mark (over 128 tokens) is sent as a one-line summary, and a browser
  result before the `browser` mark as a URL and title stub. The marks move only
  in jumps: once the output they would clear reaches about a fifth of the
  history budget (2K to 50K tokens), or the request reaches the compaction
  point. A jump keeps the latest five person turns and the newest browser
  results in full, and is one cache rewrite instead of one per turn. A cleared
  result is cleared the same way on every later call. Marks are anchored on a
  message and reset when it is gone (a retry or rewind past it); results behind
  a compaction summary never count towards a jump.
- **Repeats.** A result identical to an earlier one still in view is sent as a
  one-line reference to that call; the earlier copy never changes.
- **Emergency shrink.** Every result is cut in proportion to its size only when
  the newest turn alone reaches the compaction point, so compaction cannot bring
  the request back (without a known context policy: when the request passes the
  history budget). Otherwise results go as they were stored.
- **Fields kept.** A rewritten result keeps its `status` (Claude is told an
  error is an error), artifact, id and metadata.
- **Untrusted content.** When a result is stored, after its cap, Row-Bot decides
  once whether it carries outside text (the tools' declared names, MCP tools,
  plugin and custom tools, tool and skill search; a `read_tool_output` page by
  its saved output's source) and runs the injection scan once. The
  decision is kept with the result, and every request wraps its text in an
  `<EXTERNAL_CONTENT>` fence from it, after any shortening, so the tags are never
  cut. A result stays fenced even if its tool is later not registered. Results
  stored before this decide on each request, as before. A child agent's answer
  or error relayed to its parent in a wake-up event is fenced the same way;
  Row-Bot's own lines around it are not.
- **Older charts.** A chart result stored before charts moved to the result's
  artifact (WP D2) keeps its Plotly figure in the checkpoint, where the
  transcript reads it, and reaches the model as D2's short chart summary
  (`agent._legacy_rendered_result_text`), the same bytes on every call.

## Compaction

Compaction does what Claude Code does (`models.context_reserves`,
`agent._prepare_with_compaction`):

1. **Clear first.** When a request reaches the compaction point, old tool output
   is cleared in one jump (above). History is summarized only if the request is
   still over.
2. **Near the limit.** The compaction point leaves room for the reply and for
   the summary. Usable input is the window minus a reply allowance (15% of a
   window where input and output share it, 5% where the provider limits input
   on its own, at most 20K tokens); compaction starts a summary allowance below
   that (10% of the window, at most 13K tokens).

   | Window | Before | Now |
   |---|---|---|
   | 32K local | 24,576 | 24,575 |
   | 64K local | 49,152 | 49,151 |
   | 128K | 98,304 | 98,411 |
   | 200K | 150,000 | 167,000 (177,000 input-only) |
   | 1M | 750,000 | 967,000 |

   The user's context cap still sets the window. A custom endpoint whose server
   doesn't report the model's window, and that has no Native limit, gets its
   profile's fallback, 32,768 tokens (`capacity_source` `profile_default`,
   which readiness reports as inferred); a server's report or a Native limit
   wins, and the cap only trims. Like the 128K fallback for unknown cloud
   models it is a guess, so compaction runs at it but a request over it is
   never refused before the server answers.
3. **A full snapshot after.** The next turn context after a summary is a full
   snapshot, checked on every call, so a compaction made outside the pre-model
   hook (an overflow retry) is followed by one too.
4. **No thrashing.** A compaction that fails is not tried again for the same
   input in the same turn: the marker
   (`thread_meta.compaction_failure_json`) names the summary in force, a digest
   of every older complete group, the model and the policy. A new turn, or new
   input to age, gets one more attempt.
5. **Prepared ahead (B6).** When a turn settles at 85% or more of the
   compaction point on a cloud model, the next summary is written in the
   background (`agent._prepare_summary_ahead`) by the same boundary choice and
   summarizer a compaction in the turn uses, so it is the same summary. It is
   kept per mode in `thread_meta.prepared_summary_json`, saved only while the
   summary in force is still the one it builds on. When a request reaches the
   point, the turn uses it, inside the compaction lock and before
   `compaction_started`: no summarizer call and no pause. It is used only while
   the mode, model, provider, policy, the summary under it and the digest of
   the messages before its boundary all match; otherwise it is dropped and
   compaction runs as above. If it leaves too little room, compaction starts
   from it and summarizes only the newer range. The preparation takes the lock
   without waiting and skips when a compaction or another turn of the chat
   runs. A failed one stores nothing and sets no failure marker. Local models
   (Ollama, custom endpoints, or a compaction route to one) never prepare. Its
   summarizer calls are counted as `compaction` in the usage ledger, outside
   any turn, with `row_bot_compaction_prepared` in their run metadata. This
   changes when a summary is made, not what it says: the summarizer prompt and
   the way the summary is placed in the request are unchanged.

**Calibrated estimate.** Requests are sized at about four characters a token
(thinking blocks by their text, not their signatures), then scaled per provider
and model by a moving average of the input the provider reported over the
estimate (`row_bot.context_calibration`, kept in `usage.db`: counts only). The
factor stays between 0.5 and 3.0, is learned only from requests of 1,000
estimated tokens or more with a plausible ratio, and never on local Ollama,
whose count leaves out what it had cached. Clearing, the emergency shrink and
compaction all compare the calibrated estimate.

**Overflow.** When a provider refuses a request as too long, the overflow
raises the calibration (the request was at least the window), old output is
cleared and the history compacted, and the call is retried once
(`agent._AgentModelCall`, and the same in Chat Only). The provider refused the
request before reading it, so the retry costs nothing extra. A second overflow
stands.

**Each mode its own summary.** Agent mode and Chat Only keep separate summaries
(`summary_state_json`, `chat_only_summary_state_json`), so switching modes keeps
both.

**The splice.** The summary replaces everything before the first kept message,
found by its id: preparation drops empty and reasoning-only turns and adds
repair stubs, so a count of raw messages could cut the person's message or
orphan a tool result. The kept part is normalized again on its own.

**Thinking.** A thinking block's signature is bound to everything before it, so
a summary unbinds the kept turns' blocks. The turns that were complete when the
summary was made lose their thinking blocks in the request (text and tool
calls stay). The turn then in progress keeps them: a tool round's thinking
must stay, and the API's `drop_block` (WP A2b) covers it.

**What checkpoints keep.** The provider request the pre-model hook prepares is
never checkpointed (an untracked LangGraph channel); a model call resumed after
a failure prepares it again, to the same bytes. A model's inline
`<think>…</think>` text is stored as the answer's reasoning, where the
transcript shows it, not as answer text sent back on every call (except to
custom endpoints that replay reasoning). Images and other base64 data in stored
messages stay where they are for now (see the A4b handoff).

## Provider Rules

Prompt caching on Claude routes is automatic: there is no setting
(`row_bot.prompt_cache`, `row_bot.providers.capabilities.prompt_cache_route`,
`row_bot.providers.transports.prompt_cache_breakpoints`).

**Where the breakpoints go.**

- **System prompt.** One explicit breakpoint on the last stable system block.
- **Conversation tail.** An Agent or Chat Only request is tagged for tail
  caching (private metadata on its last message, never sent). The transport
  then caches up to the end of the request. On the direct API this is
  top-level automatic caching, which the API puts on the last cacheable block
  and moves forward as the conversation grows. The turn context never needs a
  breakpoint of its own.
- **Long appends.** Each breakpoint looks back at most 20 positions for an
  earlier cache entry. A run of `tool_use` blocks counts as one position, and
  so does a run of `tool_result` blocks. When a direct-API request adds more
  than 15 positions since the last breakpoint Row-Bot sent with the same
  prefix, one explicit breakpoint goes where that request ended.
- **Limits.** A request never carries more than four breakpoints. Stray
  markers in history are dropped before placement.
- **Summarizer.** The compaction summarizer's fixed system prompt gets a
  five-minute breakpoint. Its request is never tail-cached.
- **One-off calls** (titles, background jobs) get no tail caching.

**Lifetime (D3).** Both the system breakpoint and the tail use the request's
lifetime, so a longer one never follows a shorter one, which the API rejects.

| Request | Lifetime |
|---|---|
| A conversation a person is in: desktop and remote chat, voice, Designer, channels, channel approvals | 1 hour |
| Back-to-back loops: workflow and task runs, goal continuations (`goal` surface or `internal_goal_continuation`), child agents (`agent_child`) | 5 minutes |
| Compaction summary | 5 minutes |

A five-minute marker is `{"type": "ephemeral"}` (the API default), and a
one-hour marker adds `"ttl": "1h"`.

**Routes.**

| Route | What it gets |
|---|---|
| Direct Anthropic API | System breakpoint, top-level automatic caching, and the long-append breakpoint |
| OpenRouter `anthropic/claude-*` | The system prompt as one system message of text blocks, one per section, with the breakpoint on the last stable block, plus OpenRouter's top-level `cache_control`. Both forms are documented by OpenRouter, with `ttl`. No long-append breakpoint yet |
| Requesty Claude routes (`anthropic/`, `bedrock/`, `vertex/`) | Requesty's documented `"requesty": {"auto_cache": true}` on conversation requests. Requesty places the breakpoints itself, and it has no `ttl`. The system prompt stays one string, so the largest block is the whole prompt |
| MiniMax, OpenCode Anthropic Messages, Atlas Cloud | Nothing, until an opt-in live check shows they accept cache instructions |
| Claude Subscription | Nothing. It is moving to the Claude Agent SDK (D7) |

OpenAI, xAI, Codex, Gemini and custom OpenAI-compatible endpoints never get
Anthropic markers. Codex keeps its `prompt_cache_key` behaviour.

**Safety net.** A 400 that names `cache_control`, `ttl` or `auto_cache` on a
request that carried cache instructions is retried once without them. No other
400 is retried.

- On OpenRouter and Requesty, the route is then remembered for the process,
  so a gateway that rejects them costs one failed request per process.
- On the direct API, which documents every instruction Row-Bot sends, only
  that request is retried.
- A failure placing the system breakpoint is logged once per thread, and the
  request goes out without it.

**Truncation.** A reply that stopped at the output token limit says so on every
route, while the saved reply stays the model's own text, so the model's context
does not change:

- The desktop transcript shows a "Cut short by the output limit" chip under it.
  The checkpoint reader reads the saved reply's stop reason (`cut_short`).
- Agent mode and Chat Only stream the "cut short" notice as the reply's last
  token, so channel replies end with it, and "done" carries it.
- A workflow step or delegated agent's result ends with it (`invoke_agent`).

`providers/stop_reason.py` reads why a reply stopped from
every provider shape: OpenAI-style `finish_reason: "length"`, Ollama
`done_reason: "length"`, Anthropic `stop_reason: "max_tokens"`, Gemini
`finish_reason: "MAX_TOKENS"`, and, for Responses APIs (OpenAI, Codex, xAI
OAuth), `status: "incomplete"` with `incomplete_details.reason:
"max_output_tokens"`. Row-Bot's own transports (Codex, xAI OAuth, Ollama Cloud,
Claude Subscription and the OpenAI-compatible routes, including Anthropic-shaped
ones) end their stream with that reason.

## Tool set

A conversation binds the same tools on every turn (`agent.get_agent_graph`), so
the tools block at the start of every request stays byte-stable. Nothing a
message says changes it.

- **One turn's scope is enforced when a tool is called.** A message that
  @mentions apps narrows its turn to them; a request for one kind of media
  leaves the other media tools out (`conversation_creation.media_left_out`); a
  workflow step that names its apps uses only those. That scope
  (`integrations.scope`, the turn's `app_scope`) never removes a bound tool:
  `agent._turn_scope_refusal` refuses a call to a tool outside it, before any
  approval card or effect, with a `BLOCKED: … is not part of this turn` result
  that says what to do. `tool_search` leaves those tools out of its results,
  and `tool_invoke` reaches the same refusal. The turn context lists what the
  turn leaves out. A continued turn and the agents it starts keep its scope.
- **Apps switched off in a chat are not bound.** They are a setting of the
  conversation (`off_servers` and `off_tools` in the scope).
- **`skill_search` lists every authorized skill**, active or not. Which skills
  a turn loaded with `skill_load` or mentioned with `/skill` is read when
  `skill_search` or `skill_load` runs, never built into the bound tools.
- **External tools come in a fixed order.** MCP tools are sorted by server,
  then tool name, whatever order the servers connected in.
- **X is asked once per sign-in.** A check that cannot reach X keeps the X
  tools bound (the call itself reports the failure); only X refusing the
  sign-in leaves them out, until a new sign-in.
- **Developer tools come with a code folder.** Without one every Developer
  tool fails, so none of them (nor the Developer guide) is bound. The setup
  tools stay offered, and the runtime guidance says a code folder brings the
  Developer tools. Binding a folder adds them from the next turn.
- **Less-used tools are offered on demand on every window** (B1):
  `agent.RARELY_USED_TOOL_GROUPS` and `RARELY_USED_TOOLS`. They are not bound;
  `tool_search` lists them by group (`group: tool_a, tool_b`, names only, at
  most 1,500 characters) and returns a tool's whole input schema. Each one runs
  when called by its own name or through `tool_invoke`, wrapped before it is
  bridged, so its approval, profile limits and repeat guard are unchanged; under
  Block a destructive one is not offered at all. A profile with its own tool
  list binds exactly that list. App, plugin, Custom Tool and channel tools keep
  their descriptions in the catalog.
- **A null means left out.** Optional fields that an empty value already
  leaves unset are plain in the schema (`"default": ""`, no null branch);
  `tools.base.NullMeansUnset` drops a `null` a model sends for one before
  validation, so it behaves as the field left out.
- **The plain-chat tool block has a budget**: 38,000 provider-facing characters
  (about 9,500 tokens; about 36,000 are used) for a 200K cloud chat with no code
  folder and no apps
  (`tests/subsystem/agents/test_default_tool_budget.py`, snapshot
  `tests/snapshots/tools/plain_chat_tool_set.txt`). Raising it is a deliberate
  edit of the number in that test.

The tool list is rebuilt only on a real configuration change, which costs one
cache reset each: an MCP server connecting or disconnecting, tool settings or a
tool switched on or off, an app switched off or on in the chat, the model or
its window, the approval mode, the agent profile, or a code folder or design
bound to the conversation.

**Middle tier.** A window of 64K or less, or up to 128K for a model on this
computer, also offers Row-Bot's self-management (`agent.ON_DEMAND_TOOL_GROUPS`)
through `tool_search` instead of binding it. On this computer means Ollama's
own models and a custom endpoint saved with `execution_location: "local"`
(Run on this computer, a found server, one added by hand;
`models.is_local_custom_endpoint`), whose whole prompt is processed here.
Learning's personal-model endpoints (`managed: "learning"` and
`row-bot-personal-*`) keep the 64K rule they were trained in
(`learning/training/shape.py`).

**Small-context profile.** A model whose effective window is 32K or less (any
provider: local Ollama, a custom endpoint, or a model capped that low) gets a
smaller fixed envelope, so it can run Agent mode:

- the core tools are bound: files, shell, web search and `read_url`, memory,
  tasks (Developer's in a code folder), with the bridges (`tool_search`,
  `tool_invoke`, `skill_search`, `skill_load`, `read_tool_output`). Every other
  tool, and every external tool, is offered through `tool_search` and can be
  called by its own name or through `tool_invoke`, with the same approvals;
- tool guides are one line each, and each reads in full through `skill_load`;
- the TASKS and MEMORY guidance is sent only when those tools are bound;
- recall shows at most three memories in its smallest block, and the
  self-knowledge blocks (static and live state) are left out.

Larger windows keep the prompt as described above.

## Tool schemas and results

Every bound tool's schema is part of each request's prefix, so each topic has
one home:

- scheduling decision rules (which field for which request) are in the
  `task_create` description; how workflows run and are written, the schedule
  formats in full and the reminder examples are in `workflow_guide`, read with
  `skill_load`; the system prompt only points to them;
- the workspace scope is said once: the turn context names the workspace folder
  and says a file tool asks before it uses a full path elsewhere
  (`workspace_write_file` keeps its own note); listing and searching are one
  tool, `workspace_find`;
- status categories and settings are enums on `row_bot_status` and
  `row_bot_update_setting`, explained in `row_bot_status_guide`;
- memory rules are in the system prompt, not repeated in each memory tool;
- closed sets are JSON-schema enums, and ids the runtime already knows (the
  parent thread, run and orchestration, a goal's conversation) are not schema
  fields;
- descriptions do not start with the tool's display name.

`tests/subsystem/tools/test_tool_schema_budget.py` keeps each default tool's
schema size on record (`tests/snapshots/tools/default_tool_schema_sizes.txt`).
Record a deliberate change with `ROW_BOT_UPDATE_SNAPSHOTS=1`.

Tool results are bounded once, when they are created (`row_bot/tool_outputs.py`):

- A result over about 25K tokens (100,000 characters) keeps its head and tail,
  after a notice naming a handle. The full text is kept for the conversation
  under the data folder (`tool_outputs/`) and removed with it.
- `read_tool_output(handle, offset)` pages through it. It is bound with every
  Agent tool set.
- The saved output records the tool that produced it. A page is fenced as
  external content when that source would have been: an untrusted tool, an MCP,
  plugin or Custom Tool, or text that already held a fence (a child Agent's
  words). Row-Bot's own results, such as `wiki_read` or `task_list`, read back
  unfenced, and an unknown handle reads back fenced.
- Tighter caps apply to command output (30,000 characters), skill references,
  wiki articles, the conversation and task lists, and Gmail bodies.
- `workspace_read_file`, `developer_read_file`, `developer_get_diff` and
  `read_url` page with `offset` and `limit`. Their budgets are fixed: they do
  not grow with the context window.
- An MCP result is shown whole up to its server's `output_limit` (24,000
  characters by default); a longer one keeps its head and tail with a handle,
  as above, instead of being cut.
- A result the client renders keeps its payload out of the model's text
  (`row_bot/rendered_results.py`). A chart's Plotly figure moves to the
  ToolMessage's `artifact`, which is never sent to a provider; the transcript
  projection reads it there, and from the text for results stored before. The
  model reads a summary instead (type, title, axes, traces with their points and
  ranges) naming a handle that `read_tool_output` reads the figure from. A
  plugin's image is shown to the person as generated images are, and the model
  reads its caption.

The untrusted-content fence is added when the model reads a result, after the
cap, so the cap never cuts it.

## Verification

`tests/subsystem/agents/test_prompt_prefix_stability.py` drives the real Agent
preparation for eight provider request shapes and asserts that each request is
an exact prefix of the next one (`cache_control` ignored) across iterations and
turns. Cases that still break are strict xfails naming the work package that
fixes them. At DEBUG, the `row_bot.prompt_prefix_diff` logger names the first
section or message that changed between consecutive requests in a thread.

`tests/subsystem/agents/test_stable_tool_set.py` covers the tool set: the graph
and its tools byte-stable across media, @app and /skill turns, a rebuild only on
a configuration change, the call-time refusal before any approval (the security
contract), and Developer tools with a code folder.
`tests/subsystem/agents/test_small_context_profile.py` shows a 32K local model
getting an Agent request under its usable input, and the profile's envelope.

`tests/subsystem/agents/test_turn_context.py` covers the turn context: built
once per turn, persisted through a real graph, diff-only, a full snapshot after
compaction, and recall and skill usage once per turn.
`tests/subsystem/agents/test_turn_context_tag_safety.py` is the security
contract for the tag. `tests/subsystem/client_platform/test_turn_context_readers.py`
checks that no reader of history shows it.

`tests/subsystem/agents/test_history_hygiene.py` covers the tool-result rules:
jumps are rare and in one step, cleared results are byte-stable, the marks are
kept with the thread, browser stubs move on jumps, repeats stub the newer copy,
and the emergency shrink only happens over the limit.
`tests/subsystem/regression/test_tool_result_fields.py` covers the kept fields,
and `tests/subsystem/agents/test_untrusted_tool_results.py` the fence (store
time, older results, plugin results and wake-up events).

`tests/subsystem/agents/test_compaction_trigger.py` covers the compaction point
per window size, clearing before summarizing, the calibrated comparison and the
snapshot after a summary; `tests/subsystem/agents/test_context_calibration.py`
the calibration. `tests/subsystem/regression/test_compaction_regressions.py`
covers the splice, the failure marker, the overflow retry, the summary per mode
and thinking at compaction; `tests/subsystem/regression/test_checkpoint_hygiene.py`
what checkpoints keep, a resumed call's request, inline thinking and older
charts.

`tests/subsystem/providers/test_prompt_cache_payloads.py` covers the breakpoints
on the wire for each Claude route through the real request builders:

- the lifetime for each surface;
- the TTL ordering and the four-breakpoint limit;
- the long-append breakpoint;
- the safety net;
- the usage ledger recording cache writes, then reads, for consecutive calls.

Live provider validation is opt-in. When run, it uses the real Row-Bot runtime
and at least one latest recommended chat-capable model per configured provider.
The only acceptable provider-side failure is an explicit out-of-credits, quota,
usage-exhausted, or billing-limit response. Any authentication, schema,
unsupported parameter, cache-control, tool-call replay, streaming, routing, or
serialization failure is a bug or blocker.

The live eval measures what these rules buy on real providers.
`scripts/live_eval.py plan` lists its tasks and the estimated tokens without
any network call. `run` sends each task through the real Agent runtime, with
keys from the environment and a fresh data folder, under a USD and token cap.
It records pass/fail and the usage ledger's cache share, 1h writes, TTFT and
cost per task. `tests/e2e/test_live_verification.py` checks the provider rules
above against the real APIs: the thinking-binding controls, both cache
lifetimes, the gateway markers, the cache keys, usage shapes and background
schemas. See `tests/README.md` (Live eval).
