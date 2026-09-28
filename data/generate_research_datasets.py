"""
Synthetic research dataset generator for the Scientific Data Platform.

Generates 150+ synthetic "research datasets" spanning three study types:
  - gene_expression : organism + a set of genes with log2 fold-change findings
  - clinical_outcome : disease area + a set of outcomes with effect-size findings
  - drug_screening   : disease area + a set of compounds with effect-size findings

Entities (genes / outcomes / compounds) are drawn from small shared pools per
study type, using a power-law-ish sampling scheme, so the SAME entity shows up
across MANY different studies with related (but not identical, and sometimes
contradictory) findings. That overlap is what the cross-study synthesis engine
in synthesis/cross_study_engine.py operates on.

A handful of datasets are deliberately malformed (missing required fields,
bad types, empty findings tables, duplicate entity keys) so the ingestion
pipeline's validation/error-handling is genuinely exercised.

Output: data/raw_datasets.json  (list of dataset dicts, some malformed)

Fixed random seed for reproducibility.
"""
import json
import os
import random

random.seed(42)

OUT_PATH = os.path.join(os.path.dirname(__file__), "raw_datasets.json")

N_GOOD_DATASETS = 156
N_MALFORMED_DATASETS = 7  # deliberately broken, injected among the good ones

STUDY_TYPES = ["gene_expression", "clinical_outcome", "drug_screening"]

ORGANISMS = ["human", "mouse", "zebrafish", "rat"]
DISEASE_AREAS = [
    "oncology", "cardiology", "neurology", "immunology",
    "metabolic_disease", "infectious_disease", "nephrology",
]

# Shared entity pools per study type. Small pools + many studies => overlap.
GENE_POOL = [
    "TP53", "EGFR", "BRCA1", "BRCA2", "KRAS", "MYC", "PTEN", "PIK3CA",
    "APOE", "IL6", "TNF", "VEGFA", "CDKN2A", "RB1", "ALK", "BRAF",
    "NOTCH1", "STAT3", "HIF1A", "MTOR", "AKT1", "JAK2", "FLT3", "IDH1",
]
OUTCOME_POOL = [
    "overall_survival", "progression_free_survival", "hospitalization_rate",
    "relapse_rate", "symptom_score_change", "biomarker_normalization",
    "adverse_event_rate", "quality_of_life_score", "recurrence_rate",
    "mortality_30day",
]
COMPOUND_POOL = [
    "Metformin", "Atorvastatin", "Imatinib", "Rapamycin", "Aspirin",
    "Pembrolizumab", "Ruxolitinib", "Sofosbuxin", "Lisinopril",
    "Empagliflozin", "Trastuzumab", "Dexamethasone", "Doxorubicin",
    "Losartan", "Ibuprofen",
]

ENTITY_POOL_BY_TYPE = {
    "gene_expression": GENE_POOL,
    "clinical_outcome": OUTCOME_POOL,
    "drug_screening": COMPOUND_POOL,
}

