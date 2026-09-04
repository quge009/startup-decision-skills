#!/usr/bin/env python3
"""Validate and materialize the Interface raw-investor-name recovery overlay.

This is an offline, fail-closed materializer.  It never edits its inputs and it
only publishes new artifacts without overwriting inputs. The adjudication itself lives in a
separate versioned overlay package; this script does not infer investor names.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import struct
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import pyarrow as pa
import pyarrow.parquet as pq


RESEARCH_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from schema_contract_loader import load_schema_contract  # noqa: E402

INTERFACE_SCHEMA_PATH = RESEARCH_ROOT / "schemas/interface_events_v0.3.schema.json"
OVERLAY_SCHEMA_PATH = RESEARCH_ROOT / "schemas/interface_raw_investor_recovery_v1.schema.json"
OVERLAY_MANIFEST_SCHEMA_PATH = (
    RESEARCH_ROOT / "schemas/interface_raw_investor_recovery_manifest_v1.schema.json"
)
MATERIALIZATION_MANIFEST_SCHEMA_PATH = (
    RESEARCH_ROOT / "schemas/interface_raw_investor_recovery_materialization_v1.schema.json"
)
INTERFACE_SCHEMA, _ = load_schema_contract(INTERFACE_SCHEMA_PATH)

OVERLAY_SCHEMA_VERSION = "interface-raw-investor-recovery-v1"
OVERLAY_MANIFEST_SCHEMA_VERSION = "interface-raw-investor-recovery-manifest-v1"
MATERIALIZATION_SCHEMA_VERSION = "interface-raw-investor-recovery-materialization-v1"
ROW_HASH_ALGORITHM = "interface-source-row-sha-v1"
TARGET_PREDICATE = "raw_investor_name IS NULL OR unicode-whitespace-only"
NAME_ORDER = "title-before-summary-before-source_page, evidence-start, raw-name-utf8"
SOURCE_PAGE_QUOTE_SEMANTICS = (
    "reviewed-overlay-excerpt; offsets-relative-to-excerpt-not-full-web-page"
)
NETWORK_ACCESS = "none"
REQUIRED_PYARROW_VERSION = "25.0.0"
PARQUET_WRITE_OPTIONS = {
    "compression": "snappy",
    "version": "2.6",
    "data_page_version": "1.0",
    "use_dictionary": True,
    "write_statistics": True,
    "row_group_size": 65536,
}
STATUSES = {"RECOVERED", "NO_IDENTIFIABLE_COUNTERPARTY", "AMBIGUOUS"}
_STRIP_TOKENS = frozenset({
    "ventures", "capital", "partners", "fund", "llc", "lp", "inc", "group",
    "advisors", "holdings", "investments", "management", "corp", "co",
    "corporation", "company",
})
_RECORD_KEYS = {
    "schema_version", "record_id", "source_event_id", "source_row_sha256", "status", "names"
}
_NAME_KEYS = {"ordinal", "raw_investor_name", "evidence"}
_EVIDENCE_KEYS = {
    "source_field", "quote", "quote_start", "quote_end",
    "name_start_in_quote", "name_end_in_quote",
}
_SOURCE_PAGE_EVIDENCE_KEYS = _EVIDENCE_KEYS | {"source_url"}


def _normalize_name(raw: str) -> str:
    value = re.sub(r"[^\w\s-]", " ", (raw or "").strip().lower())
    tokens = re.sub(r"\s+", " ", value).strip().split(" ") if value.strip() else []
    while tokens and tokens[-1] in _STRIP_TOKENS:
        tokens.pop()
    return " ".join(tokens)


def _event_id(company_id: str, event_date: str | None, title: str,
              raw_investor_name: str | None) -> str:
    key = (
        f"{company_id}|{event_date or ''}|{title.strip().lower()}|"
        f"{_normalize_name(raw_investor_name or '')}"
    )
    return "int:" + hashlib.sha1(key.encode()).hexdigest()[:20]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _length_prefixed(value: bytes) -> bytes:
    return struct.pack(">Q", len(value)) + value


def source_row_sha256(row: dict[str, Any], schema: pa.Schema = INTERFACE_SCHEMA) -> str:
    """Hash one logical Interface row, independent of Parquet physical encoding."""
    if set(row) != set(schema.names):
        raise ValueError("source row fields do not exactly match Interface schema")
    digest = hashlib.sha256()
    digest.update((ROW_HASH_ALGORITHM + "\0").encode("ascii"))
    for field in schema:
        digest.update(_length_prefixed(field.name.encode("utf-8")))
        digest.update(_length_prefixed(str(field.type).encode("ascii")))
        value = row[field.name]
        if value is None:
            digest.update(b"\x00")
            digest.update(struct.pack(">Q", 0))
            continue
        digest.update(b"\x01")
        if pa.types.is_large_string(field.type) or pa.types.is_string(field.type):
            if not isinstance(value, str):
                raise ValueError(f"{field.name} is not a string")
            encoded = value.encode("utf-8")
        elif pa.types.is_float64(field.type):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{field.name} is not a float")
            encoded = struct.pack(">d", float(value))
        else:
            raise ValueError(f"unsupported Interface row hash type: {field.type}")
        digest.update(_length_prefixed(encoded))
    return digest.hexdigest()


def expected_record_id(source_event_id: str, row_sha256: str) -> str:
    payload = f"{OVERLAY_SCHEMA_VERSION}\0{source_event_id}\0{row_sha256}".encode("utf-8")
    return "irn:" + hashlib.sha256(payload).hexdigest()[:24]


def is_raw_name_empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def sorted_event_id_set_sha256(event_ids: Iterable[str]) -> str:
    values = sorted(event_ids, key=lambda value: value.encode("utf-8"))
    payload = b"\n".join(value.encode("utf-8") for value in values)
    return hashlib.sha256(payload).hexdigest()


def _require_exact_keys(value: Any, expected: set[str], location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{location} must be an object")
    if set(value) != expected:
        missing = sorted(expected - set(value))
        extra = sorted(set(value) - expected)
        raise ValueError(f"{location} fields do not match schema: missing={missing}, extra={extra}")
    return value


def _require_int(value: Any, location: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{location} must be an integer >= {minimum}")
    return value


def _validate_name(name: Any, row: dict[str, Any], location: str) -> dict[str, Any]:
    item = _require_exact_keys(name, _NAME_KEYS, location)
    _require_int(item["ordinal"], f"{location}.ordinal", 1)
    raw_name = item["raw_investor_name"]
    if not isinstance(raw_name, str) or not raw_name or not raw_name.strip():
        raise ValueError(f"{location}.raw_investor_name must be non-whitespace text")

    evidence_value = item["evidence"]
    if not isinstance(evidence_value, dict):
        raise ValueError(f"{location}.evidence must be an object")
    field = evidence_value.get("source_field")
    if field == "source_page":
        evidence = _require_exact_keys(
            evidence_value, _SOURCE_PAGE_EVIDENCE_KEYS, f"{location}.evidence"
        )
    elif field in {"title", "summary"}:
        allowed_keys = (_EVIDENCE_KEYS, _SOURCE_PAGE_EVIDENCE_KEYS)
        if set(evidence_value) not in allowed_keys:
            missing = sorted(_EVIDENCE_KEYS - set(evidence_value))
            extra = sorted(set(evidence_value) - _SOURCE_PAGE_EVIDENCE_KEYS)
            raise ValueError(
                f"{location}.evidence fields do not match schema: "
                f"missing={missing}, extra={extra}"
            )
        evidence = evidence_value
        if "source_url" in evidence and evidence["source_url"] is not None:
            raise ValueError(f"{location}.evidence.source_url must be null for {field} evidence")
    else:
        raise ValueError(
            f"{location}.evidence.source_field must be title, summary, or source_page"
        )

    quote = evidence["quote"]
    if not isinstance(quote, str) or not quote:
        raise ValueError(f"{location}.evidence.quote must be nonempty text")
    quote_start = _require_int(evidence["quote_start"], f"{location}.evidence.quote_start")
    quote_end = _require_int(evidence["quote_end"], f"{location}.evidence.quote_end")
    name_start = _require_int(
        evidence["name_start_in_quote"], f"{location}.evidence.name_start_in_quote"
    )
    name_end = _require_int(
        evidence["name_end_in_quote"], f"{location}.evidence.name_end_in_quote"
    )

    if field == "source_page":
        source_url = evidence["source_url"]
        row_source_url = row.get("source_url")
        if not isinstance(source_url, str) or not source_url:
            raise ValueError(f"{location}.evidence.source_url must be nonempty text")
        if not isinstance(row_source_url, str) or source_url != row_source_url:
            raise ValueError(
                f"{location}.evidence.source_url must byte/string-exactly match source row source_url"
            )
        # This quote is a reviewed excerpt stored in the overlay.  These offsets
        # are excerpt-relative and never claim positions in the complete web page.
        if quote_start >= quote_end or quote_end > len(quote):
            raise ValueError(f"{location} reviewed excerpt offsets are invalid")
        if not (quote_start <= name_start < name_end <= quote_end):
            raise ValueError(f"{location} name offsets fall outside reviewed excerpt offsets")
    else:
        source_text = row[field]
        if not isinstance(source_text, str):
            raise ValueError(f"source {field} is not text for {row['event_id']}")
        if quote_start >= quote_end or quote_end > len(source_text):
            raise ValueError(f"{location} quote offsets are invalid")
        if source_text[quote_start:quote_end] != quote:
            raise ValueError(f"{location} quote is not the verbatim source substring at its offsets")

    if name_start >= name_end or name_end > len(quote):
        raise ValueError(f"{location} name offsets are invalid")
    if quote[name_start:name_end] != raw_name:
        raise ValueError(f"{location} raw name is not the verbatim quote substring at its offsets")
    return item


def _name_sort_key(name: dict[str, Any]) -> tuple[int, int, int, bytes]:
    evidence = name["evidence"]
    source_rank = {"title": 0, "summary": 1, "source_page": 2}
    return (
        source_rank[evidence["source_field"]],
        evidence["quote_start"],
        evidence["name_start_in_quote"],
        name["raw_investor_name"].encode("utf-8"),
    )


def validate_overlay_record(
    record: Any, row: dict[str, Any], row_sha256: str, record_index: int,
) -> dict[str, Any]:
    location = f"overlay record {record_index}"
    item = _require_exact_keys(record, _RECORD_KEYS, location)
    if item["schema_version"] != OVERLAY_SCHEMA_VERSION:
        raise ValueError(f"{location} schema_version is invalid")
    source_event_id = item["source_event_id"]
    if not isinstance(source_event_id, str) or re.fullmatch(r"int:[0-9a-f]{20}", source_event_id) is None:
        raise ValueError(f"{location} source_event_id is invalid")
    if source_event_id != row["event_id"]:
        raise ValueError(f"{location} source event binding mismatch")
    declared_row_sha = item["source_row_sha256"]
    if not isinstance(declared_row_sha, str) or re.fullmatch(r"[0-9a-f]{64}", declared_row_sha) is None:
        raise ValueError(f"{location} source_row_sha256 is invalid")
    if declared_row_sha != row_sha256:
        raise ValueError(f"{location} source row hash mismatch")
    if item["record_id"] != expected_record_id(source_event_id, row_sha256):
        raise ValueError(f"{location} record_id does not match its source binding")
    status = item["status"]
    if status not in STATUSES:
        raise ValueError(f"{location} status is invalid")
    if not isinstance(item["names"], list):
        raise ValueError(f"{location}.names must be an array")
    names = [_validate_name(value, row, f"{location}.names[{index}]")
             for index, value in enumerate(item["names"])]
    if status == "RECOVERED" and not names:
        raise ValueError(f"{location} RECOVERED requires at least one name")
    if status == "NO_IDENTIFIABLE_COUNTERPARTY" and names:
        raise ValueError(f"{location} NO_IDENTIFIABLE_COUNTERPARTY requires zero names")
    ordinals = [name["ordinal"] for name in names]
    if sorted(ordinals) != list(range(1, len(names) + 1)):
        raise ValueError(f"{location} name ordinals must be unique and contiguous from 1")
    raw_names = [name["raw_investor_name"] for name in names]
    if len(raw_names) != len(set(raw_names)):
        raise ValueError(f"{location} contains duplicate exact raw names")
    canonical_names = sorted(names, key=_name_sort_key)
    return {**item, "names": canonical_names}


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid {label} JSON: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _load_overlay_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid overlay JSON on line {line_number}: {error}") from error
        if not isinstance(value, dict):
            raise ValueError(f"overlay line {line_number} must be an object")
        records.append(value)
    if not records:
        raise ValueError("overlay JSONL is empty")
    return records


def _validate_source_schema(table: pa.Table) -> None:
    if not table.schema.equals(INTERFACE_SCHEMA, check_metadata=True):
        raise ValueError(
            "source Interface schema/metadata does not exactly match interface_events_v0.3 contract"
        )


def _validate_manifest_shape(manifest: dict[str, Any]) -> None:
    required = {"schema_version", "source", "target_set", "canonical_exact_map", "overlay", "algorithms"}
    _require_exact_keys(manifest, required, "overlay manifest")
    if manifest["schema_version"] != OVERLAY_MANIFEST_SCHEMA_VERSION:
        raise ValueError("overlay manifest schema_version is invalid")
    _require_exact_keys(
        manifest["source"], {"path", "sha256", "rows", "columns", "schema_version"},
        "overlay manifest.source",
    )
    _require_exact_keys(
        manifest["target_set"], {"predicate", "count", "sorted_source_event_id_set_sha256"},
        "overlay manifest.target_set",
    )
    canonical = manifest["canonical_exact_map"]
    if canonical is not None:
        _require_exact_keys(
            canonical, {"path", "sha256", "rows", "key_column", "value_column", "match_mode"},
            "overlay manifest.canonical_exact_map",
        )
    _require_exact_keys(
        manifest["overlay"], {"path", "sha256", "records", "status_counts", "recovered_name_count"},
        "overlay manifest.overlay",
    )
    _require_exact_keys(
        manifest["algorithms"], {
            "source_row_sha", "event_id", "name_order", "materialization_order",
            "source_page_quote_semantics", "network_access",
        },
        "overlay manifest.algorithms",
    )


def _verify_manifest_locks(
    manifest: dict[str, Any], *, source_path: Path, source_sha: str, source_table: pa.Table,
    target_ids: set[str], overlay_path: Path, records: list[dict[str, Any]],
    canonical_path: Path | None,
) -> None:
    _validate_manifest_shape(manifest)
    source = manifest["source"]
    if source["sha256"] != source_sha:
        raise ValueError("source parquet SHA-256 does not match overlay manifest")
    if source["rows"] != source_table.num_rows or source["columns"] != source_table.num_columns:
        raise ValueError("source parquet dimensions do not match overlay manifest")
    if source["schema_version"] != "v0.3.0":
        raise ValueError("source schema version lock is not v0.3.0")
    target = manifest["target_set"]
    if target["predicate"] != TARGET_PREDICATE:
        raise ValueError("target predicate does not match recovery v1")
    if target["count"] != len(target_ids):
        raise ValueError("target count does not match overlay manifest")
    if target["sorted_source_event_id_set_sha256"] != sorted_event_id_set_sha256(target_ids):
        raise ValueError("target event-ID set digest does not match overlay manifest")
    overlay = manifest["overlay"]
    if overlay["sha256"] != sha256_file(overlay_path):
        raise ValueError("overlay JSONL SHA-256 does not match overlay manifest")
    if overlay["records"] != len(records):
        raise ValueError("overlay record count does not match overlay manifest")
    algorithms = manifest["algorithms"]
    expected_algorithms = {
        "source_row_sha": ROW_HASH_ALGORITHM,
        "event_id": "interface-v0.3-event-id",
        "name_order": NAME_ORDER,
        "materialization_order": "source-row-order then recovered-name-order",
        "source_page_quote_semantics": SOURCE_PAGE_QUOTE_SEMANTICS,
        "network_access": NETWORK_ACCESS,
    }
    if algorithms != expected_algorithms:
        raise ValueError("overlay manifest algorithm locks do not match this materializer")
    canonical = manifest["canonical_exact_map"]
    if canonical_path is None:
        if canonical is not None:
            raise ValueError("manifest locks a canonical map but --canonical-path was not supplied")
    else:
        if canonical is None:
            raise ValueError("--canonical-path requires a canonical_exact_map manifest lock")
        if canonical["sha256"] != sha256_file(canonical_path):
            raise ValueError("canonical parquet SHA-256 does not match overlay manifest")
        if canonical["key_column"] != "name" or canonical["value_column"] != "investor_id":
            raise ValueError("canonical exact-map columns are invalid")
        if canonical["match_mode"] != "utf8-byte-exact":
            raise ValueError("canonical map must use utf8-byte-exact matching")


def load_exact_canonical_map(path: Path | None, manifest: dict[str, Any]) -> dict[str, str]:
    if path is None:
        return {}
    canonical_lock = manifest["canonical_exact_map"]
    table = pq.read_table(path, columns=[canonical_lock["key_column"], canonical_lock["value_column"]])
    if table.num_rows != canonical_lock["rows"]:
        raise ValueError("canonical parquet row count does not match overlay manifest")
    mapping: dict[str, str] = {}
    for index, row in enumerate(table.to_pylist()):
        name, investor_id = row["name"], row["investor_id"]
        if name is None or investor_id is None:
            continue
        if not isinstance(name, str) or not isinstance(investor_id, str):
            raise ValueError(f"canonical row {index} has a non-string key or value")
        old = mapping.get(name)
        if old is not None and old != investor_id:
            raise ValueError(f"canonical exact name maps to multiple investor IDs: {name!r}")
        mapping[name] = investor_id
    return mapping


def _metadata_with_lineage(
    source_metadata: str, *, source_event_id: str, source_row_sha: str,
    overlay_sha: str, output_ordinal: int, name: dict[str, Any],
) -> str:
    try:
        metadata = json.loads(source_metadata)
    except json.JSONDecodeError as error:
        raise ValueError(f"source metadata_json is invalid for {source_event_id}") from error
    if not isinstance(metadata, dict):
        raise ValueError(f"source metadata_json is not an object for {source_event_id}")
    metadata = copy.deepcopy(metadata)
    if "recovery_overlay" in metadata:
        raise ValueError(f"source metadata already contains recovery_overlay for {source_event_id}")
    metadata["recovery_overlay"] = {
        "schema_version": OVERLAY_SCHEMA_VERSION,
        "source_event_id": source_event_id,
        "source_row_sha256": source_row_sha,
        "overlay_sha256": overlay_sha,
        "status": "RECOVERED",
        "ordinal": output_ordinal,
        "evidence": copy.deepcopy(name["evidence"]),
    }
    return json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_overlay_sha256(records: Iterable[dict[str, Any]]) -> str:
    """Digest validated overlay semantics independently of JSONL/name ordering."""
    canonical_records = []
    for record in records:
        canonical = copy.deepcopy(record)
        canonical["names"] = sorted(canonical["names"], key=_name_sort_key)
        for ordinal, name in enumerate(canonical["names"], 1):
            name["ordinal"] = ordinal
        canonical_records.append(canonical)
    canonical_records.sort(key=lambda item: item["source_event_id"].encode("utf-8"))
    content = b"".join(
        (json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        for record in canonical_records
    )
    return hashlib.sha256(content).hexdigest()


def _validate_status_counts(records: list[dict[str, Any]], manifest: dict[str, Any]) -> None:
    counts = Counter(record["status"] for record in records)
    declared = manifest["overlay"]["status_counts"]
    if not isinstance(declared, dict) or set(declared) != STATUSES:
        raise ValueError("overlay manifest status_counts must exactly cover all statuses")
    if {status: counts[status] for status in sorted(STATUSES)} != {
        status: declared[status] for status in sorted(STATUSES)
    }:
        raise ValueError("overlay status counts do not match overlay manifest")
    recovered_names = sum(
        len(record["names"]) for record in records if record["status"] == "RECOVERED"
    )
    if manifest["overlay"]["recovered_name_count"] != recovered_names:
        raise ValueError("recovered name count does not match overlay manifest")


def _canonical_path(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _validate_paths(inputs: dict[str, Path], outputs: dict[str, Path]) -> None:
    canonical_inputs = {name: _canonical_path(path) for name, path in inputs.items()}
    canonical_outputs = {name: _canonical_path(path) for name, path in outputs.items()}
    if len(set(canonical_outputs.values())) != len(canonical_outputs):
        raise ValueError("output targets alias each other")
    aliases = set(canonical_inputs.values()) & set(canonical_outputs.values())
    if aliases:
        raise ValueError(f"output target aliases protected input: {sorted(map(str, aliases))}")
    existing = [str(path) for path in outputs.values() if path.exists() or path.is_symlink()]
    if existing:
        raise FileExistsError(f"refusing to overwrite existing outputs: {existing}")
    for label, path in inputs.items():
        if not path.is_file():
            raise FileNotFoundError(f"{label} is not a file: {path}")


def _stage_bytes(content: bytes, final_path: Path) -> Path:
    final_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=final_path.name + ".", suffix=".tmp", dir=final_path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        return temp_path
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def _stage_parquet(table: pa.Table, final_path: Path) -> Path:
    final_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=final_path.name + ".", suffix=".tmp", dir=final_path.parent)
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        pq.write_table(table, temp_path, **PARQUET_WRITE_OPTIONS)
        with temp_path.open("rb") as handle:
            os.fsync(handle.fileno())
        return temp_path
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def _commit_new_files(staged: dict[Path, Path], post_commit_validator: Any | None = None) -> None:
    committed: list[Path] = []
    try:
        for final_path, temp_path in staged.items():
            if final_path.exists() or final_path.is_symlink():
                raise FileExistsError(f"refusing to overwrite existing output: {final_path}")
            os.replace(temp_path, final_path)
            committed.append(final_path)
        if post_commit_validator is not None:
            post_commit_validator()
    except Exception:
        for path in committed:
            path.unlink(missing_ok=True)
        raise
    finally:
        for path in staged.values():
            path.unlink(missing_ok=True)


def materialize(args: argparse.Namespace) -> dict[str, Any]:
    if pa.__version__ != REQUIRED_PYARROW_VERSION:
        raise RuntimeError(f"PyArrow {REQUIRED_PYARROW_VERSION} is required; found {pa.__version__}")
    source_path = Path(args.source_path)
    overlay_path = Path(args.overlay_path)
    overlay_manifest_path = Path(args.overlay_manifest_path)
    canonical_path = Path(args.canonical_path) if args.canonical_path is not None else None
    output_path = Path(args.output_path)
    output_manifest_path = Path(args.output_manifest_path)
    event_id_map_path = Path(args.event_id_map_path)

    inputs = {
        "source": source_path,
        "overlay": overlay_path,
        "overlay_manifest": overlay_manifest_path,
        "interface_schema": INTERFACE_SCHEMA_PATH,
        "overlay_schema": OVERLAY_SCHEMA_PATH,
        "overlay_manifest_schema": OVERLAY_MANIFEST_SCHEMA_PATH,
        "materialization_manifest_schema": MATERIALIZATION_MANIFEST_SCHEMA_PATH,
    }
    if canonical_path is not None:
        inputs["canonical"] = canonical_path
    outputs = {
        "output_parquet": output_path,
        "output_manifest": output_manifest_path,
        "event_id_map": event_id_map_path,
    }
    _validate_paths(inputs, outputs)
    input_hashes = {name: sha256_file(path) for name, path in inputs.items()}

    source_sha = input_hashes["source"]
    overlay_sha = input_hashes["overlay"]
    source_table = pq.read_table(source_path)
    _validate_source_schema(source_table)
    source_rows = source_table.to_pylist()
    source_by_id: dict[str, dict[str, Any]] = {}
    source_row_hashes: dict[str, str] = {}
    target_ids: set[str] = set()
    for row in source_rows:
        event_id = row["event_id"]
        if not isinstance(event_id, str) or re.fullmatch(r"int:[0-9a-f]{20}", event_id) is None:
            raise ValueError(f"invalid source Interface event ID: {event_id!r}")
        if event_id in source_by_id:
            raise ValueError(f"duplicate source Interface event ID: {event_id}")
        source_by_id[event_id] = row
        source_row_hashes[event_id] = source_row_sha256(row, source_table.schema)
        if is_raw_name_empty(row["raw_investor_name"]):
            target_ids.add(event_id)

    raw_records = _load_overlay_jsonl(overlay_path)
    manifest = _load_json_object(overlay_manifest_path, "overlay manifest")
    _verify_manifest_locks(
        manifest, source_path=source_path, source_sha=source_sha, source_table=source_table,
        target_ids=target_ids, overlay_path=overlay_path, records=raw_records,
        canonical_path=canonical_path,
    )
    raw_ids = [record.get("source_event_id") for record in raw_records]
    if len(raw_ids) != len(set(raw_ids)):
        raise ValueError("overlay contains duplicate source_event_id")
    overlay_ids = set(raw_ids)
    if overlay_ids != target_ids:
        missing = sorted(target_ids - overlay_ids)
        extra = sorted(overlay_ids - target_ids)
        raise ValueError(
            f"overlay must cover all and only empty-name source rows: missing={missing}, extra={extra}"
        )

    records_by_id: dict[str, dict[str, Any]] = {}
    for index, record in enumerate(raw_records, 1):
        event_id = record["source_event_id"]
        validated = validate_overlay_record(
            record, source_by_id[event_id], source_row_hashes[event_id], index
        )
        records_by_id[event_id] = validated
    _validate_status_counts(list(records_by_id.values()), manifest)
    overlay_semantic_sha = canonical_overlay_sha256(records_by_id.values())
    canonical_map = load_exact_canonical_map(canonical_path, manifest)

    output_rows: list[dict[str, Any]] = []
    id_map_rows: list[dict[str, Any]] = []
    exact_hits = 0
    exact_misses = 0
    multi_name_events = 0
    for source_row in source_rows:
        source_event_id = source_row["event_id"]
        record = records_by_id.get(source_event_id)
        if record is None:
            output_rows.append(source_row)
            continue
        source_hash = source_row_hashes[source_event_id]
        if record["status"] != "RECOVERED":
            output_rows.append(source_row)
            output_ids = [source_event_id]
        else:
            names = record["names"]
            multi_name_events += len(names) > 1
            output_ids = []
            for output_ordinal, name in enumerate(names, 1):
                raw_name = name["raw_investor_name"]
                transformed = dict(source_row)
                transformed["raw_investor_name"] = raw_name
                transformed["investor_id"] = canonical_map.get(raw_name)
                exact_hits += transformed["investor_id"] is not None
                exact_misses += transformed["investor_id"] is None
                transformed["event_id"] = _event_id(
                    transformed["company_id"], transformed["event_date"],
                    transformed["title"], raw_name,
                )
                transformed["metadata_json"] = _metadata_with_lineage(
                    source_row["metadata_json"], source_event_id=source_event_id,
                    source_row_sha=source_hash, overlay_sha=overlay_semantic_sha,
                    output_ordinal=output_ordinal, name=name,
                )
                output_rows.append(transformed)
                output_ids.append(transformed["event_id"])
        id_map_rows.append({
            "source_event_id": source_event_id,
            "source_row_sha256": source_hash,
            "status": record["status"],
            "output_event_ids": output_ids,
        })

    output_ids = [row["event_id"] for row in output_rows]
    duplicate_output_ids = sorted(
        event_id for event_id, count in Counter(output_ids).items() if count > 1
    )
    if duplicate_output_ids:
        raise ValueError(f"materialized event IDs are not globally unique: {duplicate_output_ids}")
    expected_rows = len(source_rows) + sum(
        len(record["names"]) - 1
        for record in records_by_id.values() if record["status"] == "RECOVERED"
    )
    if len(output_rows) != expected_rows:
        raise RuntimeError("materialized row count formula failed")
    if len(id_map_rows) != len(target_ids):
        raise RuntimeError("event-ID map does not exactly cover target source rows")
    mapped_ids = {event_id for row in id_map_rows for event_id in row["output_event_ids"]}
    if not mapped_ids.issubset(set(output_ids)):
        raise RuntimeError("event-ID map contains an ID absent from materialized output")

    # Validate field equivalence for every row that must remain untouched.
    output_cursor = 0
    for source_row in source_rows:
        record = records_by_id.get(source_row["event_id"])
        if record is not None and record["status"] == "RECOVERED":
            output_cursor += len(record["names"])
            continue
        if source_row_sha256(output_rows[output_cursor], source_table.schema) != source_row_hashes[source_row["event_id"]]:
            raise RuntimeError(f"untouched source row changed: {source_row['event_id']}")
        output_cursor += 1

    output_table = pa.Table.from_pylist(output_rows, schema=source_table.schema)
    if not output_table.schema.equals(source_table.schema, check_metadata=True):
        raise RuntimeError("output Interface schema or metadata changed")
    id_map_content = b"".join(
        (json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        for row in id_map_rows
    )
    staged_output: Path | None = None
    staged_id_map: Path | None = None
    staged_manifest: Path | None = None
    try:
        staged_output = _stage_parquet(output_table, output_path)
        # Validate the serialized candidate before any final path is published.
        staged_table = pq.read_table(staged_output)
        if not staged_table.schema.equals(source_table.schema, check_metadata=True):
            raise RuntimeError("staged output Interface schema or metadata changed")
        staged_rows = staged_table.to_pylist()
        output_cursor = 0
        for source_row in source_rows:
            record = records_by_id.get(source_row["event_id"])
            if record is not None and record["status"] == "RECOVERED":
                output_cursor += len(record["names"])
                continue
            if source_row_sha256(staged_rows[output_cursor], source_table.schema) != source_row_hashes[source_row["event_id"]]:
                raise RuntimeError(f"serialized untouched source row changed: {source_row['event_id']}")
            output_cursor += 1
        staged_id_map = _stage_bytes(id_map_content, event_id_map_path)
        status_counts = Counter(record["status"] for record in records_by_id.values())
        output_sha = sha256_file(staged_output)
        id_map_sha = sha256_file(staged_id_map)
        materialization_manifest = {
            "schema_version": MATERIALIZATION_SCHEMA_VERSION,
            "source": {
                "path": str(source_path), "sha256": source_sha,
                "rows": source_table.num_rows, "columns": source_table.num_columns,
                "schema_version": "v0.3.0",
            },
            "overlay": {
                "path": str(overlay_path), "sha256": overlay_sha,
                "semantic_sha256": overlay_semantic_sha,
                "manifest_path": str(overlay_manifest_path),
                "manifest_sha256": input_hashes["overlay_manifest"],
                "records": len(records_by_id),
                "status_counts": {status: status_counts[status] for status in sorted(STATUSES)},
                "recovered_name_count": sum(
                    len(record["names"]) for record in records_by_id.values()
                    if record["status"] == "RECOVERED"
                ),
            },
            "canonical_exact_map": None if canonical_path is None else {
                "path": str(canonical_path), "sha256": input_hashes["canonical"],
                "rows": manifest["canonical_exact_map"]["rows"],
                "match_mode": "utf8-byte-exact",
            },
            "algorithms": {
                "source_row_sha": ROW_HASH_ALGORITHM,
                "event_id": "interface-v0.3-event-id",
                "name_order": NAME_ORDER,
                "materialization_order": "source-row-order then recovered-name-order",
                "source_page_quote_semantics": SOURCE_PAGE_QUOTE_SEMANTICS,
                "network_access": NETWORK_ACCESS,
            },
            "output": {
                "path": str(output_path), "sha256": output_sha,
                "rows": output_table.num_rows, "columns": output_table.num_columns,
                "event_id_map_path": str(event_id_map_path),
                "event_id_map_sha256": id_map_sha,
                "event_id_map_records": len(id_map_rows),
            },
            "statistics": {
                "target_rows": len(target_ids),
                "untouched_named_rows": len(source_rows) - len(target_ids),
                "single_name_recovered_events": sum(
                    record["status"] == "RECOVERED" and len(record["names"]) == 1
                    for record in records_by_id.values()
                ),
                "multi_name_recovered_events": multi_name_events,
                "canonical_exact_hits": exact_hits,
                "canonical_exact_misses": exact_misses,
                "expected_output_rows": expected_rows,
            },
            "runtime_contract": {
                "pyarrow_version": pa.__version__,
                "required_pyarrow_version": REQUIRED_PYARROW_VERSION,
                "parquet_write_options": PARQUET_WRITE_OPTIONS,
                "materializer_sha256": sha256_file(Path(__file__).resolve()),
                "interface_schema_sha256": input_hashes["interface_schema"],
                "overlay_schema_sha256": input_hashes["overlay_schema"],
                "overlay_manifest_schema_sha256": input_hashes["overlay_manifest_schema"],
                "materialization_manifest_schema_sha256": input_hashes[
                    "materialization_manifest_schema"
                ],
            },
            "invariants": {
                "overlay_exactly_covers_empty_names": True,
                "ambiguous_not_applied": True,
                "existing_names_field_equivalent": True,
                "unrecovered_rows_field_equivalent": True,
                "event_ids_globally_unique": True,
                "outputs_below_tmp": True,
                "protected_inputs_verified_before_and_after": True,
                "source_page_quotes_are_reviewed_overlay_evidence": True,
                "source_page_quote_offsets_are_excerpt_relative_not_full_page": True,
                "pipeline_network_access_performed": False,
            },
        }
        manifest_content = (
            json.dumps(materialization_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        staged_manifest = _stage_bytes(manifest_content, output_manifest_path)

        def _validate_protected_inputs() -> None:
            for name, path in inputs.items():
                if sha256_file(path) != input_hashes[name]:
                    raise RuntimeError(f"protected input changed during materialization: {name}")

        _validate_protected_inputs()
        _commit_new_files({
            output_path: staged_output,
            event_id_map_path: staged_id_map,
            output_manifest_path: staged_manifest,
        }, post_commit_validator=_validate_protected_inputs)
    except Exception:
        for staged_path in (staged_output, staged_id_map, staged_manifest):
            if staged_path is not None:
                staged_path.unlink(missing_ok=True)
        raise
    return {"manifest": materialization_manifest, "files_written": 3}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Apply a complete, versioned Interface raw-investor-name recovery overlay"
    )
    parser.add_argument("--source-path", type=Path, required=True)
    parser.add_argument("--overlay-path", type=Path, required=True)
    parser.add_argument(
        "--overlay-manifest-path", type=Path,
        help="defaults to manifest.json next to --overlay-path",
    )
    parser.add_argument("--canonical-path", type=Path)
    parser.add_argument("--output-path", type=Path, required=True)
    parser.add_argument(
        "--output-manifest-path", type=Path,
        help="defaults to <output-path>.manifest.json",
    )
    parser.add_argument(
        "--event-id-map-path", type=Path,
        help="defaults to <output-path>.event_id_map.jsonl",
    )
    args = parser.parse_args(argv)
    if args.overlay_manifest_path is None:
        args.overlay_manifest_path = args.overlay_path.parent / "manifest.json"
    if args.output_manifest_path is None:
        args.output_manifest_path = Path(str(args.output_path) + ".manifest.json")
    if args.event_id_map_path is None:
        args.event_id_map_path = Path(str(args.output_path) + ".event_id_map.jsonl")
    return args


def main(argv: list[str] | None = None) -> int:
    result = materialize(parse_args(argv))
    manifest = result["manifest"]
    print(json.dumps({
        "files_written": result["files_written"],
        "output_path": manifest["output"]["path"],
        "output_sha256": manifest["output"]["sha256"],
        "output_rows": manifest["output"]["rows"],
        "target_rows": manifest["statistics"]["target_rows"],
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
