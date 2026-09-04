"""Compute F0.5 and four-cell verdict-to-reference statistics.

Reads a result CSV and recomputes evaluation cells from `verdict` and
`outcome_label`. Proposal evaluation is archetype-free.

**Binary classifier setup for F0.5** (per Report §4):
  - Predicted positive = verdict is PASS
  - Predicted negative = verdict is WARN ∪ FAIL
  - Only filter is AMBIG truth (no ground truth available)

| Main truth | PASS | WARN / FAIL / no-verdict |
|------------|------|--------------------------|
| SUCCESS    | TP   | FN                       |
| FAILURE    | FP   | TN                       |
| AMBIG      | filter (FILTER_truth_ambig)             ||

Usage:
  python3 compute_f05_stats.py [/path/to/results.csv]
"""

import argparse
import collections
import csv
import math
import sys
from pathlib import Path

csv.field_size_limit(sys.maxsize)

SUCCESS_TRUTH = {"POSITIVE_IPO", "POSITIVE_LATE_STAGE_FUNDED",
                 "POSITIVE_ACQUIRED", "EQUIVOCAL_DELISTED"}
FAILURE_TRUTH = {"NEGATIVE_CLOSED", "NEGATIVE_NO_TRACTION"}
AMBIG_TRUTH = {"INDETERMINATE", "UNKNOWN"}


def f_beta(precision, recall, beta=0.5):
    if precision == 0 and recall == 0:
        return 0.0
    b2 = beta * beta
    return (1 + b2) * precision * recall / (b2 * precision + recall)


def wilson_ci(p_hat, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    denom = 1 + z * z / n
    center = (p_hat + z * z / (2 * n)) / denom
    spread = z * math.sqrt(p_hat * (1 - p_hat) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, center - spread), min(1.0, center + spread))


def f05_ci_via_pr_ci(tp, fp, tn, fn):
    p_n = tp + fp
    r_n = tp + fn
    if p_n == 0 or r_n == 0:
        return (0.0, 0.0)
    p_hat = tp / p_n
    r_hat = tp / r_n
    p_lo, p_hi = wilson_ci(p_hat, p_n)
    r_lo, r_hi = wilson_ci(r_hat, r_n)
    return (f_beta(p_lo, r_lo), f_beta(p_hi, r_hi))


def cell_for(verdict, outcome_label):
    """Map (verdict, outcome_label) to an evaluation cell.

    Predicted positive = verdict == PASS.
    Predicted negative = WARN or FAIL.
    Filter only on AMBIG truth.

    Returns one of: TP, FP, TN, FN, FILTER_truth_ambig, UNEXPECTED.
    """
    if verdict not in {"PASS", "WARN", "FAIL"}:
        return "UNEXPECTED"
    if outcome_label in AMBIG_TRUTH:
        return "FILTER_truth_ambig"
    if outcome_label in SUCCESS_TRUTH:
        return "TP" if verdict == "PASS" else "FN"
    if outcome_label in FAILURE_TRUTH:
        return "FP" if verdict == "PASS" else "TN"
    return "UNEXPECTED"


def classify_truth(outcome_label):
    if outcome_label in SUCCESS_TRUTH: return "SUCCESS"
    if outcome_label in FAILURE_TRUTH: return "FAILURE"
    if outcome_label in AMBIG_TRUTH: return "AMBIG"
    return "OTHER"


def compute_stats(rows, label):
    """Recompute F0.5 and diagnostics from verdict and outcome label."""
    n_rows = len(rows)
    if n_rows == 0:
        print(f"\n=== {label}: 0 rows — skipping ===")
        return None

    cell_counts = collections.Counter()
    verdict_counts = collections.Counter()
    truth_counts = collections.Counter()
    sample_strata_counts = collections.Counter()

    for r in rows:
        verdict = (r.get("verdict") or "").strip()
        outcome = (r.get("outcome_label") or "").strip()
        cell = cell_for(verdict, outcome)
        cell_counts[cell] += 1
        verdict_counts[verdict or "(empty)"] += 1
        truth_counts[classify_truth(outcome)] += 1
        sample_strata_counts[r.get("sample_strata", "?")] += 1

    tp = cell_counts.get("TP", 0)
    fp = cell_counts.get("FP", 0)
    tn = cell_counts.get("TN", 0)
    fn = cell_counts.get("FN", 0)
    n_committed = tp + fp + tn + fn
    n_filter = cell_counts.get("FILTER_truth_ambig", 0)
    n_unexpected = cell_counts.get("UNEXPECTED", 0)

    if n_committed == 0:
        precision = recall = f05 = 0.0
        f05_ci = (0.0, 0.0)
    else:
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall   = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f05      = f_beta(precision, recall, beta=0.5)
        f05_ci   = f05_ci_via_pr_ci(tp, fp, tn, fn)

    print(f"\n{'='*70}")
    print(f"  F0.5 stats — {label}")
    print(f"{'='*70}")
    print(f"\nSample size: {n_rows}")
    print(f"By stratum:")
    for strat, n in sorted(sample_strata_counts.items(), key=lambda x: -x[1]):
        print(f"  {strat:<20}  {n}")

    print(f"\nTruth distribution (from outcome_label, external):")
    for t in ["SUCCESS", "FAILURE", "AMBIG", "OTHER"]:
        n = truth_counts.get(t, 0)
        if n:
            print(f"  {t:<10}  {n:>3}  ({100*n/n_rows:5.1f}%)")

    print(f"\nVerdict distribution (framework output):")
    for v in ["PASS", "WARN", "FAIL", "(empty)"]:
        n = verdict_counts.get(v, 0)
        if n:
            print(f"  {v:<14}  {n:>3}  ({100*n/n_rows:5.1f}%)")

    print(f"\n4-cell + filter distribution (positive=PASS; predicted-negative=WARN/FAIL):")
    for cell in ["TP", "FP", "TN", "FN", "FILTER_truth_ambig", "UNEXPECTED"]:
        n = cell_counts.get(cell, 0)
        flag = "← in F0.5" if cell in {"TP", "FP", "TN", "FN"} else "  diagnostic"
        print(f"  {cell:<22}  {n:>3}  ({100*n/n_rows:5.1f}%)  {flag}")

    print(f"\nF0.5 calculation:")
    print(f"  N (committed = TP+FP+TN+FN):  {n_committed}")
    print(f"  Precision = TP/(TP+FP) = {tp}/{tp+fp:>2}  = {precision:.3f}")
    print(f"  Recall    = TP/(TP+FN) = {tp}/{tp+fn:>2}  = {recall:.3f}")
    print(f"  F0.5 = 1.25*P*R / (0.25*P+R) = {f05:.3f}")
    print(f"  Wilson 95% CI (approx via P/R bounds): [{f05_ci[0]:.3f}, {f05_ci[1]:.3f}]")

    if n_unexpected:
        print(f"\n⚠ UNEXPECTED cells (outcome_label outside taxonomy): {n_unexpected}")

    return {
        "label": label,
        "n_rows": n_rows,
        "cells": dict(cell_counts),
        "verdict_counts": dict(verdict_counts),
        "truth_counts": dict(truth_counts),
        "precision": precision,
        "recall": recall,
        "f0_5": f05,
        "f0_5_ci": f05_ci,
        "n_committed": n_committed,
        "n_filter": n_filter,
    }


def load_csv(path):
    if not path.exists():
        return []
    with path.open() as f:
        return list(csv.DictReader(f))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("path", help="results CSV path")
    args = parser.parse_args()
    rows = load_csv(Path(args.path))
    compute_stats(rows, args.path)


if __name__ == "__main__":
    main()
