"""The embedder interface and the deterministic fake embedder (no model, no database)."""
import hashlib
import math
import subprocess
import sys

import pytest

from gsn.embed import BGE_M3_MODEL_ID, FAKE_MODEL_ID, FakeEmbedder, SentenceTransformerEmbedder, get_embedder
from gsn.retrieval import Hit, retrieve

TEXTS = ["passport renewal fee", "পাসপোর্ট নবায়নের ফি", "", "a" * 5000]


def norm(vector):
    return math.sqrt(sum(x * x for x in vector))


def reference_vector(text):
    """The documented construction, restated independently of the implementation: SHA-256 of
    ``f"{i}|{text}"`` for i in 0..31, the 1024 digest bytes mapped linearly onto [-1, 1], then scaled
    to unit length."""
    raw = b"".join(hashlib.sha256(f"{i}|{text}".encode("utf-8")).digest() for i in range(32))
    assert len(raw) == 1024
    values = [byte / 127.5 - 1.0 for byte in raw]
    length = math.sqrt(sum(v * v for v in values))
    return [v / length for v in values]


def test_fake_vectors_are_deterministic_unit_length_1024():
    embedder = FakeEmbedder()
    assert embedder.model_id == FAKE_MODEL_ID == "fake-sha256-v1"
    first = embedder.encode(TEXTS)
    second = FakeEmbedder().encode(TEXTS)  # a new instance: nothing is remembered between calls
    assert first == second
    assert len(first) == len(TEXTS)
    for vector in first:
        assert len(vector) == 1024
        assert all(isinstance(x, float) and -1.0 <= x <= 1.0 for x in vector)
        assert norm(vector) == pytest.approx(1.0, abs=1e-12)


def test_fake_vectors_follow_the_documented_construction():
    embedder = FakeEmbedder()
    for text, vector in zip(TEXTS, embedder.encode(TEXTS)):
        assert vector == pytest.approx(reference_vector(text), abs=1e-15)


def test_a_vector_does_not_depend_on_the_rest_of_the_batch_or_its_position():
    embedder = FakeEmbedder()
    alone = embedder.encode(["b"])[0]
    assert embedder.encode(["a", "b", "c"])[1] == alone
    assert embedder.encode(["b", "b"]) == [alone, alone]
    assert embedder.encode([]) == []


def test_different_texts_give_different_vectors():
    vectors = FakeEmbedder().encode(TEXTS + ["passport renewal fee "])
    assert len({tuple(v) for v in vectors}) == len(vectors)
    # Unrelated texts are nearly orthogonal (random +-1 components), never near-duplicates.
    assert abs(sum(a * b for a, b in zip(vectors[0], vectors[1]))) < 0.2


def test_encode_refuses_a_bare_string():
    # A str is a Sequence[str] of characters: encoding it would silently give one vector per character.
    with pytest.raises(TypeError):
        FakeEmbedder().encode("passport")


def test_a_fake_embedder_can_serve_retrieval():
    """``Embedder`` is structurally an ``_Encoder``: it can be handed to ``retrieve`` unchanged."""
    seen = []

    def search(service_id, vector, k):
        seen.append((service_id, len(vector), k))
        return [Hit("r1", service_id, 0.9, "text", None, None, None, "https://example.test/")]

    hits = retrieve(search, FakeEmbedder(), ["renewal fee"], ["svc"])
    assert [h.record_id for h in hits] == ["r1"]
    assert seen and all(length == 1024 for _, length, _ in seen)


def test_get_embedder_selects_fake_and_rejects_unknown():
    assert isinstance(get_embedder({"GSN_EMBEDDER": "fake"}), FakeEmbedder)
    for bad in ("FAKE", "openai", " fake", "bge-m3 ", "fake,bge-m3"):
        with pytest.raises(ValueError, match="GSN_EMBEDDER"):
            get_embedder({"GSN_EMBEDDER": bad})


def test_the_real_embedder_is_the_default_and_arrives_later():
    # Unset, empty and the explicit name all select it; its construction is not implemented yet (Task C1).
    for env in ({}, {"GSN_EMBEDDER": ""}, {"GSN_EMBEDDER": "bge-m3"}):
        with pytest.raises(NotImplementedError, match="real embedder arrives in Task C1"):
            get_embedder(env)
    with pytest.raises(NotImplementedError, match="real embedder arrives in Task C1"):
        SentenceTransformerEmbedder()
    assert BGE_M3_MODEL_ID == "BAAI/bge-m3@5617a9f61b028005a4858fdac845db406aefb181"
    assert SentenceTransformerEmbedder.model_id == BGE_M3_MODEL_ID


def test_importing_embed_does_not_import_torch():
    """Importing ``gsn.embed`` (and ``gsn.store``) loads no ML stack and no numpy.

    The subprocess makes those imports raise, so the test is meaningful even where they are not
    installed (the host venv has none): it passes only if nothing in the import chain asks for them.
    """
    code = (
        "import sys\n"
        "BLOCKED = ('torch', 'sentence_transformers', 'transformers', 'numpy')\n"
        "class Block:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name.split('.')[0] in BLOCKED:\n"
        "            raise ImportError('blocked: ' + name)\n"
        "sys.meta_path.insert(0, Block())\n"
        "import gsn.embed, gsn.store\n"
        "loaded = sorted(m for m in sys.modules if m.split('.')[0] in BLOCKED)\n"
        "assert not loaded, loaded\n"
        "print('ok')\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
