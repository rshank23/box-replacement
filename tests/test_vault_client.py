from __future__ import annotations

import httpx
import pytest

from app.libs.common.config import Settings
from app.libs.common.vault_client import VaultApiError, VaultCircuitOpenError, VaultRateLimitError
from app.libs.common.vault_client_veeva import VeevaVaultClient


def _settings(**overrides) -> Settings:
    base = dict(
        vault_client="real",
        vault_base_url="https://vault.test",
        vault_api_version="v25.1",
        vault_username="svc",
        vault_password="not-a-real-password",
        retry_backoff_s="0,0",
        retry_max_attempts=3,
        rate_limit_per_sec=1000,
        circuit_breaker_fail_max=2,
        circuit_breaker_reset_s=60,
    )
    base.update(overrides)
    return Settings(**base)


def _client(handler, **overrides) -> VeevaVaultClient:
    transport = httpx.MockTransport(handler)
    return VeevaVaultClient(_settings(**overrides), client=httpx.AsyncClient(transport=transport))


async def test_authenticate_stores_session_and_sends_it_on_later_calls():
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth"):
            return httpx.Response(200, json={"responseStatus": "SUCCESS", "sessionId": "sess-123"})
        seen.append(request.headers.get("Authorization"))
        return httpx.Response(200, json={"responseStatus": "SUCCESS", "data": []})

    client = _client(handler)
    await client.authenticate()
    await client.find_document_by_external_id("abc")
    assert seen == ["sess-123"]
    await client.aclose()


async def test_rate_limit_is_retried_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth"):
            return httpx.Response(200, json={"responseStatus": "SUCCESS", "sessionId": "s"})
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429)
        return httpx.Response(200, json={"responseStatus": "SUCCESS", "data": [{"id": "VDOC-1"}]})

    client = _client(handler, circuit_breaker_fail_max=10)
    assert await client.find_document_by_external_id("abc") == "VDOC-1"
    assert calls["n"] == 3
    await client.aclose()


async def test_rate_limit_surfaces_after_retries_are_exhausted():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth"):
            return httpx.Response(200, json={"responseStatus": "SUCCESS", "sessionId": "s"})
        return httpx.Response(429)

    client = _client(handler, circuit_breaker_fail_max=100)
    with pytest.raises(VaultRateLimitError):
        await client.find_document_by_external_id("abc")
    await client.aclose()


async def test_client_errors_are_not_retried():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth"):
            return httpx.Response(200, json={"responseStatus": "SUCCESS", "sessionId": "s"})
        calls["n"] += 1
        return httpx.Response(400, json={"responseStatus": "FAILURE"})

    client = _client(handler)
    with pytest.raises(VaultApiError):
        await client.find_document_by_external_id("abc")
    assert calls["n"] == 1
    await client.aclose()


async def test_circuit_breaker_opens_after_repeated_server_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth"):
            return httpx.Response(200, json={"responseStatus": "SUCCESS", "sessionId": "s"})
        return httpx.Response(503)

    client = _client(handler, retry_max_attempts=1, circuit_breaker_fail_max=2)
    with pytest.raises(VaultApiError):
        await client.find_document_by_external_id("a")
    with pytest.raises(VaultCircuitOpenError):
        await client.find_document_by_external_id("b")
    # The open breaker now short-circuits without touching the network.
    with pytest.raises(VaultCircuitOpenError):
        await client.find_document_by_external_id("c")
    await client.aclose()


async def test_missing_credentials_are_rejected_before_any_request():
    from app.libs.common.vault_client import VaultAuthError

    client = _client(lambda r: httpx.Response(200, json={}), vault_username="", vault_password="")
    with pytest.raises(VaultAuthError):
        await client.authenticate()
    await client.aclose()
