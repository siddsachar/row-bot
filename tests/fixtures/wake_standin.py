"""A stand-in wake word model for tests: the streaming ONNX format, but it hears loudness, not words.

The shipped "Hey Row-Bot" model takes 1280 samples of 16 kHz audio (``chunk``, float in [-1, 1]) and
state tensors, and returns its score and the next state. This one keeps one state value, the
loudness it has heard (half of the last state plus this step's mean absolute level), and scores
twice that, capped at 1. A loud step (amplitude 0.3) scores over 0.5; a quiet one never does.

It is written once, on demand, into the test's temporary folder. Building it needs the ``onnx``
package, which Row-Bot does not depend on, so the bytes are kept here instead (see ``_build``).
"""
from __future__ import annotations

import base64
from pathlib import Path

# Built by ``_build()`` with onnx 1.19 (opset 17, IR 8).
_MODEL = (
    "CAgSDHJvdy1ib3QtdGVzdDqGAwoTCgVjaHVuaxIFbGV2ZWwiA0FicwoqCgVsZXZlbBIEbWVhbiIKUmVkdWNlTWVhbioPCghr"
    "ZWVwZGltcxgBoAECChgKBXN0YXRlCgRoYWxmEgRrZXB0IgNNdWwKHAoEa2VwdAoEbWVhbhIJc3RhdGVfb3V0IgNBZGQKHgoJ"
    "c3RhdGVfb3V0CgN0d28SB2RvdWJsZWQiA011bAobCgdkb3VibGVkCgNvbmUSBmNhcHBlZCIDTWluCh4KBmNhcHBlZAoEZmxh"
    "dBIFc2NvcmUiB1Jlc2hhcGUSDHdha2Utc3RhbmRpbioOEAEiBAAAAD9CBGhhbGYqDRABIgQAAABAQgN0d28qDRABIgQAAIA/"
    "QgNvbmUqDQgBEAc6AQFCBGZsYXRaGAoFY2h1bmsSDwoNCAESCQoCCAEKAwiACloXCgVzdGF0ZRIOCgwIARIICgIIAQoCCAFi"
    "EwoFc2NvcmUSCgoICAESBAoCCAFiGwoJc3RhdGVfb3V0Eg4KDAgBEggKAggBCgIIAUIECgAQEQ=="
)


def _build() -> bytes:  # pragma: no cover - run by hand to regenerate _MODEL
    from onnx import TensorProto, helper

    nodes = [
        helper.make_node("Abs", ["chunk"], ["level"]),
        helper.make_node("ReduceMean", ["level"], ["mean"], keepdims=1),
        helper.make_node("Mul", ["state", "half"], ["kept"]),
        helper.make_node("Add", ["kept", "mean"], ["state_out"]),
        helper.make_node("Mul", ["state_out", "two"], ["doubled"]),
        helper.make_node("Min", ["doubled", "one"], ["capped"]),
        helper.make_node("Reshape", ["capped", "flat"], ["score"]),
    ]
    constants = [
        helper.make_tensor("half", TensorProto.FLOAT, [], [0.5]),
        helper.make_tensor("two", TensorProto.FLOAT, [], [2.0]),
        helper.make_tensor("one", TensorProto.FLOAT, [], [1.0]),
        helper.make_tensor("flat", TensorProto.INT64, [1], [1]),
    ]
    graph = helper.make_graph(
        nodes, "wake-standin",
        [helper.make_tensor_value_info("chunk", TensorProto.FLOAT, [1, 1280]),
         helper.make_tensor_value_info("state", TensorProto.FLOAT, [1, 1])],
        [helper.make_tensor_value_info("score", TensorProto.FLOAT, [1]),
         helper.make_tensor_value_info("state_out", TensorProto.FLOAT, [1, 1])],
        constants)
    model = helper.make_model(graph, producer_name="row-bot-test", opset_imports=[helper.make_opsetid("", 17)])
    model.ir_version = 8
    return model.SerializeToString()


def write_standin(folder: Path) -> Path:
    """The stand-in model, written into ``folder``."""
    path = folder / "wake-standin.onnx"
    path.write_bytes(base64.b64decode(_MODEL))
    return path
