"""
Scientific Data Platform - Flask REST API.

Exposes the platform's three customer-driven product feature groups on top of
the real SQLite database (data/platform.db) and the real cross-study
synthesis engine (synthesis/cross_study_engine.py):

  1. Workflow automation  -> /api/workflows, /api/workflows/<name>/run
  2. Real-time insights   -> /api/synthesis/<entity>, /api/entities
  3. Collaboration        -> /api/insights (save/list/share annotations)

Every synthesis result served here is computed fresh from the database at
request time (no caching layer), which is what makes "real-time insights" a
real property of this API rather than a name.
"""
import os
import sqlite3
import sys
from datetime import datetime, timezone

from flask import Flask, jsonify, request

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))

from synthesis.cross_study_engine import synthesize, list_multi_study_entities  # noqa: E402
from data.ingest import get_connection, DB_PATH  # noqa: E402

app = Flask(__name__)

# Named, multi-step workflows: (ingestion / synthesis steps) run in order.
WORKFLOWS = {
    "full_refresh": ["reingest", "synthesize_top_entities"],
    "synthesize_only": ["synthesize_top_entities"],
}


def db():
    path = os.environ.get("PLATFORM_DB_PATH", DB_PATH)
    return get_connection(path)


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "scientific-data-platform"})


# ---------------------------------------------------------------------------
# Real-time insights
# ---------------------------------------------------------------------------

@app.route("/api/entities", methods=["GET"])
def entities():
    """List entities that appear in multiple studies (candidates for synthesis)."""
    min_studies = request.args.get("min_studies", default=2, type=int)
    limit = request.args.get("limit", default=50, type=int)
    results = list_multi_study_entities(min_studies=min_studies, limit=limit)
    return jsonify({
        "min_studies": min_studies,
        "count": len(results),
        "entities": [{"entity": e, "n_studies": n} for e, n in results],
    })


@app.route("/api/synthesis/<entity>", methods=["GET"])
def synthesis_endpoint(entity):
    """Freshly-computed cross-study synthesis for a single entity (no caching)."""
    result = synthesize(entity)
    if result is None:
        return jsonify({"error": f"no findings for entity '{entity}'"}), 404
    return jsonify(result)