# Power-law-ish weights: a few entities are "hot" and studied very often.
def weighted_pool(pool):
    weights = []
    for i, _ in enumerate(pool):
        # first third of the pool gets much higher weight -> heavy overlap
        weights.append(5.0 if i < max(3, len(pool) // 3) else 1.0)
    return weights


def make_findings(study_type, entities):
    """Generate a small table of numeric findings for the chosen entities."""
    findings = []
    for entity in entities:
        if study_type == "gene_expression":
            # log2 fold-change, roughly centered per-gene but with noise
            base = (hash(entity) % 100) / 50.0 - 1.0  # -1..1 deterministic-ish center
            value = round(random.gauss(base, 0.6), 3)
            p_value = round(max(1e-6, random.expovariate(20)), 5)
            findings.append({
                "entity": entity,
                "metric": "log2_fold_change",
                "value": value,
                "p_value": p_value,
                "n_samples": random.randint(6, 200),
            })
        elif study_type == "clinical_outcome":
            base = (hash(entity) % 100) / 100.0 - 0.5
            value = round(random.gauss(base, 0.25), 3)  # effect size (Cohen's d-ish)
            p_value = round(max(1e-6, random.expovariate(15)), 5)
            findings.append({
                "entity": entity,
                "metric": "effect_size",
                "value": value,
                "p_value": p_value,
                "n_samples": random.randint(20, 5000),
            })
        else:  # drug_screening
            base = (hash(entity) % 100) / 100.0 - 0.5
            value = round(random.gauss(base, 0.3), 3)  # effect size vs. control
            p_value = round(max(1e-6, random.expovariate(15)), 5)
            findings.append({
                "entity": entity,
                "metric": "effect_size",
                "value": value,
                "p_value": p_value,
                "n_samples": random.randint(10, 1000),
            })
    return findings


def make_good_dataset(idx):
    study_type = STUDY_TYPES[idx % len(STUDY_TYPES)]
    pool = ENTITY_POOL_BY_TYPE[study_type]
    weights = weighted_pool(pool)
    k = random.randint(3, 8)
    entities = list(set(random.choices(pool, weights=weights, k=k)))
    if not entities:
        entities = [pool[0]]

    study_id = f"STUDY-{idx:04d}"
    record = {
        "study_id": study_id,
        "study_type": study_type,
        "title": f"{study_type.replace('_', ' ').title()} study {idx}",
        "publication_year": random.randint(2012, 2025),
        "sample_size": random.randint(15, 5000),
        "findings": make_findings(study_type, entities),
    }
    if study_type == "gene_expression":
        record["organism"] = random.choice(ORGANISMS)
        record["disease_area"] = random.choice(DISEASE_AREAS)
    else:
        record["organism"] = "human"
        record["disease_area"] = random.choice(DISEASE_AREAS)
    return record


def make_malformed_dataset(idx, kind):
    """Deliberately broken datasets, for exercising ingestion validation."""
    base = make_good_dataset(1000 + idx)
    if kind == "missing_study_type":
        del base["study_type"]
    elif kind == "bad_sample_size_type":
        base["sample_size"] = "a lot of patients"
    elif kind == "empty_findings":
        base["findings"] = []
    elif kind == "missing_study_id":
        del base["study_id"]
    elif kind == "negative_publication_year":
        base["publication_year"] = -5
    elif kind == "duplicate_entity_keys":
        # duplicate the first finding's entity with conflicting metric name
        if base["findings"]:
            dup = dict(base["findings"][0])
            dup["metric"] = "UNSUPPORTED_METRIC_TYPE"
            base["findings"].append(dup)
        base["_force_bad_metric"] = True
    elif kind == "unknown_study_type":
        base["study_type"] = "astrology_reading"

    if kind == "missing_study_id":
        # leave study_id absent so the ingestion pipeline genuinely rejects it
        # for that reason (don't re-add the field we just deleted)
        return base

    base["study_id"] = base.get("study_id", f"STUDY-BAD-{idx:03d}") + "-MALFORMED"
    return base


def generate():
    datasets = [make_good_dataset(i) for i in range(N_GOOD_DATASETS)]

    malformed_kinds = [
        "missing_study_type", "bad_sample_size_type", "empty_findings",
        "missing_study_id", "negative_publication_year",
        "duplicate_entity_keys", "unknown_study_type",
    ]
    malformed = [make_malformed_dataset(i, kind) for i, kind in enumerate(malformed_kinds[:N_MALFORMED_DATASETS])]

    all_datasets = datasets + malformed
    random.shuffle(all_datasets)

    with open(OUT_PATH, "w") as f:
        json.dump(all_datasets, f, indent=2)

    print(f"Generated {len(datasets)} well-formed + {len(malformed)} deliberately "
          f"malformed datasets = {len(all_datasets)} total -> {OUT_PATH}")
    return all_datasets


if __name__ == "__main__":
    generate()
