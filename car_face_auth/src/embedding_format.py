"""Versioned binary storage for InsightFace embedding collections."""

from __future__ import annotations

import struct
from collections.abc import Sequence

import numpy as np

MAGIC = b"BASSF001"
EMBEDDING_DIMENSION = 512
MAX_EMBEDDINGS = 10
HEADER = struct.Struct("<8sHH")
MAX_PAYLOAD_BYTES = MAX_EMBEDDINGS * EMBEDDING_DIMENSION * 4


class EmbeddingFormatError(ValueError):
    """The stored template is malformed or unsupported."""


class TemplateMigrationRequired(EmbeddingFormatError):
    """The stored template predates the safe binary format."""


def validate_embeddings(embeddings: Sequence[object]) -> list[np.ndarray]:
    if not isinstance(embeddings, (list, tuple)) or not embeddings:
        raise EmbeddingFormatError("embedding collection must be a non-empty list")
    if len(embeddings) > MAX_EMBEDDINGS:
        raise EmbeddingFormatError(
            f"embedding collection must contain at most {MAX_EMBEDDINGS} samples"
        )

    validated = []
    for embedding in embeddings:
        try:
            array = np.asarray(embedding, dtype=np.float32)
        except (TypeError, ValueError) as exc:
            raise EmbeddingFormatError("embedding must contain numeric values") from exc
        if array.shape != (EMBEDDING_DIMENSION,) or not np.isfinite(array).all():
            raise EmbeddingFormatError(
                f"embedding must be a finite {EMBEDDING_DIMENSION}-value vector"
            )
        if not np.any(array):
            raise EmbeddingFormatError("embedding vector must not be all zero")
        validated.append(np.ascontiguousarray(array, dtype="<f4"))
    return validated


def encode_embeddings(embeddings: Sequence[object]) -> bytes:
    validated = validate_embeddings(embeddings)
    payload = b"".join(embedding.tobytes(order="C") for embedding in validated)
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise EmbeddingFormatError("embedding payload exceeds the storage limit")
    return HEADER.pack(MAGIC, len(validated), EMBEDDING_DIMENSION) + payload


def decode_embeddings(blob: bytes | bytearray | memoryview) -> list[np.ndarray]:
    if not isinstance(blob, (bytes, bytearray, memoryview)):
        raise EmbeddingFormatError("face template must be binary")
    data = bytes(blob)
    if len(data) < HEADER.size or data[: len(MAGIC)] != MAGIC:
        raise TemplateMigrationRequired(
            "face template uses a legacy format; offline migration is required"
        )

    magic, count, dimension = HEADER.unpack_from(data)
    if magic != MAGIC:
        raise EmbeddingFormatError("face template magic is invalid")
    if not 1 <= count <= MAX_EMBEDDINGS:
        raise EmbeddingFormatError("face template sample count is invalid")
    if dimension != EMBEDDING_DIMENSION:
        raise EmbeddingFormatError("face template dimension is unsupported")

    payload = data[HEADER.size :]
    expected_bytes = count * dimension * 4
    if expected_bytes > MAX_PAYLOAD_BYTES or len(payload) != expected_bytes:
        raise EmbeddingFormatError("face template length is invalid")

    values = np.frombuffer(payload, dtype="<f4").reshape(count, dimension)
    return validate_embeddings([row.copy() for row in values])
