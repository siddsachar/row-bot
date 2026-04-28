"""Thoth self-knowledge — identity, capabilities, and dynamic state.

Provides the self-aware context block that is injected into the agent's
system prompt so it can accurately answer questions about itself, its
features, and its current configuration.
"""

from __future__ import annotations

import logging
from typing import Optional

from identity import get_identity_config, get_assistant_name, _DEFAULT_NAME  # get_assistant_name re-exported for convenience

logger = logging.getLogger(__name__)

# ═════════════════════════════════════════════════════════════════════════════
# STATIC: What I Am (~200 tokens)
# ═════════════════════════════════════════════════════════════════════════════

_ABOUT_THOTH_INTRO = (
    "ABOUT YOU (SELF-KNOWLEDGE):\n"
    "You are a personal AI agent that runs locally on the user's machine.\n"
    "You combine a large language model with integrated tools, a persistent\n"
    "knowledge graph, and a task automation engine. All data is stored locally\n"
    "in ~/.thoth/. You are open-source.\n\n"
)

_ABOUT_THOTH_OUTRO = (
    "When the user asks about your features, capabilities, or how to configure\n"
    "something, draw on the above knowledge. You can also use the thoth_status\n"
    "tool (when available) to look up live configuration details.\n"
)

# ═════════════════════════════════════════════════════════════════════════════
# SKILL SELF-IMPROVEMENT GUIDANCE
# ═════════════════════════════════════════════════════════════════════════════

SKILL_CREATION_GUIDANCE = (
    "SKILL SELF-IMPROVEMENT:\n"
    "You can create and improve reusable instruction packs (skills) to get\n"
    "better at tasks you perform repeatedly.\n\n"
    "CREATING SKILLS:\n"
    "- After a successful complex workflow (5+ tool calls), consider whether\n"
    "  the pattern would benefit from a skill.\n"
    "- Ask the user before creating: 'Would you like me to save this as a\n"
    "  skill so I can do it better next time?'\n"
    "- Skills are additive — never overwrite an existing skill with the same name.\n"
    "- Use thoth_create_skill with a clear name, description, and step-by-step\n"
    "  instructions distilled from what worked.\n\n"
    "PATCHING SKILLS:\n"
    "- If you notice a skill's instructions are incomplete or could be improved\n"
    "  based on experience, you can propose a patch.\n"
    "- Maximum 1 patch proposal per conversation — be selective.\n"
    "- Always explain what you want to change and why.\n"
    "- Use thoth_patch_skill — it requires user confirmation and backs up\n"
    "  the original automatically.\n"
    "- Bundled skills are patched via user-space override (originals preserved).\n"
    "- Tool guides CANNOT be patched — report discrepancies as memories instead.\n"
    "- Only improve, never regress: patches must be strictly better.\n\n"
    "TROUBLESHOOTING LEARNING:\n"
    "When you diagnose an issue, discover a workflow insight, or notice a\n"
    "discrepancy between a tool guide and actual tool behavior, save it as\n"
    "a self_knowledge memory using save_memory with category='self_knowledge'.\n"
    "- Use descriptive subjects like 'Browser: login page detection' or\n"
    "  'Shell: pip install requires --user on macOS'.\n"
    "- These auto-recall on similar future queries, making you better at\n"
    "  troubleshooting over time.\n"
    "- Do NOT save trivial issues — only patterns worth remembering.\n"
    "\n"
    "SKILL INSTRUCTIONS FORMAT:\n"
    "When writing instructions for thoth_create_skill or thoth_patch_skill,\n"
    "follow this structure:\n"
    "- Opening paragraph: describe what the skill does and when it activates.\n"
    "- Use ## headers for major sections.\n"
    "- Use numbered lists for step-by-step workflows.\n"
    "- Use bold for key terms and constraints.\n"
    "- Reference tool names in backticks, e.g. `web_search`.\n"
    "- Keep instructions concise — the body is injected into the system prompt.\n"
)

# ═════════════════════════════════════════════════════════════════════════════
# FEATURE MANIFEST — structured data for accurate self-referencing
# ═════════════════════════════════════════════════════════════════════════════

