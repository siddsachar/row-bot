from __future__ import annotations

import struct

import numpy as np
import pytest

from row_bot.flat_vector_storage import (
    MAX_VECTOR_BYTES,
    MAX_VECTOR_COUNT,
    MAX_VECTOR_DIMENSION,
    decode_flat_vectors,
)

HEADER = struct.Struct("<4siqqq?iq")


def payload(*, magic=b"IxF2", dimension=2, count=1, trained=True,
            metric=1, size=None, values=(0.25, -0.5)):
    return HEADER.pack(magic, dimension, count, 1 << 20, 1 << 20,
                       trained, metric, dimension * count if size is None else size) + struct.pack(
                           "<" + "f" * len(values), *values)


@pytest.mark.parametrize("metric", ["l2", "inner_product"])
def test_trusted_native_writer_roundtrip_without_native_deserialization(tmp_path, monkeypatch, metric):
    import faiss

    index = faiss.IndexFlatL2(2) if metric == "l2" else faiss.IndexFlatIP(2)
    original = np.array([[0.25, -0.5], [1.0, 0.0]], dtype=np.float32)
    index.add(original)
    target = tmp_path / "trusted.faiss"
    faiss.write_index(index, str(target))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Native index deserialization invoked")

    monkeypatch.setattr(faiss, "read_index", forbidden)
    monkeypatch.setattr(faiss, "deserialize_index", forbidden)
    decoded = decode_flat_vectors(target.read_bytes(), expected_dimension=2,
                                  expected_count=2, expected_metric=metric)
    assert (decoded.dimension, decoded.count, decoded.metric) == (2, 2, metric)
    np.testing.assert_array_equal(decoded.vectors, original)
    assert decoded.vectors.dtype == np.dtype("<f4")
    assert not decoded.vectors.flags.writeable


def test_read_only_bytes_view_retains_its_source():
    data = payload()
    decoded = decode_flat_vectors(data)
    del data
    np.testing.assert_array_equal(decoded.vectors, [[0.25, -0.5]])
    with pytest.raises(ValueError):
        decoded.vectors[0, 0] = 10
    with pytest.raises(ValueError):
        decoded.vectors.setflags(write=True)


@pytest.mark.parametrize("metric,magic,code", [("l2", b"IxF2", 1), ("inner_product", b"IxFI", 0)])
def test_empty_flat_index_needs_no_provider(metric, magic, code):
    result = decode_flat_vectors(payload(magic=magic, metric=code, count=0, values=()),
                                 expected_count=0, expected_metric=metric)
    assert result.vectors.shape == (0, 2)
    assert result.count == 0


@pytest.mark.parametrize("change", [
    {"magic": b"IxPQ"}, {"magic": b"IxFI"}, {"metric": 0},
    {"trained": False}, {"dimension": 0}, {"dimension": -1},
    {"dimension": MAX_VECTOR_DIMENSION + 1}, {"count": -1},
    {"count": MAX_VECTOR_COUNT + 1}, {"size": 3},
    {"size": -1}, {"values": (float("nan"), 0)},
    {"values": (float("inf"), 0)}, {"values": (-float("inf"), 0)},
])
def test_invalid_flat_data_rejected(change):
    with pytest.raises(ValueError):
        decode_flat_vectors(payload(**change))


@pytest.mark.parametrize("data", [b"", b"IxF2", payload()[:-1], payload() + b"\x00"])
def test_exact_header_and_payload_length_required(data):
    with pytest.raises(ValueError):
        decode_flat_vectors(data)


@pytest.mark.parametrize("expected", [
    {"expected_dimension": 3}, {"expected_count": 2},
    {"expected_metric": "inner_product"},
    {"expected_dimension": True}, {"expected_dimension": 2.0},
    {"expected_dimension": 0}, {"expected_dimension": MAX_VECTOR_DIMENSION + 1},
    {"expected_count": False}, {"expected_count": 1.0},
    {"expected_count": -1}, {"expected_count": MAX_VECTOR_COUNT + 1},
    {"expected_metric": "cosine"}, {"expected_metric": []},
])
def test_fingerprint_expectations_are_exact_and_bounded(expected):
    with pytest.raises(ValueError):
        decode_flat_vectors(payload(), **expected)


def test_oversized_input_rejected_before_array_allocation(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Array allocated before byte budget check")

    monkeypatch.setattr(np, "frombuffer", forbidden)
    with pytest.raises(ValueError, match="safe read budget"):
        decode_flat_vectors(bytes(MAX_VECTOR_BYTES + 1))


@pytest.mark.parametrize("data", [bytearray(payload()), memoryview(payload()), "not bytes", None])
def test_mutable_or_nonbytes_input_is_not_retained(data):
    with pytest.raises(TypeError):
        decode_flat_vectors(data)
