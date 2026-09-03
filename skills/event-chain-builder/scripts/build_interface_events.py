"""Build an interface-event Parquet from per-company JSON caches."""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).parent))
from _common import load_investor_id_map, normalize_name
from event_claim_resolution import normalize_partial_date, resolve_event_claims
from schema_contract_loader import load_schema_contract

SCHEMA_VERSION = "v0.3.0"

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas/interface_events_v0.3.schema.json"
INTERFACE_SCHEMA, EVENT_TYPES = load_schema_contract(SCHEMA_PATH)


def _event_id(
    company_id: str,
    event_date: str | None,
    title: str,
    raw_investor_name: str | None,
) -> str:
    key = (
        f"{company_id}|{event_date or ''}|{title.strip().lower()}|"
        f"{normalize_name(raw_investor_name or '')}"
    )
    return "int:" + hashlib.sha1(key.encode()).hexdigest()[:20]


def _to_row(
    event: dict, company_id: str, scraped_at: str, investor_map: dict,
) -> dict:
    event_type = event.get("event_type") or "other"
    if event_type not in EVENT_TYPES:
        event_type = "other"
    title = (event.get("title") or "").strip()
    event_date = normalize_partial_date(event.get("event_date"))
    raw_investor = (event.get("raw_investor_name") or "").strip() or None
    investor_id = investor_map.get(normalize_name(raw_investor)) if raw_investor else None
    return {
        "event_id": _event_id(company_id, event_date, title, raw_investor),
        "company_id": company_id,
        "investor_id": investor_id,
        "raw_investor_name": raw_investor,
        "event_date": event_date,
        "event_type": event_type,
        "event_subtype": event.get("event_subtype"),
        "title": title,
        "summary": (event.get("summary") or "").strip(),
        "source_url": event.get("source_url"),
        "source_type": event.get("source_type") or "tavily",
        "confidence": float(event.get("confidence") or 0.0),
        "scraped_at": scraped_at,
        "schema_version": SCHEMA_VERSION,
        "metadata_json": json.dumps({"raw": event}, ensure_ascii=False),
    }


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Build a schema-conformant interface-event Parquet from JSON caches")
    ap.add_argument("--entity-path", required=True)
    ap.add_argument("--cache-dir", required=True)
    ap.add_argument("--output-path", required=True)
    ap.add_argument("--force-output", action="store_true")
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    entity_path = Path(args.entity_path).expanduser()
    cache_dir = Path(args.cache_dir).expanduser()
    output_path = Path(args.output_path).expanduser()
    if output_path.exists() and not args.force_output:
        raise SystemExit(
            f"refusing to overwrite existing output: {output_path} "
            "(pass --force-output to replace it)")
    if not entity_path.is_file():
        raise SystemExit(f"entity parquet missing: {entity_path}")
    if not cache_dir.is_dir():
        raise SystemExit(f"cache directory missing: {cache_dir}")

    entity = pq.read_table(entity_path, columns=["slug", "company_id"]).to_pylist()
    slug_to_id = {row["slug"]: row["company_id"] for row in entity}
    investor_map = load_investor_id_map()
    scraped_at = datetime.datetime.now(datetime.UTC).isoformat(
        timespec="seconds").replace("+00:00", "Z")
    rows = []
    skipped_unknown = 0
    invalid_cache = 0
    cache_files = sorted(cache_dir.glob("*.json"))
    for cache_file in cache_files:
        company_id = slug_to_id.get(cache_file.stem)
        if company_id is None:
            skipped_unknown += 1
            continue
        try:
            events = json.loads(cache_file.read_text())
        except Exception:
            invalid_cache += 1
            continue
        if not isinstance(events, list):
            invalid_cache += 1
            continue
        rows.extend(
            _to_row(event, company_id, scraped_at, investor_map)
            for event in events if isinstance(event, dict)
        )

    rows, resolution = resolve_event_claims(rows, "interface")
    table = pa.Table.from_pylist(rows, schema=INTERFACE_SCHEMA)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, output_path)
    print(f"read {len(cache_files)} cache files from {cache_dir}")
    print(f"wrote {table.num_rows} rows to {output_path}")
    print(f"skipped_unknown_slug={skipped_unknown} invalid_cache={invalid_cache}")
    print(f"claim_resolution={json.dumps(resolution, sort_keys=True)}")


if __name__ == "__main__":
    main()
