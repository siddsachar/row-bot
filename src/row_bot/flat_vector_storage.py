"""Bounded decoding of historical flat FAISS numeric data.

This module parses data only. Index construction, metadata, fingerprints,
publication and file ownership remain with the document and knowledge owners.
"""
from __future__ import annotations

from dataclasses import dataclass
import struct
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray

MAX_VECTOR_BYTES = 256 * 1024 * 1024
MAX_VECTOR_DIMENSION = 65536
MAX_VECTOR_COUNT = 1_000_000
_HEADER = struct.Struct("<4siqqq?iq")
Metric = Literal["l2", "inner_product"]


@dataclass(frozen=True)
class FlatVectors:
    dimension: int
    count: int
    metric: Metric
    vectors: NDArray[np.float32]


def decode_flat_vectors(
    data: bytes,
    *,
    expected_dimension: int | None = None,
    expected_count: int | None = None,
    expected_metric: Metric | None = None,
) -> FlatVectors:
    """Return a read-only numeric view; never invoke native deserialization.

    Invalid bytes raise ValueError. Non-bytes input raises TypeError. The
    returned view retains the immutable bytes, including for empty indexes.
    """
    if type(data) is not bytes:
        raise TypeError("Flat vector input must be immutable bytes")
    if len(data) > MAX_VECTOR_BYTES:
        raise ValueError("Vector file exceeds the safe read budget; rebuild required")
    if expected_dimension is not None and (
        type(expected_dimension) is not int
        or not 0 < expected_dimension <= MAX_VECTOR_DIMENSION
    ):
        raise ValueError("Invalid expected vector dimension")
    if expected_count is not None and (
        type(expected_count) is not int
        or not 0 <= expected_count <= MAX_VECTOR_COUNT
    ):
        raise ValueError("Invalid expected vector count")
    if expected_metric is not None and (
        type(expected_metric) is not str
        or expected_metric not in {"l2", "inner_product"}
    ):
        raise ValueError("Invalid expected vector metric")
    if len(data) < _HEADER.size:
        raise ValueError("Incomplete vector index; rebuild required")
    magic, dimension, total, _unused_a, _unused_b, trained, metric_code, size = _HEADER.unpack_from(data)
    pair = (magic, metric_code)
    if pair not in {(b"IxF2", 1), (b"IxFI", 0)} or not trained:
        raise ValueError("Unsupported vector index; rebuild required")
    metric: Metric = "l2" if metric_code == 1 else "inner_product"
    if not 0 < dimension <= MAX_VECTOR_DIMENSION or not 0 <= total <= MAX_VECTOR_COUNT:
        raise ValueError("Invalid vector shape; rebuild required")
    if expected_dimension is not None and dimension != expected_dimension:
        raise ValueError("Vector dimension and embedding fingerprint disagree")
    if expected_count is not None and total != expected_count:
        raise ValueError("Vector count and segment manifest disagree")
    if expected_metric is not None and metric != expected_metric:
        raise ValueError("Vector metric and embedding fingerprint disagree")
    if size != dimension * total or len(data) != _HEADER.size + size * 4:
        raise ValueError("Vector shape and file size disagree; rebuild required")
    import numpy as np

    vectors = np.frombuffer(data, dtype="<f4", offset=_HEADER.size).reshape(total, dimension)
    if not np.isfinite(vectors).all():
        raise ValueError("Non-finite vector data; rebuild required")
    vectors.setflags(write=False)
    return FlatVectors(dimension, total, metric, vectors)
