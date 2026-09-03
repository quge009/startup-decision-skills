#!/usr/bin/env python3
"""Build deterministic, configuration-driven event-chain Pattern artifacts.

This analysis-only derived layer reads versioned production Parquets, never
mutates them, and writes auditable Chain, company, Pattern, and investor
artifacts. Pattern definitions and precedence are external configuration.
"""

from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from _common import make_slug

RESEARCH_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INTERFACE_SCHEMA_PATH = RESEARCH_ROOT / "schemas/interface_events_v0.3.schema.json"

VALID_WINDOW_STATUSES = {"dated", "unknown_end_date"}
ACTION_VALUE_ALPHA = 0.05
DEFAULT_EQUIVALENCE_MARGIN = 0.05
PATTERN_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
COMPARATORS = {"eq", "ne", "lt", "lte", "gt", "gte", "in", "not_in", "is_null", "not_null"}
ORDERING_COMPARATORS = {"lt", "lte", "gt", "gte"}
CHAIN_SCALAR_FIELDS = {
    "chain_id", "company_id", "company_label_v4", "chain_sequence",
    "chain_start_date", "chain_start_reason", "chain_end_date",
    "chain_window_status", "outcome_type", "outcome_round",
    "n_participant_rows", "n_exposure", "n_interface",
}
CHAIN_LIST_FIELDS = {
    "outcome_event_ids", "investor_ids", "investor_raw_names",
    "exposure_event_ids", "exposure_types", "interface_event_ids", "interface_types",
}
DERIVED_FIELDS = {
    "n_middle", "n_raw_outcome_investors", "n_resolved_outcome_investors",
    "outcome_investor_count", "outcome_investor_basis", "chain_duration_days",
    "outcome_round_bucket", "outcome_round_known", "n_prior_chains",
    "company_chain_count", "is_last_chain", "chain_position_bucket",
    "prior_same_round_count", "round_occurrence_index", "days_since_previous_same_round",
    "prior_outcome_investor_overlap_count", "prior_outcome_investor_overlap_ratio",
    "has_prior_outcome_investor_overlap", "n_ordered_batches", "n_comparable_gaps",
    "n_known_date_middle_events", "middle_span_min", "middle_span_max",
    "min_interbatch_gap_min", "min_interbatch_gap_max",
    "max_interbatch_gap_min", "max_interbatch_gap_max",
    "n_unique_interface_investors_resolved", "n_unique_interface_raw_names",
    "interface_investor_count", "interface_investor_basis", "exposure_share",
    "interface_share", "events_per_chain_year", "batches_per_chain_year",
    *(f"first_{scope}_after_start_{bound}" for scope in ("any", "exposure", "interface") for bound in ("min", "max")),
    *(f"last_{scope}_before_end_{bound}" for scope in ("any", "exposure", "interface") for bound in ("min", "max")),
}
EVENT_FIELDS = {
    "event_id", "family", "event_type", "event_subtype", "event_date",
    "date_precision", "date_min", "date_max", "investor_id", "raw_investor_name",
    "participant_key", "participant_basis", "days_before_chain_end_min",
    "days_before_chain_end_max", "days_after_chain_start_min", "days_after_chain_start_max",
}


@dataclass(frozen=True)
class DateInterval:
    minimum: date
    maximum: date
    precision: str


@dataclass(frozen=True)
class PatternDefinition:
    id: str
    name: str
    description: str
    evaluation_status: str
    not_evaluable_reason: str | None
    rule: dict[str, Any] | None
    track: str | None = None


@dataclass(frozen=True)
class PatternSet:
    config_schema_version: str
    pattern_set_id: str
    primary_order: tuple[str, ...]
    patterns: tuple[PatternDefinition, ...]
    source_path: Path
    sha256: str


@dataclass(frozen=True)
class ChainRuleContext:
    chain: dict[str, Any]
    events: tuple[dict[str, Any], ...]
    batches: tuple[dict[str, Any], ...]
    derived: dict[str, Any]


def parse_partial_date(value: Any) -> DateInterval | None:
    """Parse YYYY, YYYY-MM, or YYYY-MM-DD without inventing false precision."""
    if value is None:
        return None
    text = str(value).strip()
    try:
        if len(text) == 4 and text.isdigit():
            year = int(text)
            return DateInterval(date(year, 1, 1), date(year, 12, 31), "year")
        if len(text) == 7 and text[4] == "-":
            year, month = map(int, text.split("-"))
            last = calendar.monthrange(year, month)[1]
            return DateInterval(date(year, month, 1), date(year, month, last), "month")
        if len(text) >= 10 and text[4] == "-" and text[7] == "-":
            parsed = date.fromisoformat(text[:10])
            return DateInterval(parsed, parsed, "day")
    except (ValueError, OverflowError):
        return None
    return None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require_columns(table: pa.Table, required: set[str], path: Path) -> None:
    missing = sorted(required - set(table.column_names))
    if missing:
        raise ValueError(f"missing columns in {path}: {missing}")


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected object")
    return value


def _keys(value: Any, required: set[str], optional: set[str], path: str) -> dict[str, Any]:
    obj = _object(value, path)
    unknown = sorted(set(obj) - required - optional)
    missing = sorted(required - set(obj))
    if unknown:
        raise ValueError(f"{path}.{unknown[0]}: unknown key")
    if missing:
        raise ValueError(f"{path}.{missing[0]}: missing required key")
    return obj


def _nonempty_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path}: expected non-empty string")
    return value


def _validate_comparator(cmp: Any, path: str) -> str:
    if cmp not in COMPARATORS:
        raise ValueError(f"{path}: unknown comparator {cmp!r}")
    return str(cmp)


def _validate_field(field: Any, path: str, allowed_prefixes: tuple[str, ...]) -> str:
    if not isinstance(field, str):
        raise ValueError(f"{path}: expected field string")
    if field.startswith("chain.") and "chain." in allowed_prefixes:
        if field[6:] not in CHAIN_SCALAR_FIELDS | CHAIN_LIST_FIELDS:
            raise ValueError(f"{path}: unknown Chain field {field!r}")
        return field
    if field.startswith("derived.") and "derived." in allowed_prefixes:
        if field[8:] not in DERIVED_FIELDS:
            raise ValueError(f"{path}: unknown derived field {field!r}")
        return field
    if field.startswith("event.") and "event." in allowed_prefixes:
        if field[6:] not in EVENT_FIELDS:
            raise ValueError(f"{path}: unknown event field {field!r}")
        return field
    raise ValueError(f"{path}: field {field!r} is not allowed here")


def _validate_selector(value: Any, path: str) -> None:
    selector = _keys(value, {"scope", "family", "where"}, set(), path)
    if selector["scope"] != "middle_events":
        raise ValueError(f"{path}.scope: only 'middle_events' is supported")
    if selector["family"] not in {"any", "exposure", "interface"}:
        raise ValueError(f"{path}.family: unknown family {selector['family']!r}")
    if not isinstance(selector["where"], list):
        raise ValueError(f"{path}.where: expected array")
    for index, raw_predicate in enumerate(selector["where"]):
        predicate_path = f"{path}.where[{index}]"
        predicate = _keys(raw_predicate, {"field", "cmp"}, {"value"}, predicate_path)
        _validate_field(predicate["field"], f"{predicate_path}.field", ("event.",))
        cmp = _validate_comparator(predicate["cmp"], f"{predicate_path}.cmp")
        if cmp in {"is_null", "not_null"}:
            if "value" in predicate:
                raise ValueError(f"{predicate_path}.value: forbidden for {cmp}")
        elif "value" not in predicate:
            raise ValueError(f"{predicate_path}.value: missing required key")
        elif isinstance(predicate["value"], dict):
            raise ValueError(f"{predicate_path}.value: expected JSON scalar or array")


def _validate_value(value: Any, path: str) -> None:
    expr = _object(value, path)
    if len(expr) != 1:
        raise ValueError(f"{path}: value expression must contain exactly one construct")
    key = next(iter(expr))
    if key == "field":
        _validate_field(expr[key], f"{path}.field", ("chain.", "derived."))
    elif key == "literal":
        if isinstance(expr[key], dict):
            raise ValueError(f"{path}.literal: expected JSON scalar or array")
    elif key == "event_count":
        _validate_selector(expr[key], f"{path}.event_count")
    elif key == "event_timing":
        timing = _keys(expr[key], {"selector", "relative_to", "edge", "bound"}, set(), f"{path}.event_timing")
        _validate_selector(timing["selector"], f"{path}.event_timing.selector")
        if timing["relative_to"] not in {"start", "end"}:
            raise ValueError(f"{path}.event_timing.relative_to: expected start or end")
        if timing["edge"] not in {"first", "last"}:
            raise ValueError(f"{path}.event_timing.edge: expected first or last")
        if timing["bound"] not in {"min", "max"}:
            raise ValueError(f"{path}.event_timing.bound: expected min or max")
    elif key == "event_position":
        position = _keys(expr[key], {"selector", "scope", "edge"}, set(), f"{path}.event_position")
        _validate_selector(position["selector"], f"{path}.event_position.selector")
        if position["scope"] not in {"family", "all_middle"}:
            raise ValueError(f"{path}.event_position.scope: expected family or all_middle")
        if position["edge"] not in {"first_batch", "last_batch"}:
            raise ValueError(f"{path}.event_position.edge: expected first_batch or last_batch")
    elif key == "event_distinct_count":
        distinct = _keys(expr[key], {"selector", "field"}, set(), f"{path}.event_distinct_count")
        _validate_selector(distinct["selector"], f"{path}.event_distinct_count.selector")
        if distinct["selector"]["family"] != "interface":
            raise ValueError(f"{path}.event_distinct_count.selector: interface family required")
        if distinct["field"] != "event.participant_key":
            raise ValueError(f"{path}.event_distinct_count.field: only event.participant_key is supported")
    elif key == "value_count":
        count = _keys(
            expr[key],
            {"field", "distinct", "strip_strings", "ignore_null", "ignore_empty"},
            set(), f"{path}.value_count",
        )
        field = _validate_field(count["field"], f"{path}.value_count.field", ("chain.",))
        if field[6:] not in CHAIN_LIST_FIELDS:
            raise ValueError(f"{path}.value_count.field: expected Chain list field")
        for option in ("distinct", "strip_strings", "ignore_null", "ignore_empty"):
            if type(count[option]) is not bool:
                raise ValueError(f"{path}.value_count.{option}: expected boolean")
    else:
        raise ValueError(f"{path}.{key}: unknown value construct")


