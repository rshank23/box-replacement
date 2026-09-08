from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath, PureWindowsPath

#: Separator used to express a file located inside an archive:
#: ``STUDY-001/DE/SITE-101/docs.zip!/subfolder/file.pdf``
ARCHIVE_SEP = "!/"


def normalize_path(path: str) -> str:
    """Normalize a source path to POSIX form without leading/trailing separators."""
    cleaned = str(path).strip().replace("\\", "/")
    while "//" in cleaned:
        cleaned = cleaned.replace("//", "/")
    return cleaned.strip("/")


def relative_to_root(path: str, root: str) -> str:
    """Return ``path`` expressed relative to ``root`` (both normalized)."""
    norm_path = normalize_path(path)
    norm_root = normalize_path(root)
    if norm_root and norm_path.lower().startswith(norm_root.lower() + "/"):
        return norm_path[len(norm_root) + 1 :]
    return norm_path


@dataclass(frozen=True)
class PathInfo:
    """Structured view of an MBox source path."""

    source_path: str
    relative_path: str
    file_name: str
    extension: str
    study: str | None = None
    country: str | None = None
    site: str | None = None
    category: str | None = None
    sub_path: tuple[str, ...] = field(default_factory=tuple)
    archive_chain: tuple[str, ...] = field(default_factory=tuple)

    @property
    def depth(self) -> int:
        """Number of folder levels above the file within the MBox root."""
        return len(PurePosixPath(self.relative_path).parts) - 1

    @property
    def is_inside_archive(self) -> bool:
        return bool(self.archive_chain)

    @property
    def folder_path(self) -> str:
        parent = str(PurePosixPath(self.relative_path).parent)
        return "" if parent == "." else parent

    @property
    def study_folder(self) -> str | None:
        return self.study

    def as_dict(self) -> dict[str, object]:
        return {
            "source_path": self.source_path,
            "relative_path": self.relative_path,
            "file_name": self.file_name,
            "extension": self.extension,
            "study": self.study,
            "country": self.country,
            "site": self.site,
            "category": self.category,
            "sub_path": list(self.sub_path),
            "archive_chain": list(self.archive_chain),
        }


def parse_path(source_path: str, mbox_root: str = "") -> PathInfo:
    """Parse ``<root>/<Study>/<Country>/<Site>/<Category>/.../<file>`` into a :class:`PathInfo`.

    Archive members are expressed with ``!/`` and inherit the hierarchy of the archive
    that contains them, so a document inside a nested ZIP is still classified against
    its Study/Country/Site folder.
    """
    raw = normalize_path(source_path)
    archive_chain: list[str] = []
    if ARCHIVE_SEP in raw:
        segments = raw.split(ARCHIVE_SEP)
        outer = segments[0]
        archive_chain = [PurePosixPath(outer).name, *[PurePosixPath(s).name for s in segments[1:-1]]]
        inner_parts = [p for s in segments[1:] for p in PurePosixPath(s).parts]
        relative_outer = relative_to_root(outer, mbox_root)
        # Drop the archive file itself from the hierarchy, keep its folder + inner path.
        outer_folder = PurePosixPath(relative_outer).parent
        relative = str(outer_folder / PurePosixPath(*inner_parts)) if inner_parts else relative_outer
        relative = normalize_path(relative)
    else:
        relative = relative_to_root(raw, mbox_root)

    parts = PurePosixPath(relative).parts
    file_name = parts[-1] if parts else ""
    folders = list(parts[:-1])

    study = folders[0] if len(folders) > 0 else None
    country = folders[1] if len(folders) > 1 else None
    site = folders[2] if len(folders) > 2 else None
    category = folders[3] if len(folders) > 3 else None
    sub_path = tuple(folders[4:]) if len(folders) > 4 else ()

    extension = PureWindowsPath(file_name).suffix.lower()

    return PathInfo(
        source_path=raw,
        relative_path=relative,
        file_name=file_name,
        extension=extension,
        study=study,
        country=country,
        site=site,
        category=category,
        sub_path=sub_path,
        archive_chain=tuple(archive_chain),
    )