@app.route("/api/studies", methods=["GET"])
def list_studies():
    conn = db()
    study_type = request.args.get("study_type")
    if study_type:
        rows = conn.execute(
            "SELECT * FROM studies WHERE study_type = ? ORDER BY publication_year DESC",
            (study_type,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM studies ORDER BY publication_year DESC").fetchall()
    conn.close()
    return jsonify({"count": len(rows), "studies": [dict(r) for r in rows]})


@app.route("/api/studies/<study_id>", methods=["GET"])
def get_study(study_id):
    conn = db()
    study = conn.execute("SELECT * FROM studies WHERE study_id = ?", (study_id,)).fetchone()
    if study is None:
        conn.close()
        return jsonify({"error": "study not found"}), 404
    findings = conn.execute(
        "SELECT entity, metric, value, p_value, n_samples FROM findings WHERE study_id = ?",
        (study_id,),
    ).fetchall()
    conn.close()
    result = dict(study)
    result["findings"] = [dict(f) for f in findings]
    return jsonify(result)


# ---------------------------------------------------------------------------
# Workflow automation
# ---------------------------------------------------------------------------

@app.route("/api/workflows", methods=["GET"])
def list_workflows():
    return jsonify({"workflows": [{"name": name, "steps": steps} for name, steps in WORKFLOWS.items()]})


@app.route("/api/workflows/<name>/run", methods=["POST"])
def run_workflow(name):
    if name not in WORKFLOWS:
        return jsonify({"error": f"unknown workflow '{name}'"}), 404

    conn = db()
    started_at = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "INSERT INTO workflow_runs (workflow_name, status, started_at) VALUES (?, ?, ?)",
        (name, "running", started_at),
    )
    run_id = cur.lastrowid
    conn.commit()

    step_results = {}
    for step in WORKFLOWS[name]:
        if step == "reingest":
            from data.ingest import ingest as run_ingest
            step_results["reingest"] = run_ingest(db_path=os.environ.get("PLATFORM_DB_PATH", DB_PATH), verbose=False)
        elif step == "synthesize_top_entities":
            top = list_multi_study_entities(min_studies=3, limit=5)
            step_results["synthesize_top_entities"] = [
                {"entity": e, "n_studies": n, "synthesis": synthesize(e)} for e, n in top
            ]

    finished_at = datetime.now(timezone.utc).isoformat()
    summary = f"workflow '{name}' completed {len(WORKFLOWS[name])} step(s)"
    conn.execute(
        "UPDATE workflow_runs SET status = ?, result_summary = ?, finished_at = ? WHERE id = ?",
        ("completed", summary, finished_at, run_id),
    )
    conn.commit()
    conn.close()

    return jsonify({
        "workflow": name,
        "run_id": run_id,
        "status": "completed",
        "started_at": started_at,
        "finished_at": finished_at,
        "steps": step_results,
    })


@app.route("/api/workflows/runs", methods=["GET"])
def list_workflow_runs():
    conn = db()
    rows = conn.execute("SELECT * FROM workflow_runs ORDER BY id DESC LIMIT 50").fetchall()
    conn.close()
    return jsonify({"count": len(rows), "runs": [dict(r) for r in rows]})


# ---------------------------------------------------------------------------
# Collaboration
# ---------------------------------------------------------------------------

def _ensure_user(conn, username):
    conn.execute("INSERT OR IGNORE INTO users (username) VALUES (?)", (username,))
    conn.commit()


@app.route("/api/insights", methods=["POST"])
def create_insight():
    payload = request.get_json(silent=True) or {}
    entity = payload.get("entity")
    title = payload.get("title")
    created_by = payload.get("created_by")
    note = payload.get("note", "")
    shared_with = payload.get("shared_with", "")

    if not entity or not title or not created_by:
        return jsonify({"error": "entity, title, and created_by are required"}), 400

    result = synthesize(entity)
    if result is None:
        return jsonify({"error": f"no findings for entity '{entity}', cannot create insight"}), 404

    conn = db()
    _ensure_user(conn, created_by)
    import json as _json
    cur = conn.execute(
        """INSERT INTO insights (entity, title, note, created_by, shared_with, synthesis_snapshot)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (entity, title, note, created_by,
         ",".join(shared_with) if isinstance(shared_with, list) else shared_with,
         _json.dumps(result)),
    )
    conn.commit()
    insight_id = cur.lastrowid
    row = conn.execute("SELECT * FROM insights WHERE id = ?", (insight_id,)).fetchone()
    conn.close()
    return jsonify(dict(row)), 201


@app.route("/api/insights", methods=["GET"])
def list_insights():
    user = request.args.get("user")
    conn = db()
    if user:
        rows = conn.execute(
            """SELECT * FROM insights WHERE created_by = ? OR shared_with LIKE ?
               ORDER BY id DESC""",
            (user, f"%{user}%"),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM insights ORDER BY id DESC").fetchall()
    conn.close()
    return jsonify({"count": len(rows), "insights": [dict(r) for r in rows]})


@app.route("/api/insights/<int:insight_id>", methods=["GET"])
def get_insight(insight_id):
    conn = db()
    row = conn.execute("SELECT * FROM insights WHERE id = ?", (insight_id,)).fetchone()
    conn.close()
    if row is None:
        return jsonify({"error": "insight not found"}), 404
    return jsonify(dict(row))


@app.route("/api/insights/<int:insight_id>/share", methods=["POST"])
def share_insight(insight_id):
    payload = request.get_json(silent=True) or {}
    requester = payload.get("requester")
    share_with = payload.get("share_with")
    if not requester or not share_with:
        return jsonify({"error": "requester and share_with are required"}), 400

    conn = db()
    row = conn.execute("SELECT * FROM insights WHERE id = ?", (insight_id,)).fetchone()
    if row is None:
        conn.close()
        return jsonify({"error": "insight not found"}), 404
    if row["created_by"] != requester:
        conn.close()
        return jsonify({"error": "only the owner can share this insight"}), 403

    existing = set(filter(None, (row["shared_with"] or "").split(",")))
    existing.add(share_with)
    new_shared = ",".join(sorted(existing))
    conn.execute("UPDATE insights SET shared_with = ? WHERE id = ?", (new_shared, insight_id))
    conn.commit()
    updated = conn.execute("SELECT * FROM insights WHERE id = ?", (insight_id,)).fetchone()
    conn.close()
    return jsonify(dict(updated))


if __name__ == "__main__":
    app.run(port=5060, debug=True)
