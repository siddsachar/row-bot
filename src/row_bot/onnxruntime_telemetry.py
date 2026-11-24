"""ONNX Runtime's Windows telemetry, switched off before Row-Bot creates any ONNX Runtime session.

Microsoft's official ``onnxruntime`` builds for Windows (the PyPI wheel Row-Bot locks, used by Kokoro,
faster-whisper's voice filter and Pipecat's Silero VAD and Smart Turn) register a TraceLogging provider,
``Microsoft.ML.ONNXRuntime``, in Microsoft's telemetry group, with its events on by default
(process info, each session's model metadata and file name, errors, timings). Windows' diagnostic
data service may collect them. ``onnxruntime.disable_telemetry_events()`` turns them off for this
process; every place that creates a session calls :func:`switch_off` first. Elsewhere the call is a
no-op (ONNX Runtime has no telemetry on macOS or Linux). ``docs/VOICE_ENGINE.md`` has the review.
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_switched_off = False


def switch_off() -> None:
    """Turn ONNX Runtime's telemetry events off for this process, once, before any session exists."""

    global _switched_off
    if _switched_off:
        return
    with _lock:
        if _switched_off:
            return
        try:
            import onnxruntime
        except ImportError:  # not installed: nothing creates a session
            _switched_off = True
            return
        onnxruntime.disable_telemetry_events()
        _switched_off = True
        logger.debug("ONNX Runtime telemetry events are off for this process")


__all__ = ["switch_off"]
