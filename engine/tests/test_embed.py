"""The embedder interface, the deterministic fake embedder and the real bge-m3 embedder (no model, no database)."""
import hashlib
import math
import subprocess
import sys
import types

import pytest

from gsn.embed import (
    BGE_M3_MODEL_ID,
    BGE_M3_REVISION,
    FAKE_MODEL_ID,
    MODEL_FILES,
    MODEL_ID,
    MODEL_REPO,
    MODEL_REVISION,
    FakeEmbedder,
    MlPackagesMissingError,
    ModelMissingError,
    SentenceTransformerEmbedder,
    fetch_model,
    get_embedder,
    snapshot_bytes,
)
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


# --- the real embedder (BAAI/bge-m3 through sentence-transformers) -------------------------------------
#
# The host venv has no ML packages, so these tests put small stand-ins for ``sentence_transformers`` and
# ``huggingface_hub`` into ``sys.modules``; what is checked is how the embedder uses them (what it loads,
# when, with which arguments) and what it does with the answers. The real model is exercised in the
# release container (see docs/DEVELOPMENT.md "Model download").

PINNED_FILES = [
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


def test_the_model_pin_constants():
    assert MODEL_REPO == "BAAI/bge-m3"
    assert MODEL_REVISION == "5617a9f61b028005a4858fdac845db406aefb181"
    assert MODEL_ID == "BAAI/bge-m3@5617a9f61b028005a4858fdac845db406aefb181"
    assert MODEL_FILES == PINNED_FILES
    # the older names, still used by gsn.parity and its tests
    assert BGE_M3_REVISION == MODEL_REVISION and BGE_M3_MODEL_ID == MODEL_ID
    assert SentenceTransformerEmbedder.model_id == MODEL_ID


class FakeArray:
    """What ``SentenceTransformer.encode`` returns: something with ``tolist()`` (a numpy array)."""

    def __init__(self, rows):
        self.rows = rows

    def tolist(self):
        return [list(row) for row in self.rows]


class FakeSentenceTransformer:
    instances = []
    dimensions = 1024

    def __init__(self, name, **kwargs):
        self.name = name
        self.kwargs = kwargs
        self.encode_calls = []
        type(self).instances.append(self)

    def encode(self, texts, **kwargs):
        self.encode_calls.append((list(texts), kwargs))
        return FakeArray([[float(i + 1)] + [0.0] * (self.dimensions - 1) for i, _ in enumerate(texts)])


class MlWorld:
    """The two stand-in modules and what they were asked."""

    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.hub_calls = []
        self.hub_result = str(snapshot)  # an Exception instance is raised instead

    def snapshot_download(self, *args, **kwargs):
        self.hub_calls.append((args, kwargs))
        if isinstance(self.hub_result, Exception):
            raise self.hub_result
        return self.hub_result


@pytest.fixture
def ml(monkeypatch, tmp_path):
    """Stand-ins for the two ML modules; the fake hub cache holds a complete snapshot."""
    snapshot = tmp_path / "hub" / "models--BAAI--bge-m3" / "snapshots" / MODEL_REVISION
    for pattern in MODEL_FILES:
        target = snapshot / pattern.replace("*", "config.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x" * 10)
    world = MlWorld(snapshot)
    FakeSentenceTransformer.instances = []
    FakeSentenceTransformer.dimensions = 1024

    sentence_transformers = types.ModuleType("sentence_transformers")
    sentence_transformers.SentenceTransformer = FakeSentenceTransformer
    hub = types.ModuleType("huggingface_hub")
    hub.snapshot_download = world.snapshot_download
    monkeypatch.setitem(sys.modules, "sentence_transformers", sentence_transformers)
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    return world


def test_the_real_embedder_is_the_default_and_is_made_without_loading_anything(ml):
    for env in ({}, {"GSN_EMBEDDER": ""}, {"GSN_EMBEDDER": "bge-m3"}):
        embedder = get_embedder(env)
        assert isinstance(embedder, SentenceTransformerEmbedder)
        assert embedder.model_id == "BAAI/bge-m3@5617a9f61b028005a4858fdac845db406aefb181"
    assert FakeSentenceTransformer.instances == [] and ml.hub_calls == []


def test_making_the_embedder_imports_no_ml_package():
    """Construction, ``get_embedder`` and an empty ``encode`` must work on the host venv: the subprocess
    makes every ML import raise, so the test says something even though the host has none of them."""
    code = (
        "import sys\n"
        "BLOCKED = ('torch', 'sentence_transformers', 'transformers', 'numpy', 'huggingface_hub')\n"
        "class Block:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name.split('.')[0] in BLOCKED:\n"
        "            raise ImportError('blocked: ' + name)\n"
        "sys.meta_path.insert(0, Block())\n"
        "from gsn.embed import SentenceTransformerEmbedder, get_embedder\n"
        "e = get_embedder({})\n"
        "assert isinstance(e, SentenceTransformerEmbedder)\n"
        "assert e.model_id == 'BAAI/bge-m3@5617a9f61b028005a4858fdac845db406aefb181'\n"
        "assert SentenceTransformerEmbedder(cache_dir='/somewhere').model_id == e.model_id\n"
        "assert e.encode([]) == []\n"
        "loaded = sorted(m for m in sys.modules if m.split('.')[0] in BLOCKED)\n"
        "assert not loaded, loaded\n"
        "print('ok')\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_the_model_loads_on_the_first_encode_only_with_the_pinned_revision(ml):
    embedder = get_embedder({})
    first = embedder.encode(["a", "b"])
    second = embedder.encode(["c"])
    assert len(FakeSentenceTransformer.instances) == 1
    model = FakeSentenceTransformer.instances[0]
    assert model.name == "BAAI/bge-m3"
    assert model.kwargs["revision"] == MODEL_REVISION
    assert model.kwargs["cache_folder"] is None  # HF_HOME decides (the release image sets it to /models)
    assert model.kwargs["local_files_only"] is False  # HF_HUB_OFFLINE is not set
    assert len(first) == 2 and len(second) == 1


def test_a_given_cache_dir_is_used_for_the_check_and_for_loading(ml):
    SentenceTransformerEmbedder(cache_dir="/cache/root").encode(["a"])
    assert FakeSentenceTransformer.instances[0].kwargs["cache_folder"] == "/cache/root"
    assert ml.hub_calls and all(kwargs["cache_dir"] == "/cache/root" for _, kwargs in ml.hub_calls)


@pytest.mark.parametrize(("value", "offline"), [("1", True), ("0", False), ("", False)])
def test_local_files_only_follows_hf_hub_offline(ml, monkeypatch, value, offline):
    monkeypatch.setenv("HF_HUB_OFFLINE", value)
    get_embedder({}).encode(["a"])
    assert FakeSentenceTransformer.instances[0].kwargs["local_files_only"] is offline


def test_encode_normalises_in_batches_of_16_and_returns_plain_float_lists(ml):
    texts = ["জাতীয় পরিচয়পত্র", "passport", "amar birth certificate hariye gese"]
    vectors = get_embedder({}).encode(texts)
    texts_seen, kwargs = FakeSentenceTransformer.instances[0].encode_calls[0]
    assert texts_seen == texts
    assert kwargs == {"normalize_embeddings": True, "batch_size": 16}
    assert len(vectors) == 3
    for vector in vectors:
        assert type(vector) is list and len(vector) == 1024
        assert all(type(x) is float for x in vector)


def test_encode_of_nothing_loads_nothing_and_a_bare_string_is_refused(ml):
    embedder = get_embedder({})
    assert embedder.encode([]) == []
    assert FakeSentenceTransformer.instances == []
    with pytest.raises(TypeError):
        embedder.encode("passport")


def test_a_model_that_returns_the_wrong_number_of_dimensions_is_an_error(ml):
    FakeSentenceTransformer.dimensions = 768
    with pytest.raises(RuntimeError, match="768"):
        get_embedder({}).encode(["a"])


def test_a_model_that_is_not_in_the_cache_is_model_missing(ml):
    ml.hub_result = FileNotFoundError("Cannot find an appropriate cached snapshot folder")
    embedder = get_embedder({})
    with pytest.raises(ModelMissingError, match=r"run: gsn models fetch"):
        embedder.encode(["a"])
    assert FakeSentenceTransformer.instances == []
    # the check asked the hub for the pinned snapshot, from the local cache only
    (args, kwargs), = ml.hub_calls
    assert args == ("BAAI/bge-m3",) and kwargs["revision"] == MODEL_REVISION
    assert kwargs["allow_patterns"] == PINNED_FILES and kwargs["local_files_only"] is True


def test_a_half_downloaded_snapshot_is_model_missing(ml):
    (ml.snapshot / "pytorch_model.bin").unlink()
    with pytest.raises(ModelMissingError, match=r"run: gsn models fetch"):
        get_embedder({}).encode(["a"])
    assert FakeSentenceTransformer.instances == []


def test_a_failed_load_is_tried_again_by_the_next_encode(ml):
    embedder = get_embedder({})
    ml.hub_result = FileNotFoundError("not there yet")
    with pytest.raises(ModelMissingError):
        embedder.encode(["a"])
    ml.hub_result = str(ml.snapshot)  # `gsn models fetch` ran in between
    assert len(embedder.encode(["a"])) == 1


def test_without_the_ml_packages_the_error_says_where_the_real_embedder_runs(monkeypatch):
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)  # makes `import sentence_transformers` fail
    monkeypatch.setitem(sys.modules, "huggingface_hub", None)
    with pytest.raises(MlPackagesMissingError, match="release"):
        get_embedder({}).encode(["a"])


def test_fetch_model_downloads_the_pinned_files_and_returns_the_snapshot_path(ml):
    ml.hub_result = "/models/hub/models--BAAI--bge-m3/snapshots/" + MODEL_REVISION
    assert fetch_model() == ml.hub_result
    (args, kwargs), = ml.hub_calls
    assert args == ("BAAI/bge-m3",)
    assert kwargs == {"revision": MODEL_REVISION, "allow_patterns": PINNED_FILES, "cache_dir": None}
    fetch_model("/elsewhere")
    assert ml.hub_calls[-1][1]["cache_dir"] == "/elsewhere"


def test_fetch_model_without_the_ml_packages_says_where_it_runs(monkeypatch):
    monkeypatch.setitem(sys.modules, "huggingface_hub", None)
    with pytest.raises(MlPackagesMissingError, match="release"):
        fetch_model()


def test_snapshot_bytes_adds_up_the_files_behind_the_links(tmp_path):
    blobs = tmp_path / "blobs"
    snapshot = tmp_path / "snapshots" / "rev"
    (snapshot / "1_Pooling").mkdir(parents=True)
    blobs.mkdir()
    (blobs / "a").write_bytes(b"x" * 1000)
    (blobs / "b").write_bytes(b"y" * 234)
    try:
        (snapshot / "pytorch_model.bin").symlink_to(blobs / "a")
        (snapshot / "1_Pooling" / "config.json").symlink_to(blobs / "b")
    except OSError:  # no symlink right on this machine: plain files give the same total
        (snapshot / "pytorch_model.bin").write_bytes(b"x" * 1000)
        (snapshot / "1_Pooling" / "config.json").write_bytes(b"y" * 234)
    assert snapshot_bytes(str(snapshot)) == 1234
