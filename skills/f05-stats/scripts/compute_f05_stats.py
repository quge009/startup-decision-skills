"""Compute F0.5 and four-cell verdict-to-reference statistics.

Reads A5/A6 step 1 results CSV and recomputes evaluation cells fresh from
(framework class, verdict, outcome_label) per Report §4.

**Framework outputs 2 things per candidate:**
  - `class`: archetype classification (one of 7 in-scope archetypes OR "out-of-scope")
  - `verdict`: PASS / WARN / FAIL — design intent is one verdict per candidate.
    Under v1.4 implementation, OOS-class candidates short-circuit at Step 1b and
    produce no verdict; v1.5 will close this gap.

**Truth has 2 dimensions:**
  - Main truth (used for verdict F0.5): SUCCESS / FAILURE / AMBIGUOUS — derived
    from `outcome_label` (external Crunchbase signal)
  - Aux truth (used for class accuracy): independent archetype labeler — NOT
    framework code; built in step 2. Currently absent in step 1 demo.

**Binary classifier setup for F0.5** (per Report §4):
  - Predicted positive = verdict is PASS
  - Predicted negative = verdict is WARN ∪ FAIL ∪ no-verdict
    (no-verdict from v1.4 OOS short-circuit counts here — functionally
    "framework didn't recommend investment")
  - Only filter is AMBIG truth (no ground truth available)

| Main truth | PASS | WARN / FAIL / no-verdict |
|------------|------|--------------------------|
| SUCCESS    | TP   | FN                       |
| FAILURE    | FP   | TN                       |
| AMBIG      | filter (FILTER_truth_ambig)             ||

For diagnostic purposes the script reports composition of TN / FN by whether
the candidate came from v1.4 OOS short-circuit (no-verdict) path or from a
verdict path.

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
IN_SCOPE_ARCH = {"Distribution", "Frontier-model-owner", "OSS-on-rented-GPU",
                 "Hyperscaler-bundle", "Hardware-vertical", "Enterprise/Rental", "CN-private"}


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


def cell_for(framework_class, verdict, outcome_label):
    """Map (framework_class, verdict, outcome_label) → cell per Report §4.

    Predicted positive = verdict == PASS.
    Predicted negative = WARN ∪ FAIL ∪ no-verdict (v1.4 OOS short-circuit).
    Filter only on AMBIG truth.

    Returns one of: TP, FP, TN, FN, FILTER_truth_ambig, UNEXPECTED.
    """
    if outcome_label in AMBIG_TRUTH:
        return "FILTER_truth_ambig"
    if outcome_label in SUCCESS_TRUTH:
        return "TP" if verdict == "PASS" else "FN"
    if outcome_label in FAILURE_TRUTH:
        return "FP" if verdict == "PASS" else "TN"
    return "UNEXPECTED"


def is_oos_no_verdict(framework_class, verdict):
    """Diagnostic: did this candidate come from v1.4 OOS short-circuit (no verdict produced)?"""
    return framework_class not in IN_SCOPE_ARCH or verdict not in {"PASS", "WARN", "FAIL"}


def classify_truth(outcome_label):
    if outcome_label in SUCCESS_TRUTH: return "SUCCESS"
    if outcome_label in FAILURE_TRUTH: return "FAILURE"
    if outcome_label in AMBIG_TRUTH: return "AMBIG"
    return "OTHER"


def compute_stats(rows, label):
    """Recompute F0.5 + diagnostics fresh from (verdict, outcome_label) using
    Option 2 mapping. Ignores legacy evaluation_class column from CSV.
    """
    n_rows = len(rows)
    if n_rows == 0:
        print(f"\n=== {label}: 0 rows — skipping ===")
        return None

    cell_counts = collections.Counter()
    # Diagnostic sub-counter: of TN/FN cells, how many came from v1.4 OOS short-circuit
    tn_oos_no_verdict = 0
    fn_oos_no_verdict = 0
    verdict_counts = collections.Counter()
    truth_counts = collections.Counter()
    archetype_cells = collections.defaultdict(collections.Counter)  # by A5/A6 framework archetype output (viewer dimension)
    sample_strata_counts = collections.Counter()

    # Archetype reproducibility (A3 step 1 vs A5/A6 Step 1b) — diagnostic only,
    # not part of truth definition. Measures LLM reproducibility of the same
    # SKILL.md path executed twice.
    archetype_match = 0
    archetype_mismatch_to_oos = 0
    archetype_mismatch_other = 0
    archetype_mismatch_oos_to_inscope = 0
    archetype_a5a6_missing = 0

    for r in rows:
        verdict = (r.get("verdict") or "").strip()
        outcome = (r.get("outcome_label") or "").strip()
        framework_class = (r.get("archetype_primary_a5a6") or "").strip()
        cell = cell_for(framework_class, verdict, outcome)
        cell_counts[cell] += 1
        if cell == "TN" and is_oos_no_verdict(framework_class, verdict):
            tn_oos_no_verdict += 1
        if cell == "FN" and is_oos_no_verdict(framework_class, verdict):
            fn_oos_no_verdict += 1
        verdict_counts[verdict or "(empty)"] += 1
        truth_counts[classify_truth(outcome)] += 1
        a5a6 = framework_class or (r.get("archetype_primary_a3") or "?").strip()
        archetype_cells[a5a6][cell] += 1
        sample_strata_counts[r.get("sample_strata", "?")] += 1

        a3 = (r.get("archetype_primary_a3") or "").strip()
        a6 = framework_class
        if not a6:
            archetype_a5a6_missing += 1
        elif a3 == a6:
            archetype_match += 1
        else:
            if a3 in IN_SCOPE_ARCH and a6 == "out-of-scope":
                archetype_mismatch_to_oos += 1
            elif a3 in IN_SCOPE_ARCH and a6 in IN_SCOPE_ARCH:
                archetype_mismatch_other += 1
            elif a3 == "out-of-scope" and a6 in IN_SCOPE_ARCH:
                archetype_mismatch_oos_to_inscope += 1
            else:
                archetype_mismatch_other += 1

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
    for v in ["PASS", "WARN", "FAIL", "OUT_OF_SCOPE", "ERROR"]:
        n = verdict_counts.get(v, 0)
        if n:
            print(f"  {v:<14}  {n:>3}  ({100*n/n_rows:5.1f}%)")

    print(f"\n4-cell + filter distribution (per Report §4: positive=PASS verdict; predicted-negative includes WARN/FAIL/no-verdict):")
    for cell in ["TP", "FP", "TN", "FN", "FILTER_truth_ambig", "UNEXPECTED"]:
        n = cell_counts.get(cell, 0)
        flag = "← in F0.5" if cell in {"TP", "FP", "TN", "FN"} else "  diagnostic"
        print(f"  {cell:<22}  {n:>3}  ({100*n/n_rows:5.1f}%)  {flag}")

    tn_in_scope = tn - tn_oos_no_verdict
    fn_in_scope = fn - fn_oos_no_verdict
    if tn_oos_no_verdict + fn_oos_no_verdict > 0:
        print(f"\nDiagnostic — composition of TN / FN by v1.4 OOS short-circuit:")
        print(f"  TN = {tn_in_scope:>3} (in-scope class, WARN/FAIL verdict)  + {tn_oos_no_verdict:>3} (OOS class, no verdict)")
        print(f"  FN = {fn_in_scope:>3} (in-scope class, WARN/FAIL verdict)  + {fn_oos_no_verdict:>3} (OOS class, no verdict)")
        oos_short = tn_oos_no_verdict + fn_oos_no_verdict
        if n_committed:
            print(f"  v1.4 OOS short-circuit accounts for {oos_short}/{n_committed} ({100*oos_short/n_committed:.0f}%) of the 4-cell.")

    print(f"\nF0.5 calculation:")
    print(f"  N (committed = TP+FP+TN+FN):  {n_committed}")
    print(f"  Precision = TP/(TP+FP) = {tp}/{tp+fp:>2}  = {precision:.3f}")
    print(f"  Recall    = TP/(TP+FN) = {tp}/{tp+fn:>2}  = {recall:.3f}")
    print(f"  F0.5 = 1.25*P*R / (0.25*P+R) = {f05:.3f}")
    print(f"  Wilson 95% CI (approx via P/R bounds): [{f05_ci[0]:.3f}, {f05_ci[1]:.3f}]")

    print(f"\nArchetype reproducibility (DIAGNOSTIC, not in truth):")
    print(f"  A3 step 1 (classify_only path) vs A5/A6 Step 1b (full pipeline path) — same SKILL.md,")
    print(f"  same input. Discrepancies measure LLM non-determinism, NOT framework correctness.")
    n_arch_total = archetype_match + archetype_mismatch_to_oos + archetype_mismatch_other + archetype_mismatch_oos_to_inscope + archetype_a5a6_missing
    if n_arch_total > 0:
        print(f"  Match (a3 == a5a6): {archetype_match}/{n_arch_total} = {100*archetype_match/n_arch_total:.1f}%")
        print(f"  A3 in-scope → A5/A6 OOS: {archetype_mismatch_to_oos} ({100*archetype_mismatch_to_oos/n_arch_total:.1f}%)")
        print(f"  A3 in-scope → different in-scope: {archetype_mismatch_other} ({100*archetype_mismatch_other/n_arch_total:.1f}%)")
        print(f"  A3 OOS → A5/A6 in-scope: {archetype_mismatch_oos_to_inscope} ({100*archetype_mismatch_oos_to_inscope/n_arch_total:.1f}%)")
        if archetype_a5a6_missing:
            print(f"  Missing a5a6 archetype: {archetype_a5a6_missing}")

    if n_unexpected:
        print(f"\n⚠ UNEXPECTED cells (outcome_label outside taxonomy): {n_unexpected}")

    print(f"\nPer-archetype verdict viewer (using A5/A6 archetype as grouping; NOT truth):")
    print(f"  {'archetype':<25} {'N':>4} {'TP':>3} {'FP':>3} {'TN':>3} {'FN':>3} {'amb':>4} {'P':>5} {'R':>5} {'F0.5':>5}")
    for arch, cells in sorted(archetype_cells.items(), key=lambda x: -sum(x[1].values())):
        n_arch = sum(cells.values())
        if n_arch < 5:
            continue
        a_tp = cells.get("TP", 0)
        a_fp = cells.get("FP", 0)
        a_tn = cells.get("TN", 0)
        a_fn = cells.get("FN", 0)
        a_amb = cells.get("FILTER_truth_ambig", 0)
        a_n_commit = a_tp + a_fp + a_tn + a_fn
        if a_n_commit >= 4:
            a_p = a_tp / (a_tp + a_fp) if (a_tp + a_fp) else 0.0
            a_r = a_tp / (a_tp + a_fn) if (a_tp + a_fn) else 0.0
            a_f = f_beta(a_p, a_r, 0.5)
            print(f"  {arch:<25} {n_arch:>4} {a_tp:>3} {a_fp:>3} {a_tn:>3} {a_fn:>3} {a_amb:>4} "
                  f"{a_p:>5.2f} {a_r:>5.2f} {a_f:>5.2f}")
        else:
            print(f"  {arch:<25} {n_arch:>4} {a_tp:>3} {a_fp:>3} {a_tn:>3} {a_fn:>3} {a_amb:>4}  "
                  f"(committed N={a_n_commit} too few)")

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
        "archetype_match_rate": (archetype_match / max(n_arch_total, 1)),
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
