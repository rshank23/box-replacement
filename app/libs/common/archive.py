from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .path_parser import ARCHIVE_SEP, normalize_path


class CorruptArchiveError(Exception):
    """Raised when an archive cannot be read or violates a safety limit."""


@dataclass(frozen=True)
class ExtractedFile:
    """A file materialised from an archive."""

    virtual_path: str  # e.g. STUDY-001/DE/SITE-101/docs.zip!/report.pdf
    real_path: Path  # location on the staging filesystem
    size_bytes: int
    depth: int


#: Guard against decompression bombs: total uncompressed bytes per top-level archive.
MAX_TOTAL_UNCOMPRESSED_BYTES = 20 * 1024 * 1024 * 1024
#: Guard against pathological compression ratios of a single member.
MAX_COMPRESSION_RATIO = 200


def _safe_destination(dest_dir: Path, member_name: str) -> Path:
    """Resolve an archive member inside ``dest_dir``, rejecting Zip-Slip traversal."""
    member = PurePosixPath(member_name.replace("\\", "/"))
    if member.is_absolute() or any(part == ".." for part in member.parts):
        raise CorruptArchiveError(f"Unsafe archive member path: {member_name!r}")
    target = (dest_dir / Path(*member.parts)).resolve()
    root = dest_dir.resolve()
    if not str(target).startswith(str(root)):
        raise CorruptArchiveError(f"Archive member escapes staging directory: {member_name!r}")
    return target


def read_archive_error(archive_path: Path) -> str | None:
    """Return why an archive is unreadable, or None when it opens cleanly."""
    try:
        with zipfile.ZipFile(archive_path) as zf:
            bad = zf.testzip()
        if bad is not None:
            return f"Corrupt entry {bad!r} in {archive_path.name}"
    except zipfile.BadZipFile as exc:
        return f"{archive_path.name} is not a readable ZIP archive: {exc}"
    except OSError as exc:
        return f"Unable to read {archive_path.name}: {exc}"
    return None


def extract_archive(
    archive_path: Path,
    dest_dir: Path,
    *,
    virtual_prefix: str,
    max_depth: int = -1,
    max_file_bytes: int | None = None,
    _depth: int = 1,
    _budget: list[int] | None = None,
) -> list[ExtractedFile]:
    """Recursively extract ``archive_path`` into ``dest_dir``.

    ``max_depth`` of ``-1`` means "extract nested archives at every level"; ``1`` means
    only the top-level archive is opened and inner archives are returned as files.
    """
    if _budget is None:
        _budget = [MAX_TOTAL_UNCOMPRESSED_BYTES]

    results: list[ExtractedFile] = []
    dest_dir.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(archive_path) as zf:
            bad = zf.testzip()
            if bad is not None:
                raise CorruptArchiveError(f"Corrupt entry {bad!r} in {archive_path.name}")
            for info in zf.infolist():
                if info.is_dir():
                    continue
                if info.file_size > _budget[0]:
                    raise CorruptArchiveError(f"Archive exceeds uncompressed size budget: {archive_path.name}")
                if info.compress_size > 0 and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO:
                    raise CorruptArchiveError(f"Suspicious compression ratio in {archive_path.name}")
                if max_file_bytes is not None and info.file_size > max_file_bytes:
                    raise CorruptArchiveError(
                        f"Archive member {info.filename!r} exceeds the maximum allowed file size"
                    )

                target = _safe_destination(dest_dir, info.filename)
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as dst:
                    written = 0
                    while chunk := src.read(1024 * 1024):
                        written += len(chunk)
                        if written > _budget[0]:
                            raise CorruptArchiveError(
                                f"Archive exceeds uncompressed size budget: {archive_path.name}"
                            )
                        dst.write(chunk)
                _budget[0] -= target.stat().st_size

                virtual = f"{normalize_path(virtual_prefix)}{ARCHIVE_SEP}{normalize_path(info.filename)}"
                is_archive = target.suffix.lower() == ".zip"
                can_recurse = max_depth == -1 or _depth < max_depth

                if is_archive and can_recurse:
                    nested_dir = target.parent / f"__{target.stem}_d{_depth}"
                    results.extend(
                        extract_archive(
                            target,
                            nested_dir,
                            virtual_prefix=virtual,
                            max_depth=max_depth,
                            max_file_bytes=max_file_bytes,
                            _depth=_depth + 1,
                            _budget=_budget,
                        )
                    )
                else:
                    results.append(
                        ExtractedFile(
                            virtual_path=virtual,
                            real_path=target,
                            size_bytes=target.stat().st_size,
                            depth=_depth,
                        )
                    )
    except zipfile.BadZipFile as exc:
        raise CorruptArchiveError(f"{archive_path.name} is not a readable ZIP archive: {exc}") from exc
    except OSError as exc:
        raise CorruptArchiveError(f"Unable to read {archive_path.name}: {exc}") from exc

    return results
