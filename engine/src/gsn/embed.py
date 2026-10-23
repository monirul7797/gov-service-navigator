"""Embedders: the thing that turns text into the vectors the release stores and the search compares.

Two implementations share one small interface:

* ``FakeEmbedder`` -- deterministic, offline, no model. Its vectors carry no meaning (two texts that
  mean the same thing are *not* closer than two that do not); they exist so the release machinery
  (hashing, reuse, storage, search order, publishing) can be tested and smoke-tested without
  downloading a 2 GB model. A release built with it is stamped ``fake-sha256-v1`` and the publish
  command refuses it unless asked.
* ``SentenceTransformerEmbedder`` -- the real BAAI/bge-m3 model. Its construction is not implemented
  yet (Task C1 adds it, together with the locked ML dependency tree).

Importing this module must stay cheap: it never imports ``torch``, ``sentence_transformers`` or
``numpy``. The real embedder will import them inside its constructor, so only the ``release``
container, which has them, ever loads them.
"""
from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from typing import Protocol

DIMENSIONS = 1024

FAKE_MODEL_ID = "fake-sha256-v1"
BGE_M3_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
BGE_M3_MODEL_ID = f"BAAI/bge-m3@{BGE_M3_REVISION}"  # the id stored on releases built with the real model


class Embedder(Protocol):
    """What the release build and the evals need from an embedder.

    ``model_id`` identifies the model exactly (it is stored on every version and release, and a
    stored vector is reused only for the same model). ``encode`` returns one vector of ``DIMENSIONS``
    floats per text, in order, each of unit length. It is also a ``gsn.retrieval`` encoder: anything
    that satisfies this protocol can be passed wherever retrieval wants one.
    """

    model_id: str

    def encode(self, texts: Sequence[str]) -> list[list[float]]: ...


def _check_texts(texts: Sequence[str]) -> None:
    if isinstance(texts, str):
        # A str is a Sequence[str] of characters: it would silently give one vector per character.
        raise TypeError("encode() takes a sequence of texts, not a single string")


class FakeEmbedder:
    """Deterministic stand-in for the real model: the same text always gives the same vector.

    The vector of a text is built from 32 SHA-256 digests, those of ``f"{i}|{text}"`` for i in 0..31.
    Their 1024 bytes are mapped linearly onto [-1, 1] (``byte / 127.5 - 1``, never exactly 0, so the
    vector is never the zero vector) and the result is scaled to unit length. Changing any of this
    changes every fake vector ever stored, so it must come with a new ``model_id``.
    """

    model_id = FAKE_MODEL_ID

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        _check_texts(texts)
        return [self._vector(text) for text in texts]

    @staticmethod
    def _vector(text: str) -> list[float]:
        raw = b"".join(hashlib.sha256(f"{i}|{text}".encode("utf-8")).digest() for i in range(32))
        values = [byte / 127.5 - 1.0 for byte in raw]
        length = math.sqrt(sum(v * v for v in values))
        return [v / length for v in values]


class SentenceTransformerEmbedder:
    """BAAI/bge-m3 through ``sentence-transformers`` (Task C1 implements it; until then it refuses)."""

    model_id = BGE_M3_MODEL_ID

    def __init__(self) -> None:
        raise NotImplementedError("real embedder arrives in Task C1")

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        raise NotImplementedError("real embedder arrives in Task C1")


def get_embedder(env: Mapping[str, str]) -> Embedder:
    """The embedder ``GSN_EMBEDDER`` names: ``fake``, or ``bge-m3`` (also when unset or empty).

    Anything else is a ``ValueError`` -- the choice decides what a release is stamped with, so a
    typo must not fall back to some other model.
    """
    name = env.get("GSN_EMBEDDER") or "bge-m3"
    if name == "fake":
        return FakeEmbedder()
    if name == "bge-m3":
        return SentenceTransformerEmbedder()
    raise ValueError(f"GSN_EMBEDDER must be 'fake' or 'bge-m3', got {name!r}")
