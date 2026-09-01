"""A4 — Train/val split for cohort_v2_classified.csv.

Splits per the design agreed in A3:
  - Scope: full cohort_v2_classified.csv (rows = whatever A3 has classified)
  - Stratification: by archetype_primary (8 buckets); outcome_label distribution
    falls out naturally within each archetype bucket.
  - Ratio: 70% train / 30% val (chosen step 1 demo ratio; see _PLAN.md)
  - Seed: 42 (reproducibility)
  - Edge: archetype with 1 row -> goes to train (val needs ≥1).

Outputs:
  cohort_v2_train.csv
  cohort_v2_val.csv
  (under the filtered dir, default
   `<home>/_data/pipeline_benchmark/crunchbase_filtered/`; override via
   CB_DATA_HOME for the base, or CB_FILTERED_DIR per-dir.)

Validation prints:
  - per-archetype counts in train + val (to confirm stratification preserved)
  - per-outcome_label counts in train + val (to confirm natural balance)
"""

import csv
import math
import os
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

csv.field_size_limit(sys.maxsize)

def _default_filtered_dir():
    home = Path(os.environ.get("CB_DATA_HOME", str(Path.home())))
    return Path(os.environ.get("CB_FILTERED_DIR", str(home / "_data" / "pipeline_benchmark" / "crunchbase_filtered")))

INPUT = Path(os.environ.get("CB_A4_INPUT", str(_default_filtered_dir() / "cohort_v2_classified.csv")))
TRAIN_OUT = Path(os.environ.get("CB_A4_TRAIN", str(_default_filtered_dir() / "cohort_v2_train.csv")))
VAL_OUT = Path(os.environ.get("CB_A4_VAL", str(_default_filtered_dir() / "cohort_v2_val.csv")))

VAL_RATIO = 0.30
SEED = 42


def main():
    random.seed(SEED)
    print(f"=== A4 train/val split ===", file=sys.stderr)
    print(f"  input:  {INPUT}", file=sys.stderr)
    print(f"  ratio:  {1-VAL_RATIO:.0%} train / {VAL_RATIO:.0%} val", file=sys.stderr)
    print(f"  seed:   {SEED}", file=sys.stderr)
    print(f"  strata: archetype_primary", file=sys.stderr)

    with INPUT.open() as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)
    print(f"  rows:   {len(rows):,}", file=sys.stderr)

    # Group by archetype, shuffle deterministically, split 80/20
    by_arch = defaultdict(list)
    for r in rows:
        by_arch[r["archetype_primary"]].append(r)

    train_rows = []
    val_rows = []
    print(f"\n  {'archetype':<22}  {'pool':>5}  {'train':>5}  {'val':>3}", file=sys.stderr)
    for arch in sorted(by_arch.keys(), key=lambda k: -len(by_arch[k])):
        pool = by_arch[arch][:]
        random.shuffle(pool)
        n_val = max(1, math.ceil(len(pool) * VAL_RATIO)) if len(pool) >= 2 else 0
        # Edge: if pool has 1 row, put it in train (val needs ≥1 row but we'd
        # leave train empty for that bucket — better to keep it in train)
        n_train = len(pool) - n_val
        train_rows.extend(pool[:n_train])
        val_rows.extend(pool[n_train:])
        print(f"  {arch:<22}  {len(pool):>5,}  {n_train:>5,}  {n_val:>3,}", file=sys.stderr)

    print(f"\n  {'TOTAL':<22}  {len(rows):>5,}  {len(train_rows):>5,}  {len(val_rows):>3,}", file=sys.stderr)

    # Write
    for path, rows_out in [(TRAIN_OUT, train_rows), (VAL_OUT, val_rows)]:
        with path.open("w", newline="") as f:
            w = csv.DictWriter(
                f, fieldnames=fieldnames, extrasaction="ignore",
                quoting=csv.QUOTE_ALL, escapechar="\\",
            )
            w.writeheader()
            for r in rows_out:
                w.writerow(r)
        print(f"  wrote {len(rows_out):,} rows to {path}", file=sys.stderr)

    # Verify outcome_label balance
    print(f"\n=== outcome_label distribution check ===", file=sys.stderr)
    train_oc = Counter(r.get("outcome_label", "") for r in train_rows)
    val_oc = Counter(r.get("outcome_label", "") for r in val_rows)
    all_labels = sorted(set(train_oc) | set(val_oc))
    print(f"  {'label':<28}  {'train':>7}  {'train%':>6}  {'val':>5}  {'val%':>6}", file=sys.stderr)
    for label in all_labels:
        t = train_oc[label]
        v = val_oc[label]
        tp = 100 * t / max(len(train_rows), 1)
        vp = 100 * v / max(len(val_rows), 1)
        print(f"  {label:<28}  {t:>7,}  {tp:>5.2f}%  {v:>5,}  {vp:>5.2f}%", file=sys.stderr)

    # Archetype-positive subset count (the rows that will actually drive A5/A6)
    archetype_positive = {
        "Distribution", "Frontier-model-owner", "OSS-on-rented-GPU",
        "Hyperscaler-bundle", "Hardware-vertical", "Enterprise/Rental",
        "CN-private",
    }
    train_pos = sum(1 for r in train_rows if r["archetype_primary"] in archetype_positive)
    val_pos = sum(1 for r in val_rows if r["archetype_primary"] in archetype_positive)
    print(f"\n=== archetype-positive subset (drives A5/A6 evaluation) ===", file=sys.stderr)
    print(f"  train: {train_pos:,} archetype-positive of {len(train_rows):,} ({100*train_pos/len(train_rows):.2f}%)", file=sys.stderr)
    print(f"  val:   {val_pos:,} archetype-positive of {len(val_rows):,} ({100*val_pos/len(val_rows):.2f}%)", file=sys.stderr)


if __name__ == "__main__":
    main()
