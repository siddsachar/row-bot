"""Shared first-run choices and Setup Center steps."""

# 4: no preset models; the first run asks how Row-Bot should think (decision 9).
ONBOARDING_VERSION = 4

INTENT_OPTIONS: dict[str, str] = {
    "chat": "Chat assistant",
    "research": "Research and documents",
    "workflows": "Workflow automation",
    "designer": "Designs",
    "developer": "Code",
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
    "apps": {
        "title": "Apps",
        "description": "Connect the services you use: Gmail and Calendar, GitHub, Slack, Telegram and more.",
    },
    "skills": {
        "title": "Skills",
        "description": "Add skills that teach Row-Bot how you like things done.",
    },
    "tools": {
        "title": "Tools",
        "description": "Review search, browser, shell, and filesystem tools.",
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
