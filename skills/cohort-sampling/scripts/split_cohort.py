#!/usr/bin/env python3
"""Create a deterministic stratified train/validation CSV split."""

import argparse
import csv
import math
import random
from collections import Counter, defaultdict
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--train-output", type=Path, required=True)
    parser.add_argument("--validation-output", type=Path, required=True)
    parser.add_argument("--stratify-column", required=True)
    parser.add_argument("--validation-ratio", type=float, default=0.3)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    if not 0 < args.validation_ratio < 1:
        parser.error("--validation-ratio must be between 0 and 1")
    with args.input.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        fields, rows = reader.fieldnames, list(reader)
    if not fields or args.stratify_column not in fields:
        raise ValueError(f"missing stratification column: {args.stratify_column}")
    groups = defaultdict(list)
    for row in rows:
        groups[row[args.stratify_column]].append(row)
    rng = random.Random(args.seed)
    train, validation = [], []
    for key in sorted(groups):
        group = groups[key][:]
        rng.shuffle(group)
        count = max(1, math.ceil(len(group) * args.validation_ratio)) if len(group) > 1 else 0
        train.extend(group[:-count] if count else group)
        validation.extend(group[-count:] if count else [])
    for path, subset in ((args.train_output, train), (args.validation_output, validation)):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, quoting=csv.QUOTE_ALL)
            writer.writeheader()
            writer.writerows(subset)
    print({"input": len(rows), "train": len(train), "validation": len(validation),
           "strata": dict(Counter(row[args.stratify_column] for row in rows))})


if __name__ == "__main__":
    main()