def _validate_rule(value: Any, path: str) -> None:
    rule = _object(value, path)
    if "op" not in rule:
        if rule:
            unknown = next(iter(rule))
            raise ValueError(f"{path}.{unknown}: unknown key")
        raise ValueError(f"{path}.op: missing required key")
    op = rule["op"]
    if op in {"all", "any"}:
        node = _keys(rule, {"op", "rules"}, set(), path)
        if not isinstance(node["rules"], list) or not node["rules"]:
            raise ValueError(f"{path}.rules: expected non-empty array")
        for index, child in enumerate(node["rules"]):
            _validate_rule(child, f"{path}.rules[{index}]")
    elif op == "not":
        node = _keys(rule, {"op", "rule"}, set(), path)
        _validate_rule(node["rule"], f"{path}.rule")
    elif op in {"present", "absent"}:
        node = _keys(rule, {"op", "selector"}, set(), path)
        _validate_selector(node["selector"], f"{path}.selector")
    elif op == "compare":
        node = _keys(rule, {"op", "left", "cmp"}, {"right"}, path)
        _validate_value(node["left"], f"{path}.left")
        cmp = _validate_comparator(node["cmp"], f"{path}.cmp")
        if cmp in {"is_null", "not_null"}:
            if "right" in node:
                raise ValueError(f"{path}.right: forbidden for {cmp}")
        else:
            if "right" not in node:
                raise ValueError(f"{path}.right: missing required key")
            _validate_value(node["right"], f"{path}.right")
    elif op == "sequence":
        node = _keys(
            rule, {"op", "steps", "ordering", "min_gap_days", "max_gap_days", "same_values"}, set(), path,
        )
        if not isinstance(node["steps"], list) or len(node["steps"]) < 2:
            raise ValueError(f"{path}.steps: expected at least two selectors")
        for index, selector in enumerate(node["steps"]):
            _validate_selector(selector, f"{path}.steps[{index}]")
        if node["ordering"] != "strict_before":
            raise ValueError(f"{path}.ordering: only 'strict_before' is supported")
        for field in ("min_gap_days", "max_gap_days"):
            setting = node[field]
            if setting is not None and (type(setting) is not int or setting < 0):
                raise ValueError(f"{path}.{field}: expected null or nonnegative integer")
        if node["min_gap_days"] is not None and node["max_gap_days"] is not None:
            if node["min_gap_days"] > node["max_gap_days"]:
                raise ValueError(f"{path}: min_gap_days cannot exceed max_gap_days")
        if not isinstance(node["same_values"], list):
            raise ValueError(f"{path}.same_values: expected array")
        if len(set(node["same_values"])) != len(node["same_values"]):
            raise ValueError(f"{path}.same_values: duplicate fields")
        for index, field in enumerate(node["same_values"]):
            _validate_field(field, f"{path}.same_values[{index}]", ("event.",))
    else:
        raise ValueError(f"{path}.op: unknown operator {op!r}")


def validate_pattern_config(payload: Any, path: Path) -> PatternSet:
    root = _keys(payload, {"config_schema_version", "pattern_set_id", "primary_order", "patterns"}, set(), "$")
    if root["config_schema_version"] != "1.0":
        raise ValueError("$.config_schema_version: expected '1.0'")
    pattern_set_id = _nonempty_string(root["pattern_set_id"], "$.pattern_set_id")
    if not isinstance(root["patterns"], list) or not root["patterns"]:
        raise ValueError("$.patterns: expected non-empty array")
    definitions: list[PatternDefinition] = []
    seen: set[str] = set()
    for index, raw_definition in enumerate(root["patterns"]):
        item_path = f"$.patterns[{index}]"
        definition = _keys(
            raw_definition,
            {"id", "name", "description", "evaluation_status", "not_evaluable_reason", "rule"},
            {"track"}, item_path,
        )
        track = definition.get("track")
        if track is not None and track not in {"ACTION_ELIGIBLE", "DESCRIPTIVE_ASSOCIATION"}:
            raise ValueError(f"{item_path}.track: invalid Pattern track {track!r}")
        pattern_id = _nonempty_string(definition["id"], f"{item_path}.id")
        if not PATTERN_ID_RE.fullmatch(pattern_id):
            raise ValueError(f"{item_path}.id: invalid Pattern ID {pattern_id!r}")
        if pattern_id in seen:
            raise ValueError(f"{item_path}.id: duplicate Pattern ID {pattern_id!r}")
        seen.add(pattern_id)
        name = _nonempty_string(definition["name"], f"{item_path}.name")
        description = _nonempty_string(definition["description"], f"{item_path}.description")
        status = definition["evaluation_status"]
        if status not in {"EVALUABLE", "NOT_EVALUABLE"}:
            raise ValueError(f"{item_path}.evaluation_status: invalid status {status!r}")
        reason = definition["not_evaluable_reason"]
        rule = definition["rule"]
        if status == "EVALUABLE":
            if reason is not None:
                raise ValueError(f"{item_path}.not_evaluable_reason: must be null for EVALUABLE Pattern")
            if rule is None:
                raise ValueError(f"{item_path}.rule: must be non-null for EVALUABLE Pattern")
            _validate_rule(rule, f"{item_path}.rule")
        else:
            if rule is not None:
                raise ValueError(f"{item_path}.rule: must be null for NOT_EVALUABLE Pattern")
            reason = _nonempty_string(reason, f"{item_path}.not_evaluable_reason")
        definitions.append(PatternDefinition(pattern_id, name, description, status, reason, rule, track))
    if not isinstance(root["primary_order"], list):
        raise ValueError("$.primary_order: expected array")
    primary_order = root["primary_order"]
    for index, value in enumerate(primary_order):
        if not isinstance(value, str):
            raise ValueError(f"$.primary_order[{index}]: expected Pattern ID string")
    if len(primary_order) != len(set(primary_order)):
        raise ValueError("$.primary_order: duplicate Pattern IDs")
    missing = sorted(seen - set(primary_order))
    unknown = sorted(set(primary_order) - seen)
    if missing or unknown or len(primary_order) != len(seen):
        raise ValueError(f"$.primary_order: must contain every configured ID exactly once; missing={missing}, unknown={unknown}")
    resolved_path = path.expanduser().resolve()
    digest = sha256_file(resolved_path) if resolved_path.is_file() else hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return PatternSet("1.0", pattern_set_id, tuple(primary_order), tuple(definitions), resolved_path, digest)


def load_pattern_config(path: Path) -> PatternSet:
    resolved = path.expanduser().resolve()
    try:
        payload = json.loads(resolved.read_text())
    except OSError as exc:
        raise ValueError(f"{resolved}: cannot read Pattern spec: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{resolved}: invalid JSON at line {exc.lineno} column {exc.colno}: {exc.msg}") from exc
    try:
        return validate_pattern_config(payload, resolved)
    except ValueError as exc:
        raise ValueError(f"{resolved}: {exc}") from exc


def pattern_set_payload(pattern_set: PatternSet) -> dict[str, Any]:
    return {
        "config_schema_version": pattern_set.config_schema_version,
        "pattern_set_id": pattern_set.pattern_set_id,
        "primary_order": list(pattern_set.primary_order),
        "patterns": [
            {
                "id": item.id,
                "name": item.name,
                "description": item.description,
                "evaluation_status": item.evaluation_status,
                "not_evaluable_reason": item.not_evaluable_reason,
                "rule": item.rule,
                **({"track": item.track} if item.track is not None else {}),
            }
            for item in pattern_set.patterns
        ],
    }


def _compare_values(left: Any, cmp: str, right: Any = None, *, null_ordering_error: bool = False) -> bool:
    if cmp == "is_null":
        return left is None
    if cmp == "not_null":
        return left is not None
    if cmp == "eq":
        return left == right
    if cmp == "ne":
        return left != right
    if cmp in ORDERING_COMPARATORS and (left is None or right is None):
        if null_ordering_error:
            raise ValueError(f"cannot apply {cmp} to null required Chain/derived value")
        return False
    if cmp in {"in", "not_in"}:
        if not isinstance(right, (list, tuple, set, frozenset, str)):
            raise ValueError(f"right operand for {cmp} must be an array or string")
        result = left in right
        return result if cmp == "in" else not result
    try:
        if cmp == "lt":
            return left < right
        if cmp == "lte":
            return left <= right
        if cmp == "gt":
            return left > right
        if cmp == "gte":
            return left >= right
    except TypeError as exc:
        raise ValueError(f"invalid comparison {left!r} {cmp} {right!r}: {exc}") from exc
    raise ValueError(f"unknown comparator at runtime: {cmp}")


