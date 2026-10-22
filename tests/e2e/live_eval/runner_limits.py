"""What the live eval's caps hold: local models cost nothing (D25), so their calls are recorded
but never charged against a run's caps."""
from __future__ import annotations

FREE_PROVIDERS = frozenset({"ollama"})
