from __future__ import annotations

import uuid


async def test_health_and_openapi_contract(api_client):
    assert (await api_client.get("/health")).status_code == 200

    schema = (await api_client.get("/openapi.json")).json()
    paths = schema["paths"]
    for expected in (
        "/api/mbox/browse",
        "/api/transfers/submit",
        "/api/failures",
        "/api/failures/{failure_id}/resolve",
        "/api/mappings",
        "/api/mappings/{mapping_id}",
        "/api/audit",
        "/api/dashboard/stats",
    ):
        assert expected in paths, f"missing endpoint {expected}"
    assert "post" in paths["/api/transfers/submit"]
    assert "put" in paths["/api/failures/{failure_id}/resolve"]


async def test_browse_lists_studies(api_client):
    response = await api_client.get("/api/mbox/browse")
    assert response.status_code == 200
    names = {entry["name"] for entry in response.json()}
    assert {"STUDY-001", "STUDY-002", "STUDY-003"} <= names


async def test_browse_rejects_path_traversal(api_client):
    response = await api_client.get("/api/mbox/browse", params={"study": "../../etc"})
    assert response.status_code == 400


async def test_submit_and_correlation_id_round_trip(api_client):
    correlation_id = str(uuid.uuid4())
    response = await api_client.post(
        "/api/transfers/submit",
        json={
            "correlation_id": correlation_id,
            "files": [
                {
                    "source_path": "STUDY-001/US/SITE-101/Trial Management/tmf-plan.pdf",
                    "initiated_by": "USER",
                }
            ],
        },
        headers={"X-Correlation-Id": correlation_id},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["submitted"] == 1
    assert body["results"][0]["status"] == "SUCCESS"
    assert body["correlation_id"] == correlation_id
    assert response.headers["X-Correlation-Id"] == correlation_id

    audit = (await api_client.get("/api/audit", params={"correlation_id": correlation_id})).json()
    assert any(entry["action"] == "UPLOAD" for entry in audit)


async def test_failure_resolution_reprocesses_the_transfer(api_client):
    submit = await api_client.post(
        "/api/transfers/submit",
        json={"files": [{"source_path": "STUDY-004/US/SITE-101/Trial Management/orphan.pdf"}]},
    )
    assert submit.json()["results"][0]["status"] == "EXCEPTION"

    failures = (await api_client.get("/api/failures", params={"status": "OPEN"})).json()
    assert failures
    failure_id = failures[0]["failure_id"]

    resolve = await api_client.put(
        f"/api/failures/{failure_id}/resolve",
        json={
            "mapping_override": {
                "target_study": "STUDY-001",
                "target_country": "US",
                "target_site": "SITE-101",
                "document_type": "Trial Management",
                "document_subtype": "Trial Master File Plan",
                "classification": "Essential Document",
            },
            "persist_as_rule": True,
            "reason": "Study folder was renamed after database lock",
        },
    )
    assert resolve.status_code == 200
    body = resolve.json()
    assert body["transfer"]["status"] == "SUCCESS"
    assert body["resolution_status"] == "RESOLVED"


async def test_mapping_crud_and_dashboard(api_client):
    created = await api_client.post(
        "/api/mappings",
        json={
            "source_pattern": "STUDY-009/US/SITE-1/Docs",
            "match_type": "EXACT",
            "document_type": "Trial Management",
            "document_subtype": "Monitoring Plan",
            "classification": "Essential Document",
        },
    )
    assert created.status_code == 201
    mapping_id = created.json()["mapping_id"]

    updated = await api_client.put(f"/api/mappings/{mapping_id}", json={"priority": 5})
    assert updated.status_code == 200
    assert updated.json()["version"] == 2

    stats = (await api_client.get("/api/dashboard/stats")).json()
    assert "transfers_by_status" in stats
    assert stats["mapping_hit_rate"] >= 0
