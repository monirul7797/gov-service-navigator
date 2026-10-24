"""Embedders: the thing that turns text into the vectors the release stores and the search compares.

Two implementations share one small interface:

* ``FakeEmbedder`` -- deterministic, offline, no model. Its vectors carry no meaning (two texts that
  mean the same thing are *not* closer than two that do not); they exist so the release machinery
  (hashing, reuse, storage, search order, publishing) can be tested and smoke-tested without
  downloading a 2 GB model. A release built with it is stamped ``fake-sha256-v1`` and the publish
  command refuses it unless asked.
* ``SentenceTransformerEmbedder`` -- the real BAAI/bge-m3 model at one pinned revision. It is lazy: making
  one imports nothing and touches no file, and the model is loaded by the first ``encode``. The model
  files are never downloaded implicitly: ``gsn models fetch`` (``fetch_model``) is the only code that
  uses the network, and a load whose files are not in the local cache is a ``ModelMissingError``.

Importing this module must stay cheap: it never imports ``torch``, ``sentence_transformers``,
``huggingface_hub`` or ``numpy``. The real embedder imports them inside its methods, so only the
``release`` container, which has them, ever loads them -- and ``get_embedder`` works on a host without
them.
"""
from __future__ import annotations

import hashlib
import math
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol

DIMENSIONS = 1024

FAKE_MODEL_ID = "fake-sha256-v1"

MODEL_REPO = "BAAI/bge-m3"
MODEL_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
MODEL_ID = f"{MODEL_REPO}@{MODEL_REVISION}"  # the id stored on releases built with the real model
# Exactly the files of that revision which the ChromaDB baseline's snapshot holds and loads (the weights
# are `pytorch_model.bin`, about 2.3 GB; the repository's ONNX and other formats are not fetched).
MODEL_FILES = [
    "1_Pooling/*",
    "config.json",
    "config_sentence_transformers.json",
    "modules.json",
    "pytorch_model.bin",
    "sentence_bert_config.json",
    "sentencepiece.bpe.model",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
]
BATCH_SIZE = 16  # as in the baseline run
FETCH_HINT = "run: gsn models fetch"

# The older names, still imported by gsn.parity and its tests.
BGE_M3_REVISION = MODEL_REVISION
BGE_M3_MODEL_ID = MODEL_ID

_OFFLINE_VALUES = {"1", "ON", "YES", "TRUE"}  # what huggingface_hub reads as HF_HUB_OFFLINE being on


class Embedder(Protocol):
    """What the release build and the evals need from an embedder.

    ``model_id`` identifies the model exactly (it is stored on every version and release, and a
    stored vector is reused only for the same model). ``encode`` returns one vector of ``DIMENSIONS``
    floats per text, in order, each of unit length. It is also a ``gsn.retrieval`` encoder: anything
    that satisfies this protocol can be passed wherever retrieval wants one.
    """

    model_id: str

    def encode(self, texts: Sequence[str]) -> list[list[float]]: ...


class ModelMissingError(Exception):
    """The pinned model is not (completely) in the local model cache: ``gsn models fetch`` downloads it."""


class MlPackagesMissingError(Exception):
    """``sentence-transformers`` / ``huggingface_hub`` are not installed: only the release image has them."""


def hub_is_offline(env: Mapping[str, str] | None = None) -> bool:
    """Whether ``HF_HUB_OFFLINE`` is on (default: this process's environment)."""
    return (os.environ if env is None else env).get("HF_HUB_OFFLINE", "").strip().upper() in _OFFLINE_VALUES


