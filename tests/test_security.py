from __future__ import annotations

import os

import pytest

from app.libs.common.config import get_settings
from app.libs.common.security import issue_dev_token


@pytest.fixture
def auth_on():
    os.environ["AUTH_ENABLED"] = "true"
    get_settings.cache_clear()
    yield
    os.environ["AUTH_ENABLED"] = "false"
    get_settings.cache_clear()


async def test_admin_endpoint_requires_authentication(api_client, auth_on):
    response = await api_client.post("/api/mappings", json={"source_pattern": "X"})
    assert response.status_code == 401


async def test_admin_endpoint_rejects_non_admin_role(api_client, auth_on):
    token = issue_dev_token("alice", ["tmf_reader"])
    response = await api_client.post(
        "/api/mappings",
        json={"source_pattern": "STUDY-500/US/S/D", "document_type": "Trial Management"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


async def test_admin_endpoint_accepts_admin_role(api_client, auth_on):
    token = issue_dev_token("carol", ["tmf_admin"])
    response = await api_client.post(
        "/api/mappings",
        json={"source_pattern": "STUDY-501/US/S/D", "document_type": "Trial Management"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201
    assert response.json()["created_by"] == "carol"


async def test_tampered_token_is_rejected(api_client, auth_on):
    token = issue_dev_token("mallory", ["tmf_admin"])
    response = await api_client.get("/api/mappings", headers={"Authorization": f"Bearer {token}x"})
    assert response.status_code == 401


async def test_dev_token_endpoint_is_disabled_by_default(api_client):
    response = await api_client.post("/api/admin/dev-token", json={"subject": "dev", "roles": ["tmf_admin"]})
    assert response.status_code == 404
