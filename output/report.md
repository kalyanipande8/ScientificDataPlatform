# Scientific Data Platform - Pipeline Run Report

## Ingestion

- Total datasets generated: 163
- Accepted: 156
- Rejected (validation failures): 7

## Cross-study entity overlap

- Entities appearing in >=2 studies: 48
- Entities appearing in >=5 studies: 45
- Entities appearing in >=10 studies: 23

## Example cross-study synthesis results

### progression_free_survival (39 studies, 39 findings)

- Pooled estimate (sample-size weighted): 0.2201
- Naive mean: 0.2439
- Consensus direction: positive
- Disagreement: high (score=0.4551, direction_disagreement_fraction=0.1282)

### overall_survival (38 studies, 38 findings)

- Pooled estimate (sample-size weighted): 0.3598
- Naive mean: 0.3953
- Consensus direction: positive
- Disagreement: moderate (score=0.3244, direction_disagreement_fraction=0.0526)

### hospitalization_rate (36 studies, 36 findings)

- Pooled estimate (sample-size weighted): 0.0395
- Naive mean: 0.0886
- Consensus direction: mixed
- Disagreement: high (score=0.6167, direction_disagreement_fraction=0.3611)

### Aspirin (33 studies, 33 findings)

- Pooled estimate (sample-size weighted): 0.1862
- Naive mean: 0.1941
- Consensus direction: mixed
- Disagreement: high (score=0.5636, direction_disagreement_fraction=0.2727)

### Rapamycin (32 studies, 32 findings)

- Pooled estimate (sample-size weighted): 0.3488
- Naive mean: 0.3112
- Consensus direction: positive
- Disagreement: high (score=0.4561, direction_disagreement_fraction=0.1562)

## Figures

- `figures/entity_study_count_distribution.png`
- `figures/studies_per_type.png`
