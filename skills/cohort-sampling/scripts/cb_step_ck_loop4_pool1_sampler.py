"""Loop 4 pool 1 sampler — labeled-only 4000 rows from untouched cohort_v2, 7:3 train/val split.

Fork of iter 3 sampler (`cb_step_p_iter3_sampler.py`) with:
  - Scale: pool 1 (396) × 10.1 → 4000 total labeled rows
    Sub-label counts computed by fraction × 4000 rounded to nearest int:
      POSITIVE_LATE_STAGE_FUNDED  848  (84/396 × 4000)
      POSITIVE_IPO                 61  (6/396 × 4000)
      POSITIVE_ACQUIRED            61  (6/396 × 4000)
      NEGATIVE_NO_TRACTION       2606  (258/396 × 4000)
      NEGATIVE_CLOSED             424  (42/396 × 4000)
      ─────────────────────────────
      Total                      4000
  - Exclusion: pool 1's 396 ids (iter 3 + iter 5 samples) —— Loop 4 needs
    unbiased holdout, cannot reuse rows already touched by prior iters.
  - Clean cards constraint: A5.2 full-cohort output at
    `workspace_a5/working/<id>/cleaned_card.md` (100% hit rate on 37,569
    cohort_v2 rows per 2026-08-05 spot-check).
  - Seed=45 (iter 3=43, iter 5=44, chronological continuation).
  - 7:3 train/val split, stratified per sub-label (each sub-label
    independently split 7:3 to preserve labeled distribution in both halves).

Rationale (see `_AUDIT_2026-07-06.md §20.5` + `_ITERATIONS.md iter 17`):
  Loop 4 = frozen unbiased holdout for Q1 (baseline generalization test).
  Follows pool 1 sub-label structure for apples-to-apples F0.5 comparison
  vs pool 1 F0.5=0.6301. Val 1199 preserved as clean external-headline
  holdout in case future top-5 candidate rerun consumes train 2801.

F0.5 CI half-width (Wilson, F0.5=0.63):
  - Full 4000 (baseline validation): ±0.015
  - Train 2801 (future feature rerun): ±0.018
  - Val 1199 (future external headline): ±0.028

Output (paths overridable via CLI flags or env vars, see --help):
  <out-train>/a5_loop4_pool1_train.csv (2801 rows)
  <out-val>/a5_loop4_pool1_val.csv (1199 rows)

Usage:
  python3 cb_step_ck_loop4_pool1_sampler.py             # dry-run (default): print planned distribution + first 5 ids per sub-label + first 5 id sanity check per output, DO NOT write CSVs
  python3 cb_step_ck_loop4_pool1_sampler.py --write     # actually write output CSVs

All input/output paths default to
  `<home>/.data/pipeline_benchmark/crunchbase_filtered/` (override home via
  CB_DATA_HOME; per-file overrides via --input-csv / --pool1-iter3 /
  --pool1-iter5 / --clean-a5 / --out-train / --out-val).
"""

import argparse
import csv
import os
import random
import sys
from collections import Counter
from pathlib import Path

csv.field_size_limit(sys.maxsize)

# --- Config ---
def _default_filtered_dir():
    home = Path(os.environ.get("CB_DATA_HOME", str(Path.home())))
    return home / "_data" / "pipeline_benchmark" / "crunchbase_filtered"


def _default_clean_a5():
    home = Path(os.environ.get("CB_DATA_HOME", str(Path.home())))
    return home / "agents" / "business-predictive-model" / "workspace_a5" / "working"

SEED = 45
TRAIN_RATIO = 0.7  # per-sublabel: train = round(count * 0.7), val = count - train

