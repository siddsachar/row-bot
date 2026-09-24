"""Shared first-run choices and Setup Center steps."""

ONBOARDING_VERSION = 3

INTENT_OPTIONS: dict[str, str] = {
    "chat": "Chat assistant",
    "research": "Research and documents",
    "workflows": "Workflow automation",
    "designer": "Designer Studio",
    "developer": "Developer Studio",
    "channels": "Messaging channels",
    "local": "Local/private AI",
}

SETUP_STEPS: dict[str, dict[str, str]] = {
    "models": {
        "title": "Models",
        "description": "Connect a model provider and choose defaults.",
    },
    "knowledge": {
        "title": "Knowledge",
        "description": "Set up memory, documents, and embeddings.",
    },
    "workflows": {
        "title": "Workflows",
        "description": "Add starter workflows and delivery defaults.",
    },
    "designer": {
        "title": "Designer",
        "description": "Create design projects, decks, pages, and mockups.",
    },
    "developer": {
        "title": "Developer",
        "description": "Connect code workspaces and create Custom Tools.",
    },
    "channels": {
        "title": "Channels",
        "description": "Connect Telegram, WhatsApp, Discord, Slack, or SMS.",
    },
    "accounts": {
        "title": "Accounts",
        "description": "Connect Gmail, Calendar, X, and other accounts.",
    },
    "tools": {
        "title": "Tools & Skills",
        "description": "Review search, browser, shell, filesystem, and skills.",
    },
    "extensions": {
        "title": "MCP & Plugins",
        "description": "Add external tools through MCP servers and plugins.",
    },
    "voice": {
        "title": "Voice",
        "description": "Configure speech-to-text and text-to-speech.",
    },
    "final": {
        "title": "Final Check",
        "description": "Review readiness and fix anything missing.",
    },
}
