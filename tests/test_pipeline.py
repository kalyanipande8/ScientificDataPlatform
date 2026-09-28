"""
Real pytest tests for the ingestion pipeline and cross-study synthesis engine.
"""
import json
import os
import sqlite3
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from data.generate_research_datasets import generate
from data.ingest import ingest, validate_dataset
from synthesis.cross_study_engine import synthesize, list_multi_study_entities

TEST_DB = os.path.join(HERE, "test_pipeline.db")
TEST_RAW = os.path.join(HERE, "test_raw_datasets.json")


@pytest.fixture(scope="module")
def ingested_summary():
    generate()  # writes data/raw_datasets.json with fixed seed
    summary = ingest(db_path=TEST_DB, verbose=False)
    yield summary
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


def test_generated_at_least_150_datasets():
    raw_path = os.path.join(ROOT, "data", "raw_datasets.json")
    with open(raw_path) as f:
        data = json.load(f)
    assert len(data) >= 150


def test_ingestion_accepts_and_rejects(ingested_summary):
    assert ingested_summary["accepted"] > 140
    assert ingested_summary["rejected"] >= 5
    assert ingested_summary["accepted"] + ingested_summary["rejected"] == ingested_summary["total"]


def test_ingestion_log_written(ingested_summary):
    log_path = os.path.join(ROOT, "data", "ingestion_log.json")
    assert os.path.exists(log_path)
    with open(log_path) as f:
        log = json.load(f)
    assert log["accepted"] == ingested_summary["accepted"]
    assert log["rejected"] == ingested_summary["rejected"]
    rejected_entries = [e for e in log["entries"] if e["status"] == "rejected"]
    assert len(rejected_entries) == ingested_summary["rejected"]
    for entry in rejected_entries:
        assert entry["reason"]  # a real reason string, not blank


def test_validate_dataset_rejects_missing_field():
    bad = {"study_id": "X", "study_type": "gene_expression", "title": "t",
           "publication_year": 2020, "sample_size": 10, "organism": "human"}
    # missing disease_area and findings
    is_valid, reason = validate_dataset(bad)
    assert not is_valid
    assert "disease_area" in reason


def test_validate_dataset_accepts_good_record():
    good = {
        "study_id": "X1", "study_type": "gene_expression", "title": "t",
        "publication_year": 2020, "sample_size": 10, "organism": "human",
        "disease_area": "oncology",
        "findings": [{"entity": "TP53", "metric": "log2_fold_change", "value": 0.5,
                      "p_value": 0.01, "n_samples": 10}],
    }
    is_valid, reason = validate_dataset(good)
    assert is_valid
    assert reason is None


def test_multi_study_entities_exist(ingested_summary):
    overlap = list_multi_study_entities(min_studies=2, db_path=TEST_DB)
    assert len(overlap) > 0


def test_synthesis_on_real_multi_study_entity(ingested_summary):
    overlap = list_multi_study_entities(min_studies=3, db_path=TEST_DB, limit=1)
    assert overlap, "expected at least one entity with 3+ studies"
    entity, n_studies = overlap[0]
    result = synthesize(entity, db_path=TEST_DB)
    assert result is not None
    assert result["entity"] == entity
    assert result["n_studies"] == n_studies
    assert result["n_findings"] >= n_studies
    assert "pooled_estimate" in result
    assert result["disagreement_level"] in ("low", "moderate", "high")


def test_synthesis_returns_none_for_unknown_entity(ingested_summary):
    result = synthesize("NOT_A_REAL_ENTITY_XYZ", db_path=TEST_DB)
    assert result is None


# ---------------------------------------------------------------------------
# Correctness checks on the synthesis logic itself, using constructed data
# with KNOWN consistent vs. contradictory findings.
# ---------------------------------------------------------------------------

@pytest.fixture
def constructed_db():
    conn = sqlite3.connect(TEST_DB + ".constructed")
    conn.executescript("""
        CREATE TABLE studies (
            study_id TEXT PRIMARY KEY, study_type TEXT, title TEXT,
            publication_year INTEGER, sample_size INTEGER, organism TEXT, disease_area TEXT
        );
        CREATE TABLE findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT, study_id TEXT, entity TEXT,
            metric TEXT, value REAL, p_value REAL, n_samples INTEGER
        );
    """)
    conn.commit()
    yield TEST_DB + ".constructed"
    conn.close()
    os.remove(TEST_DB + ".constructed")


def _insert_study_and_finding(db_path, study_id, entity, value, n_samples=100):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO studies VALUES (?, 'gene_expression', 't', 2020, ?, 'human', 'oncology')",
        (study_id, n_samples),
    )
    conn.execute(
        "INSERT INTO findings (study_id, entity, metric, value, p_value, n_samples) "
        "VALUES (?, ?, 'log2_fold_change', ?, 0.01, ?)",
        (study_id, entity, value, n_samples),
    )
    conn.commit()
    conn.close()


def test_consistent_findings_yield_low_disagreement(constructed_db):
    entity = "CONSISTENT_GENE"
    for i, value in enumerate([1.0, 1.1, 0.9, 1.05]):
        _insert_study_and_finding(constructed_db, f"CONSISTENT-{i}", entity, value)

    result = synthesize(entity, db_path=constructed_db)
    assert result["disagreement_level"] == "low"
    assert result["consensus_direction"] == "positive"
    assert result["direction_disagreement_fraction"] == 0.0


def test_contradictory_findings_yield_high_disagreement(constructed_db):
    entity = "CONTRADICTORY_GENE"
    for i, value in enumerate([2.0, -2.0, 1.8, -1.9]):
        _insert_study_and_finding(constructed_db, f"CONTRA-{i}", entity, value)

    result = synthesize(entity, db_path=constructed_db)
    assert result["disagreement_level"] == "high"
    assert result["consensus_direction"] == "mixed"
    assert result["direction_disagreement_fraction"] >= 0.4
