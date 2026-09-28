"""
Real pytest + Flask test client tests for the Scientific Data Platform API.
Runs against a real seeded SQLite database (built fresh in a module fixture).
"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

TEST_DB = os.path.join(HERE, "test_api.db")
os.environ["PLATFORM_DB_PATH"] = TEST_DB

from data.generate_research_datasets import generate
from data.ingest import ingest
from synthesis.cross_study_engine import list_multi_study_entities


@pytest.fixture(scope="module")
def client():
    generate()
    ingest(db_path=TEST_DB, verbose=False)
    from app.main import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"


def test_list_studies(client):
    resp = client.get("/api/studies")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["count"] > 100
    assert "studies" in data


def test_get_single_study(client):
    resp = client.get("/api/studies")
    study_id = resp.get_json()["studies"][0]["study_id"]
    resp2 = client.get(f"/api/studies/{study_id}")
    assert resp2.status_code == 200
    body = resp2.get_json()
    assert body["study_id"] == study_id
    assert "findings" in body


def test_get_study_not_found(client):
    resp = client.get("/api/studies/DOES-NOT-EXIST")
    assert resp.status_code == 404


def test_entities_endpoint(client):
    resp = client.get("/api/entities?min_studies=3")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["count"] >= 1
    assert all(e["n_studies"] >= 3 for e in data["entities"])


def test_synthesis_endpoint_real_time(client):
    entities = list_multi_study_entities(min_studies=3, db_path=TEST_DB, limit=1)
    assert entities
    entity_name = entities[0][0]
    resp = client.get(f"/api/synthesis/{entity_name}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["entity"] == entity_name
    assert "pooled_estimate" in data
    assert "disagreement_level" in data


def test_synthesis_endpoint_unknown_entity(client):
    resp = client.get("/api/synthesis/NOT_A_REAL_ENTITY_XYZ")
    assert resp.status_code == 404


def test_list_workflows(client):
    resp = client.get("/api/workflows")
    assert resp.status_code == 200
    names = [w["name"] for w in resp.get_json()["workflows"]]
    assert "full_refresh" in names
    assert "synthesize_only" in names


def test_run_workflow(client):
    resp = client.post("/api/workflows/synthesize_only/run")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "completed"
    assert "synthesize_top_entities" in data["steps"]


def test_run_unknown_workflow(client):
    resp = client.post("/api/workflows/nonexistent/run")
    assert resp.status_code == 404


def test_create_and_get_insight(client):
    entities = list_multi_study_entities(min_studies=3, db_path=TEST_DB, limit=1)
    entity_name = entities[0][0]
    resp = client.post("/api/insights", json={
        "entity": entity_name,
        "title": "Worth watching",
        "created_by": "kalyani",
        "note": "consistent effect across studies",
    })
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["entity"] == entity_name
    insight_id = body["id"]

    resp2 = client.get(f"/api/insights/{insight_id}")
    assert resp2.status_code == 200
    assert resp2.get_json()["created_by"] == "kalyani"


def test_create_insight_missing_fields(client):
    resp = client.post("/api/insights", json={"entity": "x"})
    assert resp.status_code == 400


def test_share_insight_ownership_enforced(client):
    entities = list_multi_study_entities(min_studies=3, db_path=TEST_DB, limit=1)
    entity_name = entities[0][0]
    create_resp = client.post("/api/insights", json={
        "entity": entity_name, "title": "t", "created_by": "alice",
    })
    insight_id = create_resp.get_json()["id"]

    # wrong owner tries to share -> 403
    bad = client.post(f"/api/insights/{insight_id}/share", json={
        "requester": "bob", "share_with": "carol",
    })
    assert bad.status_code == 403

    # real owner shares -> 200
    good = client.post(f"/api/insights/{insight_id}/share", json={
        "requester": "alice", "share_with": "carol",
    })
    assert good.status_code == 200
    assert "carol" in good.get_json()["shared_with"]


def test_list_insights_by_user(client):
    resp = client.get("/api/insights?user=alice")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["count"] >= 1
