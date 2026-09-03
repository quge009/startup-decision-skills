#!/usr/bin/env python3
"""Build a universe-agnostic canonical registry from reviewed Pattern records."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

RESULTS = ("RECOMMEND", "AVOID", "INDIFFERENT", "INCONCLUSIVE")
REQUIRED = {"pattern_id", "result", "rule"}


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), allow_nan=False) + "\n").encode()


def load_records(path: Path, batch: str) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    rows = value.get("patterns") if isinstance(value, dict) else value
    if not isinstance(rows, list):
        raise ValueError(f"{path}: expected a JSON array or an object with patterns[]")
    output = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or not REQUIRED <= set(row):
            raise ValueError(f"{path}: pattern {index} lacks {sorted(REQUIRED)}")
        if row["result"] not in RESULTS:
            raise ValueError(f"{path}: invalid result {row['result']!r}")
        item = dict(row)
        item["batch"] = batch
        output.append(item)
    return output


def build_registry(inputs: list[tuple[str, Path]], universe_id: str,
                   universe_size: int, ordering: str) -> dict[str, Any]:
    records = [row for batch, path in inputs for row in load_records(path, batch)]
    ids = [row["pattern_id"] for row in records]
    if len(ids) != len(set(ids)):
        duplicates = sorted({item for item in ids if ids.count(item) > 1})
        raise ValueError(f"duplicate pattern_id: {duplicates[:10]}")
    records.sort(key=lambda row: row["pattern_id"].encode("utf-8"))
    counts = {result: sum(row["result"] == result for row in records) for result in RESULTS}
    sources = [{"batch": batch, "path": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
               for batch, path in inputs]
    return {
        "schema_version": "pattern-registry-v1",
        "universe": {"id": universe_id, "size": universe_size, "ordering": ordering},
        "counts": {"total": len(records), "by_result": counts},
        "sources": sources,
        "patterns": records,
    }


def write_atomic(path: Path, value: Any) -> None:
    path = path.expanduser().resolve()
    if path.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical_bytes(value))
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try: os.unlink(temporary)
        except FileNotFoundError: pass
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", action="append", required=True, metavar="NAME=JSON",
                        help="Reviewed pattern JSON; repeat for each batch")
    parser.add_argument("--universe-id", required=True)
    parser.add_argument("--universe-size", required=True, type=int)
    parser.add_argument("--ordering", default="company_id UTF-8 lexical ascending")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    inputs = []
    for value in args.batch:
        if "=" not in value:
            parser.error("--batch must be NAME=JSON")
        name, raw_path = value.split("=", 1)
        if not name or not raw_path:
            parser.error("--batch must be NAME=JSON")
        inputs.append((name, Path(raw_path).expanduser().resolve()))
    if args.universe_size < 1:
        parser.error("--universe-size must be positive")
    registry = build_registry(inputs, args.universe_id, args.universe_size, args.ordering)
    write_atomic(args.output, registry)
    print(json.dumps({"output": str(args.output.resolve()), "patterns": registry["counts"]["total"]}))


if __name__ == "__main__":
    main()
