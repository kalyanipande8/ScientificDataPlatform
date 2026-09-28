"""
Orchestrates the full pipeline:
  generate datasets -> ingest (with real validation logging) ->
  run real cross-study synthesis on several multi-study entities ->
  write output/report.md + figures.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from data.generate_research_datasets import generate
from data.ingest import ingest, DB_PATH
from synthesis.cross_study_engine import list_multi_study_entities, synthesize

OUTPUT_DIR = os.path.join(HERE, "output")
FIGURES_DIR = os.path.join(OUTPUT_DIR, "figures")


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(FIGURES_DIR, exist_ok=True)

    print("=== Step 1: generating synthetic research datasets ===")
    all_datasets = generate()

    print("\n=== Step 2: ingesting datasets into SQLite ===")
    ingestion_summary = ingest()

    print("\n=== Step 3: cross-study synthesis on multi-study entities ===")
    overlap_2plus = list_multi_study_entities(min_studies=2)
    overlap_5plus = list_multi_study_entities(min_studies=5)
    overlap_10plus = list_multi_study_entities(min_studies=10)

    top_entities = list_multi_study_entities(min_studies=3, limit=5)
    synthesis_results = []
    for entity, n_studies in top_entities:
        result = synthesize(entity)
        synthesis_results.append(result)
        print(f"  {entity}: {n_studies} studies -> pooled={result['pooled_estimate']}, "
              f"consensus={result['consensus_direction']}, disagreement={result['disagreement_level']}")

    print("\n=== Step 4: figures ===")
    # Figure 1: distribution of dataset-count per entity
    conn_counts = [n for _, n in list_multi_study_entities(min_studies=1)]
    fig1_path = os.path.join(FIGURES_DIR, "entity_study_count_distribution.png")
    plt.figure(figsize=(7, 4.5))
    plt.hist(conn_counts, bins=range(1, max(conn_counts) + 2), color="#4C72B0", edgecolor="white", align="left")
    plt.xlabel("Number of studies referencing an entity")
    plt.ylabel("Number of entities")
    plt.title("Cross-study overlap: how many studies reference each entity")
    plt.tight_layout()
    plt.savefig(fig1_path, dpi=130)
    plt.close()

    # Figure 2: studies per type breakdown
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT study_type, COUNT(*) FROM studies GROUP BY study_type").fetchall()
    conn.close()
    types = [r[0] for r in rows]
    counts = [r[1] for r in rows]
    fig2_path = os.path.join(FIGURES_DIR, "studies_per_type.png")
    plt.figure(figsize=(6, 4.5))
    plt.bar(types, counts, color=["#4C72B0", "#DD8452", "#55A868"])
    plt.ylabel("Number of ingested studies")
    plt.title("Ingested studies by type")
    plt.tight_layout()
    plt.savefig(fig2_path, dpi=130)
    plt.close()

    print(f"  wrote {fig1_path}")
    print(f"  wrote {fig2_path}")

    print("\n=== Step 5: report ===")
    report_path = os.path.join(OUTPUT_DIR, "report.md")
    with open(report_path, "w") as f:
        f.write("# Scientific Data Platform - Pipeline Run Report\n\n")
        f.write("## Ingestion\n\n")
        f.write(f"- Total datasets generated: {len(all_datasets)}\n")
        f.write(f"- Accepted: {ingestion_summary['accepted']}\n")
        f.write(f"- Rejected (validation failures): {ingestion_summary['rejected']}\n\n")

        f.write("## Cross-study entity overlap\n\n")
        f.write(f"- Entities appearing in >=2 studies: {len(overlap_2plus)}\n")
        f.write(f"- Entities appearing in >=5 studies: {len(overlap_5plus)}\n")
        f.write(f"- Entities appearing in >=10 studies: {len(overlap_10plus)}\n\n")

        f.write("## Example cross-study synthesis results\n\n")
        for r in synthesis_results:
            f.write(f"### {r['entity']} ({r['n_studies']} studies, {r['n_findings']} findings)\n\n")
            f.write(f"- Pooled estimate (sample-size weighted): {r['pooled_estimate']}\n")
            f.write(f"- Naive mean: {r['naive_mean']}\n")
            f.write(f"- Consensus direction: {r['consensus_direction']}\n")
            f.write(f"- Disagreement: {r['disagreement_level']} (score={r['disagreement_score']}, "
                     f"direction_disagreement_fraction={r['direction_disagreement_fraction']})\n\n")

        f.write("## Figures\n\n")
        f.write("- `figures/entity_study_count_distribution.png`\n")
        f.write("- `figures/studies_per_type.png`\n")

    with open(os.path.join(OUTPUT_DIR, "synthesis_results.json"), "w") as f:
        json.dump(synthesis_results, f, indent=2)

    print(f"  wrote {report_path}")
    print("\nDone.")


if __name__ == "__main__":
    main()
