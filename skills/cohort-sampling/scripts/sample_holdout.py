#!/usr/bin/env python3
"""Sample a reproducible stratified holdout while excluding prior IDs."""

import argparse
import csv
import random
from collections import Counter, defaultdict
from pathlib import Path


def read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        return list(reader.fieldnames or []), list(reader)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--exclude", type=Path, action="append", default=[])
    parser.add_argument("--id-column", default="id")
    parser.add_argument("--label-column", default="outcome_label")
    parser.add_argument("--sample-size", type=int, required=True)
    parser.add_argument("--train-ratio", type=float, default=0.7)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--train-output", type=Path, required=True)
    parser.add_argument("--validation-output", type=Path, required=True)
    args = parser.parse_args()
    if args.sample_size < 1 or not 0 < args.train_ratio < 1:
        parser.error("sample size must be positive and train ratio between 0 and 1")
    fields, rows = read_rows(args.input)
    for name in (args.id_column, args.label_column):
        if name not in fields:
            raise ValueError(f"missing column: {name}")
    excluded = set()
    for path in args.exclude:
        _, prior = read_rows(path)
        excluded.update(row.get(args.id_column, "") for row in prior)
    pools = defaultdict(list)
    for row in rows:
        if row[args.id_column] not in excluded and row[args.label_column]:
            pools[row[args.label_column]].append(row)
    available = sum(map(len, pools.values()))
    if args.sample_size > available:
        raise ValueError(f"requested {args.sample_size}, only {available} eligible rows")
    rng = random.Random(args.seed)
    labels = sorted(pools)
    raw = {key: args.sample_size * len(pools[key]) / available for key in labels}
    quota = {key: int(raw[key]) for key in labels}
    for key in sorted(labels, key=lambda k: (raw[k] - quota[k], k), reverse=True)[:args.sample_size-sum(quota.values())]:
        quota[key] += 1
    train, validation = [], []
    for key in labels:
        rng.shuffle(pools[key])
        selected = pools[key][:quota[key]]
        cut = round(len(selected) * args.train_ratio)
        train.extend(selected[:cut]); validation.extend(selected[cut:])
    rng.shuffle(train); rng.shuffle(validation)
    for path, subset in ((args.train_output, train), (args.validation_output, validation)):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, quoting=csv.QUOTE_ALL)
            writer.writeheader(); writer.writerows(subset)
    print({"sample": len(train) + len(validation), "train": len(train),
           "validation": len(validation), "by_label": dict(Counter(row[args.label_column] for row in train + validation))})


if __name__ == "__main__":
    main()