FEATURE_MANIFEST: list[dict[str, str]] = [
    {
        "feature": "Web Search",
        "keywords": "search, web, internet, google, news, current events",
        "description": "Search the web via Tavily or DuckDuckGo for real-time information.",
        "configure": "Settings → Search to choose provider and set API keys.",
    },
    {
        "feature": "Knowledge Graph",
        "keywords": "memory, remember, know, knowledge, graph, entities, relationships",
        "description": "Personal knowledge graph storing memories about people, preferences, facts, events, places, and projects with automatic relationship linking.",
        "configure": "Settings → Knowledge to view and manage stored entities.",
    },
    {
        "feature": "Task Automation",
        "keywords": "tasks, schedule, reminders, automation, cron, recurring, daily briefing",
        "description": "Schedule reminders, recurring jobs, monitoring tasks, and multi-step pipelines with 7 trigger types.",
        "configure": "Open the Tasks tab or ask the assistant to create a task.",
    },
    {
        "feature": "Voice Input & TTS",
        "keywords": "voice, speech, whisper, talk, speak, listen, microphone, tts",
        "description": "Hands-free voice input via Whisper STT and text-to-speech output with multiple voice options.",
        "configure": "Settings → Voice to choose Whisper model size and TTS voice.",
    },
    {
        "feature": "Browser Automation",
        "keywords": "browser, web, click, navigate, fill form, scrape, playwright, chromium",
        "description": "Visible Chromium browser automation — navigate, click, fill forms, extract data. Logins persist across sessions.",
        "configure": "Settings → Search to enable the browser tool.",
    },
    {
        "feature": "Gmail & Calendar",
        "keywords": "email, gmail, calendar, google, events, send email, draft",
        "description": "Read, draft, and send emails. Create and manage Google Calendar events.",
        "configure": "Settings → Accounts to connect your Google account.",
    },
    {
        "feature": "Shell Access",
        "keywords": "shell, terminal, command, script, run, execute, pip, git",
        "description": "Run shell commands on the user's machine. Dangerous commands require approval.",
        "configure": "Settings → System to enable/disable shell access.",
    },
    {
        "feature": "Document Library",
        "keywords": "documents, pdf, upload, files, knowledge base, attachments",
        "description": "Upload PDFs and text files as a persistent knowledge base. Drag-and-drop or use the paperclip button.",
        "configure": "Settings → Documents to manage uploaded files.",
    },
    {
        "feature": "Multi-Channel Messaging",
        "keywords": "telegram, discord, slack, whatsapp, sms, channels, messaging",
        "description": "Access the assistant via Telegram, Discord, Slack, WhatsApp, or SMS in addition to the web UI.",
        "configure": "Settings → Channels to set up messaging integrations.",
    },
    {
        "feature": "Habit & Health Tracker",
        "keywords": "tracker, habits, medication, symptoms, exercise, streak, health, logging",
        "description": "Log medications, symptoms, exercise, or any recurring activity. Streak analysis, trend charts, and CSV export.",
        "configure": "Settings → Tracker to view logged data and charts.",
    },
    {
        "feature": "Skills System",
        "keywords": "skills, instruction packs, deep research, daily briefing, humanizer",
        "description": "Bundled instruction packs that shape behavior — Deep Research, Daily Briefing, Humanizer, and more. Users can create custom skills.",
        "configure": "Settings → Skills to enable/disable skill packs.",
    },
    {
        "feature": "Vision & Image Analysis",
        "keywords": "vision, image, picture, photo, screenshot, analyze image, see",
        "description": "Analyze images using vision-capable models. Describe, extract text, or answer questions about images.",
        "configure": "Settings → Models to select a vision model.",
    },
    {
        "feature": "File Operations",
        "keywords": "files, workspace, read, write, create, export, pdf, csv, excel",
        "description": "Read, write, and organize files in the sandboxed workspace (~/Documents/Thoth).",
        "configure": "Settings → System to configure workspace path.",
    },
    {
        "feature": "YouTube Integration",
        "keywords": "youtube, video, transcript, watch, search videos",
        "description": "Search YouTube videos and fetch full transcripts for content analysis.",
        "configure": "Enabled by default. No configuration needed.",
    },
    {
        "feature": "Charts & Visualization",
        "keywords": "chart, graph, plot, visualize, data, bar chart, line chart",
        "description": "Generate charts and data visualizations from conversations or data files.",
        "configure": "Enabled by default. No configuration needed.",
    },
    {
        "feature": "Plugin System",
        "keywords": "plugins, extensions, community, marketplace, install plugin",
        "description": "Community plugin system for extending capabilities. Browse and install from the marketplace.",
        "configure": "Settings → Plugins to manage installed plugins.",
    },
    {
        "feature": "MCP Client",
        "keywords": "mcp, model context protocol, external tools, tool server, marketplace, registry",
        "description": "Connect external Model Context Protocol servers as native dynamic tools with global kill switch, per-server enablement, per-tool toggles, approval gates for destructive tools, recommended starter recipes, overlap/risk labels, marketplace browse/import, diagnostics, and isolated failure handling.",
        "configure": "Settings → MCP to add, test, import, browse, enable, and troubleshoot MCP servers.",
    },
    {
        "feature": "Dream Cycle",
        "keywords": "dream, nightly, memory refinement, consolidation, background processing",
        "description": "Nightly 5-phase memory refinement process that consolidates, deduplicates, enriches the knowledge graph, and generates automated insights.",
        "configure": "Runs automatically. No configuration needed.",
    },
    {
        "feature": "Insights Engine",
        "keywords": "insights, self-improvement, analysis, suggestions, error patterns, skill proposals",
        "description": "Automated system analysis during the dream cycle. Generates actionable insights about error patterns, skill proposals, tool configuration, knowledge quality, usage patterns, and system health.",
        "configure": "View insights in the Workflow Console. Dismiss, pin, investigate, or apply suggested changes.",
    },
    {
        "feature": "Conversation History",
        "keywords": "history, past conversations, search chat, threads, previous",
        "description": "Full conversation history with search. Find past discussions and continue previous threads.",
        "configure": "Access via the threads panel or ask to search past conversations.",
    },
    {
        "feature": "Image Generation",
        "keywords": "image, generate, create image, dall-e, picture, illustration, edit image",
        "description": "Generate and edit images using DALL-E or compatible models. Requires a cloud provider with image generation support.",
        "configure": "Requires an OpenAI or OpenRouter API key. Enabled automatically when a cloud provider is configured.",
    },
    {
        "feature": "X / Twitter",
        "keywords": "x, twitter, tweet, post, social media, engage, timeline",
        "description": "Read your X/Twitter timeline, post tweets, and engage with content.",
        "configure": "Settings → Tools to enable the X tool, then set API credentials.",
    },
    {
        "feature": "Designer Studio",
        "keywords": "designer, design, slides, presentation, deck, document, one-pager, report, marketing, landing page, hero page, wireframe, mockup, app mockup, prototype, click-through, storyboard, shot list, layout, brand, export, pptx, pdf, html, png, publish link, share link, chart, ai image, ai video, refine text",
        "description": "Create multi-page visual designs across five modes — slide decks, documents, scrollable landing pages, interactive app/UI mockups (phone or desktop), and video storyboards. Live HTML preview, brand theming, AI image and video generation, copy refinement, charts, brand-lint, and export to PDF/HTML/PNG/PPTX. Landing pages and app mockups can be published as interactive click-through bundles via shareable links.",
        "configure": "Open the Designer tab on the home screen, or ask the assistant to create a design / landing page / app mockup / storyboard.",
    },
    {
        "feature": "Wiki Vault",
        "keywords": "wiki, obsidian, vault, notes, markdown, personal wiki",
        "description": "Obsidian-compatible personal wiki auto-generated from the knowledge graph. Read, search, and export conversations as wiki pages.",
        "configure": "Enabled by default. Access via the wiki tool.",
    },
    {
        "feature": "ArXiv Search",
        "keywords": "arxiv, research, papers, academic, scientific, publications",
        "description": "Search and retrieve academic papers from arXiv.",
        "configure": "Enabled by default. No configuration needed.",
    },
    {
        "feature": "Wikipedia",
        "keywords": "wikipedia, encyclopedia, wiki lookup, reference",
        "description": "Search and read Wikipedia articles for factual reference.",
        "configure": "Enabled by default. No configuration needed.",
    },
]


