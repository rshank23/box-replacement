"""Filesystem-backed Vault client for the migration proof of concept.

Instead of calling Veeva Vault, documents are filed into a destination folder tree.
It honours the same contracts as the real client -- rate limiting, idempotency on
``external_id__v``, and the Unclassified fallback -- so the pipeline under test is
the production pipeline, only the sink differs.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from aiolimiter import AsyncLimiter

from .config import Settings, get_settings
from .hashing import md5_file
from .logging_config import get_logger
from .metrics import VAULT_API_LATENCY
from .vault_client import (
    FIELD_EXTERNAL_ID,
    FIELD_NAME,
    FIELD_SITE,
    FIELD_STUDY,
    FIELD_STUDY_COUNTRY,
    FIELD_SUBTYPE,
    FIELD_TYPE,
    FakeVaultClient,
    VaultApiError,
)

log = get_logger(__name__)

INDEX_FILE = "_index.json"
METADATA_DIR = "_metadata"


def _safe_segment(value: Any, fallback: str) -> str:
    """Turn a metadata value into a single, safe folder or file name."""
    text = str(value or "").strip()
    for bad in '<>:"/\\|?*':
        text = text.replace(bad, "-")
    while ".." in text:
        text = text.replace("..", "")
    text = text.strip(". -")
    return text or fallback


class FilesystemVaultClient:
    """Files documents into ``DESTINATION_ROOT`` or ``UNCLASSIFIED_ROOT``."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.destination = Path(self.settings.destination_root).expanduser().resolve()
        self.unclassified = Path(self.settings.unclassified_root).expanduser().resolve()
        self._limiter = AsyncLimiter(max_rate=max(self.settings.rate_limit_per_sec, 0.1), time_period=1)
        self._authenticated = False
        self._reference = FakeVaultClient()
        self.destination.mkdir(parents=True, exist_ok=True)
        self.unclassified.mkdir(parents=True, exist_ok=True)
        self._index: dict[str, Any] = self._load_index()

    # ------------------------------------------------------------------ index
    @property
    def _index_path(self) -> Path:
        return self.destination / INDEX_FILE

    def _load_index(self) -> dict[str, Any]:
        try:
            return json.loads(self._index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"sequence": 0, "documents": {}, "by_external_id": {}}

    def _save_index(self) -> None:
        self._index_path.write_text(json.dumps(self._index, indent=2), encoding="utf-8")

    # ------------------------------------------------------------ interface
    async def authenticate(self) -> None:
        self._authenticated = True

    async def keep_alive(self) -> None:
        return None

    async def end_session(self) -> None:
        self._authenticated = False

    async def stage_file(self, path: str | Path) -> dict:
        p = Path(path)
        if not p.is_file():
            raise VaultApiError(f"Staged source not found: {p}")
        # Staging is a no-op on a local filesystem; keep the real path for the copy.
        return {"path": str(p), "name": p.name, "size": p.stat().st_size}

    async def create_document(self, metadata: dict, file_or_staged: Any) -> str:
        if not self._authenticated:
            await self.authenticate()

        source = Path(
            file_or_staged["path"] if isinstance(file_or_staged, dict) else file_or_staged
        )
        if not source.is_file():
            raise VaultApiError(f"Source file not found: {source}")

        external_id = str(metadata.get(FIELD_EXTERNAL_ID) or "")
        existing = self._index["by_external_id"].get(external_id)
        if external_id and existing:
            return existing

        async with self._limiter:
            started = time.perf_counter()
            try:
                document_id, target = self._write(metadata, source)
            finally:
                VAULT_API_LATENCY.labels(operation="create_document").observe(time.perf_counter() - started)

        log.info(
            "poc_document_filed",
            document_id=document_id,
            target=str(target),
            unclassified=self._is_unclassified(metadata),
        )
        return document_id

    def _is_unclassified(self, metadata: dict) -> bool:
        return str(metadata.get(FIELD_TYPE) or "") == self.settings.unclassified_document_type

    def _target_path(self, metadata: dict, file_name: str) -> Path:
        study = _safe_segment(metadata.get(FIELD_STUDY), "UNKNOWN-STUDY")
        country = _safe_segment(metadata.get(FIELD_STUDY_COUNTRY), "UNKNOWN-COUNTRY")
        site = _safe_segment(metadata.get(FIELD_SITE), "UNKNOWN-SITE")

        if self._is_unclassified(metadata):
            return self.unclassified / study / country / site / file_name

        doc_type = _safe_segment(metadata.get(FIELD_TYPE), "Unfiled")
        subtype = _safe_segment(metadata.get(FIELD_SUBTYPE), "Unfiled")
        return self.destination / study / country / site / doc_type / subtype / file_name

    def _write(self, metadata: dict, source: Path) -> tuple[str, Path]:
        file_name = _safe_segment(metadata.get(FIELD_NAME) or source.name, source.name)
        target = self._target_path(metadata, file_name)

        root = self.unclassified if self._is_unclassified(metadata) else self.destination
        if root not in target.resolve().parents:
            raise VaultApiError(f"Refusing to file outside {root}: {target}")

        target.parent.mkdir(parents=True, exist_ok=True)

        # Identical content at the same target is the same document: overwrite rather than
        # accumulate "(2)" copies, which is exactly the duplication this pipeline prevents.
        if target.exists() and md5_file(target) != md5_file(source):
            stem, suffix = target.stem, target.suffix
            counter = 2
            while target.exists():
                target = target.with_name(f"{stem} ({counter}){suffix}")
                counter += 1

        shutil.copy2(source, target)

        self._index["sequence"] += 1
        document_id = f"FS-{self._index['sequence']:06d}"
        record = {
            "id": document_id,
            "path": str(target),
            "size": target.stat().st_size,
            "checksum": md5_file(target),
            "unclassified": self._is_unclassified(metadata),
            "metadata": {k: v for k, v in metadata.items()},
        }
        self._index["documents"][document_id] = record
        external_id = str(metadata.get(FIELD_EXTERNAL_ID) or "")
        if external_id:
            self._index["by_external_id"][external_id] = document_id

        metadata_dir = (self.unclassified if record["unclassified"] else self.destination) / METADATA_DIR
        metadata_dir.mkdir(parents=True, exist_ok=True)
        (metadata_dir / f"{document_id}.json").write_text(
            json.dumps(record, indent=2), encoding="utf-8"
        )
        self._save_index()
        return document_id, target

    async def get_document_info(self, vault_document_id: str) -> dict:
        record = self._index["documents"].get(vault_document_id)
        if record is None:
            raise VaultApiError(f"Document {vault_document_id} not found", status_code=404)
        return {
            "id": record["id"],
            "size": record["size"],
            "checksum": record["checksum"],
            "status": "Filed",
            "metadata": record["metadata"],
        }

    async def find_document_by_external_id(self, external_id: str) -> str | None:
        return self._index["by_external_id"].get(external_id)

    async def get_picklist_values(self, picklist_name: str) -> list[dict]:
        return await self._reference.get_picklist_values(picklist_name)

    async def get_document_type_hierarchy(self) -> dict[str, list[dict]]:
        return await self._reference.get_document_type_hierarchy()

    async def get_api_versions(self) -> dict[str, str]:
        return {"filesystem": str(self.destination)}

    async def aclose(self) -> None:
        return None

    # ------------------------------------------------------------------ POC
    def reset(self) -> dict[str, int]:
        """Empty both sinks so the demo can be re-run. Does not touch the audit trail."""
        removed = 0
        for root in (self.destination, self.unclassified):
            for child in root.iterdir():
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
                removed += 1
        self._index = {"sequence": 0, "documents": {}, "by_external_id": {}}
        self._save_index()
        return {"removed_entries": removed}
