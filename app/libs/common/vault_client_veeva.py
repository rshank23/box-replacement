"""Veeva Vault REST client (API v26.2).

Endpoints used:
  POST   /api/{v}/auth                                        User name and password
  POST   /api/{v}/keep-alive                                  Session keep alive
  DELETE /api/{v}/session                                     End session
  GET    /api/                                                Retrieve API versions
  POST   /api/{v}/query                                       VQL
  GET    /api/{v}/objects/picklists/{picklist}                Retrieve picklist values
  GET    /api/{v}/metadata/objects/documents/types            Retrieve all document types
  GET    /api/{v}/metadata/objects/documents/types/{t}        Retrieve document type (subtypes)
  GET    /api/{v}/metadata/objects/documents/types/{t}/subtypes/{s}   Classifications
  POST   /api/{v}/objects/documents                           Create single document
  GET    /api/{v}/objects/documents/{id}                      Retrieve document
  POST   /api/{v}/services/file_staging/items                 Create file (<= 50 MB)
  POST   /api/{v}/services/file_staging/upload                Create resumable upload session
  PUT    /api/{v}/services/file_staging/upload/{session}      Upload file part
  POST   /api/{v}/services/file_staging/upload/{session}      Commit upload session
  GET    /api/{v}/services/jobs/{job_id}                      Retrieve job status
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import pybreaker
from aiolimiter import AsyncLimiter
from tenacity import AsyncRetrying, RetryError, retry_if_exception_type, stop_after_attempt, wait_exponential

from .config import Settings, get_settings
from .logging_config import get_logger
from .metrics import VAULT_API_LATENCY, VAULT_CIRCUIT_OPEN
from .vault_client import (
    FIELD_EXTERNAL_ID,
    FIELD_FORMAT,
    FIELD_MD5,
    FIELD_SIZE,
    FIELD_STATUS,
    JOB_POLL_INTERVAL_S,
    STAGING_SIMPLE_LIMIT_BYTES,
    VaultApiError,
    VaultAuthError,
    VaultCircuitOpenError,
    VaultJobError,
    VaultRateLimitError,
    VaultRequestError,
)

log = get_logger(__name__)

_RETRYABLE = (VaultRateLimitError, httpx.TransportError, httpx.TimeoutException)

#: Vault terminates a session after inactivity; the hard ceiling is 48 hours.
MAX_SESSION_LIFETIME_S = 47 * 3600


class _BreakerListener(pybreaker.CircuitBreakerListener):
    def state_change(self, cb, old_state, new_state):  # noqa: ANN001, ANN201
        VAULT_CIRCUIT_OPEN.set(1 if new_state.name == "open" else 0)
        log.warning("vault_circuit_state_change", old=getattr(old_state, "name", None), new=new_state.name)


class VeevaVaultClient:
    """Vault REST client with rate limiting, retry/backoff and a circuit breaker."""

    def __init__(self, settings: Settings | None = None, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings or get_settings()
        self._base = self.settings.vault_base_url.rstrip("/")
        self._api = f"{self._base}/api/{self.settings.vault_api_version}"
        self._session_id: str | None = None
        self._session_started: float = 0.0
        self._last_activity: float = 0.0
        self.vault_id: int | None = None
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0))
        self._limiter = AsyncLimiter(max_rate=max(self.settings.rate_limit_per_sec, 0.1), time_period=1)
        self._breaker = pybreaker.CircuitBreaker(
            fail_max=self.settings.circuit_breaker_fail_max,
            reset_timeout=self.settings.circuit_breaker_reset_s,
            exclude=[VaultRequestError],
            listeners=[_BreakerListener()],
        )

    # ------------------------------------------------------------------ core
    def _auth_value(self) -> str | None:
        """API access tokens are the recommended credential; sessions are the fallback."""
        if self.settings.vault_auth_mode == "token":
            return f"Bearer {self.settings.vault_api_token}" if self.settings.vault_api_token else None
        return self._session_id

    def _headers(self, extra: dict | None = None) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        auth = self._auth_value()
        if auth:
            headers["Authorization"] = auth
        if extra:
            headers.update(extra)
        return headers

    @staticmethod
    def _raise_for_vault_status(response: httpx.Response, operation: str) -> dict:
        if response.status_code == 429:
            raise VaultRateLimitError(f"Vault rate limit hit during {operation}")
        if response.status_code >= 500:
            raise VaultApiError(f"Vault server error during {operation}", status_code=response.status_code)
        if response.status_code >= 400:
            raise VaultRequestError(
                f"Vault rejected {operation} (HTTP {response.status_code})", status_code=response.status_code
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise VaultApiError(f"Non-JSON response during {operation}") from exc
        if payload.get("responseStatus") == "FAILURE":
            errors = payload.get("errors") or []
            codes = [e.get("type") for e in errors]
            message = "; ".join(str(e.get("message")) for e in errors) or "unspecified error"
            if "API_LIMIT_EXCEEDED" in codes:
                raise VaultRateLimitError(f"Vault API limit exceeded during {operation}")
            if "INVALID_SESSION_ID" in codes:
                raise VaultAuthError(f"Vault session is no longer valid during {operation}")
            raise VaultRequestError(
                f"Vault {operation} failed: {message}", status_code=response.status_code, payload=payload
            )
        return payload

    async def _request(self, method: str, url: str, operation: str, **kwargs: Any) -> dict:
        extra_headers = kwargs.pop("headers", None)

        async def _call() -> dict:
            async with self._limiter:
                started = time.perf_counter()
                try:
                    response = await self._client.request(
                        method, url, headers=self._headers(extra_headers), **kwargs
                    )
                finally:
                    VAULT_API_LATENCY.labels(operation=operation).observe(time.perf_counter() - started)
                self._last_activity = time.time()
                return self._raise_for_vault_status(response, operation)

        backoff = self.settings.retry_backoff_schedule or [1]
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self.settings.retry_max_attempts),
                wait=wait_exponential(multiplier=backoff[0], max=backoff[-1]),
                retry=retry_if_exception_type(_RETRYABLE),
                reraise=True,
            ):
                with attempt:
                    try:
                        # pybreaker's context-manager form gates entry and records the
                        # outcome, which keeps the async call outside the breaker's lock.
                        with self._breaker.calling():
                            return await _call()
                    except pybreaker.CircuitBreakerError as exc:
                        raise VaultCircuitOpenError(str(exc)) from exc
        except RetryError as exc:  # pragma: no cover - reraise=True makes this unlikely
            raise VaultApiError(f"Vault {operation} exhausted retries") from exc
        raise VaultApiError(f"Vault {operation} produced no response")

    async def _ensure_session(self) -> None:
        if self.settings.vault_auth_mode == "token":
            if not self.settings.vault_api_token:
                raise VaultAuthError("VAULT_API_TOKEN is not configured")
            return
        if self._session_id is None or time.time() - self._session_started > MAX_SESSION_LIFETIME_S:
            await self.authenticate()

    # ---------------------------------------------------------- authentication
    async def authenticate(self) -> None:
        """Establish a session, or validate the configured API access token."""
        if self.settings.vault_auth_mode == "token":
            if not self.settings.vault_api_token:
                raise VaultAuthError("VAULT_API_TOKEN is not configured")
            await self.get_api_versions()
            log.info("vault_authenticated", vault=self._base, mode="token")
            return

        if not self.settings.vault_username or not self.settings.vault_password:
            raise VaultAuthError("VAULT_USERNAME / VAULT_PASSWORD are not configured")

        payload = await self._request(
            "POST",
            f"{self._api}/auth",
            operation="authenticate",
            data={"username": self.settings.vault_username, "password": self.settings.vault_password},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        session_id = payload.get("sessionId")
        if not session_id:
            raise VaultAuthError("Vault authentication response contained no sessionId")

        self._session_id = session_id
        self._session_started = time.time()
        self._last_activity = self._session_started
        self.vault_id = payload.get("vaultId")
        self._assert_expected_vault(payload)
        log.info("vault_authenticated", vault=self._base, mode="password", vault_id=self.vault_id)

    def _assert_expected_vault(self, payload: dict) -> None:
        """Guard against authentication defaulting silently landing us in the wrong Vault."""
        expected = self.settings.vault_expected_vault_id
        if expected is None:
            return
        if self.vault_id != expected:
            available = [v.get("id") for v in (payload.get("vaultIds") or [])]
            raise VaultAuthError(
                f"Authenticated against Vault {self.vault_id}, expected {expected} (available: {available}). "
                "The intended Vault may be inactive; refusing to archive into the wrong Vault."
            )

    async def keep_alive(self) -> None:
        if self.settings.vault_auth_mode == "token" or self._session_id is None:
            return
        await self._request("POST", f"{self._api}/keep-alive", operation="keep_alive")

    async def keep_alive_if_idle(self, idle_seconds: int) -> None:
        if self._session_id and time.time() - self._last_activity > idle_seconds:
            await self.keep_alive()

    async def end_session(self) -> None:
        if self.settings.vault_auth_mode == "token" or self._session_id is None:
            return
        try:
            await self._request("DELETE", f"{self._api}/session", operation="end_session")
        except (VaultApiError, VaultAuthError) as exc:
            log.warning("vault_end_session_failed", error=str(exc))
        finally:
            self._session_id = None

    async def get_api_versions(self) -> dict[str, str]:
        payload = await self._request("GET", f"{self._base}/api/", operation="api_versions")
        return dict(payload.get("values") or {})

    # -------------------------------------------------------------- documents
    async def create_document(self, metadata: dict, file_or_staged: Any) -> str:
        await self._ensure_session()
        fields = {k: ("" if v is None else str(v)) for k, v in metadata.items()}
        headers: dict[str, str] = {}
        if self.settings.vault_migration_mode:
            headers["X-VaultAPI-MigrationMode"] = "true"
            if self.settings.vault_no_triggers:
                headers["X-VaultAPI-NoTriggers"] = "true"

        if isinstance(file_or_staged, dict):
            fields["file"] = str(file_or_staged.get("path"))
            payload = await self._request(
                "POST",
                f"{self._api}/objects/documents",
                operation="create_document",
                data=fields,
                headers=headers or None,
            )
        else:
            p = Path(file_or_staged)
            with open(p, "rb") as fh:
                payload = await self._request(
                    "POST",
                    f"{self._api}/objects/documents",
                    operation="create_document",
                    data=fields,
                    files={"file": (p.name, fh)},
                    headers=headers or None,
                )

        doc_id = payload.get("id") or (payload.get("data") or {}).get("id")
        if doc_id is None:
            raise VaultApiError("Vault document creation returned no id", payload=payload)
        return str(doc_id)

    async def get_document_info(self, vault_document_id: str) -> dict:
        await self._ensure_session()
        payload = await self._request(
            "GET", f"{self._api}/objects/documents/{quote(str(vault_document_id))}", operation="get_document"
        )
        doc = payload.get("document") or {}
        return {
            "id": str(doc.get("id", vault_document_id)),
            "size": doc.get(FIELD_SIZE),
            "checksum": doc.get(FIELD_MD5),
            "status": doc.get(FIELD_STATUS),
            "format": doc.get(FIELD_FORMAT),
            "metadata": doc,
        }

    async def find_document_by_external_id(self, external_id: str) -> str | None:
        """Idempotency probe: resolve an already-created document by its external id."""
        await self._ensure_session()
        safe = external_id.replace("'", "''")
        payload = await self._request(
            "POST",
            f"{self._api}/query",
            operation="query_external_id",
            data={"q": f"SELECT id FROM documents WHERE {FIELD_EXTERNAL_ID} = '{safe}'"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        rows = payload.get("data") or []
        return str(rows[0]["id"]) if rows else None

    # ----------------------------------------------------------- reference data
    async def get_picklist_values(self, picklist_name: str) -> list[dict]:
        await self._ensure_session()
        payload = await self._request(
            "GET",
            f"{self._api}/objects/picklists/{quote(picklist_name)}",
            operation="get_picklist",
        )
        return [
            {"name": item.get("name"), "label": item.get("label")}
            for item in (payload.get("picklistValues") or [])
            if item.get("name")
        ]

    async def get_document_type_hierarchy(self) -> dict[str, list[dict]]:
        """Walk Type > Subtype > Classification.

        `type__v`, `subtype__v` and `classification__v` are a document type hierarchy,
        not picklists, so they must be read from the document metadata API.
        """
        await self._ensure_session()
        types_payload = await self._request(
            "GET", f"{self._api}/metadata/objects/documents/types", operation="get_document_types"
        )

        types: list[dict] = []
        subtypes: list[dict] = []
        classifications: list[dict] = []

        for entry in types_payload.get("types") or []:
            type_label = entry.get("label") or entry.get("name")
            type_name = self._name_from_url(entry.get("value")) or entry.get("name") or type_label
            if not type_label:
                continue
            types.append({"name": type_label, "label": type_label, "api_name": type_name})

            type_payload = await self._request(
                "GET",
                f"{self._api}/metadata/objects/documents/types/{quote(str(type_name))}",
                operation="get_document_type",
            )
            for sub in type_payload.get("subtypes") or []:
                subtype_label = sub.get("label") or sub.get("name")
                subtype_name = self._name_from_url(sub.get("value")) or sub.get("name") or subtype_label
                if not subtype_label:
                    continue
                subtypes.append({"name": subtype_label, "label": subtype_label, "type": type_label})

                sub_payload = await self._request(
                    "GET",
                    f"{self._api}/metadata/objects/documents/types/{quote(str(type_name))}"
                    f"/subtypes/{quote(str(subtype_name))}",
                    operation="get_document_subtype",
                )
                for cls in sub_payload.get("classifications") or []:
                    label = cls.get("label") or cls.get("name")
                    if label:
                        classifications.append(
                            {
                                "name": label,
                                "label": label,
                                "type": type_label,
                                "subtype": subtype_label,
                            }
                        )

        return {"types": types, "subtypes": subtypes, "classifications": classifications}

    @staticmethod
    def _name_from_url(value: Any) -> str | None:
        """Type metadata links carry the API name as the last URL segment."""
        if not isinstance(value, str) or "/" not in value:
            return None
        return value.rstrip("/").rsplit("/", 1)[-1] or None

    # ---------------------------------------------------------- file staging
    async def stage_file(self, path: str | Path) -> dict:
        """Upload to file staging, switching to a resumable session above 50 MB."""
        await self._ensure_session()
        p = Path(path)
        size = p.stat().st_size
        if size > STAGING_SIMPLE_LIMIT_BYTES:
            return await self._stage_file_resumable(p, size)

        with open(p, "rb") as fh:
            payload = await self._request(
                "POST",
                f"{self._api}/services/file_staging/items",
                operation="stage_file",
                data={"kind": "file", "path": f"/{p.name}", "overwrite": "true"},
                files={"file": (p.name, fh)},
            )
        data = payload.get("data") or {}
        return {"path": data.get("path", f"/{p.name}"), "name": p.name, "size": size}

    async def _stage_file_resumable(self, path: Path, size: int) -> dict:
        session = await self._request(
            "POST",
            f"{self._api}/services/file_staging/upload",
            operation="create_upload_session",
            data={"path": f"/{path.name}", "size": str(size), "overwrite": "true"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        data = session.get("data") or {}
        session_id = data.get("id")
        if not session_id:
            raise VaultApiError("Vault returned no upload session id", payload=session)

        part_size = self.settings.vault_file_part_bytes
        part_number = 0
        try:
            with open(path, "rb") as fh:
                while chunk := fh.read(part_size):
                    part_number += 1
                    await self._request(
                        "PUT",
                        f"{self._api}/services/file_staging/upload/{quote(str(session_id), safe='')}",
                        operation="upload_file_part",
                        content=chunk,
                        headers={
                            "Content-Type": "application/octet-stream",
                            "Content-Length": str(len(chunk)),
                            "X-VaultAPI-FilePartNumber": str(part_number),
                        },
                    )

            commit = await self._request(
                "POST",
                f"{self._api}/services/file_staging/upload/{quote(str(session_id), safe='')}",
                operation="commit_upload_session",
                headers={"Content-Type": "application/json"},
            )
        except Exception:
            await self._abort_upload_session(session_id)
            raise

        job_id = (commit.get("data") or {}).get("job_id")
        if job_id is not None:
            await self.await_job(job_id)

        log.info("vault_resumable_upload_complete", file=path.name, parts=part_number, size_bytes=size)
        return {"path": data.get("path", f"/{path.name}"), "name": path.name, "size": size}

    async def _abort_upload_session(self, session_id: str) -> None:
        try:
            await self._request(
                "DELETE",
                f"{self._api}/services/file_staging/upload/{quote(str(session_id), safe='')}",
                operation="abort_upload_session",
            )
        except Exception as exc:
            log.warning("vault_abort_upload_session_failed", error=str(exc), session_id=str(session_id))

    # ----------------------------------------------------------------- jobs
    async def await_job(self, job_id: Any, timeout_s: int | None = None) -> dict:
        """Poll Job Status until it settles. Vault allows one poll per 10s per job."""
        deadline = time.time() + (timeout_s or self.settings.vault_job_timeout_s)
        terminal_ok = {"SUCCESS", "COMPLETED"}
        terminal_bad = {"ERRORS_ENCOUNTERED", "CANCELLED", "TIMEOUT", "MISSED_SCHEDULE"}

        while True:
            payload = await self._request(
                "GET", f"{self._api}/services/jobs/{quote(str(job_id))}", operation="get_job_status"
            )
            data = payload.get("data") or {}
            status = str(data.get("status") or "").upper()
            if status in terminal_ok:
                return data
            if status in terminal_bad:
                raise VaultJobError(f"Vault job {job_id} finished with status {status}")
            if time.time() >= deadline:
                raise VaultJobError(f"Vault job {job_id} did not finish within the timeout (last status {status})")
            await asyncio.sleep(JOB_POLL_INTERVAL_S)

    async def aclose(self) -> None:
        await self._client.aclose()
