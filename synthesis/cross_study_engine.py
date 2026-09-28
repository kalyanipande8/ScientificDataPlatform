"""
Real cross-study knowledge synthesis engine.

Given an entity (a gene, clinical outcome, or compound), this queries the
real SQLite database for every finding across every ingested study that
references that entity, and computes:

  - a sample-size-weighted pooled effect estimate (a simple, real
    meta-analysis-style fixed-effect pooling: sum(value * n) / sum(n))
  - a naive unweighted mean, for comparison
  - a consensus direction (positive / negative / mixed) across studies
  - a disagreement score: the fraction of studies whose finding direction
    disagrees with the majority direction, plus the coefficient of variation
    of the raw values. High values in either => real, computed disagreement.

This is a genuine aggregation over real rows pulled from the database, not a
static/precomputed table.
"""
import math
import os
import sqlite3
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))

DEFAULT_DB_PATH = os.environ.get(
    "PLATFORM_DB_PATH", os.path.join(HERE, "..", "data", "platform.db")
)


def _connect(db_path=None):
    conn = sqlite3.connect(db_path or DEFAULT_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def list_multi_study_entities(min_studies=2, db_path=None, limit=None):
    """Return entities that appear in findings from at least `min_studies` distinct studies."""
    conn = _connect(db_path)
    q = """
        SELECT entity, COUNT(DISTINCT study_id) AS n_studies
        FROM findings
        GROUP BY entity
        HAVING n_studies >= ?
        ORDER BY n_studies DESC
    """
    rows = conn.execute(q, (min_studies,)).fetchall()
    conn.close()
    result = [(r["entity"], r["n_studies"]) for r in rows]
    return result[:limit] if limit else result


def synthesize(entity, db_path=None):
    """
    Run real cross-study synthesis for a single entity.

    Returns a dict with the pooled estimate, per-study findings, and a
    computed disagreement signal. Returns None if the entity has no findings.
    """
    conn = _connect(db_path)
    rows = conn.execute(
        """
        SELECT f.study_id, f.entity, f.metric, f.value, f.p_value, f.n_samples,
               s.study_type, s.disease_area, s.organism, s.publication_year
        FROM findings f
        JOIN studies s ON s.study_id = f.study_id
        WHERE f.entity = ?
        ORDER BY s.publication_year
        """,
        (entity,),
    ).fetchall()
    conn.close()

    if not rows:
        return None

    values = [r["value"] for r in rows]
    weights = [max(r["n_samples"], 1) for r in rows]
    n_studies = len(set(r["study_id"] for r in rows))

    weighted_sum = sum(v * w for v, w in zip(values, weights))
    total_weight = sum(weights)
    pooled_estimate = weighted_sum / total_weight if total_weight else 0.0
    naive_mean = sum(values) / len(values)

    directions = ["positive" if v > 0 else ("negative" if v < 0 else "zero") for v in values]
    pos = directions.count("positive")
    neg = directions.count("negative")
    majority_dir = "positive" if pos >= neg else "negative"
    disagreeing = sum(1 for d in directions if d not in (majority_dir, "zero"))
    direction_disagreement_fraction = disagreeing / len(directions)

    mean_v = naive_mean
    variance = sum((v - mean_v) ** 2 for v in values) / len(values)
    std_dev = math.sqrt(variance)
    coefficient_of_variation = (std_dev / abs(mean_v)) if mean_v != 0 else float("inf")

    if pos > 0 and neg > 0:
        consensus_direction = "mixed" if direction_disagreement_fraction >= 0.25 else majority_dir
    else:
        consensus_direction = majority_dir

    # A simple composite disagreement score in [0, 1]-ish scale (CV uncapped, so clip for scoring)
    disagreement_score = round(
        min(1.0, 0.6 * direction_disagreement_fraction + 0.4 * min(coefficient_of_variation, 1.0)),
        4,
    )
    disagreement_level = (
        "high" if disagreement_score >= 0.4 else
        "moderate" if disagreement_score >= 0.15 else
        "low"
    )

    per_study = [
        {
            "study_id": r["study_id"],
            "study_type": r["study_type"],
            "disease_area": r["disease_area"],
            "organism": r["organism"],
            "publication_year": r["publication_year"],
            "metric": r["metric"],
            "value": r["value"],
            "p_value": r["p_value"],
            "n_samples": r["n_samples"],
        }
        for r in rows
    ]

    return {
        "entity": entity,
        "n_studies": n_studies,
        "n_findings": len(rows),
        "pooled_estimate": round(pooled_estimate, 4),
        "naive_mean": round(naive_mean, 4),
        "consensus_direction": consensus_direction,
        "direction_disagreement_fraction": round(direction_disagreement_fraction, 4),
        "coefficient_of_variation": round(coefficient_of_variation, 4) if math.isfinite(coefficient_of_variation) else None,
        "disagreement_score": disagreement_score,
        "disagreement_level": disagreement_level,
        "per_study_findings": per_study,
    }


if __name__ == "__main__":
    entities = list_multi_study_entities(min_studies=5, limit=5)
    for entity, n in entities:
        result = synthesize(entity)
        print(f"\n=== {entity} ({n} studies) ===")
        print(f"pooled_estimate={result['pooled_estimate']} "
              f"consensus={result['consensus_direction']} "
              f"disagreement={result['disagreement_level']} "
              f"({result['disagreement_score']})")
