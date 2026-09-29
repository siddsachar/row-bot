"""Where start-up stands.

Written by the start-up sequence in ``row_bot.app``; read by ``/readyz``,
``/api/startup-state`` (the launcher's splash) and ``scripts/smoke_app.py``.
The warnings also reach the React client and Monitor through ``app_notices``.
"""
from __future__ import annotations

ready = False
status = "Starting…"
warnings: list[str] = []
