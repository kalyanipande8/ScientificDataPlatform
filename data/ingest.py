"""
Real ingestion pipeline for the Scientific Data Platform.

Reads data/raw_datasets.json (produced by generate_research_datasets.py),
validates each dataset's schema, logs ingestion status per dataset (to
data/ingestion_log.json and stdout), and loads valid datasets into a real
SQLite database (data/platform.db).

Deliberately malformed datasets are expected to fail validation and are
recorded as rejected, with the specific reason, rather than silently dropped.
"""
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(__file__)
RAW_PATH = os.path.join(HERE, "raw_datasets.json")
DB_PATH = os.environ.get("PLATFORM_DB_PATH", os.path.join(HERE, "platform.db"))
LOG_PATH = os.path.join(HERE, "ingestion_log.json")

VALID_STUDY_TYPES = {"gene_expression", "clinical_outcome", "drug_screening"}
VALID_METRICS = {"log2_fold_change", "effect_size"}

REQUIRED_TOP_LEVEL_FIELDS = [
    "study_id", "study_type", "title", "publication_year",
    "sample_size", "organism", "disease_area", "findings",
]


def validate_dataset(record):
    """Return (is_valid, reason_or_None)."""
    if not isinstance(record, dict):
        return False, "record is not a JSON object"

    for field in REQUIRED_TOP_LEVEL_FIELDS:
        if field not in record:
            return False, f"missing required field '{field}'"

    if record["study_type"] not in VALID_STUDY_TYPES:
        return False, f"invalid study_type '{record['study_type']}'"

    if not isinstance(record["sample_size"], int) or record["sample_size"] <= 0:
        return False, f"sample_size must be a positive int, got {record['sample_size']!r}"

    if not isinstance(record["publication_year"], int) or record["publication_year"] < 1900:
        return False, f"publication_year invalid: {record['publication_year']!r}"

    findings = record["findings"]
    if not isinstance(findings, list) or len(findings) == 0:
        return False, "findings must be a non-empty list"

    for f in findings:
        if not isinstance(f, dict):
            return False, "each finding must be an object"
        for key in ("entity", "metric", "value", "p_value", "n_samples"):
            if key not in f:
                return False, f"finding missing field '{key}'"
        if f["metric"] not in VALID_METRICS:
            return False, f"finding has unsupported metric '{f['metric']}'"
        if not isinstance(f["value"], (int, float)):
            return False, "finding 'value' must be numeric"

    return True, None


SCHEMA = """
CREATE TABLE IF NOT EXISTS studies (
    study_id TEXT PRIMARY KEY,
    study_type TEXT NOT NULL,
    title TEXT NOT NULL,
    publication_year INTEGER NOT NULL,
    sample_size INTEGER NOT NULL,
    organism TEXT,
    disease_area TEXT
);

CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    study_id TEXT NOT NULL,
    entity TEXT NOT NULL,
    metric TEXT NOT NULL,
    value REAL NOT NULL,
    p_value REAL,
    n_samples INTEGER,
    FOREIGN KEY (study_id) REFERENCES studies(study_id)
);

CREATE INDEX IF NOT EXISTS idx_findings_entity ON findings(entity);
CREATE INDEX IF NOT EXISTS idx_studies_type ON studies(study_type);

CREATE TABLE IF NOT EXISTS ingestion_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    study_id TEXT,
    status TEXT NOT NULL,
    reason TEXT,
    ingested_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS insights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity TEXT NOT NULL,
    title TEXT NOT NULL,
    note TEXT,
    created_by TEXT NOT NULL,
    shared_with TEXT,
    synthesis_snapshot TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS workflow_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_name TEXT NOT NULL,
    status TEXT NOT NULL,
    result_summary TEXT,
    started_at TEXT DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT
);
"""


def get_connection(db_path=None):
    conn = sqlite3.connect(db_path or DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def reset_database(db_path=None):
    path = db_path or DB_PATH
    if os.path.exists(path):
        os.remove(path)
    conn = get_connection(path)
    init_schema(conn)
    return conn


def ingest(raw_path=None, db_path=None, verbose=True):
    raw_path = raw_path or RAW_PATH
    with open(raw_path) as f:
        records = json.load(f)

    conn = reset_database(db_path)
    cur = conn.cursor()

    accepted, rejected = 0, 0
    log_entries = []

    for record in records:
        is_valid, reason = validate_dataset(record)
        study_id = record.get("study_id", "<unknown>") if isinstance(record, dict) else "<unknown>"

        if not is_valid:
            rejected += 1
            log_entries.append({"study_id": study_id, "status": "rejected", "reason": reason})
            cur.execute(
                "INSERT INTO ingestion_log (study_id, status, reason) VALUES (?, ?, ?)",
                (study_id, "rejected", reason),
            )
            if verbose:
                print(f"[REJECTED] {study_id}: {reason}")
            continue

        try:
            cur.execute(
                """INSERT INTO studies (study_id, study_type, title, publication_year,
                                         sample_size, organism, disease_area)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (record["study_id"], record["study_type"], record["title"],
                 record["publication_year"], record["sample_size"],
                 record["organism"], record["disease_area"]),
            )
            for finding in record["findings"]:
                cur.execute(
                    """INSERT INTO findings (study_id, entity, metric, value, p_value, n_samples)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (record["study_id"], finding["entity"], finding["metric"],
                     finding["value"], finding["p_value"], finding["n_samples"]),
                )
            accepted += 1
            log_entries.append({"study_id": study_id, "status": "accepted", "reason": None})
            cur.execute(
                "INSERT INTO ingestion_log (study_id, status, reason) VALUES (?, ?, ?)",
                (study_id, "accepted", None),
            )
            if verbose:
                print(f"[ACCEPTED] {study_id} ({record['study_type']}, "
                      f"{len(record['findings'])} findings)")
        except sqlite3.IntegrityError as e:
            rejected += 1
            conn.rollback()
            reason = f"integrity error: {e}"
            log_entries.append({"study_id": study_id, "status": "rejected", "reason": reason})
            cur.execute(
                "INSERT INTO ingestion_log (study_id, status, reason) VALUES (?, ?, ?)",
                (study_id, "rejected", reason),
            )
            if verbose:
                print(f"[REJECTED] {study_id}: {reason}")

    conn.commit()
    conn.close()

    with open(LOG_PATH, "w") as f:
        json.dump({"accepted": accepted, "rejected": rejected, "entries": log_entries}, f, indent=2)

    if verbose:
        print(f"\nIngestion complete: {accepted} accepted, {rejected} rejected "
              f"(of {len(records)} total datasets). DB: {db_path or DB_PATH}")

    return {"accepted": accepted, "rejected": rejected, "total": len(records)}


if __name__ == "__main__":
    ingest()
