import hashlib

from gsn.hashing import HASH_VERSION, canonical_json, content_hash, text_hash


def test_canonical_json_is_key_order_independent_and_keeps_bangla():
    # UTF-8 bytes, not \uXXXX escapes; keys sorted; no whitespace.
    assert canonical_json({"b": 1, "a": "জন্ম"}) == '{"a":"জন্ম","b":1}'.encode("utf-8")
    assert canonical_json({"a": "জন্ম", "b": 1}) == canonical_json({"b": 1, "a": "জন্ম"})
    assert b"\\u" not in canonical_json({"a": "জন্ম"})


def test_canonical_json_sorts_nested_keys_but_keeps_list_order():
    assert canonical_json({"z": {"b": 1, "a": 2}, "l": [3, 1, 2]}) == b'{"l":[3,1,2],"z":{"a":2,"b":1}}'


def test_content_hash_changes_with_any_field():
    base = {"id": "nid-001", "text": "জন্ম নিবন্ধন", "fee": 100, "tags": ["a", "b"]}
    seen = {content_hash(base)}
    changed = [
        {**base, "id": "nid-002"},
        {**base, "text": "জন্ম নিবন্ধন "},
        {**base, "fee": 101},
        {**base, "tags": ["b", "a"]},
        {**base, "extra": None},
        {k: v for k, v in base.items() if k != "fee"},
    ]
    for record in changed:
        seen.add(content_hash(record))
    assert len(seen) == 1 + len(changed)


def test_content_hash_ignores_key_order_and_is_sha256_hex_of_canonical_json():
    record = {"b": 1, "a": "জন্ম"}
    assert content_hash(record) == content_hash({"a": "জন্ম", "b": 1})
    digest = content_hash(record)
    assert digest == hashlib.sha256(canonical_json(record)).hexdigest()
    assert len(digest) == 64 and digest == digest.lower()


def test_hash_format_is_pinned_by_known_vectors():
    # Vectors computed independently with `sha256sum` on the exact canonical bytes. If these change,
    # every stored record version changes identity: bump HASH_VERSION instead of editing them.
    assert HASH_VERSION == 1
    assert content_hash({"a": 1}) == "015abd7f5cc57a2dd94b7590f04ad8084273905ee33ec5cebeae62276a97f862"
    assert (
        content_hash({"b": [1, 2, {"c": None}], "a": "জন্ম"})
        == "3eedd007d23796408abafc75ca3a868354edc1c65e16d88cb88de63bcbb48090"
    )


def test_text_hash_is_sha256_of_utf8():
    assert text_hash("abc") == hashlib.sha256(b"abc").hexdigest()
    assert text_hash("জন্ম") == hashlib.sha256("জন্ম".encode("utf-8")).hexdigest()
    assert text_hash("") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert text_hash("a") != text_hash("a ")