def select_events(selector: dict[str, Any], context: ChainRuleContext) -> list[dict[str, Any]]:
    selected = []
    for event in context.events:
        if selector["family"] != "any" and event["family"] != selector["family"]:
            continue
        matches = True
        for predicate in selector["where"]:
            left = event[predicate["field"][6:]]
            right = predicate.get("value")
            if not _compare_values(left, predicate["cmp"], right, null_ordering_error=False):
                matches = False
                break
        if matches:
            selected.append(event)
    return selected


def _batches_for_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build overlap-aware batches for an arbitrary typed/family event subset."""
    dated = sorted(
        (event for event in events if event.get("date_interval") is not None),
        key=lambda item: (
            item["date_interval"].minimum, item["date_interval"].maximum,
            item["family"], item.get("event_type") or "", item["event_id"],
        ),
    )
    batches: list[dict[str, Any]] = []
    for event in dated:
        interval = event["date_interval"]
        if not batches or interval.minimum > batches[-1]["maximum"]:
            batches.append({"minimum": interval.minimum, "maximum": interval.maximum, "events": [event]})
        else:
            batches[-1]["maximum"] = max(batches[-1]["maximum"], interval.maximum)
            batches[-1]["events"].append(event)
    return batches


def _event_timing(options: dict[str, Any], context: ChainRuleContext) -> int | None:
    events = select_events(options["selector"], context)
    relative = options["relative_to"]
    # A founding sentinel is deliberately not converted into a fake date.
    if relative == "start" and context.chain.get("chain_start_date") == "founding":
        return None
    if relative == "end" and context.chain.get("chain_window_status") != "dated":
        return None
    prefix = "days_after_chain_start" if relative == "start" else "days_before_chain_end"
    first = options["edge"] == "first"
    minimum, maximum = _timing_pair(events, prefix, first=first)
    return minimum if options["bound"] == "min" else maximum


def _event_position(options: dict[str, Any], context: ChainRuleContext) -> bool:
    selected_ids = {event["event_id"] for event in select_events(options["selector"], context)}
    if not selected_ids:
        return False
    if options["scope"] == "all_middle":
        batches = list(context.batches)
    else:
        family = options["selector"]["family"]
        if family == "any":
            raise ValueError("family-scoped event_position requires a concrete selector family")
        batches = _batches_for_events([event for event in context.events if event["family"] == family])
    if not batches:
        return False
    edge = batches[0] if options["edge"] == "first_batch" else batches[-1]
    return any(event["event_id"] in selected_ids for event in edge["events"])


def evaluate_value(expr: dict[str, Any], context: ChainRuleContext) -> Any:
    if "field" in expr:
        field = expr["field"]
        namespace, name = field.split(".", 1)
        return context.chain[name] if namespace == "chain" else context.derived[name]
    if "literal" in expr:
        return expr["literal"]
    if "event_count" in expr:
        return len(select_events(expr["event_count"], context))
    if "event_timing" in expr:
        return _event_timing(expr["event_timing"], context)
    if "event_position" in expr:
        return _event_position(expr["event_position"], context)
    if "event_distinct_count" in expr:
        selected = select_events(expr["event_distinct_count"]["selector"], context)
        return len({event["participant_key"] for event in selected if event.get("participant_key")})
    options = expr["value_count"]
    values = list(context.chain[options["field"][6:]])
    normalized = []
    for value in values:
        if value is None and options["ignore_null"]:
            continue
        if options["strip_strings"] and isinstance(value, str):
            value = value.strip()
        if options["ignore_empty"] and (value == "" or value == []):
            continue
        normalized.append(value)
    if options["distinct"]:
        try:
            return len(set(normalized))
        except TypeError as exc:
            raise ValueError("value_count distinct values must be hashable") from exc
    return len(normalized)


def _evaluate_sequence(rule: dict[str, Any], context: ChainRuleContext) -> bool:
    candidates = [select_events(selector, context) for selector in rule["steps"]]
    same_names = [field[6:] for field in rule["same_values"]]

    def search(step: int, chosen: list[dict[str, Any]]) -> bool:
        if step == len(candidates):
            return True
        for event in candidates[step]:
            if any(event["event_id"] == prior["event_id"] for prior in chosen):
                continue
            interval = event["date_interval"]
            if interval is None:
                continue
            if chosen:
                previous = chosen[-1]
                previous_interval = previous["date_interval"]
                if previous_interval is None or previous_interval.maximum >= interval.minimum:
                    continue
                minimum_gap = (interval.minimum - previous_interval.maximum).days
                maximum_gap = (interval.maximum - previous_interval.minimum).days
                if rule["min_gap_days"] is not None and minimum_gap < rule["min_gap_days"]:
                    continue
                if rule["max_gap_days"] is not None and maximum_gap > rule["max_gap_days"]:
                    continue
            if same_names:
                failed = False
                for name in same_names:
                    value = event[name]
                    if value is None or any(prior[name] is None or prior[name] != value for prior in chosen):
                        failed = True
                        break
                if failed:
                    continue
            if search(step + 1, chosen + [event]):
                return True
        return False

    return search(0, [])


def evaluate_rule(rule: dict[str, Any], context: ChainRuleContext) -> bool:
    op = rule["op"]
    if op == "all":
        return all(evaluate_rule(child, context) for child in rule["rules"])
    if op == "any":
        return any(evaluate_rule(child, context) for child in rule["rules"])
    if op == "not":
        return not evaluate_rule(rule["rule"], context)
    if op == "present":
        return bool(select_events(rule["selector"], context))
    if op == "absent":
        return not select_events(rule["selector"], context)
    if op == "compare":
        left = evaluate_value(rule["left"], context)
        right = evaluate_value(rule["right"], context) if "right" in rule else None
        source_field = rule["left"].get("field")
        null_error = bool(source_field and source_field.startswith("chain."))
        return _compare_values(left, rule["cmp"], right, null_ordering_error=null_error)
    if op == "sequence":
        return _evaluate_sequence(rule, context)
    raise ValueError(f"unknown rule operator at runtime: {op}")


def evaluate_pattern_flags(context: ChainRuleContext, pattern_set: PatternSet) -> dict[str, bool | None]:
    return {
        definition.id: (
            evaluate_rule(definition.rule, context)
            if definition.evaluation_status == "EVALUABLE" and definition.rule is not None
            else None
        )
        for definition in pattern_set.patterns
    }


def normalize_identity(value: Any) -> str | None:
    if value is None:
        return None
    text = " ".join(unicodedata.normalize("NFKC", str(value)).casefold().split())
    return text or None


def normalize_round_bucket(value: Any) -> str:
    """Map noisy round text to a fixed, frequency-independent taxonomy."""
    text = normalize_identity(value)
    if not text:
        return "unknown"
    text = re.sub(r"[_\-–—]+", " ", text)
    text = re.sub(r"\b(round|financing|funding)\b", " ", text)
    text = " ".join(text.split())
    if text in {"unknown", "n/a", "na", "none", "pending", "undisclosed", "not disclosed"}:
        return "unknown"
    aliases = {
        "pre seed": "pre_seed", "preseed": "pre_seed", "seed": "seed",
        "angel": "angel", "bridge": "bridge_or_extension", "extension": "bridge_or_extension",
        "convertible": "convertible", "convertible note": "convertible", "safe": "convertible",
        "debt": "debt", "venture debt": "debt", "grant": "grant",
        "crowdfunding": "crowdfunding", "private equity": "private_equity", "pe": "private_equity",
    }
    if text in aliases:
        return aliases[text]
    if any(token in text for token in ("bridge", "extension")):
        return "bridge_or_extension"
    match = re.fullmatch(r"(?:series|serie)\s*([a-z])(?:\s+\d+)?", text)
    if match:
        return f"series_{match.group(1)}"
    return "other"


def build_event_lookup(exposure_rows: list[dict], interface_rows: list[dict]) -> dict[str, dict]:
    lookup: dict[str, dict] = {}
    for family, rows in (("exposure", exposure_rows), ("interface", interface_rows)):
        for row in rows:
            event_id = row["event_id"]
            if event_id in lookup:
                raise ValueError(f"duplicate event_id across middle-event tables: {event_id}")
            interval = parse_partial_date(row.get("event_date"))
            investor_id = row.get("investor_id") if family == "interface" else None
            raw_name = row.get("raw_investor_name") if family == "interface" else None
            normalized_raw = normalize_identity(raw_name)
            participant_key = f"id:{investor_id.strip()}" if isinstance(investor_id, str) and investor_id.strip() else (
                f"raw:{normalized_raw}" if normalized_raw else None
            )
            lookup[event_id] = {
                "event_id": event_id, "company_id": row["company_id"], "family": family,
                "event_type": row.get("event_type"), "event_subtype": row.get("event_subtype"),
                "event_date": row.get("event_date"), "date_interval": interval,
                "date_precision": interval.precision if interval else None,
                "date_min": interval.minimum.isoformat() if interval else None,
                "date_max": interval.maximum.isoformat() if interval else None,
                "investor_id": investor_id, "raw_investor_name": raw_name,
                "participant_key": participant_key,
                "participant_basis": "resolved_id" if participant_key and participant_key.startswith("id:") else (
                    "normalized_raw_name" if participant_key else None
                ),
            }
    return lookup


def order_middle_events(chain: dict, event_lookup: dict[str, dict]) -> tuple[list[dict], int]:
    """Return overlap-aware ordered batches and count of undated referenced events."""
    if chain["chain_window_status"] != "dated":
        return [], 0
    events: list[dict] = []
    undated = 0
    for event_id in list(chain["exposure_event_ids"]) + list(chain["interface_event_ids"]):
        if event_id not in event_lookup:
            raise ValueError(f"chain references missing middle event: {event_id}")
        event = event_lookup[event_id]
        if event["date_interval"] is None:
            undated += 1
            continue
        events.append(event)
    events.sort(key=lambda item: (
        item["date_interval"].minimum, item["date_interval"].maximum,
        item["family"], item.get("event_type") or "", item["event_id"],
    ))
    batches: list[dict] = []
    for event in events:
        interval = event["date_interval"]
        if not batches or interval.minimum > batches[-1]["maximum"]:
            batches.append({"minimum": interval.minimum, "maximum": interval.maximum, "events": [event]})
        else:
            batches[-1]["maximum"] = max(batches[-1]["maximum"], interval.maximum)
            batches[-1]["events"].append(event)
    for batch in batches:
        batch["events"].sort(key=lambda item: (item["family"], item.get("event_type") or "", item["event_id"]))
    return batches, undated


def outcome_investor_count(chain: dict) -> tuple[int, str]:
    raw_names = {normalize_identity(value) for value in chain["investor_raw_names"]}
    raw_names.discard(None)
    if raw_names:
        return len(raw_names), "unique_raw_names"
    resolved = {str(value).strip() for value in chain["investor_ids"] if value is not None and str(value).strip()}
    return len(resolved), "resolved_ids_fallback"


def _parse_exact_boundary(value: Any) -> date | None:
    interval = parse_partial_date(value)
    return interval.minimum if interval is not None and interval.precision == "day" else None


def _context_events(chain: dict[str, Any], event_lookup: dict[str, dict]) -> tuple[dict[str, Any], ...]:
    start = None if chain["chain_start_date"] == "founding" else _parse_exact_boundary(chain["chain_start_date"])
    end = _parse_exact_boundary(chain["chain_end_date"]) if chain["chain_window_status"] == "dated" else None
    events = []
    for event_id in list(chain["exposure_event_ids"]) + list(chain["interface_event_ids"]):
        if event_id not in event_lookup:
            raise ValueError(f"chain references missing middle event: {event_id}")
        source = event_lookup[event_id]
        event = dict(source)
        interval = event["date_interval"]
        event["days_before_chain_end_min"] = (end - interval.maximum).days if end and interval else None
        event["days_before_chain_end_max"] = (end - interval.minimum).days if end and interval else None
        event["days_after_chain_start_min"] = (interval.minimum - start).days if start and interval else None
        event["days_after_chain_start_max"] = (interval.maximum - start).days if start and interval else None
        events.append(event)
    events.sort(key=lambda item: (
        item["date_interval"].minimum if item["date_interval"] else date.max,
        item["date_interval"].maximum if item["date_interval"] else date.max,
        item["family"], item.get("event_type") or "", item["event_id"],
    ))
    return tuple(events)


def _timing_pair(events: list[dict[str, Any]], prefix: str, *, first: bool) -> tuple[int | None, int | None]:
    dated = [event for event in events if event["date_interval"] is not None and event.get(prefix + "_min") is not None]
    if not dated:
        return None, None
    if first:
        edge = min(event["date_interval"].minimum for event in dated)
        boundary = max(event["date_interval"].maximum for event in dated if event["date_interval"].minimum == edge)
        selected = [event for event in dated if event["date_interval"].minimum <= boundary]
    else:
        edge = max(event["date_interval"].maximum for event in dated)
        boundary = min(event["date_interval"].minimum for event in dated if event["date_interval"].maximum == edge)
        selected = [event for event in dated if event["date_interval"].maximum >= boundary]
    return min(event[prefix + "_min"] for event in selected), max(event[prefix + "_max"] for event in selected)


def _base_chain_derived(chain: dict[str, Any], events: tuple[dict[str, Any], ...], batches: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    outcome_count, outcome_basis = outcome_investor_count(chain)
    start = None if chain["chain_start_date"] == "founding" else _parse_exact_boundary(chain["chain_start_date"])
    end = _parse_exact_boundary(chain["chain_end_date"]) if chain["chain_window_status"] == "dated" else None
    duration = (end - start).days if start is not None and end is not None else None
    n_middle = int(chain["n_exposure"]) + int(chain["n_interface"])
    interface_events = [event for event in events if event["family"] == "interface"]
    resolved = {str(event["investor_id"]).strip() for event in interface_events if event.get("investor_id") and str(event["investor_id"]).strip()}
    raw_names = {normalize_identity(event.get("raw_investor_name")) for event in interface_events}
    raw_names.discard(None)
    # Identity is selected independently for each row: a resolved ID wins for that
    # row, otherwise its normalized raw name is the fallback.  Deduplicate only
    # after constructing these row-level keys; globally preferring all raw names
    # can double-count aliases attached to resolved rows and drop mixed evidence.
    participant_keys = {event["participant_key"] for event in interface_events if event.get("participant_key")}
    participant_bases = {event["participant_basis"] for event in interface_events if event.get("participant_key")}
    if participant_bases == {"resolved_id"}:
        interface_basis = "resolved_ids"
    elif participant_bases == {"normalized_raw_name"}:
        interface_basis = "normalized_raw_names_fallback"
    elif participant_bases:
        interface_basis = "mixed_resolved_id_raw_fallback"
    else:
        interface_basis = "none"
    interface_count = len(participant_keys)
    gaps = [
        ((right["minimum"] - left["maximum"]).days, (right["maximum"] - left["minimum"]).days)
        for left, right in zip(batches, batches[1:])
    ]
    round_bucket = normalize_round_bucket(chain.get("outcome_round"))
    derived: dict[str, Any] = {
        "n_middle": n_middle,
        "n_raw_outcome_investors": len(chain["investor_raw_names"]),
        "n_resolved_outcome_investors": len(chain["investor_ids"]),
        "outcome_investor_count": outcome_count, "outcome_investor_basis": outcome_basis,
        "chain_duration_days": duration, "outcome_round_bucket": round_bucket,
        "outcome_round_known": round_bucket not in {"unknown", "other"},
        "n_ordered_batches": len(batches), "n_comparable_gaps": len(gaps),
        "n_known_date_middle_events": sum(event["date_interval"] is not None for event in events),
        "middle_span_min": ((batches[-1]["minimum"] - batches[0]["maximum"]).days if len(batches) >= 2 else None),
        "middle_span_max": ((batches[-1]["maximum"] - batches[0]["minimum"]).days if len(batches) >= 2 else None),
        "min_interbatch_gap_min": min((item[0] for item in gaps), default=None),
        "min_interbatch_gap_max": min((item[1] for item in gaps), default=None),
        "max_interbatch_gap_min": max((item[0] for item in gaps), default=None),
        "max_interbatch_gap_max": max((item[1] for item in gaps), default=None),
        "n_unique_interface_investors_resolved": len(resolved),
        "n_unique_interface_raw_names": len(raw_names),
        "interface_investor_count": interface_count, "interface_investor_basis": interface_basis,
        "exposure_share": int(chain["n_exposure"]) / n_middle if n_middle else None,
        "interface_share": int(chain["n_interface"]) / n_middle if n_middle else None,
        "events_per_chain_year": n_middle * 365.25 / duration if duration and duration > 0 else None,
        "batches_per_chain_year": len(batches) * 365.25 / duration if duration and duration > 0 else None,
        "n_prior_chains": 0, "company_chain_count": 1, "is_last_chain": True,
        "chain_position_bucket": "first", "prior_same_round_count": 0,
        "round_occurrence_index": 1, "days_since_previous_same_round": None,
        "prior_outcome_investor_overlap_count": 0, "prior_outcome_investor_overlap_ratio": None,
        "has_prior_outcome_investor_overlap": False,
    }
    for scope in ("any", "exposure", "interface"):
        scoped = list(events) if scope == "any" else [event for event in events if event["family"] == scope]
        first_min, first_max = _timing_pair(scoped, "days_after_chain_start", first=True)
        last_min, last_max = _timing_pair(scoped, "days_before_chain_end", first=False)
        derived[f"first_{scope}_after_start_min"] = first_min
        derived[f"first_{scope}_after_start_max"] = first_max
        derived[f"last_{scope}_before_end_min"] = last_min
        derived[f"last_{scope}_before_end_max"] = last_max
    return derived


def build_chain_rule_context(
    chain: dict[str, Any], event_lookup: dict[str, dict], batches: list[dict] | None = None,
    derived_overrides: dict[str, Any] | None = None,
) -> ChainRuleContext:
    if batches is None:
        batches, _ = order_middle_events(chain, event_lookup)
    events = _context_events(chain, event_lookup)
    derived = _base_chain_derived(chain, events, tuple(batches))
    if derived_overrides:
        derived.update(derived_overrides)
    return ChainRuleContext(chain, events, tuple(batches), derived)


def _outcome_identity_keys(chain: dict[str, Any]) -> set[str]:
    result = {f"id:{str(value).strip()}" for value in chain["investor_ids"] if value is not None and str(value).strip()}
    result.update(f"raw:{name}" for name in (normalize_identity(value) for value in chain["investor_raw_names"]) if name)
    return result


def build_chain_rule_contexts(chain_rows: list[dict[str, Any]], event_lookup: dict[str, dict]) -> list[ChainRuleContext]:
    """Build deterministic local + prior-history contexts without labels or outcome statistics."""
    ordered = sorted(chain_rows, key=lambda row: (row["company_id"], row["chain_sequence"], row["chain_id"]))
    company_counts = Counter(row["company_id"] for row in ordered)
    priors: dict[str, list[ChainRuleContext]] = defaultdict(list)
    contexts: list[ChainRuleContext] = []
    for chain in ordered:
        base = build_chain_rule_context(chain, event_lookup)
        previous = priors[chain["company_id"]]
        bucket = base.derived["outcome_round_bucket"]
        same_round = [item for item in previous if item.derived["outcome_round_bucket"] == bucket and bucket != "unknown"]
        current_keys = _outcome_identity_keys(chain)
        prior_keys = set().union(*(_outcome_identity_keys(item.chain) for item in previous)) if previous else set()
        overlap = current_keys & prior_keys
        ordinal = len(previous) + 1
        if ordinal == 1:
            position = "first"
        elif ordinal == company_counts[chain["company_id"]]:
            position = "last"
        elif ordinal <= 3:
            position = "early"
        else:
            position = "later"
        current_end = _parse_exact_boundary(chain.get("chain_end_date"))
        previous_end = _parse_exact_boundary(same_round[-1].chain.get("chain_end_date")) if same_round else None
        overrides = {
            "n_prior_chains": len(previous), "company_chain_count": company_counts[chain["company_id"]],
            "is_last_chain": ordinal == company_counts[chain["company_id"]],
            "chain_position_bucket": position, "prior_same_round_count": len(same_round),
            "round_occurrence_index": len(same_round) + 1,
            "days_since_previous_same_round": (current_end - previous_end).days if current_end and previous_end else None,
            "prior_outcome_investor_overlap_count": len(overlap),
            "prior_outcome_investor_overlap_ratio": len(overlap) / len(current_keys) if current_keys else None,
            "has_prior_outcome_investor_overlap": bool(overlap),
        }
        context = ChainRuleContext(base.chain, base.events, base.batches, {**base.derived, **overrides})
        contexts.append(context)
        previous.append(context)
    return contexts


def primary_seed(flags: dict[str, bool | None], evaluable: bool, pattern_set: PatternSet) -> str:
    if not evaluable:
        return "NOT_EVALUABLE"
    return next((pattern_id for pattern_id in pattern_set.primary_order if flags[pattern_id] is True), "UNMATCHED")


def partial_boundary_diagnostics(chain: dict, batches: list[dict]) -> tuple[bool | None, list[dict]]:
    if chain["chain_window_status"] != "dated":
        return None, []
    start = None if chain["chain_start_date"] == "founding" else _parse_exact_boundary(chain["chain_start_date"])
    end = _parse_exact_boundary(chain["chain_end_date"])
    ambiguities = []
    for batch in batches:
        for event in batch["events"]:
            interval = event["date_interval"]
            if interval.precision == "day":
                continue
            boundaries = []
            if start is not None and interval.minimum < start <= interval.maximum:
                boundaries.append("start")
            if end is not None and interval.minimum < end <= interval.maximum:
                boundaries.append("end")
            if boundaries:
                ambiguities.append({
                    "event_id": event["event_id"], "event_date": event["event_date"],
                    "date_min": interval.minimum.isoformat(), "date_max": interval.maximum.isoformat(),
                    "crossed_boundaries": boundaries,
                })
    return bool(ambiguities), ambiguities


def chain_feature_rows(
    chain_rows: list[dict], event_lookup: dict[str, dict], pattern_set: PatternSet,
) -> list[dict]:
    features: list[dict] = []
    pattern_ids = [definition.id for definition in pattern_set.patterns]
    statuses = {definition.id: definition.evaluation_status for definition in pattern_set.patterns}
    for chain in sorted(chain_rows, key=lambda row: (row["company_id"], row["chain_sequence"], row["chain_id"])):
        batches, undated = order_middle_events(chain, event_lookup)
        context = build_chain_rule_context(chain, event_lookup, batches)
        batch_tokens, batch_payload = [], []
        for batch in batches:
            tokens = [("E" if event["family"] == "exposure" else "I") + ":" + event["event_type"] for event in batch["events"]]
            batch_tokens.append("{" + "|".join(tokens) + "}")
            batch_payload.append({
                "date_min": batch["minimum"].isoformat(), "date_max": batch["maximum"].isoformat(),
                "events": [{
                    "event_id": event["event_id"], "family": event["family"],
                    "event_type": event["event_type"], "event_subtype": event["event_subtype"],
                    "event_date": event["event_date"],
                } for event in batch["events"]],
            })
        flags = evaluate_pattern_flags(context, pattern_set)
        evaluable = chain["chain_window_status"] == "dated"
        matched = [pattern_id for pattern_id in pattern_ids if flags[pattern_id] is True]
        primary = primary_seed(flags, evaluable, pattern_set)
        has_ambiguity, ambiguities = partial_boundary_diagnostics(chain, batches)
        all_exact = all(event["date_interval"].precision == "day" for batch in batches for event in batch["events"])
        if not evaluable:
            order_status = "unknown_end"
        elif not batches:
            order_status = "no_middle"
        elif all_exact and all(len(batch["events"]) == 1 for batch in batches):
            order_status = "exact"
        else:
            order_status = "partial_batches"
        row = {
            "chain_id": chain["chain_id"], "company_id": chain["company_id"],
            "company_label_v4": chain["company_label_v4"], "chain_sequence": int(chain["chain_sequence"]),
            "chain_start_date": chain["chain_start_date"], "chain_start_reason": chain["chain_start_reason"],
            "chain_end_date": chain["chain_end_date"], "chain_window_status": chain["chain_window_status"],
            "outcome_type": chain["outcome_type"], "outcome_round": chain.get("outcome_round"),
            "n_participant_rows": int(chain["n_participant_rows"]),
            "n_resolved_outcome_investors": context.derived["n_resolved_outcome_investors"],
            "n_raw_outcome_investors": context.derived["n_raw_outcome_investors"],
            "n_outcome_investors": context.derived["outcome_investor_count"],
            "outcome_investor_basis": context.derived["outcome_investor_basis"],
            "chain_duration_days": context.derived["chain_duration_days"],
            "n_exposure": int(chain["n_exposure"]), "n_interface": int(chain["n_interface"]),
            "n_middle": context.derived["n_middle"], "n_ordered_batches": len(batches),
            "n_undated_referenced_events": undated,
            "has_partial_date_boundary_ambiguity": has_ambiguity,
            "n_partial_date_boundary_ambiguities": len(ambiguities) if evaluable else None,
            "partial_date_boundary_ambiguity_json": json_text(ambiguities), "order_status": order_status,
            "ordered_batch_signature": ">".join(batch_tokens), "ordered_timeline_json": json_text(batch_payload),
            "exposure_type_counts_json": json_text(Counter(chain["exposure_types"])),
            "interface_type_counts_json": json_text(Counter(chain["interface_types"])),
            "signature_seed_flags_json": json_text(flags), "n_signature_seed_flags": len(matched),
            "is_mixed_signature_seed": len(matched) > 1, "primary_seed": primary,
            "pattern_evaluation_statuses_json": json_text(statuses),
        }
        row.update({f"signature_seed_{pattern_id}": flags[pattern_id] for pattern_id in pattern_ids})
        features.append(row)
    return features


def company_profile_rows(
    entity_rows: list[dict], chain_features: list[dict], pattern_set: PatternSet,
) -> list[dict]:
    chains_by_company: dict[str, list[dict]] = defaultdict(list)
    for row in chain_features:
        chains_by_company[row["company_id"]].append(row)
    profiles = []
    for entity in sorted(entity_rows, key=lambda row: row["company_id"]):
        label = entity["label_v4"]
        if label not in {"SUCCESS", "FAILURE"}:
            continue
        rows = chains_by_company.get(entity["company_id"], [])
        evaluable = [row for row in rows if row["chain_window_status"] == "dated"]
        profile = {
            "company_id": entity["company_id"], "company_canonical_name": entity["company_canonical_name"],
            "company_label_v4": label, "has_chain": bool(rows), "has_evaluable_chain": bool(evaluable),
            "n_chains": len(rows), "n_evaluable_chains": len(evaluable),
            "n_unknown_end_chains": len(rows) - len(evaluable),
            "n_middle_events": sum(row["n_middle"] for row in rows),
            "n_mixed_signature_seed_chains": sum(bool(row["is_mixed_signature_seed"]) for row in evaluable),
            "n_unmatched_primary_seed_chains": sum(row["primary_seed"] == "UNMATCHED" for row in evaluable),
        }
        for definition in pattern_set.patterns:
            pattern_id = definition.id
            if definition.evaluation_status != "EVALUABLE":
                signature_count = signature_any = primary_count = primary_any = None
            else:
                signature_count = sum(row[f"signature_seed_{pattern_id}"] is True for row in evaluable)
                primary_count = sum(row["primary_seed"] == pattern_id for row in evaluable)
                signature_any = signature_count > 0 if evaluable else None
                primary_any = primary_count > 0 if evaluable else None
            profile[f"signature_n_{pattern_id}_chains"] = signature_count
            profile[f"signature_any_{pattern_id}"] = signature_any
            profile[f"primary_n_{pattern_id}_chains"] = primary_count
            profile[f"primary_any_{pattern_id}"] = primary_any
        profiles.append(profile)
    return profiles


def normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def tost_p_value(
    success_taking: int, failure_taking: int, success_not_taking: int,
    failure_not_taking: int, delta: float,
) -> float:
    if not 0.0 < delta < 1.0:
        raise ValueError("equivalence margin delta must be between zero and one")
    n1 = success_taking + failure_taking
    n0 = success_not_taking + failure_not_taking
    if n1 == 0 or n0 == 0:
        raise ValueError("taking and not-taking groups must both be non-empty")
    p1, p0 = success_taking / n1, success_not_taking / n0
    difference = p1 - p0
    se = math.sqrt(p1 * (1.0 - p1) / n1 + p0 * (1.0 - p0) / n0)
    if se == 0.0:
        p_lower = 0.0 if difference > -delta else 1.0
        p_upper = 0.0 if difference < delta else 1.0
    else:
        p_lower = 1.0 - normal_cdf((difference + delta) / se)
        p_upper = normal_cdf((difference - delta) / se)
    return max(p_lower, p_upper)


def classify_action_value(ate_success: float, p_value: float, p_tost: float | None) -> str:
    if p_value < ACTION_VALUE_ALPHA:
        if ate_success > 0.0:
            return "RECOMMEND"
        if ate_success < 0.0:
            return "AVOID"
        return "INDIFFERENT"
    if p_tost is not None and p_tost < ACTION_VALUE_ALPHA:
        return "INDIFFERENT"
    return "INCONCLUSIVE"


def action_value_metrics(
    success_taking: int, failure_taking: int, success_not_taking: int,
    failure_not_taking: int, equivalence_margin: float | None = DEFAULT_EQUIVALENCE_MARGIN,
) -> dict[str, float | str | None]:
    n1 = success_taking + failure_taking
    n0 = success_not_taking + failure_not_taking
    if n1 == 0 or n0 == 0:
        raise ValueError("taking and not-taking groups must both be non-empty")
    p1, p0 = success_taking / n1, success_not_taking / n0
    ate_success = p1 - p0
    n = n1 + n0
    denominator = n1 * n0 * (success_taking + success_not_taking) * (failure_taking + failure_not_taking)
    chi_square = (
        n * (success_taking * failure_not_taking - failure_taking * success_not_taking) ** 2 / denominator
        if denominator else 0.0
    )
    p_value = math.erfc(math.sqrt(chi_square / 2.0))
    p_tost = tost_p_value(
        success_taking, failure_taking, success_not_taking, failure_not_taking, equivalence_margin,
    ) if equivalence_margin is not None else None
    return {
        "taking_success_rate": p1, "not_taking_success_rate": p0,
        "ate_success": ate_success, "ate_failure": -ate_success,
        "chi_square": chi_square, "p_value": p_value, "p_tost": p_tost,
        "result": classify_action_value(ate_success, p_value, p_tost),
    }


def empty_action_value_row(definition: PatternDefinition, status: str) -> dict:
    return {
        "pattern": definition.id, "pattern_name": definition.name,
        "pattern_description": definition.description, "evaluation_status": status,
        "not_evaluable_reason": definition.not_evaluable_reason,
        "taking_success": None, "taking_failure": None, "taking_total": None,
        "taking_success_rate": None, "not_taking_success": None,
        "not_taking_failure": None, "not_taking_total": None,
        "not_taking_success_rate": None, "ate_success": None, "ate_failure": None,
        "chi_square": None, "p_value": None, "p_tost": None, "result": None,
    }


def pattern_prevalence(
    company_profiles: list[dict], pattern_set: PatternSet,
    equivalence_margin: float | None = DEFAULT_EQUIVALENCE_MARGIN,
) -> dict:
    scope = Counter(row["company_label_v4"] for row in company_profiles)
    observable = Counter(row["company_label_v4"] for row in company_profiles if row["has_evaluable_chain"])
    success_rows = [row for row in company_profiles if row["company_label_v4"] == "SUCCESS" and row["has_evaluable_chain"]]
    failure_rows = [row for row in company_profiles if row["company_label_v4"] == "FAILURE" and row["has_evaluable_chain"]]
    action_values = []
    for definition in pattern_set.patterns:
        pattern_id = definition.id
        if definition.evaluation_status != "EVALUABLE":
            action_values.append(empty_action_value_row(definition, definition.evaluation_status))
            continue
        if not success_rows or not failure_rows:
            action_values.append(empty_action_value_row(definition, "INSUFFICIENT_COHORT_DATA"))
            continue
        success_taking = sum(row[f"signature_any_{pattern_id}"] is True for row in success_rows)
        failure_taking = sum(row[f"signature_any_{pattern_id}"] is True for row in failure_rows)
        success_not_taking = len(success_rows) - success_taking
        failure_not_taking = len(failure_rows) - failure_taking
        if success_taking + failure_taking == 0 or success_not_taking + failure_not_taking == 0:
            action_values.append(empty_action_value_row(definition, "INSUFFICIENT_PATTERN_SUPPORT"))
            continue
        action_values.append({
            "pattern": pattern_id, "pattern_name": definition.name,
            "pattern_description": definition.description, "evaluation_status": definition.evaluation_status,
            "not_evaluable_reason": definition.not_evaluable_reason,
            "taking_success": success_taking, "taking_failure": failure_taking,
            "taking_total": success_taking + failure_taking,
            "not_taking_success": success_not_taking, "not_taking_failure": failure_not_taking,
            "not_taking_total": success_not_taking + failure_not_taking,
            **action_value_metrics(
                success_taking, failure_taking, success_not_taking,
                failure_not_taking, equivalence_margin,
            ),
        })
    return {
        "pattern_set_id": pattern_set.pattern_set_id,
        "pattern_config_sha256": pattern_set.sha256,
        "scope_companies": dict(scope), "ordered_observable_companies": dict(observable),
        "equivalence_margin": equivalence_margin, "pattern_average_action_value": action_values,
    }


def _core_interface_types() -> set[str]:
    payload = json.loads(DEFAULT_INTERFACE_SCHEMA_PATH.read_text())
    excluded = {"funding_participation", "service_provider", "other"}
    return set(payload["event_type_enum"]) - excluded


def interface_identity_and_profiles(
    interface_rows: list[dict], investor_rows: list[dict], company_labels: dict[str, str],
) -> tuple[dict, list[dict], list[dict]]:
    core_interface_types = _core_interface_types()
    investor_by_id = {row["investor_id"]: row for row in investor_rows}
    if len(investor_by_id) != len(investor_rows):
        raise ValueError("duplicate investor_id in investor entity table")
    slug_to_ids: dict[str, set[str]] = defaultdict(set)
    for row in investor_rows:
        slug_to_ids[make_slug(row["name"])].add(row["investor_id"])
    status_counts = Counter()
    raw_names_by_status: dict[str, set[str]] = defaultdict(set)
    resolved_rows: list[dict] = []
    normalized_exact_candidate_rows = 0
    identity_rows: list[dict] = []
    for row in interface_rows:
        investor_id = row.get("investor_id")
        associated_entity_id = row.get("associated_entity_id")
        counterparty_status = row.get("counterparty_resolution_status")
        raw_name = (row.get("raw_investor_name") or "").strip()
        analysis_candidate_id = None
        if investor_id:
            if investor_id not in investor_by_id:
                raise ValueError(f"interface investor_id missing from entity table: {investor_id}")
            status = "production_resolved"
            resolved_rows.append(row)
        elif associated_entity_id:
            status = "associated_entity"
        elif counterparty_status == "AMBIGUOUS":
            status = "ambiguous_identity"
        elif not raw_name:
            status = "missing_identity"
        else:
            candidates = slug_to_ids.get(make_slug(raw_name), set())
            if len(candidates) == 1:
                status = "analysis_normalized_exact_candidate"
                analysis_candidate_id = next(iter(candidates))
                normalized_exact_candidate_rows += 1
            else:
                status = "unresolved_named"
        identity_rows.append({
            "event_id": row["event_id"], "company_id": row["company_id"],
            "event_type": row["event_type"], "raw_investor_name": raw_name or None,
            "production_investor_id": investor_id,
            "analysis_normalized_exact_candidate_id": analysis_candidate_id,
            "identity_status": status,
        })
        status_counts[status] += 1
        if raw_name:
            raw_names_by_status[status].add(raw_name)
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in resolved_rows:
        grouped[row["investor_id"]].append(row)
    profiles = []
    for investor_id, rows in sorted(grouped.items()):
        entity = investor_by_id[investor_id]
        type_counts = Counter(row["event_type"] for row in rows)
        core_rows = [row for row in rows if row["event_type"] in core_interface_types]
        company_ids = {row["company_id"] for row in rows}
        labels = Counter(company_labels.get(company_id, "UNKNOWN") for company_id in company_ids)
        profiles.append({
            "investor_id": investor_id, "investor_name": entity["name"],
            "investor_type_json": json_text(entity.get("investor_type") or []),
            "n_interface_events": len(rows), "n_core_post_investment_events": len(core_rows),
            "n_companies": len(company_ids), "n_success_companies": labels["SUCCESS"],
            "n_failure_companies": labels["FAILURE"], "event_type_counts_json": json_text(type_counts),
            "core_event_type_counts_json": json_text(Counter(row["event_type"] for row in core_rows)),
            "mode_discovery_eligible": len(core_rows) >= 5 and len(company_ids) >= 3,
        })
    coverage = {
        "total_interface_rows": len(interface_rows), "row_status_counts": dict(status_counts),
        "unique_raw_name_counts": {status: len(names) for status, names in sorted(raw_names_by_status.items())},
        "production_resolved_investors": len(grouped),
        "analysis_normalized_exact_candidate_rows": normalized_exact_candidate_rows,
        "mode_discovery_eligible_investors": sum(row["mode_discovery_eligible"] for row in profiles),
        "core_interface_types": sorted(core_interface_types),
    }
    return coverage, profiles, identity_rows


def write_parquet(rows: list[dict], path: Path, column_types: dict[str, pa.DataType] | None = None) -> None:
    if not rows:
        raise ValueError(f"refusing to write empty parquet: {path.name}")
    table = pa.Table.from_pylist(rows)
    for name, arrow_type in (column_types or {}).items():
        index = table.column_names.index(name)
        table = table.set_column(index, name, pa.array([row[name] for row in rows], type=arrow_type))
    pq.write_table(table, path)


def render_summary(prevalence: dict, identity: dict, pattern_set: PatternSet) -> str:
    margin = prevalence["equivalence_margin"]
    definitions = {definition.id: definition for definition in pattern_set.patterns}
    lines = [
        "# Pattern Average Action Value", "",
        f"Pattern definitions: `{pattern_set.pattern_set_id}` (SHA-256 `{pattern_set.sha256}`).", "",
        "This deterministic observational baseline estimates each Pattern independently from multi-label signature presence.", "",
        "## Chain observability", "",
        f"- Scope companies: SUCCESS {prevalence['scope_companies'].get('SUCCESS', 0)}, FAILURE {prevalence['scope_companies'].get('FAILURE', 0)}",
        f"- Companies with at least one dated/evaluable chain: SUCCESS {prevalence['ordered_observable_companies'].get('SUCCESS', 0)}, FAILURE {prevalence['ordered_observable_companies'].get('FAILURE', 0)}",
        "", "## Pattern average action value", "",
        "ATE = SUCCESS rate among companies taking P minus SUCCESS rate among companies not taking P.",
        f"Rule: if two-sided independent two-proportion p < {ACTION_VALUE_ALPHA:.2f}, ATE > 0 => RECOMMEND, ATE < 0 => AVOID, or exact-zero ATE => INDIFFERENT; otherwise, TOST p < {ACTION_VALUE_ALPHA:.2f} => INDIFFERENT; else INCONCLUSIVE.",
        f"Equivalence margin delta: {margin if margin is not None else 'NOT_SET; INDIFFERENT is disabled'}.",
        "No BH/FDR adjustment enters the per-Pattern Rule.", "",
        "| Pattern | Name | Taking P: SUCCESS/total | Not taking P: SUCCESS/total | ATE SUCCESS | ATE FAILURE | p-value | p_TOST | Result |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in prevalence["pattern_average_action_value"]:
        if row["evaluation_status"] != "EVALUABLE":
            status = row["evaluation_status"]
            lines.append(f"| {row['pattern']} | {row['pattern_name']} | {status} | {status} | — | — | — | — | {status} |")
            continue
        p_tost = "—" if row["p_tost"] is None else f"{row['p_tost']:.3g}"
        lines.append(
            f"| {row['pattern']} | {row['pattern_name']} | {row['taking_success']}/{row['taking_total']} ({row['taking_success_rate']:.2%}) | "
            f"{row['not_taking_success']}/{row['not_taking_total']} ({row['not_taking_success_rate']:.2%}) | "
            f"{row['ate_success']:+.2%} | {row['ate_failure']:+.2%} | {row['p_value']:.3g} | {p_tost} | {row['result']} |"
        )
    non_evaluable = [definition for definition in pattern_set.patterns if definition.evaluation_status != "EVALUABLE"]
    if non_evaluable:
        lines += ["", "## Non-evaluable Patterns", ""]
        lines.extend(f"- **{definition.id} {definition.name}** — {definition.not_evaluable_reason}" for definition in non_evaluable)
    lines += [
        "", "## Pattern definitions", "",
        *[f"- **{definition.id} {definition.name}** — {definition.description}" for definition in definitions.values()],
        "", "## Interface identity gate", "",
        f"- Interface rows: {identity['total_interface_rows']}",
        f"- Row status counts: `{json_text(identity['row_status_counts'])}`",
        f"- Production-resolved investors: {identity['production_resolved_investors']}",
        f"- Investors eligible for behavior-mode discovery gate: {identity['mode_discovery_eligible_investors']}", "",
        "Only production-resolved investor IDs enter the primary investor profile. Normalized-exact candidates are reported for sensitivity and do not overwrite production identity; normalization collisions remain unresolved.", "",
    ]
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entity-path", required=True)
    parser.add_argument("--investor-path", required=True)
    parser.add_argument("--exposure-path", required=True)
    parser.add_argument("--interface-path", required=True)
    parser.add_argument("--chain-path", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--entity-label-column", required=True)
    parser.add_argument("--chain-label-column", required=True)
    parser.add_argument("--success-label", default="SUCCESS")
    parser.add_argument("--failure-label", default="FAILURE")
    parser.add_argument(
        "--pattern-spec-path", "--pattern-config", dest="pattern_spec_path",
        required=True,
        help="validated external JSON Pattern specification",
    )
    parser.add_argument("--equivalence-margin", type=float, default=DEFAULT_EQUIVALENCE_MARGIN)
    return parser.parse_args()


def _require_list(chain: dict, field: str) -> list:
    value = chain.get(field)
    if not isinstance(value, list):
        raise ValueError(f"chain {chain.get('chain_id')} field {field} must be a list")
    return value


def validate_input_contract(
    entity_rows: list[dict], exposure_rows: list[dict], interface_rows: list[dict],
    chain_rows: list[dict], event_lookup: dict[str, dict],
) -> dict[str, str]:
    labels = {}
    for row in entity_rows:
        company_id = row["company_id"]
        if company_id in labels:
            raise ValueError(f"duplicate company_id in entity input: {company_id}")
        labels[company_id] = row["label_v4"]
    for family, rows in (("exposure", exposure_rows), ("interface", interface_rows)):
        for row in rows:
            if row["company_id"] not in labels:
                raise ValueError(f"{family} event {row['event_id']} has unknown company_id: {row['company_id']}")
    for chain in chain_rows:
        chain_id, company_id = chain["chain_id"], chain["company_id"]
        if company_id not in labels:
            raise ValueError(f"chain {chain_id} has unknown company_id: {company_id}")
        if chain["company_label_v4"] != labels[company_id]:
            raise ValueError(f"chain {chain_id} label mismatch: {chain['company_label_v4']} != {labels[company_id]}")
        status = chain["chain_window_status"]
        if status not in VALID_WINDOW_STATUSES:
            raise ValueError(f"chain {chain_id} has invalid chain_window_status: {status}")
        if status == "dated" and _parse_exact_boundary(chain["chain_end_date"]) is None:
            raise ValueError(f"dated chain {chain_id} has invalid chain_end_date")
        if status == "unknown_end_date" and chain["chain_end_date"] != "unknown":
            raise ValueError(f"unknown-end chain {chain_id} must use chain_end_date=unknown")
        if chain["chain_start_date"] != "founding" and _parse_exact_boundary(chain["chain_start_date"]) is None:
            raise ValueError(f"chain {chain_id} has invalid chain_start_date")
        outcome_ids = _require_list(chain, "outcome_event_ids")
        if int(chain["n_participant_rows"]) != len(outcome_ids):
            raise ValueError(f"chain {chain_id} n_participant_rows/outcome_event_ids mismatch")
        for family in ("exposure", "interface"):
            ids = _require_list(chain, f"{family}_event_ids")
            types = _require_list(chain, f"{family}_types")
            if int(chain[f"n_{family}"]) != len(ids) or len(ids) != len(types):
                raise ValueError(f"chain {chain_id} {family} count/list alignment mismatch")
            if len(set(ids)) != len(ids):
                raise ValueError(f"chain {chain_id} has duplicate {family} event refs")
            for event_id, event_type in zip(ids, types):
                event = event_lookup.get(event_id)
                if event is None:
                    raise ValueError(f"chain references missing middle event: {event_id}")
                if event["family"] != family:
                    raise ValueError(f"chain {chain_id} references {event_id} as wrong family")
                if event["company_id"] != company_id:
                    raise ValueError(f"chain {chain_id} references cross-company event: {event_id}")
                if event["event_type"] != event_type:
                    raise ValueError(f"chain {chain_id} type mismatch for event: {event_id}")
        if status == "unknown_end_date" and (int(chain["n_exposure"]) or int(chain["n_interface"])):
            raise ValueError(f"unknown-end chain {chain_id} must not contain middle events")
    return labels


def git_provenance(anchor: Path) -> dict[str, Any]:
    try:
        root = subprocess.run(["git", "-C", str(anchor), "rev-parse", "--show-toplevel"], check=True, capture_output=True, text=True).stdout.strip()
        commit = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "-C", root, "status", "--porcelain"], check=True, capture_output=True, text=True).stdout)
        return {"repository_root": root, "commit": commit, "dirty": dirty}
    except (OSError, subprocess.CalledProcessError) as exc:
        return {"repository_root": None, "commit": None, "dirty": None, "error": str(exc)}


def output_metadata(path: Path) -> dict[str, Any]:
    if path.suffix == ".parquet":
        metadata = pq.read_metadata(path)
        rows, columns = metadata.num_rows, metadata.num_columns
    elif path.suffix == ".json":
        payload = json.loads(path.read_text())
        rows, columns = ((1, len(payload)) if isinstance(payload, dict) else (len(payload), 1))
    else:
        rows, columns = len(path.read_text().splitlines()), 1
    return {"bytes": path.stat().st_size, "rows": rows, "columns": columns, "sha256": sha256_file(path)}


def main() -> None:
    args = parse_args()
    try:
        pattern_set = load_pattern_config(Path(args.pattern_spec_path))
    except ValueError as exc:
        raise SystemExit(f"invalid Pattern spec: {exc}") from exc
    paths = {
        "entity": Path(args.entity_path).expanduser().resolve(),
        "investor": Path(args.investor_path).expanduser().resolve(),
        "exposure": Path(args.exposure_path).expanduser().resolve(),
        "interface": Path(args.interface_path).expanduser().resolve(),
        "chains": Path(args.chain_path).expanduser().resolve(),
    }
    output_dir = Path(args.output_dir).expanduser().resolve()
    if args.equivalence_margin is not None and not 0.0 < args.equivalence_margin < 1.0:
        raise SystemExit("--equivalence-margin must be between zero and one")
    for name, path in paths.items():
        if not path.is_file():
            raise SystemExit(f"missing {name} input: {path}")
        if path == output_dir or output_dir in path.parents:
            raise SystemExit(f"output/input alias is not allowed: {path}")
    if output_dir.exists():
        raise SystemExit(f"refusing to overwrite existing output directory: {output_dir}")

    tables = {name: pq.read_table(path) for name, path in paths.items()}
    require_columns(tables["entity"], {"company_id", "company_canonical_name", args.entity_label_column}, paths["entity"])
    require_columns(tables["investor"], {"investor_id", "name", "investor_type"}, paths["investor"])
    require_columns(tables["exposure"], {"event_id", "company_id", "event_date", "event_type", "event_subtype"}, paths["exposure"])
    require_columns(tables["interface"], {"event_id", "company_id", "investor_id", "raw_investor_name", "event_date", "event_type", "event_subtype"}, paths["interface"])
    require_columns(tables["chains"], {
        "chain_id", "company_id", args.chain_label_column, "chain_sequence",
        "chain_start_date", "chain_start_reason", "chain_end_date", "chain_window_status",
        "outcome_type", "outcome_round", "outcome_event_ids", "n_participant_rows",
        "investor_ids", "investor_raw_names", "n_exposure", "exposure_event_ids",
        "exposure_types", "n_interface", "interface_event_ids", "interface_types",
    }, paths["chains"])

    rows = {name: table.to_pylist() for name, table in tables.items()}
    def canonical_label(value: Any) -> str:
        if str(value) == args.success_label:
            return "SUCCESS"
        if str(value) == args.failure_label:
            return "FAILURE"
        return str(value)
    for row in rows["entity"]:
        row["label_v4"] = canonical_label(row[args.entity_label_column])
    for row in rows["chains"]:
        row["company_label_v4"] = canonical_label(row[args.chain_label_column])
    if len({row["chain_id"] for row in rows["chains"]}) != len(rows["chains"]):
        raise SystemExit("duplicate chain_id in chain input")
    event_lookup = build_event_lookup(rows["exposure"], rows["interface"])
    try:
        company_labels = validate_input_contract(rows["entity"], rows["exposure"], rows["interface"], rows["chains"], event_lookup)
        chain_features = chain_feature_rows(rows["chains"], event_lookup, pattern_set)
        company_profiles = company_profile_rows(rows["entity"], chain_features, pattern_set)
        prevalence = pattern_prevalence(company_profiles, pattern_set, args.equivalence_margin)
        identity, investor_profiles, interface_identity_rows = interface_identity_and_profiles(
            rows["interface"], rows["investor"], company_labels,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=output_dir.name + ".tmp-", dir=output_dir.parent))
    try:
        pattern_ids = [definition.id for definition in pattern_set.patterns]
        chain_types = {f"signature_seed_{pattern_id}": pa.bool_() for pattern_id in pattern_ids}
        company_types = {}
        for pattern_id in pattern_ids:
            company_types.update({
                f"signature_n_{pattern_id}_chains": pa.int64(), f"signature_any_{pattern_id}": pa.bool_(),
                f"primary_n_{pattern_id}_chains": pa.int64(), f"primary_any_{pattern_id}": pa.bool_(),
            })
        write_parquet(chain_features, temp_dir / "chain_ordered_features.parquet", chain_types)
        write_parquet(company_profiles, temp_dir / "company_chain_profiles.parquet", company_types)
        write_parquet(investor_profiles, temp_dir / "investor_interface_profiles.parquet")
        write_parquet(interface_identity_rows, temp_dir / "interface_identity_rows.parquet")
        (temp_dir / "pattern_prevalence.json").write_text(json.dumps(prevalence, indent=2, sort_keys=True, allow_nan=False) + "\n")
        (temp_dir / "interface_identity_coverage.json").write_text(json.dumps(identity, indent=2, sort_keys=True, allow_nan=False) + "\n")
        (temp_dir / "summary.md").write_text(render_summary(prevalence, identity, pattern_set))
        resolved_spec = pattern_set_payload(pattern_set)
        (temp_dir / "patterns_resolved.json").write_text(json.dumps(resolved_spec, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
        outputs = {path.name: output_metadata(path) for path in sorted(temp_dir.iterdir())}
        script_path = Path(__file__).resolve()
        common_path = script_path.parent / "_common.py"
        statuses = {definition.id: definition.evaluation_status for definition in pattern_set.patterns}
        manifest = {
            "analysis_contract": "event-chain-pattern-analysis-v1", "argv": list(sys.argv),
            "runtime": {"python": platform.python_version(), "python_implementation": platform.python_implementation(), "pyarrow": pa.__version__},
            "git": git_provenance(script_path.parent),
            "code_inputs": {
                "script": {"path": str(script_path), "sha256": sha256_file(script_path)},
                "_common": {"path": str(common_path), "sha256": sha256_file(common_path)},
            },
            "configuration_inputs": {
                "pattern_config": {
                    "path": str(pattern_set.source_path), "sha256": pattern_set.sha256,
                    "content": resolved_spec, "config_schema_version": pattern_set.config_schema_version,
                    "pattern_set_id": pattern_set.pattern_set_id,
                    "resolved_copy": "patterns_resolved.json",
                }
            },
            "inputs": {
                name: {"path": str(path), "rows": tables[name].num_rows, "columns": tables[name].num_columns, "sha256": sha256_file(path)}
                for name, path in paths.items()
            },
            "parameters": {
                "seed_primary_order": list(pattern_set.primary_order),
                "pattern_evaluation_statuses": statuses,
                "pattern_definitions_source": "configuration_inputs.pattern_config",
                "average_action_value": {
                    "unit": "company with at least one dated/evaluable chain",
                    "pattern_presence": "any multi-label signature chain",
                    "estimand": "SUCCESS risk(taking P) - SUCCESS risk(not taking P)",
                    "difference_test": "two-sided pooled two-proportion z-test; equivalent Pearson chi-square df=1",
                    "alpha": ACTION_VALUE_ALPHA, "equivalence_margin": args.equivalence_margin,
                    "equivalence_test": "TOST normal approximation with pre-set equivalence margin",
                    "result_rule": "if difference p < alpha: sign(ATE) => RECOMMEND/AVOID, exact zero => INDIFFERENT; elif TOST p < alpha => INDIFFERENT; else INCONCLUSIVE",
                    "multiple_testing": "none; each Pattern Rule is independent",
                },
                "signature_flags_role": "primary Pattern presence for average action value",
                "strict_primary_role": "diagnostic feature only",
                "outcome_investor_count": "unique non-empty raw names; resolved-ID fallback only when raw names empty",
                "investor_primary_view": "production_resolved_only",
                "investor_mode_gate": ">=5 core events and >=3 companies",
                "production_chain_window_semantics": {
                    "later_dated_chains": "[start,end)",
                    "first_dated_chain": "founding sentinel means all dated events before end",
                    "unknown_end_date": "no middle events",
                },
                "partial_date_boundary_ambiguity": "feature flags referenced non-day event intervals that can cross a known boundary",
                "next_sensitivity_not_run": "outcome-day right-closed window sensitivity is NEXT work, not run in this analysis",
            },
            "outputs": outputs,
        }
        (temp_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n")
        os.replace(temp_dir, output_dir)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise

    print(f"chains: {len(chain_features)}")
    print(f"scope companies: {len(company_profiles)}")
    print(f"production-resolved interface investors: {len(investor_profiles)}")
    print(f"wrote: {output_dir}")


if __name__ == "__main__":
    main()
