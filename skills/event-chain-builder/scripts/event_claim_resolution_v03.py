"""Shared normalization and claim-resolution rules for Step-10 v0.3 builders.

An event ID is the logical-event identity contract. Every event ID produces one
canonical event row. All source claims remain in ``metadata_json.claims``; the
canonical claim is the uniquely highest-confidence claim, or the earliest claim
in cache/participant encounter order when the maximum confidence is tied.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import re
from collections import defaultdict
from typing import Any

_DATE_RE = re.compile(r"^\d{4}(?:-\d{2})?(?:-\d{2})?$")
_PROVENANCE_FIELDS = {
    "event_id", "metadata_json", "confidence", "source_url", "source_type",
    "source_table", "data_source", "scraped_at",
}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def normalize_partial_date(value: Any) -> str | None:
    """Return a strict calendar-valid YYYY[/MM[/DD]] value or ``None``.

    Unsupported precision markers (quarters, question marks, prose, obfuscated
    values) are not converted to artificial boundary dates. The original value
    remains available in ``metadata_json.raw``.
    """

    if value is None:
        return None
    text = str(value).strip()
    if not text or not _DATE_RE.fullmatch(text):
        return None
    try:
        if len(text) == 4:
            dt.date(int(text), 1, 1)
        elif len(text) == 7:
            dt.date(int(text[:4]), int(text[5:7]), 1)
        else:
            dt.date.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    return text


def _metadata(row: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(row.get("metadata_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        value = {}
    return value if isinstance(value, dict) else {}


def _raw_claim(row: dict[str, Any]) -> dict[str, Any]:
    raw = _metadata(row).get("raw")
    return copy.deepcopy(raw) if isinstance(raw, dict) else {}


def _confidence(row: dict[str, Any]) -> float:
    try:
        return float(row.get("confidence") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _source_label(row: dict[str, Any]) -> str:
    raw = _raw_claim(row)
    for value in (
        row.get("source_url"), raw.get("source_url"), row.get("source_type"),
        raw.get("source_type"), row.get("data_source"), row.get("source_table"),
    ):
        text = str(value).strip() if value is not None else ""
        if text:
            return text
    return "unavailable"


def _conflict_fields(rows: list[dict[str, Any]]) -> list[str]:
    fields = sorted(set().union(*(row.keys() for row in rows)) - _PROVENANCE_FIELDS)
    return [
        field for field in fields
        if len({_canonical(row.get(field)) for row in rows}) > 1
    ]


def resolve_event_claims(
    rows: list[dict[str, Any]], family: str,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Select one canonical claim per event ID and retain every raw claim.

    Input order is cache-file order, then cache-array order, then participant
    expansion order. This order is the deterministic tie-break when more than
    one claim has the maximum confidence.
    """

    if family not in {"funding", "exposure", "interface"}:
        raise ValueError(f"unsupported event family: {family}")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    order: list[str] = []
    for row in rows:
        event_id = str(row["event_id"])
        if event_id not in grouped:
            order.append(event_id)
        grouped[event_id].append(row)

    stats = {
        "input_rows": len(rows),
        "output_rows": 0,
        "collision_groups": 0,
        "claims_collapsed": 0,
        "unique_highest_confidence_groups": 0,
        "tied_highest_confidence_groups": 0,
        "semantic_conflict_groups": 0,
    }
    output: list[dict[str, Any]] = []
    for event_id in order:
        claims = grouped[event_id]
        if len(claims) == 1:
            output.append(claims[0])
            continue

        stats["collision_groups"] += 1
        stats["claims_collapsed"] += len(claims) - 1
        confidences = [_confidence(row) for row in claims]
        highest = max(confidences)
        highest_positions = [i for i, value in enumerate(confidences) if value == highest]
        selected_position = highest_positions[0]
        if len(highest_positions) == 1:
            stats["unique_highest_confidence_groups"] += 1
        else:
            stats["tied_highest_confidence_groups"] += 1

        conflict_fields = _conflict_fields(claims)
        if conflict_fields:
            stats["semantic_conflict_groups"] += 1
        selected = copy.deepcopy(claims[selected_position])
        metadata = _metadata(selected)
        metadata["claims"] = [_raw_claim(row) for row in claims]
        metadata["claim_resolution"] = {
            "claim_count": len(claims),
            "confidence_tied": len(highest_positions) > 1,
            "conflict_fields": conflict_fields,
            "event_id": event_id,
            "mode": "canonical_claim_selected",
            "selected_claim_ordinal": selected_position + 1,
            "selected_confidence": highest,
            "selection_rule": "highest_confidence_then_earliest_cache_order",
            "sources": [_source_label(row) for row in claims],
        }
        selected["metadata_json"] = json.dumps(metadata, ensure_ascii=False)
        output.append(selected)

    event_ids = [str(row["event_id"]) for row in output]
    if len(event_ids) != len(set(event_ids)):
        raise ValueError("claim resolution produced duplicate event_id values")
    stats["output_rows"] = len(output)
    return output, stats
