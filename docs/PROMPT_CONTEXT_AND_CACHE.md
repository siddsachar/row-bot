# Prompt Context And Cache Contract

Row-Bot assembles prompts from named sections with explicit stability. This
keeps provider payloads predictable and prevents per-turn data from being marked
as cacheable provider input.

## Stability Classes

Stable sections may be reused across turns when user settings, provider/model
selection, enabled tools, skills, plugins, or thread profile configuration have
not changed. Ephemeral sections are generated for the current turn and must not
be prompt-cache breakpoints.

Cache-eligible stable sections:

- `agent.root`: identity line and agent guidelines from the ReAct system prompt.
- `agent.profile`: active thread Agent Profile instructions.
- `platform.context`: local platform and shell guidance.
- `self_knowledge.static`: static Row-Bot feature knowledge and optional
  self-improvement guidance.
- `skills.tool_guides`: automatic tool guide instructions for enabled tools.
- `skills.manual`: explicitly or globally enabled manual skills.
- `skills.plugins`: enabled plugin skill instructions.
- `background.override`: static background-workflow override.
- `chat_only.root`: Chat Only identity, behavior rules, and profile context.

Ephemeral sections:

- `turn.date_time`: current date/time line.
- `turn.runtime_mode`: active runtime mode, approval mode, and enabled tool
  names for this turn.
- `turn.conversation_summary`: checkpoint summary merged for context trimming.
- `turn.memory_recall`: auto-recalled long-term memory block.
- `self_knowledge.dynamic_state`: current provider/model, channels, MCP,
  Designer, Dream Cycle, and update status.
- `turn.developer_context`: Developer Studio repository/runtime context.
- `turn.designer_project`: active Designer project prompt.
- `turn.background_persistent_thread`: persistent-thread reminder.
- `turn.channel_state`: channel-specific transient state.
- `turn.wind_down`: tool-call-limit warning.
- Conversation, assistant, and tool history.

## Provider Rules

Direct Anthropic API is the only provider that receives Anthropic
`cache_control` markers in this rollout. Markers are placed only on stable
system content. Conversation history and ephemeral system sections must never be
marked.

Anthropic-compatible routes that are not the direct Anthropic API, including
MiniMax, OpenCode Anthropic Messages, and Claude Subscription, do not receive
`cache_control` markers until their transports are explicitly proven to preserve
and accept those content blocks.

OpenAI, OpenRouter, xAI OAuth Responses, Codex Responses, AtlasCloud, and custom
OpenAI-compatible endpoints do not receive Anthropic markers. Codex keeps its
existing `prompt_cache_key` behavior.

## Verification

Deterministic tests assert the section inventory, stable-prefix fingerprint
behavior, provider marker gating, Chat Only separation, source-test-map
selection, and provider transport regressions.

Live provider validation is opt-in. When run, it uses the real Row-Bot runtime
and at least one latest recommended chat-capable model per configured provider.
The only acceptable provider-side failure is an explicit out-of-credits, quota,
usage-exhausted, or billing-limit response. Any authentication, schema,
unsupported parameter, cache-control, tool-call replay, streaming, routing, or
serialization failure is a bug or blocker.