TARGETS = {
    "POSITIVE_LATE_STAGE_FUNDED": 848,
    "POSITIVE_IPO": 61,
    "POSITIVE_ACQUIRED": 61,
    "NEGATIVE_NO_TRACTION": 2606,
    "NEGATIVE_CLOSED": 424,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="actually write CSVs (default: dry-run)")
    ap.add_argument("--input-csv", default=os.environ.get("CB_INPUT_CSV", str(_default_filtered_dir() / "cohort_v2_augmented.csv")))
    ap.add_argument("--pool1-iter3", default=os.environ.get("CB_POOL1_ITER3", str(_default_filtered_dir() / "a5_train_sample_iter3.csv")))
    ap.add_argument("--pool1-iter5", default=os.environ.get("CB_POOL1_ITER5", str(_default_filtered_dir() / "a5_train_sample_iter5.csv")))
    ap.add_argument("--clean-a5", default=os.environ.get("CB_CLEAN_A5", str(_default_clean_a5())))
    ap.add_argument("--out-train", default=os.environ.get("CB_OUT_TRAIN", str(_default_filtered_dir() / "a5_loop4_pool1_train.csv")))
    ap.add_argument("--out-val", default=os.environ.get("CB_OUT_VAL", str(_default_filtered_dir() / "a5_loop4_pool1_val.csv")))
    args = ap.parse_args()

    INPUT_CSV = Path(args.input_csv)
    POOL1_ITER3 = Path(args.pool1_iter3)
    POOL1_ITER5 = Path(args.pool1_iter5)
    CLEAN_A5 = Path(args.clean_a5)
    OUT_TRAIN = Path(args.out_train)
    OUT_VAL = Path(args.out_val)

    # Load pool 1 exclusion set
    pool1_ids = set()
    for path in (POOL1_ITER3, POOL1_ITER5):
        with path.open() as f:
            for r in csv.DictReader(f):
                pool1_ids.add(r["id"])
    print(f"Pool 1 exclusion: {len(pool1_ids)} rows")

    # Build per-sublabel pools with constraints applied
    pools = {label: [] for label in TARGETS}
    fieldnames = None
    scanned = 0
    with INPUT_CSV.open() as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames)
        for r in reader:
            scanned += 1
            o = (r.get("outcome_label") or "").strip()
            if o not in TARGETS:
                continue
            if r["id"] in pool1_ids:
                continue
            if not (CLEAN_A5 / r["id"] / "cleaned_card.md").exists():
                continue
            pools[o].append(r)
    print(f"Scanned {scanned} cohort rows\n")

    # Availability check
    print(f"{'Sub-label':<32} {'Pool':>6} {'Need':>6} {'HR':>7}")
    for label, need in TARGETS.items():
        avail = len(pools[label])
        hr = f"{avail/need:.1f}x" if need else "inf"
        marker = "" if avail >= need else "  ❌ SHORT"
        print(f"  {label:<32} {avail:>6} {need:>6} {hr:>7}{marker}")
        if avail < need:
            print(f"    ERROR: pool insufficient for {label}", file=sys.stderr)
            sys.exit(1)

    # Sample + 7:3 split
    rng = random.Random(SEED)
    train_rows, val_rows = [], []
    print(f"\n{'Sub-label':<32} {'Sample':>6} {'Train':>6} {'Val':>6}")
    for label, need in TARGETS.items():
        picked = rng.sample(pools[label], need)
        n_train = round(need * TRAIN_RATIO)
        n_val = need - n_train
        # Shuffle picked so first n_train become train
        rng.shuffle(picked)
        for i, row in enumerate(picked):
            row["loop4_strata"] = label
            row["loop4_split"] = "train" if i < n_train else "val"
        train_rows.extend(picked[:n_train])
        val_rows.extend(picked[n_train:])
        print(f"  {label:<32} {need:>6} {n_train:>6} {n_val:>6}")

    print(f"\nTotal picked: {len(train_rows) + len(val_rows)}")
    print(f"  Train: {len(train_rows)}")
    print(f"  Val:   {len(val_rows)}")

    tc = Counter((r["outcome_label"], r["loop4_split"]) for r in train_rows + val_rows)
    print(f"\nStratum × split verification:")
    for label in TARGETS:
        print(f"  {label:<32} train={tc[(label,'train')]:>4}  val={tc[(label,'val')]:>4}")

    print(f"\nFirst 5 train ids per sub-label (sanity check for randomness):")
    for label in TARGETS:
        ids = [r["id"][:8] for r in train_rows if r["outcome_label"] == label][:5]
        print(f"  {label:<32} {ids}")

    print(f"\nFirst 5 val ids per sub-label:")
    for label in TARGETS:
        ids = [r["id"][:8] for r in val_rows if r["outcome_label"] == label][:5]
        print(f"  {label:<32} {ids}")

    if not args.write:
        print(f"\n[DRY RUN] Not writing CSVs. Re-run with --write to persist.")
        return

    out_fields = fieldnames + ["loop4_strata", "loop4_split"]
    OUT_TRAIN.parent.mkdir(parents=True, exist_ok=True)
    with OUT_TRAIN.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=out_fields, extrasaction="ignore")
        w.writeheader()
        for r in train_rows:
            w.writerow(r)
    print(f"\nOK wrote {OUT_TRAIN} ({len(train_rows)} rows)")

    with OUT_VAL.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=out_fields, extrasaction="ignore")
        w.writeheader()
        for r in val_rows:
            w.writerow(r)
    print(f"OK wrote {OUT_VAL} ({len(val_rows)} rows)")


if __name__ == "__main__":
    main()
