---
name: f05-stats
description: "Compute F0.5 (precision-weighted F-measure), Wilson 95% confidence intervals, confusion-matrix cell statistics, and per-class breakdowns for binary classification results such as startup-success prediction. Pure stdlib + argparse, reads a results CSV and reports F0.5 with confidence bands. Use for calibration metrics, leaderboard scoring, model-comparison reporting. Trigger on: 'compute F0.5', 'Wilson CI', 'precision recall F0.5'."
---

# F0.5 Stats — precision-weighted calibration metrics

Computes F0.5 (the precision-weighted F-measure used in calibration where
precision matters more than recall), Wilson 95% confidence intervals, the
4-cell confusion statistics, and per-class breakdowns from a results CSV.

## Functions

| Function | Purpose |
|---|---|
| `f_beta(precision, recall, beta=0.5)` | F0.5 = `1.25·P·R / (0.25·P + R)` |
| `wilson_ci(p_hat, n, z=1.96)` | Wilson score interval for a proportion |
| `f05_ci_via_pr_ci(tp, fp, tn, fn)` | F0.5 CI propagated from P/R CIs |
| `cell_for(framework_class, verdict, outcome_label)` | verdict↔truth → TP/FP/TN/FN/FILTER_* cell |
| `compute_stats(rows, label)` | per-class P/R/F0.5 + cells |

## Usage

```bash
# F0.5 stats over a results CSV (columns per the results schema)
python3 scripts/cb_step_l_f05_stats.py <results.csv> [--split train|val|all]
python3 scripts/cb_step_l_f05_stats.py                  # reads train + val
```

Import in Python:

```python
from cb_step_l_f05_stats import f_beta, wilson_ci, f05_ci_via_pr_ci, cell_for
f05 = f_beta(precision, recall, beta=0.5)
```

## Requirements

Pure Python stdlib (`argparse`, `collections`, `csv`, `math`, `pathlib`).
No external deps.