def _build_capabilities_text_from_manifest() -> str:
    """Render the capability bullets from the structured feature manifest."""
    lines = ["Core capabilities:"]
    for entry in FEATURE_MANIFEST:
        feature = (entry.get("feature", "") or "").strip()
        description = " ".join((entry.get("description", "") or "").split())
        if feature and description:
            lines.append(f"- {feature}: {description}")
        elif feature:
            lines.append(f"- {feature}")
    return "\n".join(lines)


def _build_about_thoth() -> str:
    """Build the static self-knowledge description from the manifest."""
    return "\n".join([
        _ABOUT_THOTH_INTRO.rstrip(),
        _build_capabilities_text_from_manifest(),
        _ABOUT_THOTH_OUTRO.rstrip(),
    ]) + "\n"


ABOUT_THOTH = _build_about_thoth()


def lookup_features(query: str) -> list[dict[str, str]]:
    """Find features matching a query string (case-insensitive keyword match).

    Useful for the agent to find relevant features when composing social
    media posts or answering user questions about capabilities.
    """
    query_lower = query.lower()
    query_words = set(query_lower.split())
    results = []
    for entry in FEATURE_MANIFEST:
        keywords = set(entry["keywords"].lower().split(", "))
        # Match if any query word appears in keywords or feature name
        feature_lower = entry["feature"].lower()
        if (query_words & keywords) or any(w in feature_lower for w in query_words):
            results.append(entry)
    return results


