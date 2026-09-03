"""Build a funding-event Parquet from per-company JSON caches.

The builder is network-free. It accepts caches produced by the v0.2 collector
as a documented compatibility migration; future collection uses the matching
v0.3 collector. The output schema is defined explicitly below.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).parent))
from _common import load_investor_id_map, make_slug, normalize_name
from event_claim_resolution import normalize_partial_date, resolve_event_claims
from schema_contract_loader import load_schema_contract

SCHEMA_VERSION = "v0.3.0"

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas/funding_events_v0.3.schema.json"
FUNDING_SCHEMA, EVENT_TYPES = load_schema_contract(SCHEMA_PATH)


def _event_id(
    company_id: str,
    event_type: str,
    round_name: str | None,
    announce_date: str | None,
    raw_investor_name: str | None,
    role: str,
) -> str:
    key = "|".join([
        company_id,
        event_type,
        round_name or "",
        announce_date or "",
        normalize_name(raw_investor_name or "unknown"),
        role,
    ])
    return "fund:" + hashlib.sha1(key.encode()).hexdigest()[:20]


def _participants(event: dict, event_type: str) -> list[tuple[str | None, str]]:
    if event_type in {"funding_round", "funding_failed", "funding_withdrawn"}:
        values: list[tuple[str | None, str]] = []
        lead = event.get("lead_investor")
        if lead:
            values.append((lead, "lead"))
        values.extend((name, "co") for name in (event.get("co_investors") or []) if name)
        return values or [(None, "unknown")]
    if event_type == "acquired":
        return [(event.get("acquirer"), "acquirer")]
    return [(None, "unknown")]


def _to_int(value):
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _explode_event(event: dict, company: dict) -> list[dict]:
    event_type = event.get("event_type") or "funding_round"
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unsupported funding event_type: {event_type!r}")

    if event_type in {"funding_round", "funding_failed", "funding_withdrawn"}:
        round_name = str(event.get("round_name") or "").strip() or "unknown"
    else:
        round_name = None
    announce_date = normalize_partial_date(event.get("announce_date"))
    source_type = event.get("source_type") or "tavily"
    migrated_v02 = "source_type" not in event and "source_url" not in event
    source_table = "web_search_v02" if migrated_v02 else "web_search_v03"
    rows = []
    for raw_name, role in _participants(event, event_type):
        rows.append({
            "event_id": _event_id(
                company["company_id"], event_type, round_name,
                announce_date, raw_name, role,
            ),
            "company_id": company["company_id"],
            "investor_id": None,
            "investor_name": None,
            "raw_investor_name": raw_name,
            "role": role,
            "company_normalized_name": company["company_dedup_key"],
            "company_name_raw": company["company_canonical_name"],
            "round_name": round_name,
            "announce_date": announce_date,
            "amount_usd": _to_int(event.get("amount_usd")),
            "per_vc_amount_usd": None,
            "source_table": source_table,
            "source_url": event.get("source_url"),
            "confidence": float(event.get("confidence") or 0.0),
            "schema_version": SCHEMA_VERSION,
            "investor_type": None,
            "event_type": event_type,
            "event_subtype": round_name,
            "position_change_shares": None,
            "position_value_usd": None,
            "data_source": f"{source_type}+deepseek",
            "metadata_json": json.dumps({
                "raw": event,
                "cache_migration": "v0.2-compatible" if migrated_v02 else None,
            }, ensure_ascii=False),
        })
    return rows


def _match_investors(rows: list[dict], inv_id_map: dict) -> None:
    for row in rows:
        raw = row["raw_investor_name"]
        if not raw:
            continue
        investor_id = inv_id_map.get(make_slug(raw)) or inv_id_map.get(normalize_name(raw))
        if investor_id:
            row["investor_id"] = investor_id
            row["investor_name"] = raw


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Build a schema-conformant funding-event Parquet from JSON caches")
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

    entity = pq.read_table(entity_path).to_pylist()
    by_slug = {row["slug"]: row for row in entity}
    rows: list[dict] = []
    skipped_unknown = 0
    invalid_cache = 0
    cache_files = sorted(cache_dir.glob("*.json"))
    for cache_file in cache_files:
        company = by_slug.get(cache_file.stem)
        if company is None:
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
        for event in events:
            if isinstance(event, dict):
                rows.extend(_explode_event(event, company))

    _match_investors(rows, load_investor_id_map())
    rows, resolution = resolve_event_claims(rows, "funding")
    table = pa.Table.from_pylist(rows, schema=FUNDING_SCHEMA)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, output_path)

    print(f"read {len(cache_files)} cache files from {cache_dir}")
    print(f"wrote {table.num_rows} rows to {output_path}")
    print(f"skipped_unknown_slug={skipped_unknown} invalid_cache={invalid_cache}")
    print(f"claim_resolution={json.dumps(resolution, sort_keys=True)}")


if __name__ == "__main__":
    main()
