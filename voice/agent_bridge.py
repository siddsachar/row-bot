from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from voice.actions import classify_active_run_control, submit_voice_text

logger = logging.getLogger(__name__)


VOICE_BRAIN_STRATEGY = "thoth-consult"
REALTIME_DIRECT_TOOL_POLICY = "blocked"
REALTIME_ALLOWED_BRIDGE_TOOLS = ("thoth_agent_consult", "thoth_agent_control")
REALTIME_WAIT_TOOL = "wait_for_user"
REALTIME_ALLOWED_TOOLS = (*REALTIME_ALLOWED_BRIDGE_TOOLS, REALTIME_WAIT_TOOL)


def realtime_bridge_tool_declarations() -> list[dict[str, Any]]:
    """Return the only tools exposed to the Realtime voice model."""
    return [
        {
            "type": "function",
            "name": "thoth_agent_consult",
            "description": (
                "Delegate substantive user requests to the normal Thoth agent. "
                "Use this for facts, memory, files, browser/computer control, "
                "tools, approvals, and any work that should be done by Thoth."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "request": {
                        "type": "string",
                        "description": "The user's substantive request or question.",
                    },
                    "context": {
                        "type": "string",
                        "description": "Optional short voice-turn context.",
                    },
                },
                "required": ["request"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "thoth_agent_control",
            "description": (
                "Control or query the currently active Thoth run. Use for status, "
                "cancel, steering, or follow-up while Thoth is already working."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["status", "cancel", "steer", "follow_up"],
                    },
                    "text": {
                        "type": "string",
                        "description": "Steering or follow-up text when applicable.",
                    },
                },
                "required": ["action"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": REALTIME_WAIT_TOOL,
            "description": (
                "Use this when the latest audio should not receive a spoken "
                "response, such as silence, background noise, assistant echo, "
                "side conversation, or speech not addressed to Thoth. This "
                "quietly ends the turn and keeps listening."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        },
    ]


_NON_ACTIONABLE_UTTERANCES = {
    "thanks",
    "thank you",
    "okay",
    "ok",
    "got it",
    "never mind",
    "nevermind",
    "bye",
    "goodbye",
}


class VoiceAgentBridge:
    """Narrow bridge from voice transports into the normal Thoth agent path."""

    def __init__(
        self,
        *,
        send_message: Callable[..., Any],
        active_generation: Callable[[], Any | None],
        surface: Callable[[], str] | str | None = None,
        thread_id: Callable[[], str] | str | None = None,
    ) -> None:
        self._send_message = send_message
        self._active_generation = active_generation
        self._surface = surface
        self._thread_id = thread_id

    async def submit_user_transcript(self, text: str) -> dict[str, Any]:
        clean = str(text or "").strip()
        logger.info(
            "voice.realtime.pipeline %s",
            {"stage": "bridge_submit_start", "text_chars": len(clean)},
        )
        control = self.control_active_run(clean)
        if control["handled"]:
            logger.info(
                "voice.realtime.pipeline %s",
                {
                    "stage": "bridge_control_handled",
                    "control": control.get("control"),
                    "text_chars": len(clean),
                },
            )
            return control
        await submit_voice_text(
            self._send_message,
            clean,
            surface=self._resolve_meta(self._surface),
            thread_id=self._resolve_meta(self._thread_id),
        )
        logger.info(
            "voice.realtime.pipeline %s",
            {"stage": "bridge_submit_done", "text_chars": len(clean)},
        )
        return {"handled": False, "control": "consult", "status": "submitted"}

    async def handle_realtime_function_call(
        self,
        *,
        name: str,
        arguments: str | dict[str, Any] | None,
        queue_consult: Callable[[dict[str, Any]], None] | None = None,
        call_id: str = "",
    ) -> dict[str, Any]:
        clean_name = str(name or "").strip()
        if clean_name == REALTIME_WAIT_TOOL:
            logger.info(
                "voice.realtime.pipeline %s",
                {"stage": "bridge_wait_for_user", "call_id": call_id},
            )
            return {
                "handled": True,
                "deferred": False,
                "silent": True,
                "output": self._json_output(
                    status="waiting",
                    speakable="",
                    worker="realtime",
                ),
            }
        if clean_name not in REALTIME_ALLOWED_BRIDGE_TOOLS:
            return {
                "handled": True,
                "deferred": False,
                "output": self._json_output(
                    status="blocked",
                    speakable="That tool is not available to realtime voice.",
                    direct_tool_policy=REALTIME_DIRECT_TOOL_POLICY,
                ),
            }

        parsed = self._parse_arguments(arguments)
        if clean_name == "thoth_agent_control":
            result = self._handle_control_tool(parsed)
            return {"handled": True, "deferred": False, "output": self._json_output(**result)}

        request = str(parsed.get("request") or parsed.get("question") or parsed.get("text") or "").strip()
        if not request:
            return {
                "handled": True,
                "deferred": False,
                "silent": True,
                "output": self._json_output(
                    status="ignored_empty_request",
                    speakable="",
                ),
            }
        active_gen = self._active_generation()
        if self._matches_active_forced_consult(active_gen, request):
            setattr(active_gen, "realtime_tool_call_id", call_id)
            setattr(active_gen, "realtime_tool_name", clean_name)
            setattr(active_gen, "realtime_forced_consult", False)
            logger.info(
                "voice.realtime.pipeline %s",
                {
                    "stage": "bridge_consult_deduped",
                    "call_id": call_id,
                    "request_chars": len(request),
                },
            )
            return {"handled": True, "deferred": True, "output": ""}
        if queue_consult:
            queue_consult({"call_id": call_id, "name": clean_name, "request": request})
        await self.submit_user_transcript(request)
        return {"handled": True, "deferred": True, "output": ""}

    async def force_consult_if_substantive(
        self,
        transcript: str,
        *,
        queue_consult: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        clean = str(transcript or "").strip()
        if not self.should_force_consult(clean):
            return {"handled": False, "status": "skipped"}
        if self._matches_active_forced_consult(self._active_generation(), clean):
            logger.info(
                "voice.realtime.pipeline %s",
                {"stage": "bridge_forced_consult_deduped", "request_chars": len(clean)},
            )
            return {"handled": True, "status": "deduped"}
        if queue_consult:
            queue_consult({"call_id": "", "name": "forced_consult", "request": clean})
        await self.submit_user_transcript(clean)
        return {"handled": True, "status": "submitted"}

    @staticmethod
    def should_force_consult(transcript: str) -> bool:
        clean = " ".join(str(transcript or "").strip().lower().split())
        if not clean or clean in _NON_ACTIONABLE_UTTERANCES:
            return False
        if len(clean) < 8 and not clean.endswith("?"):
            return False
        if clean.endswith((" and", " or", " but", " so", " then", " to")):
            return False
        return True

    @staticmethod
    def _matches_active_forced_consult(gen: Any | None, request: str) -> bool:
        if gen is None:
            return False
        if not bool(getattr(gen, "realtime_forced_consult", False)):
            return False
        active_request = str(getattr(gen, "realtime_consult_request", "") or "")
        return _normalized_request(active_request) == _normalized_request(request)

    def active_run_status(self) -> dict[str, Any]:
        gen = self._active_generation()
        if gen is None:
            return {
                "active": False,
                "status": "idle",
                "tools": [],
                "approval_needed": False,
                "cancel_available": False,
                "steer_available": False,
                "follow_up_available": False,
                "queued_controls": [],
            }
        pending_tools = getattr(gen, "pending_tools", {}) or {}
        tool_names = [
            str(tool.get("name") or "")
            for tool in pending_tools.values()
            if isinstance(tool, dict)
        ]
        queued = list(getattr(gen, "voice_control_queue", []) or [])
        return {
            "active": True,
            "status": str(getattr(gen, "status", "streaming") or "streaming"),
            "tools": tool_names,
            "approval_needed": bool(getattr(gen, "interrupt_data", None)),
            "cancel_available": bool(getattr(gen, "stop_event", None)),
            "steer_available": True,
            "follow_up_available": True,
            "queued_controls": [
                {
                    "kind": str(item.get("kind") or ""),
                    "text_chars": len(str(item.get("text") or "")),
                }
                for item in queued
                if isinstance(item, dict)
            ],
        }

    def cancel_active_run(self) -> bool:
        gen = self._active_generation()
        if gen is None:
            return False
        stop_event = getattr(gen, "stop_event", None)
        if stop_event is None:
            return False
        stop_event.set()
        return True

    def control_active_run(self, text: str) -> dict[str, Any]:
        gen = self._active_generation()
        if gen is None:
            return {"handled": False, "control": "none", "status": "idle"}
        clean = str(text or "").strip()
        control = classify_active_run_control(clean)
        if control == "none":
            return {"handled": False, "control": "none", "status": "ignored"}
        if control == "status":
            return {
                "handled": True,
                "control": "status",
                "status": self.active_run_status(),
                "speakable": self._status_speakable(),
            }
        if control == "cancel":
            cancelled = self.cancel_active_run()
            return {
                "handled": True,
                "control": "cancel",
                "status": "cancelled" if cancelled else "not_available",
                "speakable": "Stopping that." if cancelled else "There is no active run to stop.",
            }
        queue = getattr(gen, "voice_control_queue", None)
        if queue is None:
            queue = []
            setattr(gen, "voice_control_queue", queue)
        queue.append({"kind": control, "text": clean})
        return {
            "handled": True,
            "control": control,
            "status": "queued",
            "queued": len(queue),
            "speakable": "Got it. I'll apply that after the current step.",
        }

    def _handle_control_tool(self, parsed: dict[str, Any]) -> dict[str, Any]:
        action = str(parsed.get("action") or "status").strip().lower()
        text = str(parsed.get("text") or "").strip()
        if action == "status":
            return {
                "status": "ok",
                "control": "status",
                "active_run": self.active_run_status(),
                "speakable": self._status_speakable(),
            }
        if action == "cancel":
            cancelled = self.cancel_active_run()
            return {
                "status": "cancelled" if cancelled else "not_available",
                "control": "cancel",
                "speakable": "Stopping that." if cancelled else "There is no active run to stop.",
            }
        control_text = text or action
        result = self.control_active_run(control_text)
        return {
            "status": result.get("status", "queued"),
            "control": result.get("control", action),
            "speakable": result.get("speakable", "Got it."),
        }

    @staticmethod
    def _parse_arguments(arguments: str | dict[str, Any] | None) -> dict[str, Any]:
        if isinstance(arguments, dict):
            return arguments
        if not arguments:
            return {}
        try:
            parsed = json.loads(str(arguments))
        except Exception:
            return {"request": str(arguments)}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _json_output(**payload: Any) -> str:
        return json.dumps(payload, ensure_ascii=False)

    def _status_speakable(self) -> str:
        status = self.active_run_status()
        if not status.get("active"):
            return "Thoth is idle."
        if status.get("approval_needed"):
            return "Thoth is waiting for your approval in the app."
        tools = status.get("tools") or []
        if tools:
            return f"Thoth is using {tools[0]}."
        if status.get("queued_controls"):
            return "Thoth is working and has your follow-up queued."
        return "Thoth is working on your request."

    @staticmethod
    def _resolve_meta(value: Callable[[], str] | str | None) -> str:
        if callable(value):
            try:
                return str(value() or "")
            except Exception:
                return ""
        return str(value or "")


def _normalized_request(text: str) -> str:
    return " ".join(str(text or "").strip().lower().split())
