from __future__ import annotations

from pathlib import Path

from toem_reid.hashing import canonical_json, sha256_bytes, sha256_canonical, sha256_file


def test_sha256_file_matches_known_digest(tmp_path: Path) -> None:
    path = tmp_path / "a.bin"
    path.write_bytes(b"abc")
    assert sha256_file(path) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert sha256_bytes(b"abc") == sha256_file(path)


def test_canonical_json_is_key_order_independent_and_compact() -> None:
    assert canonical_json({"b": 1, "a": [1, 2]}) == b'{"a":[1,2],"b":1}'
    assert sha256_canonical({"b": 1, "a": 2}) == sha256_canonical({"a": 2, "b": 1})


def test_canonical_json_rejects_non_finite_numbers() -> None:
    import pytest

    with pytest.raises(ValueError, match="Out of range float"):
        canonical_json({"x": float("nan")})
