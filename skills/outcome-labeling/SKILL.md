---
name: outcome-labeling
description: "Map a Crunchbase / opensporks company row to a deterministic 8-label outcome taxonomy (POSITIVE_IPO / POSITIVE_LATE_STAGE_FUNDED / POSITIVE_ACQUIRED / EQUIVOCAL_DELISTED / NEGATIVE_CLOSED / NEGATIVE_NO_TRACTION / INDETERMINATE / UNKNOWN), plus the verdict-to-truth bridge that maps a (verdict, outcome_label) pair into evaluation cells (TP/FP/TN/FN/FILTER_*). Pure stdlib, self-testing. Use for outcome ground-truthing in startup-prediction / VC calibration work. Trigger on: 'label a Crunchbase row outcome', 'compute outcome label', 'outcome truth labeling'."
---

# Outcome Labeling — Crunchbase outcome ground truth

Deterministic mapping of a Crunchbase / opensporks organization row to an
outcome label, and the verdict↔truth bridge for F0.5-calibration.

## Data model

`outcome_label_mapping(row)` assigns every row exactly one of 8 labels:

| Label | Definition (from opensporks fields) | Truth class |
|---|---|---|
| `POSITIVE_IPO` | `ipo_status == "public"` | SUCCESS |
| `POSITIVE_LATE_STAGE_FUNDED` | `operating_status == active` AND `last_funding_type ∈ {series_b, series_c, private_equity, post_ipo_equity}` | SUCCESS |
| `POSITIVE_ACQUIRED` | `growth_insight_description` regex-matches acquisition event | SUCCESS |
| `EQUIVOCAL_DELISTED` | `ipo_status == "delisted"` | SUCCESS (default) |
| `NEGATIVE_CLOSED` | `operating_status == "closed"` | FAILURE |
| `NEGATIVE_NO_TRACTION` | active, no Series-A+ funding ≥ 5 yr post-founding | FAILURE |
| `INDETERMINATE` | active, fits neither positive nor negative | AMBIGUOUS (filter) |
| `UNKNOWN` | both status fields missing | AMBIGUOUS (filter) |

Snapshot date is 2024-08-01 per the opensporks scrape window.

## Verdict ↔ truth bridge

`verdict_outcome_evaluation_class(verdict, outcome_label)` maps each
`(framework verdict, outcome_label)` pair to one evaluation cell. Binary
precision / recall / F0.5 are computed ONLY over the 4 committed cells
(TP / FP / TN / FN); hedged predictions (WARN, OUT_OF_SCOPE) and ambiguous
truths (INDETERMINATE, UNKNOWN) land in `FILTER_*` diagnostic cells.

## Usage

```bash
# The outcome-label mapping + verdict-truth bridge are pure functions.
python3 scripts/cb_outcome_label.py   # runs the full 30-case self-test
```

Import in Python:

```python
from cb_outcome_label import outcome_label_mapping, verdict_outcome_evaluation_class
label = outcome_label_mapping(row_dictionary)
cell  = verdict_outcome_evaluation_class(verdict, label)
```

## Requirements

Pure Python stdlib (`re`, `datetime`). No external deps.

## Self-test

`python3 scripts/cb_outcome_label.py` runs a 30-case regression self-test
and exits non-zero if any case regresses. Run before upstream batch jobs
depend on the labeling.
