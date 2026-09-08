from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


class VaultError(Exception):
    """Base class for Vault client failures."""


class VaultAuthError(VaultError):
    pass


class VaultRateLimitError(VaultError):
    pass


class VaultApiError(VaultError):
    def __init__(self, message: str, status_code: int | None = None, payload: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


class VaultRequestError(VaultApiError):
    """A 4xx rejection caused by our request. Never retried, never trips the breaker."""


class VaultCircuitOpenError(VaultError):
    pass


class VaultJobError(VaultError):
    """An asynchronous Vault job finished in a non-SUCCESS state."""


#: Vault document fields used by this integration (v26.2 names).
FIELD_NAME = "name__v"
FIELD_TYPE = "type__v"
FIELD_SUBTYPE = "subtype__v"
FIELD_CLASSIFICATION = "classification__v"
FIELD_LIFECYCLE = "lifecycle__v"
FIELD_STUDY = "study__v"
FIELD_STUDY_COUNTRY = "study_country__v"
FIELD_SITE = "site__v"
FIELD_PRODUCT = "product__v"
FIELD_EXTERNAL_ID = "external_id__v"
FIELD_SIZE = "size__v"
FIELD_MD5 = "md5checksum__v"
FIELD_STATUS = "status__v"
FIELD_FORMAT = "format__v"

#: Fields Vault accepts on an Unclassified document in an eTMF Vault; everything else is ignored.
UNCLASSIFIED_ALLOWED_FIELDS = frozenset(
    {FIELD_TYPE, FIELD_LIFECYCLE, FIELD_PRODUCT, FIELD_STUDY, FIELD_STUDY_COUNTRY, FIELD_SITE}
)

#: Vault rejects a simple file-staging upload above this size; larger files need a session.
STAGING_SIMPLE_LIMIT_BYTES = 50 * 1024 * 1024
#: Job Status may only be polled once every ten seconds per job id.
JOB_POLL_INTERVAL_S = 10


@runtime_checkable
class VaultClient(Protocol):
    """The subset of the Veeva Vault REST API (v26.2) used by this backend."""

    async def authenticate(self) -> None: ...

    async def keep_alive(self) -> None: ...

    async def end_session(self) -> None: ...

    async def stage_file(self, path: str | Path) -> dict: ...

    async def create_document(self, metadata: dict, file_or_staged: Any) -> str: ...

    async def get_document_info(self, vault_document_id: str) -> dict: ...

    async def find_document_by_external_id(self, external_id: str) -> str | None: ...

    async def get_picklist_values(self, picklist_name: str) -> list[dict]: ...

    async def get_document_type_hierarchy(self) -> dict[str, list[dict]]: ...

    async def get_api_versions(self) -> dict[str, str]: ...

    async def aclose(self) -> None: ...


class FakeVaultClient:
    """In-memory stand-in for Vault used by local dev, unit tests and demos.

    Mirrors the real client's contracts: idempotency on ``external_id__v``, the
    Type > Subtype > Classification hierarchy, and the 50 MB staging split.
    """

    DEFAULT_PICKLISTS: dict[str, list[str]] = {
        "study__v": ["STUDY-001", "STUDY-002", "STUDY-003"],
        "country__v": ["US", "DE", "JP", "GB"],
        "site__v": ["SITE-101", "SITE-102", "SITE-201", "SITE-301"],
    }

    #: Type > Subtype > Classification, as returned by the document type metadata API.
    DEFAULT_TYPE_HIERARCHY: dict[str, dict[str, list[str]]] = {
        "Trial Management": {
            "Trial Master File Plan": ["Essential Document", "Supporting Document"],
            "Monitoring Plan": ["Essential Document"],
        },
        "Central Trial Documents": {
            "Investigator Brochure": ["Essential Document", "Supporting Document"],
        },
        "Site Management": {
            "Site Signature Sheet": ["Essential Document"],
        },
        "Safety Reporting": {
            "SAE Report": ["Essential Document", "Correspondence"],
        },
        "Unclassified": {"Unclassified": ["Unclassified"]},
    }

    def __init__(self, latency_s: float = 0.0) -> None:
        self._authenticated = False
        self._latency = latency_s
        self._documents: dict[str, dict] = {}
        self._by_external_id: dict[str, str] = {}
        self._staged: dict[str, dict] = {}
        self._seq = 0
        self.picklists: dict[str, list[str]] = {k: list(v) for k, v in self.DEFAULT_PICKLISTS.items()}
        self.type_hierarchy: dict[str, dict[str, list[str]]] = {
            t: {s: list(c) for s, c in subs.items()} for t, subs in self.DEFAULT_TYPE_HIERARCHY.items()
        }
        self.keep_alive_calls = 0
        self.resumable_uploads = 0

    async def _tick(self) -> None:
        if self._latency:
            await asyncio.sleep(self._latency)

    async def authenticate(self) -> None:
        await self._tick()
        self._authenticated = True

    async def keep_alive(self) -> None:
        await self._tick()
        if not self._authenticated:
            raise VaultAuthError("No active session to keep alive")
        self.keep_alive_calls += 1

    async def end_session(self) -> None:
        await self._tick()
        self._authenticated = False

    async def stage_file(self, path: str | Path) -> dict:
        await self._tick()
        p = Path(path)
        if not p.exists():
            raise VaultApiError(f"Staged source not found: {p}")
        size = p.stat().st_size
        if size > STAGING_SIMPLE_LIMIT_BYTES:
            self.resumable_uploads += 1
        ref = {"path": f"/{p.name}", "name": p.name, "size": size}
        self._staged[ref["path"]] = ref
        return ref

    async def create_document(self, metadata: dict, file_or_staged: Any) -> str:
        await self._tick()
        if not self._authenticated:
            raise VaultAuthError("Not authenticated")
        ext_id = str(metadata.get(FIELD_EXTERNAL_ID) or "")
        if ext_id and ext_id in self._by_external_id:
            return self._by_external_id[ext_id]

        size = 0
        if isinstance(file_or_staged, dict):
            size = int(file_or_staged.get("size") or 0)
        elif file_or_staged is not None:
            p = Path(file_or_staged)
            size = p.stat().st_size if p.exists() else 0

        self._seq += 1
        doc_id = f"VDOC-{self._seq:06d}"
        self._documents[doc_id] = {
            "id": doc_id,
            "size": size,
            "checksum": None,
            "metadata": dict(metadata),
        }
        if ext_id:
            self._by_external_id[ext_id] = doc_id
        return doc_id

    async def get_document_info(self, vault_document_id: str) -> dict:
        await self._tick()
        doc = self._documents.get(vault_document_id)
        if doc is None:
            raise VaultApiError(f"Document {vault_document_id} not found", status_code=404)
        return doc

    async def find_document_by_external_id(self, external_id: str) -> str | None:
        await self._tick()
        return self._by_external_id.get(external_id)

    async def get_picklist_values(self, picklist_name: str) -> list[dict]:
        await self._tick()
        return [{"name": v, "label": v} for v in self.picklists.get(picklist_name, [])]

    async def get_document_type_hierarchy(self) -> dict[str, list[dict]]:
        await self._tick()
        types: list[dict] = []
        subtypes: list[dict] = []
        classifications: list[dict] = []
        for type_label, subs in self.type_hierarchy.items():
            types.append({"name": type_label, "label": type_label})
            for subtype_label, classes in subs.items():
                subtypes.append({"name": subtype_label, "label": subtype_label, "type": type_label})
                for classification in classes:
                    classifications.append(
                        {
                            "name": classification,
                            "label": classification,
                            "type": type_label,
                            "subtype": subtype_label,
                        }
                    )
        return {"types": types, "subtypes": subtypes, "classifications": classifications}

    async def get_api_versions(self) -> dict[str, str]:
        await self._tick()
        return {"v26.2": "https://fake.veevavault.com/api/v26.2"}

    async def aclose(self) -> None:
        return None
