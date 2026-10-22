"""Checks the live eval cannot automate: a person runs them in the desktop app or a test workspace.

They come from the cost plan's work-package handoffs. ``scripts/live_eval.py
manual`` prints them, and every run's ``report.md`` ends with them.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ManualCheck:
    check_id: str
    source: str
    steps: str
    expect: str


MANUAL_CHECKS: tuple[ManualCheck, ...] = (
    ManualCheck(
        "desktop-chart", "D2",
        "In the desktop app ask for a chart of a small table; expand the 'Created a chart' step; reopen the "
        "conversation; ask 'which day was highest?'; ask to change the chart type. Also open a conversation "
        "that has a chart from before D2.",
        "The chart renders with its caption, the step shows the summary, it still renders after reopening, the "
        "model answers from the summary or reads the figure with read_tool_output, and calls create_chart again "
        "for the new type. The old chart still renders.",
    ),
    ManualCheck(
        "slack-threads", "E1a",
        "In a Slack test workspace: a top-level mention, a mention in the thread, and a plain thread reply "
        "(with message.channels on, then off); two threads in one channel; a DM, /new, then another DM; an "
        "approval card inside a thread (button and yes/no reply); a delegated child result arriving in the "
        "thread; one message sharing 3 images.",
        "Each thread is its own conversation and threads stay apart; /new starts a new DM conversation; "
        "approvals and child results land in the thread; the 3 images get one reply.",
    ),
    ManualCheck(
        "discord-threads", "E1a",
        "On a Discord test server: mention Row-Bot (it opens a thread), reply in the thread without a mention; "
        "remove Create Public Threads and repeat; a DM with /new; one message with 2 images and a voice note.",
        "The thread continues one conversation; without the permission the channel keeps one conversation, "
        "/new works and the log line appears once; the attachments get one reply.",
    ),
    ManualCheck(
        "channel-albums", "E1a",
        "Telegram and WhatsApp: send an album of 3 photos, then /new; WhatsApp also a single photo. SMS: /new, "
        "then a message.",
        "Each album gets one reply; /new starts a new conversation that the next message goes to.",
    ),
    ManualCheck(
        "mcp-over-24k", "D1/D2",
        "Connect an MCP server whose tool returns more than 24K characters (for example a documentation search) "
        "and ask a question that needs the whole result.",
        "A shortened-result notice and a handle appear, the model reads the rest with read_tool_output, and the "
        "read-back pages arrive fenced as external content.",
    ),
    ManualCheck(
        "local-32k-agent", "A5 (F43)",
        "Pick an Ollama model with a native 32K window (num_ctx 32768) and start an Agent-mode conversation with "
        "a tool task.",
        "Before A5 Agent mode is refused (known, F43). After A5 the small-context profile runs the turn: tool "
        "guides as an index, deferred non-core tools, and `ollama ps` shows one load with an unchanged num_ctx.",
    ),
    ManualCheck(
        "upgrade-existing-threads", "E1a/A2/A4a",
        "Upgrade a profile copy that has existing conversations: slack_C…/slack_D…/discord_…/sms_…/whatsapp_… "
        "threads, a thread that loaded a skill with skill_load, and a long thread with tool output from before "
        "A4a. Continue each.",
        "DMs and contacts continue their old conversation and channels start new ones; the skill body arrives "
        "once in the next turn context; the old long thread clears earlier tool output in one jump (INFO "
        "'History hygiene' line) and older web/file results stay fenced.",
    ),
    ManualCheck(
        "desktop-pause-1h-cache", "A3/D3",
        "With an Anthropic model, hold a desktop conversation with tool use, pause for 6-10 minutes, then send "
        "another message. Ask Row-Bot 'how many tokens did this conversation use?' (row_bot_status usage).",
        "The call after the pause reads the cache (cache_read dominates input in the ledger), and earlier calls "
        "show 1-hour cache writes.",
    ),
    ManualCheck(
        "usage-tooltip", "B1",
        "Hover the context ring after a few turns, then open its details.",
        "The hover shows tokens used and the share from cache; the details show the sentence with the provider's "
        "reported cost where there is one (OpenRouter).",
    ),
    ManualCheck(
        "max-tokens-notice", "A3 (F17)",
        "Ask an Anthropic model for a very long answer with a low output limit for that model.",
        "The desktop shows the 'cut short' notice when the reply stops on max_tokens.",
    ),
    ManualCheck(
        "oauth-providers-ledger", "B1/B2",
        "xAI OAuth and Claude Subscription cannot be configured from environment variables (Codex, the ChatGPT "
        "subscription, has its own lane through an eval profile: ROW_BOT_LIVE_EVAL_CODEX_PROFILE). Start Row-Bot "
        "with ROW_BOT_DATA_DIR set to a separate validation folder (never the everyday ~/.row-bot), sign in to the "
        "provider there, and run a short Agent conversation with two tool rounds and a second turn, plus one Chat "
        "Only turn. Then run `uv run python scripts/live_eval.py ledger --data-dir <that folder>`.",
        "Every call has input and output counts (Codex/xAI OAuth `response.completed` usage reached the ledger); "
        "the second turn reads the cache (Codex derived session key works); no replay 400 after a tool round "
        "(replayed reasoning is accepted).",
    ),
    ManualCheck(
        "goals-paused-on-launch", "C3",
        "Quit Row-Bot while a goal runs and start it again; separately, point a goal's verifier at an unavailable "
        "model.",
        "The goal comes back paused with one 'Goals paused' notice and Resume continues it; the broken verifier "
        "pauses the goal after 2 turns with a notification.",
    ),
    ManualCheck(
        "plugin-image-result", "D2",
        "Run a plugin tool that returns image_result, then reload the conversation.",
        "The image shows live and after the reload; the step says 'Created 1 attachment'.",
    ),
    ManualCheck(
        "designer-turn", "A2",
        "Make a Designer edit, then ask about the current page list in the next turn.",
        "The page list and brand appear in the turn context and the edit is reflected next turn.",
    ),
    ManualCheck(
        "documents-long-pdf", "C3",
        "Upload a long PDF on a cloud model and on a 32K Ollama model; quit mid-map and restart once.",
        "Comparable knowledge articles; the restart resumes on the same windows (document_map_plans).",
    ),
    ManualCheck(
        "dream-and-extraction", "C2",
        "On a profile copy with long conversations: run memory extraction twice after the upgrade, rename a chat, "
        "then leave a local Ollama model resident overnight with Dream Cycle on.",
        "The first run reads only changed conversations (their tails), the second makes no calls, a rename makes "
        "no calls; Dream runs in its window and the next night shows fewer `dream` calls in the ledger.",
    ),
)
