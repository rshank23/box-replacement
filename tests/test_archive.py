from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from app.libs.common.archive import CorruptArchiveError, extract_archive


def _make_zip(path: Path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return path


def test_extracts_nested_archives_at_all_levels(tmp_path: Path):
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as zf:
        zf.writestr("deep.pdf", b"deep")
    outer = _make_zip(tmp_path / "outer.zip", {"top.pdf": b"top", "sub/inner.zip": inner.getvalue()})

    files = extract_archive(outer, tmp_path / "stage", virtual_prefix="S/C/T/outer.zip", max_depth=-1)

    virtuals = sorted(f.virtual_path for f in files)
    assert virtuals == [
        "S/C/T/outer.zip!/sub/inner.zip!/deep.pdf",
        "S/C/T/outer.zip!/top.pdf",
    ]
    assert all(f.real_path.exists() for f in files)


def test_depth_limit_leaves_inner_archive_as_a_file(tmp_path: Path):
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as zf:
        zf.writestr("deep.pdf", b"deep")
    outer = _make_zip(tmp_path / "outer.zip", {"sub/inner.zip": inner.getvalue()})

    files = extract_archive(outer, tmp_path / "stage", virtual_prefix="outer.zip", max_depth=1)

    assert [f.virtual_path for f in files] == ["outer.zip!/sub/inner.zip"]


def test_corrupt_archive_raises(tmp_path: Path):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"PK\x03\x04definitely-not-a-zip")
    with pytest.raises(CorruptArchiveError):
        extract_archive(bad, tmp_path / "stage", virtual_prefix="bad.zip")


def test_zip_slip_is_rejected(tmp_path: Path):
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../../escaped.pdf", b"pwned")
    with pytest.raises(CorruptArchiveError):
        extract_archive(evil, tmp_path / "stage", virtual_prefix="evil.zip")
    assert not (tmp_path.parent / "escaped.pdf").exists()


def test_member_exceeding_max_size_is_rejected(tmp_path: Path):
    big = _make_zip(tmp_path / "big.zip", {"large.pdf": b"x" * 5000})
    with pytest.raises(CorruptArchiveError):
        extract_archive(big, tmp_path / "stage", virtual_prefix="big.zip", max_file_bytes=1000)
