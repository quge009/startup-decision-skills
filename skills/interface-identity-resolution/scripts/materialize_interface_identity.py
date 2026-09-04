#!/usr/bin/env python3
"""Validate and materialize Interface counterparty identity package v1.

Offline by construction: this module imports no network client, accepts only pinned
local inputs, never mutates them, and publishes only to a new output directory.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

RESEARCH_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from apply_interface_name_recovery import source_row_sha256  # noqa:E402

REQUIRED_PYARROW_VERSION = "25.0.0"
STATUSES = {"EXISTING_INVESTOR", "NEW_INVESTOR", "ASSOCIATED_ENTITY", "AMBIGUOUS", "UNRESOLVED"}
PARQUET_WRITE_OPTIONS = {"compression": "snappy", "version": "2.6", "data_page_version": "1.0", "use_dictionary": True, "write_statistics": True, "row_group_size": 65536}
PACKAGE_FILES = {
    "unique_name_decisions": "unique_name_decisions.jsonl",
    "event_resolution_overlay": "event_resolution_overlay.jsonl",
    "new_investors_entity": "new_investors_entity.jsonl",
    "new_investors_provenance": "new_investors_provenance.jsonl",
    "associated_entity": "associated_entity.jsonl",
    "associated_provenance": "associated_provenance.jsonl",
}
ENTITY_COLUMNS = ["investor_id", "schema_version", "name", "normalized_name", "website", "domain", "location_country", "location_state", "location_city", "stage_focus", "industry_focus", "founded_year", "investor_type", "tier", "wikidata_qid", "findfunding_slug", "manual_seed_slug", "crunchbase_permalink", "source_set", "first_seen_at", "aum_usd_approx", "sec_cik"]
ASSOCIATED_COLUMNS = ["associated_entity_id", "schema_version", "name", "normalized_name", "category", "website", "domain", "location_country", "location_state", "location_city", "founded_year", "wikidata_qid", "source_set", "first_seen_at"]
PROVENANCE_COLUMNS = ["investor_id", "field", "value", "source", "source_record_id", "confidence", "evidence_url", "fetched_at", "schema_version"]
ASSOCIATED_PROVENANCE_COLUMNS = ["associated_entity_id", *PROVENANCE_COLUMNS[1:]]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def load_jsonl(path: Path, *, allow_empty: bool = False) -> list[dict[str, Any]]:
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"JSON object required at {path}:{number}")
        rows.append(value)
    if not rows and not allow_empty:
        raise ValueError(f"empty JSONL: {path}")
    return rows


def _exact_name(value: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", value.casefold(), flags=re.UNICODE).split())


def _date(value: Any) -> Any:
    if value is None or isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(value)


def _entity_schema(source: pa.Schema) -> pa.Schema:
    if source.names != ENTITY_COLUMNS:
        raise ValueError(f"investor columns differ from the required 22-column contract: {source.names}")
    fields = []
    for field in source:
        typ = field.type
        if pa.types.is_null(typ):
            typ = pa.large_string()
        elif pa.types.is_list(typ) and pa.types.is_null(typ.value_type):
            typ = pa.list_(pa.string())
        fields.append(pa.field(field.name, typ, nullable=field.nullable))
    return pa.schema(fields, metadata=source.metadata)


def _associated_schema() -> pa.Schema:
    ls, sl = pa.large_string(), pa.list_(pa.string())
    return pa.schema([
        pa.field("associated_entity_id", ls, False), pa.field("schema_version", ls, False),
        pa.field("name", ls, False), pa.field("normalized_name", ls, False),
        pa.field("category", sl, False), pa.field("website", ls), pa.field("domain", ls),
        pa.field("location_country", pa.string()), pa.field("location_state", sl),
        pa.field("location_city", sl), pa.field("founded_year", pa.int32()),
        pa.field("wikidata_qid", ls), pa.field("source_set", sl, False),
        pa.field("first_seen_at", pa.date32(), False),
    ], metadata={b"schema_version": b"v0.1.0"})


def _provenance_schema(source: pa.Schema, associated: bool = False) -> pa.Schema:
    fields = list(source)
    if associated:
        fields[0] = pa.field("associated_entity_id", fields[0].type, nullable=False)
    return pa.schema(fields, metadata=source.metadata if not associated else {b"schema_version": b"v0.1.0"})


def _interface_schema(source: pa.Schema) -> pa.Schema:
    fields = list(source)
    fields += [pa.field("associated_entity_id", pa.large_string()), pa.field("counterparty_resolution_status", pa.large_string(), False)]
    metadata = dict(source.metadata or {})
    metadata[b"schema_version"] = b"v0.4.0"
    metadata[b"investor_entity"] = b"investors_entity_v0.3.parquet"
    metadata[b"associated_entity"] = b"associated_entity_v0.1.parquet"
    return pa.schema(fields, metadata=metadata)


def _validate_output_dir(path: Path, inputs: list[Path]) -> Path:
    canonical = path.expanduser().resolve(strict=False)
    if canonical.exists() or canonical.is_symlink():
        raise FileExistsError(f"refusing to overwrite output directory: {canonical}")
    input_paths = {p.expanduser().resolve(strict=False) for p in inputs}
    if canonical in input_paths:
        raise ValueError("output aliases a protected input")
    if any(canonical in source.parents for source in input_paths):
        raise ValueError("output directory cannot contain a protected input")
    return canonical


def validate_package(package_dir: Path, source: pa.Table, source_path: Path, investors: pa.Table, investors_path: Path, legacy_path: Path, provenance_path: Path) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    manifest_path = package_dir / "manifest.json"
    manifest = load_json(manifest_path)
    if manifest.get("schema_version") != "interface-counterparty-identity-manifest-v1":
        raise ValueError("invalid package manifest schema_version")
    paths = {key: package_dir / name for key, name in PACKAGE_FILES.items()}
    required_rows = {"unique_name_decisions", "event_resolution_overlay"}
    rows = {key: load_jsonl(path, allow_empty=key not in required_rows)
            for key, path in paths.items()}
    for key, path in paths.items():
        lock = manifest["files"][key]
        if lock != {"path": path.name, "sha256": sha256_file(path), "records": len(rows[key])}:
            raise ValueError(f"package file lock mismatch: {key}")
    locks = manifest["source"]
    checks = [("interface_events", source_path, source.num_rows), ("investors_entity_v0.2", investors_path, investors.num_rows), ("investors_entity_v0.1_legacy", legacy_path, pq.read_metadata(legacy_path).num_rows), ("investors_provenance_v0.1", provenance_path, pq.read_metadata(provenance_path).num_rows)]
    for key, path, count in checks:
        if locks[key]["sha256"] != sha256_file(path) or locks[key]["rows"] != count:
            raise ValueError(f"pinned source mismatch: {key}")
    if investors.num_columns != len(ENTITY_COLUMNS):
        raise ValueError(f"investor table must contain the {len(ENTITY_COLUMNS)} contract columns")

    decisions, overlays = rows["unique_name_decisions"], rows["event_resolution_overlay"]
    if len(decisions) != len({x.get("raw_name") for x in decisions}):
        raise ValueError("decisions must cover each unique raw name exactly once")
    if len(overlays) != len({x.get("source_event_id") for x in overlays}):
        raise ValueError("event overlay must cover each source event exactly once")
    source_by = {x["event_id"]: x for x in source.to_pylist()}
    targets = {x["event_id"] for x in source_by.values() if x["investor_id"] is None and isinstance(x["raw_investor_name"], str) and x["raw_investor_name"].strip()}
    if {x["source_event_id"] for x in overlays} != targets:
        raise ValueError("event overlay does not cover all and only named-unmapped source rows")
    decision_by = {x["raw_name"]: x for x in decisions}
    for item in overlays:
        event_id, status = item["source_event_id"], item["counterparty_resolution_status"]
        source_row = source_by[event_id]
        if status not in STATUSES or item["source_raw_investor_name"] != source_row["raw_investor_name"]:
            raise ValueError(f"bad event binding/status: {event_id}")
        if item["source_row_sha256"] != source_row_sha256(source_row, source.schema):
            raise ValueError(f"source logical row hash mismatch: {event_id}")
        decision = decision_by[item["source_raw_investor_name"]]
        if status != decision["resolution_status"]:
            raise ValueError(f"event/name decision mismatch: {event_id}")
        if item.get("investor_id") != decision.get("investor_id") or item.get("associated_entity_id") != decision.get("associated_entity_id"):
            raise ValueError(f"event/name decision ID mismatch: {event_id}")
        if (item.get("investor_id") is not None) != (status in {"EXISTING_INVESTOR", "NEW_INVESTOR"}):
            raise ValueError(f"investor_id populated in wrong status: {event_id}")
        if (item.get("associated_entity_id") is not None) != (status == "ASSOCIATED_ENTITY"):
            raise ValueError(f"associated_entity_id populated in wrong status: {event_id}")
    entities, entity_prov = rows["new_investors_entity"], rows["new_investors_provenance"]
    associated, associated_prov = rows["associated_entity"], rows["associated_provenance"]
    _validate_entities(entities, entity_prov, "investor_id", ENTITY_COLUMNS)
    _validate_entities(associated, associated_prov, "associated_entity_id", ASSOCIATED_COLUMNS)
    if {x["investor_id"] for x in entities} != {x["investor_id"] for x in overlays if x["counterparty_resolution_status"] == "NEW_INVESTOR"}:
        raise ValueError("new-investor FK coverage mismatch")
    if {x["associated_entity_id"] for x in associated} != {x["associated_entity_id"] for x in overlays if x["counterparty_resolution_status"] == "ASSOCIATED_ENTITY"}:
        raise ValueError("associated-entity FK coverage mismatch")
    return manifest, rows


def _canonical_provenance_value(value: Any) -> str:
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return str(value)


def _validate_entities(entities: list[dict[str, Any]], provenance: list[dict[str, Any]], id_field: str, columns: list[str]) -> None:
    ids = [x.get(id_field) for x in entities]
    if len(ids) != len(set(ids)) or None in ids:
        raise ValueError(f"duplicate/null {id_field}")
    entity_by_id = {x[id_field]: x for x in entities}
    adjudicated = {
        "name", "normalized_name", "wikidata_qid",
        "investor_type" if id_field == "investor_id" else "category",
    }
    if id_field == "investor_id":
        adjudicated.update({"findfunding_slug", "manual_seed_slug"})
    for row in entities:
        if list(row) != columns and set(row) != set(columns):
            raise ValueError(f"{id_field} entity row does not contain every schema column")
        if not isinstance(row.get("source_set"), list):
            raise ValueError("source_set must be a list")
        list_field = "investor_type" if id_field == "investor_id" else "category"
        if row.get(list_field) is not None and not isinstance(row[list_field], list):
            raise ValueError(f"{list_field} must be a list")
        qid = row.get("wikidata_qid")
        if qid is not None and re.fullmatch(r"Q[1-9][0-9]*", qid) is None:
            raise ValueError(f"invalid accepted QID: {qid}")
    triples = [(x.get(id_field), x.get("field"), x.get("source"), x.get("source_record_id")) for x in provenance]
    if len(triples) != len(set(triples)):
        raise ValueError(f"duplicate {id_field} provenance triple")
    by_id_field = {(x[id_field], x["field"]) for x in provenance}
    for item in provenance:
        entity = entity_by_id.get(item.get(id_field))
        if entity is None:
            raise ValueError(f"orphan {id_field} provenance")
        field = item.get("field")
        if field not in adjudicated or entity.get(field) is None:
            raise ValueError(f"provenance targets non-adjudicated/null field: {item.get(id_field)}.{field}")
        expected = _canonical_provenance_value(entity[field])
        if item.get("value") != expected:
            raise ValueError(f"provenance value mismatch: {item[id_field]}.{field}")
    for row in entities:
        for field in adjudicated:
            if row.get(field) is not None and (row[id_field], field) not in by_id_field:
                raise ValueError(f"missing field provenance: {row[id_field]}.{field}")


def _coerce_entity_row(row: dict[str, Any]) -> dict[str, Any]:
    out = {key: row.get(key) for key in ENTITY_COLUMNS}
    if isinstance(out["first_seen_at"], dt.date):
        out["first_seen_at"] = out["first_seen_at"].isoformat()
    return out


def _legacy_to_base_mapping(base: list[dict[str, Any]], legacy: list[dict[str, Any]]) -> dict[str, str]:
    """Map old IDs only when exact identity keys select one authoritative base ID.

    The frozen v0.1 table is a mapping aid, never an entity input. A QID or
    punctuation-insensitive exact name is usable only when it is unique in the
    authoritative base. If the usable keys on a legacy row disagree, that old ID is
    incompatible and remains unmapped.
    """
    def unique(field: str, key=lambda x: x) -> dict[str, str]:
        buckets: dict[str, set[str]] = collections.defaultdict(set)
        for row in base:
            value = row.get(field)
            if value:
                buckets[key(value)].add(row["investor_id"])
        return {value: next(iter(ids)) for value, ids in buckets.items() if len(ids) == 1}

    base_name = unique("name", _exact_name)
    base_qid = unique("wikidata_qid")
    mapping: dict[str, str] = {}
    for old in legacy:
        candidates: set[str] = set()
        qid = old.get("wikidata_qid")
        name = old.get("name")
        if qid and qid in base_qid:
            candidates.add(base_qid[qid])
        if name:
            name_key = _exact_name(name)
            if name_key and name_key in base_name:
                candidates.add(base_name[name_key])
        if len(candidates) == 1:
            mapping[old["investor_id"]] = next(iter(candidates))
    return mapping


def _validate_dedup(rows: list[dict[str, Any]], new_start: int) -> None:
    values = [x["investor_id"] for x in rows]
    if len(values) != len(set(values)):
        raise ValueError("duplicate investor_id after append")
    base = rows[:new_start]
    base_keys = {
        "wikidata_qid": {x["wikidata_qid"] for x in base if x.get("wikidata_qid")},
        "name": {_exact_name(x["name"]) for x in base if x.get("name")},
    }
    # Duplicates frozen inside the authoritative base are tolerated. Newly
    # Adjudicated rows are checked only against the supplied base keys and one
    # another; the legacy mapping aid is not entity/adjudication evidence.
    for field, key in (("wikidata_qid", lambda x: x), ("name", _exact_name)):
        seen: dict[str, str] = {}
        for row in rows[new_start:]:
            value = row.get(field)
            if not value:
                continue
            k = key(value)
            if k in base_keys[field]:
                raise ValueError(f"appended investor collides with base {field}: {value}")
            old = seen.get(k)
            if old and old != row["investor_id"]:
                raise ValueError(f"appended investor {field} collision: {value}")
            seen[k] = row["investor_id"]


def _stage_table(table: pa.Table, destination: Path) -> Path:
    fd, name = tempfile.mkstemp(prefix=destination.name+".", suffix=".tmp", dir=destination.parent)
    os.close(fd); path = Path(name)
    pq.write_table(table, path, **PARQUET_WRITE_OPTIONS)
    return path


def materialize(args: argparse.Namespace) -> dict[str, Any]:
    if pa.__version__ != REQUIRED_PYARROW_VERSION:
        raise RuntimeError(f"PyArrow {REQUIRED_PYARROW_VERSION} required; found {pa.__version__}")
    package_dir, source_path = Path(args.package_dir), Path(args.source_interface_path)
    investors_path, legacy_path = Path(args.investors_entity_path), Path(args.legacy_investors_entity_path)
    provenance_path, output_dir = Path(args.investors_provenance_path), Path(args.output_dir)
    package_inputs = [package_dir/"manifest.json", *(package_dir/name for name in PACKAGE_FILES.values())]
    inputs = [source_path, investors_path, legacy_path, provenance_path, *package_inputs]
    for path in inputs:
        if not path.is_file(): raise FileNotFoundError(path)
    output_dir = _validate_output_dir(output_dir, inputs)
    protected = {str(path): sha256_file(path) for path in inputs}
    source, investors = pq.read_table(source_path), pq.read_table(investors_path)
    legacy, provenance = pq.read_table(legacy_path), pq.read_table(provenance_path)
    manifest, package = validate_package(package_dir, source, source_path, investors, investors_path, legacy_path, provenance_path)
    base_rows = [_coerce_entity_row(x) for x in investors.to_pylist()]
    base_ids = {x["investor_id"] for x in base_rows}

    overlay = {x["source_event_id"]: x for x in package["event_resolution_overlay"]}
    interface_rows = []
    for original in source.to_pylist():
        row = dict(original); item = overlay.get(row["event_id"])
        if item:
            row["investor_id"] = item["investor_id"]
            row["associated_entity_id"] = item["associated_entity_id"]
            row["counterparty_resolution_status"] = item["counterparty_resolution_status"]
        else:
            row["associated_entity_id"] = None
            row["counterparty_resolution_status"] = "EXISTING_INVESTOR" if row["investor_id"] else "UNRESOLVED"
        row["schema_version"] = "v0.4.0"
        interface_rows.append(row)

    required_existing = {x["investor_id"] for x in interface_rows if x["counterparty_resolution_status"] == "EXISTING_INVESTOR"}
    if not required_existing <= base_ids:
        raise ValueError(f"published existing IDs absent from supplied investor base: {sorted(required_existing-base_ids)}")

    # The legacy entity snapshot is a pinned mapping aid only. Never append its
    # The materialized entity is exactly the supplied base plus package additions.
    legacy_remap = _legacy_to_base_mapping(base_rows, legacy.to_pylist())
    entity_rows = [*base_rows, *package["new_investors_entity"]]
    _validate_dedup(entity_rows, investors.num_rows)
    investor_ids = {x["investor_id"] for x in entity_rows}
    expected_entity_rows = investors.num_rows + len(package["new_investors_entity"])
    if len(entity_rows) != expected_entity_rows:
        raise ValueError(f"materialized investor entity must contain exactly {expected_entity_rows} rows; found {len(entity_rows)}")
    if not {x["investor_id"] for x in interface_rows if x["investor_id"]} <= investor_ids:
        raise ValueError("Interface investor FK failure")

    provenance_rows = []
    retained_legacy_provenance_rows = 0
    dropped_legacy_provenance_rows = 0
    for original in provenance.to_pylist():
        target = legacy_remap.get(original["investor_id"])
        if target is None:
            dropped_legacy_provenance_rows += 1
            continue
        item = dict(original)
        item["investor_id"] = target
        provenance_rows.append(item)
        retained_legacy_provenance_rows += 1
    retained_base_provenance_ids = {x["investor_id"] for x in provenance_rows}
    provenance_rows += [{**x, "fetched_at": _date(x["fetched_at"])} for x in package["new_investors_provenance"]]
    provenance_keys = [(x["investor_id"], x["field"], x["source"], x["source_record_id"]) for x in provenance_rows]
    if len(provenance_keys) != len(set(provenance_keys)):
        raise ValueError("duplicate investor provenance row after legacy remap")
    provenance_ids = {x["investor_id"] for x in provenance_rows}
    new_ids = {x["investor_id"] for x in package["new_investors_entity"]}
    if not new_ids <= provenance_ids or not provenance_ids <= investor_ids:
        raise ValueError("investor provenance FK/coverage failure")
    associated_rows = [{**x, "first_seen_at": _date(x["first_seen_at"])} for x in package["associated_entity"]]
    associated_prov = [{**x, "fetched_at": _date(x["fetched_at"])} for x in package["associated_provenance"]]
    associated_ids = {x["associated_entity_id"] for x in associated_rows}
    if {x["associated_entity_id"] for x in interface_rows if x["associated_entity_id"]} != associated_ids:
        raise ValueError("Interface associated-entity FK failure")

    entity_schema = _entity_schema(investors.schema)
    tables = {
        "interface_events_v0.4.parquet": pa.Table.from_pylist(interface_rows, schema=_interface_schema(source.schema)),
        "investors_entity_v0.3.parquet": pa.Table.from_pylist(entity_rows, schema=entity_schema),
        "investors_provenance_v0.2.parquet": pa.Table.from_pylist(provenance_rows, schema=_provenance_schema(provenance.schema)),
        "associated_entity_v0.1.parquet": pa.Table.from_pylist(associated_rows, schema=_associated_schema()),
        "associated_provenance_v0.1.parquet": pa.Table.from_pylist(associated_prov, schema=_provenance_schema(provenance.schema, associated=True)),
    }
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=output_dir.name+".", dir=output_dir.parent))
    try:
        outputs = {}
        for name, table in tables.items():
            path = staging/name
            pq.write_table(table, path, **PARQUET_WRITE_OPTIONS)
            reread = pq.read_table(path)
            metadata_keys_preserved = set(table.schema.metadata or {}) <= set(reread.schema.metadata or {})
            if not reread.schema.equals(table.schema, check_metadata=False) or not metadata_keys_preserved or reread.num_rows != table.num_rows:
                raise RuntimeError(f"serialized table validation failed: {name}")
            outputs[name] = {"sha256": sha256_file(path), "rows": table.num_rows, "columns": table.num_columns}
        result_manifest = {
            "schema_version": "interface-counterparty-identity-materialization-v1",
            "package_manifest_sha256": sha256_file(package_dir/"manifest.json"),
            "inputs": {Path(path).name: digest for path, digest in sorted(protected.items())},
            "outputs": outputs,
            "statistics": {
                "source_interface_rows": source.num_rows,
                "resolved_target_rows": len(overlay),
                "package_overlay_rows": len(overlay),
                "base_investors": investors.num_rows,
                "legacy_entities_appended": 0,
                "new_investors": len(package["new_investors_entity"]),
                "total_investors": len(entity_rows),
                "legacy_entity_ids_mapped_to_base": len(legacy_remap),
                "legacy_provenance_rows_input": provenance.num_rows,
                "retained_legacy_provenance_rows": retained_legacy_provenance_rows,
                "dropped_legacy_provenance_rows": dropped_legacy_provenance_rows,
                "associated_entities": len(associated_rows),
                "investors_with_any_provenance": len(provenance_ids),
                "base_v0_2_investors_with_retained_provenance": len(retained_base_provenance_ids),
                "base_v0_2_investors_without_retained_provenance": len(base_ids - retained_base_provenance_ids),
            },
            "runtime": {"pyarrow_version": pa.__version__, "parquet_write_options": PARQUET_WRITE_OPTIONS, "network_access": "none"},
            "invariants": {"source_event_ids_unchanged": [x["event_id"] for x in source.to_pylist()] == [x["event_id"] for x in interface_rows], "source_raw_names_unchanged": [x["raw_investor_name"] for x in source.to_pylist()] == [x["raw_investor_name"] for x in interface_rows], "event_semantics_unchanged": True, "foreign_keys_valid": True, "authoritative_base_preserved_value_for_value": entity_rows[:len(base_rows)] == base_rows, "legacy_entities_appended": False, "legacy_provenance_retained_only_for_unique_base_mapping": True, "interface_recovery_field_provenance_complete": True, "base_table_field_provenance_completeness_asserted": False, "duplicates_rejected": True, "new_output_directory": True, "network_access_performed": False},
        }
        expected = manifest.get("expected_materialization")
        if expected is not None:
            if not isinstance(expected, dict) or expected.get("outputs") != outputs:
                raise ValueError("materialized outputs differ from package expected_materialization")
            expected_statistics = expected.get("statistics")
            if not isinstance(expected_statistics, dict) or any(
                result_manifest["statistics"].get(key) != value for key, value in expected_statistics.items()
            ):
                raise ValueError("materialization statistics differ from package expected_materialization")
        (staging/"materialization_manifest.json").write_text(json.dumps(result_manifest, ensure_ascii=False, indent=2, sort_keys=True)+"\n", encoding="utf-8")
        for path, digest in protected.items():
            if sha256_file(Path(path)) != digest: raise RuntimeError(f"protected input changed: {path}")
        os.replace(staging, output_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return result_manifest


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-dir", type=Path, required=True)
    parser.add_argument("--source-interface-path", type=Path, required=True)
    parser.add_argument("--investors-entity-path", type=Path, required=True)
    parser.add_argument("--legacy-investors-entity-path", type=Path, required=True)
    parser.add_argument("--investors-provenance-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    result = materialize(parse_args(argv))
    print(json.dumps({"output_rows": {k:v["rows"] for k,v in result["outputs"].items()}, "statistics": result["statistics"]}, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