def _ml_packages_missing(what: str) -> MlPackagesMissingError:
    return MlPackagesMissingError(
        f"{what} needs the ML packages (sentence-transformers, huggingface_hub), which only the release image "
        "has: run it as `docker compose --profile tools run --rm release ...` (set GSN_EMBEDDER=fake for tests)"
    )


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
    """BAAI/bge-m3 through ``sentence-transformers``, loaded lazily from the local model cache.

    ``cache_dir`` is the Hugging Face hub cache; ``None`` leaves it to ``HF_HOME``/``HF_HUB_CACHE`` (the
    release image sets ``HF_HOME=/models``, a volume). Vectors are normalised to unit length and returned as
    plain lists of Python floats. Whether the hub may use the network while loading follows
    ``HF_HUB_OFFLINE`` (the image sets it to 1); the files must already be in the cache either way.
    """

    model_id = MODEL_ID

    def __init__(self, cache_dir: str | None = None) -> None:
        self._cache_dir = cache_dir
        self._model = None

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        _check_texts(texts)
        if not texts:
            return []
        model = self._model if self._model is not None else self._load()
        rows = model.encode(list(texts), normalize_embeddings=True, batch_size=BATCH_SIZE).tolist()
        vectors = [[float(x) for x in row] for row in rows]
        if len(vectors) != len(texts):
            raise RuntimeError(f"the model returned {len(vectors)} vectors for {len(texts)} texts")
        for vector in vectors:
            if len(vector) != DIMENSIONS:
                raise RuntimeError(f"the model returned vectors of {len(vector)} dimensions, expected {DIMENSIONS}")
        return vectors

    def _load(self):
        try:
            from huggingface_hub import snapshot_download
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise _ml_packages_missing("the real embedder") from exc
        self._require_snapshot(snapshot_download)
        self._model = SentenceTransformer(
            MODEL_REPO, revision=MODEL_REVISION, cache_folder=self._cache_dir, local_files_only=hub_is_offline()
        )
        return self._model

    def _require_snapshot(self, snapshot_download) -> None:
        """Raise ``ModelMissingError`` unless every pinned file is in the cache (nothing is downloaded)."""
        try:
            path = snapshot_download(
                MODEL_REPO, revision=MODEL_REVISION, allow_patterns=MODEL_FILES, cache_dir=self._cache_dir,
                local_files_only=True,
            )
        except (OSError, ValueError) as exc:  # the hub's LocalEntryNotFoundError is both
            raise ModelMissingError(FETCH_HINT) from exc
        snapshot = Path(path)
        if not all(any(file.exists() for file in snapshot.glob(pattern)) for pattern in MODEL_FILES):
            raise ModelMissingError(FETCH_HINT)  # an interrupted download leaves some files out


def fetch_model(cache_dir: str | None = None) -> str:
    """Download the pinned files of the model into the hub cache; returns the snapshot directory.

    This is the one place that uses the network (``gsn models fetch``, run with ``HF_HUB_OFFLINE=0``).
    Files already in the cache are not downloaded again.
    """
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise _ml_packages_missing("`gsn models fetch`") from exc
    return snapshot_download(MODEL_REPO, revision=MODEL_REVISION, allow_patterns=MODEL_FILES, cache_dir=cache_dir)


def snapshot_bytes(snapshot: str) -> int:
    """Total size of the files in a snapshot directory (the links the hub cache uses are followed)."""
    return sum(file.stat().st_size for file in Path(snapshot).rglob("*") if file.is_file())


def get_embedder(env: Mapping[str, str]) -> Embedder:
    """The embedder ``GSN_EMBEDDER`` names: ``fake``, or ``bge-m3`` (also when unset or empty).

    Anything else is a ``ValueError`` -- the choice decides what a release is stamped with, so a
    typo must not fall back to some other model. The real embedder is only *made* here: it imports
    nothing and loads nothing until its first ``encode``.
    """
    name = env.get("GSN_EMBEDDER") or "bge-m3"
    if name == "fake":
        return FakeEmbedder()
    if name == "bge-m3":
        return SentenceTransformerEmbedder()
    raise ValueError(f"GSN_EMBEDDER must be 'fake' or 'bge-m3', got {name!r}")
