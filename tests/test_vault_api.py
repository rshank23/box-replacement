"""Contract tests for the Veeva Vault v26.2 endpoints this backend calls."""

from __future__ import annotations

import httpx
import pytest

from app.libs.common.config import Settings
from app.libs.common.vault_client import (
    FIELD_EXTERNAL_ID,
    STAGING_SIMPLE_LIMIT_BYTES,
    VaultAuthError,
    VaultJobError,
    VaultRequestError,
)
from app.libs.common.vault_client_veeva import VeevaVaultClient

SUCCESS = {"responseStatus": "SUCCESS"}


def _settings(**overrides) -> Settings:
    base = dict(
        vault_client="real",
        vault_base_url="https://myvault.veevavault.com",
        vault_api_version="v26.2",
        vault_auth_mode="password",
        vault_username="svc",
        vault_password="not-a-real-password",
        retry_backoff_s="0,0",
        retry_max_attempts=2,
        rate_limit_per_sec=1000,
        circuit_breaker_fail_max=50,
        vault_job_timeout_s=1,
    )
    base.update(overrides)
    return Settings(**base)


def _client(handler, **overrides) -> VeevaVaultClient:
    return VeevaVaultClient(
        _settings(**overrides), client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


# ------------------------------------------------------------- authentication
async def test_password_auth_sends_session_id_without_bearer():
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth"):
            return httpx.Response(200, json={**SUCCESS, "sessionId": "SESS", "vaultId": 1776})
        seen.append(request.headers.get("Authorization"))
        return httpx.Response(200, json={**SUCCESS, "data": []})

    client = _client(handler)
    await client.authenticate()
    await client.find_document_by_external_id("abc")
    assert seen == ["SESS"]
    assert client.vault_id == 1776
    await client.aclose()


async def test_token_auth_sends_bearer_and_skips_the_auth_endpoint():
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert not request.url.path.endswith("/auth"), "token mode must not call /auth"
        seen.append(request.headers.get("Authorization"))
        return httpx.Response(200, json={**SUCCESS, "values": {"v26.2": "https://x/api/v26.2"}})

    client = _client(handler, vault_auth_mode="token", vault_api_token="veeva-vault-abc123")
    await client.authenticate()
    assert seen == ["Bearer veeva-vault-abc123"]
    await client.aclose()


async def test_token_mode_requires_a_token():
    client = _client(lambda r: httpx.Response(200, json=SUCCESS), vault_auth_mode="token", vault_api_token="")
    with pytest.raises(VaultAuthError):
        await client.authenticate()
    await client.aclose()


async def test_authentication_defaulting_to_the_wrong_vault_is_refused():
    """Vault may authenticate against a different Vault when the intended one is inactive."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                **SUCCESS,
                "sessionId": "SESS",
                "vaultId": 1782,
                "vaultIds": [{"id": 1776, "name": "TMF"}, {"id": 1782, "name": "Platform"}],
            },
        )

    client = _client(handler, vault_expected_vault_id=1776)
    with pytest.raises(VaultAuthError, match="expected 1776"):
        await client.authenticate()
    await client.aclose()


async def test_keep_alive_and_end_session():
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path.endswith("/auth"):
            return httpx.Response(200, json={**SUCCESS, "sessionId": "SESS"})
        return httpx.Response(200, json=SUCCESS)

    client = _client(handler)
    await client.authenticate()
    await client.keep_alive()
    await client.end_session()

    assert ("POST", "/api/v26.2/keep-alive") in calls
    assert ("DELETE", "/api/v26.2/session") in calls
    await client.aclose()


async def test_invalid_session_error_surfaces_as_auth_error():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth"):
            return httpx.Response(200, json={**SUCCESS, "sessionId": "SESS"})
        return httpx.Response(
            200, json={"responseStatus": "FAILURE", "errors": [{"type": "INVALID_SESSION_ID", "message": "x"}]}
        )

    client = _client(handler)
    await client.authenticate()
    with pytest.raises(VaultAuthError):
        await client.find_document_by_external_id("abc")
    await client.aclose()


# ------------------------------------------------------------------ documents
async def test_create_document_uses_the_documents_endpoint_and_returns_the_id():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth"):
            return httpx.Response(200, json={**SUCCESS, "sessionId": "SESS"})
        assert request.url.path == "/api/v26.2/objects/documents"
        body = request.content.decode(errors="ignore")
        assert FIELD_EXTERNAL_ID in body
        return httpx.Response(200, json={**SUCCESS, "id": 773})

    client = _client(handler)
    await client.authenticate()
    doc_id = await client.create_document({FIELD_EXTERNAL_ID: "abc", "name__v": "d.pdf"}, {"path": "/d.pdf"})
    assert doc_id == "773"
    await client.aclose()


async def test_migration_mode_headers_are_sent_when_enabled():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth"):
            return httpx.Response(200, json={**SUCCESS, "sessionId": "SESS"})
        seen.update(request.headers)
        return httpx.Response(200, json={**SUCCESS, "id": 1})

    client = _client(handler, vault_migration_mode=True, vault_no_triggers=True)
    await client.authenticate()
    await client.create_document({"name__v": "d.pdf"}, {"path": "/d.pdf"})
    assert seen.get("x-vaultapi-migrationmode") == "true"
    assert seen.get("x-vaultapi-notriggers") == "true"
    await client.aclose()


async def test_get_document_info_reads_the_versioned_field_names():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth"):
            return httpx.Response(200, json={**SUCCESS, "sessionId": "SESS"})
        return httpx.Response(
            200,
            json={
                **SUCCESS,
                "document": {
                    "id": 450,
                    "size__v": 11599,
                    "md5checksum__v": "94e18bdbcf695c905a5968429e0c5204",
                    "status__v": "Draft",
                    "format__v": "application/pdf",
                },
            },
        )

    client = _client(handler)
    await client.authenticate()
    info = await client.get_document_info("450")
    assert info["size"] == 11599
    assert info["checksum"] == "94e18bdbcf695c905a5968429e0c5204"
    assert info["status"] == "Draft"
    await client.aclose()


# --------------------------------------------------------------- file staging
async def test_small_file_uses_simple_staging(tmp_path):
    source = tmp_path / "small.pdf"
    source.write_bytes(b"x" * 1024)
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/auth"):
            return httpx.Response(200, json={**SUCCESS, "sessionId": "SESS"})
        return httpx.Response(200, json={**SUCCESS, "data": {"path": "/small.pdf"}})

    client = _client(handler)
    await client.authenticate()
    staged = await client.stage_file(source)

    assert staged["path"] == "/small.pdf"
    assert "/api/v26.2/services/file_staging/items" in paths
    assert not any("file_staging/upload" in p for p in paths)
    await client.aclose()


async def test_large_file_uses_a_resumable_upload_session(tmp_path, monkeypatch):
    monkeypatch.setattr("app.libs.common.vault_client_veeva.STAGING_SIMPLE_LIMIT_BYTES", 1024)
    source = tmp_path / "large.pdf"
    source.write_bytes(b"y" * 4096)

    parts: list[int] = []
    committed: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/auth"):
            return httpx.Response(200, json={**SUCCESS, "sessionId": "SESS"})
        if path == "/api/v26.2/services/file_staging/upload" and request.method == "POST":
            return httpx.Response(200, json={**SUCCESS, "data": {"id": "SESSION1", "path": "/large.pdf"}})
        if path.startswith("/api/v26.2/services/file_staging/upload/") and request.method == "PUT":
            parts.append(int(request.headers["X-VaultAPI-FilePartNumber"]))
            assert request.headers["Content-Type"] == "application/octet-stream"
            return httpx.Response(200, json={**SUCCESS, "data": {"part_number": parts[-1]}})
        if path.startswith("/api/v26.2/services/file_staging/upload/") and request.method == "POST":
            committed.append(path)
            return httpx.Response(200, json={**SUCCESS, "data": {"job_id": 100954}})
        if path.startswith("/api/v26.2/services/jobs/"):
            return httpx.Response(200, json={**SUCCESS, "data": {"id": 100954, "status": "SUCCESS"}})
        raise AssertionError(f"unexpected call {request.method} {path}")

    # 5 MB minimum part size is clamped by the settings property, so one part covers the file.
    client = _client(handler)
    await client.authenticate()
    staged = await client.stage_file(source)

    assert parts == [1]
    assert committed, "the upload session must be committed"
    assert staged["size"] == 4096
    await client.aclose()


async def test_failed_part_upload_aborts_the_session(tmp_path, monkeypatch):
    monkeypatch.setattr("app.libs.common.vault_client_veeva.STAGING_SIMPLE_LIMIT_BYTES", 1024)
    source = tmp_path / "large.pdf"
    source.write_bytes(b"z" * 4096)
    aborted: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/auth"):
            return httpx.Response(200, json={**SUCCESS, "sessionId": "SESS"})
        if path == "/api/v26.2/services/file_staging/upload":
            return httpx.Response(200, json={**SUCCESS, "data": {"id": "SESSION1"}})
        if request.method == "PUT":
            return httpx.Response(400, json={"responseStatus": "FAILURE"})
        if request.method == "DELETE":
            aborted.append(path)
            return httpx.Response(200, json=SUCCESS)
        raise AssertionError(f"unexpected call {request.method} {path}")

    client = _client(handler)
    await client.authenticate()
    with pytest.raises(VaultRequestError):
        await client.stage_file(source)
    assert aborted, "an interrupted session must be aborted so it does not occupy a slot"
    await client.aclose()


# -------------------------------------------------------------------- jobs
async def test_job_failure_is_reported(monkeypatch):
    monkeypatch.setattr("app.libs.common.vault_client_veeva.JOB_POLL_INTERVAL_S", 0)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth"):
            return httpx.Response(200, json={**SUCCESS, "sessionId": "SESS"})
        return httpx.Response(200, json={**SUCCESS, "data": {"id": 1, "status": "ERRORS_ENCOUNTERED"}})

    client = _client(handler)
    await client.authenticate()
    with pytest.raises(VaultJobError):
        await client.await_job(1)
    await client.aclose()


# --------------------------------------------------------- reference data
async def test_document_type_hierarchy_is_walked_to_classifications():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/auth"):
            return httpx.Response(200, json={**SUCCESS, "sessionId": "SESS"})
        if path == "/api/v26.2/metadata/objects/documents/types":
            return httpx.Response(
                200,
                json={
                    **SUCCESS,
                    "types": [
                        {
                            "label": "Trial Management",
                            "value": "https://v/api/v26.2/metadata/objects/documents/types/trial__c",
                        }
                    ],
                },
            )
        if path == "/api/v26.2/metadata/objects/documents/types/trial__c":
            return httpx.Response(
                200,
                json={
                    **SUCCESS,
                    "subtypes": [
                        {
                            "label": "Monitoring Plan",
                            "value": "https://v/api/v26.2/metadata/objects/documents/types/trial__c/subtypes/mon__c",
                        }
                    ],
                },
            )
        if path == "/api/v26.2/metadata/objects/documents/types/trial__c/subtypes/mon__c":
            return httpx.Response(200, json={**SUCCESS, "classifications": [{"label": "Essential Document"}]})
        raise AssertionError(f"unexpected call {path}")

    client = _client(handler)
    await client.authenticate()
    hierarchy = await client.get_document_type_hierarchy()

    assert [t["label"] for t in hierarchy["types"]] == ["Trial Management"]
    assert hierarchy["subtypes"][0]["type"] == "Trial Management"
    assert hierarchy["classifications"][0]["name"] == "Essential Document"
    assert hierarchy["classifications"][0]["subtype"] == "Monitoring Plan"
    await client.aclose()


async def test_picklist_values_are_parsed():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth"):
            return httpx.Response(200, json={**SUCCESS, "sessionId": "SESS"})
        assert request.url.path == "/api/v26.2/objects/picklists/country__v"
        return httpx.Response(
            200, json={**SUCCESS, "picklistValues": [{"name": "us__v", "label": "United States"}]}
        )

    client = _client(handler)
    await client.authenticate()
    values = await client.get_picklist_values("country__v")
    assert values == [{"name": "us__v", "label": "United States"}]
    await client.aclose()


def test_staging_limit_matches_the_documented_50mb():
    assert STAGING_SIMPLE_LIMIT_BYTES == 50 * 1024 * 1024


def test_file_part_size_is_clamped_to_the_documented_range():
    assert _settings(vault_file_part_mb=1).vault_file_part_bytes == 5 * 1024 * 1024
    assert _settings(vault_file_part_mb=999).vault_file_part_bytes == 52 * 1024 * 1024
