from __future__ import annotations

from pathlib import Path

from app.libs.common.hashing import external_id, sha256_bytes, sha256_file


def test_streamed_and_in_memory_hashes_agree(tmp_path: Path):
    payload = b"clinical trial document contents"
    target = tmp_path / "doc.pdf"
    target.write_bytes(payload)
    assert sha256_file(target) == sha256_bytes(payload)


def test_external_id_is_deterministic_and_path_sensitive():
    a = external_id("STUDY-001/US/SITE-101/Cat/a.pdf", "abc123")
    b = external_id("STUDY-001/US/SITE-101/Cat/a.pdf", "abc123")
    c = external_id("STUDY-001/US/SITE-102/Cat/a.pdf", "abc123")
    assert a == b
    assert a != c
    assert len(a) == 64
