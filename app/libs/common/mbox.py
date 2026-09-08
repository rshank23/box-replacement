from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .archive import CorruptArchiveError, extract_archive
from .config import Settings, get_settings
from .path_parser import ARCHIVE_SEP, normalize_path, parse_path


class MBoxPathError(Exception):
    """Raised when a requested path is outside the configured MBox root."""


@dataclass(frozen=True)
class MBoxEntry:
    name: str
    relative_path: str
    is_dir: bool
    size_bytes: int | None
    modified_at: datetime | None


def _root(settings: Settings | None = None, root: str = "source") -> Path:
    settings = settings or get_settings()
    paths = {
        "source": settings.mbox_root,
        "destination": settings.destination_root,
        "unclassified": settings.unclassified_root,
    }
    if root not in paths:
        raise MBoxPathError(f"Unknown root {root!r}; expected one of {sorted(paths)}")
    return Path(paths[root]).expanduser().resolve()


def resolve_within_root(relative_path: str, settings: Settings | None = None, root: str = "source") -> Path:
    """Resolve a user-supplied relative path inside the named root, rejecting traversal."""
    base = _root(settings, root)
    cleaned = normalize_path(relative_path or "")
    if cleaned.split(ARCHIVE_SEP)[0] != cleaned:
        cleaned = cleaned.split(ARCHIVE_SEP)[0]
    candidate = (base / cleaned).resolve() if cleaned else base
    if candidate != base and base not in candidate.parents:
        raise MBoxPathError(f"Path escapes the {root} root: {relative_path!r}")
    return candidate


def browse(
    study: str | None = None,
    country: str | None = None,
    site: str | None = None,
    settings: Settings | None = None,
) -> list[MBoxEntry]:
    """List the immediate children of ``<root>/<study>/<country>/<site>``."""
    parts = [p for p in (study, country, site) if p]
    return browse_path("/".join(parts), settings)


def _is_internal(path: Path, base: Path) -> bool:
    """Sidecar metadata and dot-files are bookkeeping, not migrated documents."""
    relative = path.relative_to(base)
    return any(part.startswith(("_", ".")) for part in relative.parts)


def browse_path(relative_path: str, settings: Settings | None = None, root: str = "source") -> list[MBoxEntry]:
    """List the immediate children of an arbitrary folder below the named root."""
    target = resolve_within_root(relative_path, settings, root)
    if not target.exists() or not target.is_dir():
        return []
    base = _root(settings, root)
    entries: list[MBoxEntry] = []
    for child in sorted(target.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
        if child.name.startswith(("_", ".")):
            continue
        stat = child.stat()
        entries.append(
            MBoxEntry(
                name=child.name,
                relative_path=child.relative_to(base).as_posix(),
                is_dir=child.is_dir(),
                size_bytes=None if child.is_dir() else stat.st_size,
                modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
            )
        )
    return entries


def count_files(settings: Settings | None = None, root: str = "source") -> int:
    base = _root(settings, root)
    if not base.exists():
        return 0
    return sum(1 for p in base.rglob("*") if p.is_file() and not _is_internal(p, base))


def iter_source_files(settings: Settings | None = None) -> Iterator[Path]:
    """Yield every regular file below MBOX_ROOT."""
    root = _root(settings)
    if not root.exists():
        return
    for path in sorted(root.rglob("*")):
        if path.is_file():
            yield path


def relative_of(path: Path, settings: Settings | None = None) -> str:
    return path.resolve().relative_to(_root(settings)).as_posix()


@contextmanager
def materialize(source_path: str, settings: Settings | None = None) -> Iterator[Path]:
    """Yield a real filesystem path for ``source_path``.

    Plain files are yielded in place. Archive members (``archive.zip!/inner.pdf``) are
    extracted into a temporary staging directory that is removed on exit.
    """
    settings = settings or get_settings()
    if ARCHIVE_SEP not in source_path:
        real = resolve_within_root(source_path, settings)
        if not real.is_file():
            raise FileNotFoundError(f"Source file not found: {source_path}")
        yield real
        return

    outer = source_path.split(ARCHIVE_SEP)[0]
    archive = resolve_within_root(outer, settings)
    if not archive.is_file():
        raise FileNotFoundError(f"Archive not found: {outer}")

    staging = Path(tempfile.mkdtemp(prefix="mbox-stage-"))
    try:
        extracted = extract_archive(
            archive,
            staging,
            virtual_prefix=normalize_path(relative_of(archive, settings)),
            max_depth=settings.zip_max_depth,
            max_file_bytes=settings.max_file_bytes,
        )
        wanted = normalize_path(relative_to_root_virtual(source_path, settings))
        for item in extracted:
            if normalize_path(item.virtual_path) == wanted:
                yield item.real_path
                return
        raise CorruptArchiveError(f"Archive member not found: {source_path}")
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def relative_to_root_virtual(source_path: str, settings: Settings | None = None) -> str:
    """Express a (possibly archive-virtual) path relative to MBOX_ROOT."""
    settings = settings or get_settings()
    root_name = normalize_path(str(_root(settings)))
    cleaned = normalize_path(source_path)
    if cleaned.lower().startswith(root_name.lower() + "/"):
        return cleaned[len(root_name) + 1 :]
    return cleaned


def describe(source_path: str, settings: Settings | None = None):
    """Parse ``source_path`` into a :class:`~app.libs.common.path_parser.PathInfo`."""
    settings = settings or get_settings()
    return parse_path(relative_to_root_virtual(source_path, settings), "")
