#!/usr/bin/env python3
"""Create a fail-closed, review-ready Interface identity package."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

import pyarrow.parquet as pq

from apply_interface_name_recovery import source_row_sha256

PACKAGE_FILES = {
    "unique_name_decisions": "unique_name_decisions.jsonl",
    "event_resolution_overlay": "event_resolution_overlay.jsonl",
    "new_investors_entity": "new_investors_entity.jsonl",
    "new_investors_provenance": "new_investors_provenance.jsonl",
    "associated_entity": "associated_entity.jsonl",
    "associated_provenance": "associated_provenance.jsonl",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_jsonl(path: Path, rows: list[dict]) -> None:
    payload = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    path.write_text(payload, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interface", type=Path, required=True)
    parser.add_argument("--investors", type=Path, required=True)
    parser.add_argument("--legacy-investors", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir.expanduser().resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite {output}")
    output.mkdir(parents=True)

    table = pq.read_table(args.interface)
    by_name: dict[str, list[dict]] = defaultdict(list)
    for row in table.to_pylist():
        name = row.get("raw_investor_name")
        if row.get("investor_id") is None and isinstance(name, str) and name.strip():
            by_name[name].append(row)
    decisions, overlays = [], []
    for name in sorted(by_name, key=lambda value: value.encode("utf-8")):
        rows = by_name[name]
        event_ids = sorted((row["event_id"] for row in rows), key=lambda value: value.encode("utf-8"))
        decisions.append({
            "schema_version": "interface-counterparty-name-decision-v1",
            "raw_name": name, "normalized_name": " ".join(name.casefold().split()),
            "resolution_status": "UNRESOLVED", "investor_id": None,
            "associated_entity_id": None, "confidence": "low",
            "rationale": "Pending human review", "evidence_urls": [],
            "adjudication_sources": [], "row_count": len(rows), "event_ids": event_ids,
        })
        for row in sorted(rows, key=lambda item: item["event_id"].encode("utf-8")):
            overlays.append({
                "schema_version": "interface-counterparty-event-resolution-v1",
                "source_event_id": row["event_id"], "source_raw_investor_name": name,
                "source_row_sha256": source_row_sha256(row, table.schema),
                "counterparty_resolution_status": "UNRESOLVED",
                "investor_id": None, "associated_entity_id": None,
            })

    content = {"unique_name_decisions": decisions, "event_resolution_overlay": overlays,
               "new_investors_entity": [], "new_investors_provenance": [],
               "associated_entity": [], "associated_provenance": []}
    for key, filename in PACKAGE_FILES.items():
        write_jsonl(output / filename, content[key])
    sources = {
        "interface_events": args.interface,
        "investors_entity_v0.2": args.investors,
        "investors_entity_v0.1_legacy": args.legacy_investors,
        "investors_provenance_v0.1": args.provenance,
    }
    manifest = {
        "schema_version": "interface-counterparty-identity-manifest-v1",
        "source": {key: {"path": path.name, "sha256": sha256(path),
                         "rows": pq.read_metadata(path).num_rows}
                   for key, path in sources.items()},
        "files": {key: {"path": filename, "sha256": sha256(output / filename),
                         "records": len(content[key])}
                  for key, filename in PACKAGE_FILES.items()},
        "counts": {"unique_names": len(decisions), "target_events": len(overlays)},
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"output": str(output), "unique_names": len(decisions),
                      "target_events": len(overlays)}))


if __name__ == "__main__":
    main()