def build_identity_line() -> str:
    """Build the dynamic identity opening line from user preferences."""
    cfg = get_identity_config()
    name = cfg.get("name") or _DEFAULT_NAME
    personality = cfg.get("personality", "")
    line = f"You are {name}, a knowledgeable personal assistant with access to tools."
    if personality:
        line += f" {personality}"
    return line


def get_dynamic_state() -> str:
    """Build a short summary of Thoth's current runtime state.

    Uses lazy imports to avoid circular dependencies and only queries
    state that is cheap to compute.
    """
    parts: list[str] = []

    # Current model
    try:
        from models import get_current_model
        model = get_current_model()
        parts.append(f"- Current model: {model}")
    except Exception:
        pass

    # API keys configured
    try:
        from api_keys import get_key, OPENROUTER_KEY_DEFINITIONS, OPENAI_KEY_DEFINITIONS, ANTHROPIC_KEY_DEFINITIONS, GOOGLE_KEY_DEFINITIONS
        providers = []
        if get_key("OPENROUTER_API_KEY"):
            providers.append("OpenRouter")
        if get_key("OPENAI_API_KEY"):
            providers.append("OpenAI")
        if get_key("ANTHROPIC_API_KEY"):
            providers.append("Anthropic")
        if get_key("GOOGLE_API_KEY"):
            providers.append("Google AI")
        if providers:
            parts.append(f"- Cloud providers configured: {', '.join(providers)}")
        else:
            parts.append("- Cloud providers: none configured (local only)")
    except Exception:
        pass

    # Memory/entity count
    try:
        from knowledge_graph import count_entities
        count = count_entities()
        parts.append(f"- Knowledge graph: {count} entities")
    except Exception:
        pass

    # Last dream cycle summary
    try:
        from dream_cycle import get_dream_status
        dream_status = get_dream_status()
        if dream_status.get("last_summary"):
            parts.append(f"- Last dream cycle: {dream_status['last_summary']}")
    except Exception:
        pass

    # Active channels
    try:
        from channels.registry import running_channels
        active = running_channels()
        if active:
            names = [ch.name for ch in active]
            parts.append(f"- Active channels: {', '.join(names)}")
        else:
            parts.append("- Active channels: Web UI only")
    except Exception:
        pass

    # Designer projects
    try:
        from designer.storage import list_projects as _list_designer_projects
        _designer_count = len(_list_designer_projects())
        if _designer_count:
            parts.append(f"- Designer projects: {_designer_count}")
    except Exception:
        pass

    # Enabled skills
    try:
        from skills import get_enabled_manual_skills
        enabled = get_enabled_manual_skills()
        if enabled:
            names = [s.display_name for s in enabled]
            parts.append(f"- Enabled skills: {', '.join(names)}")
    except Exception:
        pass

    # MCP external tool status
    try:
        from mcp_client.runtime import get_status_summary
        mcp = get_status_summary()
        if mcp.get("enabled") or mcp.get("server_count"):
            server_bits = f"{mcp.get('connected_server_count', 0)}/{mcp.get('enabled_server_count', 0)} connected"
            parts.append(
                f"- MCP client: {'enabled' if mcp.get('enabled') else 'disabled'}, "
                f"servers {server_bits}, {mcp.get('enabled_tool_count', 0)} enabled external tools"
            )
            failed = [name for name, st in (mcp.get("servers") or {}).items() if st.get("status") in {"failed", "dependency_missing"}]
            if failed:
                parts.append(f"- MCP attention: {', '.join(failed[:5])} need troubleshooting")
    except Exception:
        pass

    # Pending Thoth update (auto-update)
    try:
        import updater
        info = updater.get_update_state().available
        if info is not None:
            parts.append(
                f"- Update available: v{info.version} ({info.channel} channel) — "
                f"the user can install via Settings → Preferences → Updates "
                f"or by asking you to run thoth_install_update."
            )
    except Exception:
        pass

    if not parts:
        return ""

    return "Current state:\n" + "\n".join(parts) + "\n"


def build_self_knowledge_block() -> str:
    """Assemble the full self-knowledge block for prompt injection.

    Returns a string suitable for inserting as a SystemMessage.
    Omits skill-improvement guidance when self-improvement is disabled.
    """
    sections = [ABOUT_THOTH]

    try:
        from identity import is_self_improvement_enabled
        if is_self_improvement_enabled():
            sections.append(SKILL_CREATION_GUIDANCE)
    except Exception:
        sections.append(SKILL_CREATION_GUIDANCE)  # safe fallback: include

    state = get_dynamic_state()
    if state:
        sections.append(state)

    return "\n".join(sections)
