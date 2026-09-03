#!/usr/bin/env python3
"""Freeze the complete explicit typed-event × structural grammar.

Generation is label blind: it opens only three explicitly supplied v0.3 tables,
projects the SAFE_* columns below, validates the three checked-in contracts, and
publishes atomically to a new directory.  Labels and prior patterns/results are
not accepted by this command.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import platform
import resource
import shutil
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import pyarrow as pa
import pyarrow.parquet as pq

import analyze_patterns as engine

RESEARCH_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CHAIN_SCHEMA = RESEARCH_ROOT / "schemas/chains_v0.3.schema.json"
DEFAULT_EXPOSURE_SCHEMA = RESEARCH_ROOT / "schemas/exposure_events_v0.3.schema.json"
DEFAULT_INTERFACE_SCHEMA = RESEARCH_ROOT / "schemas/interface_events_v0.4.schema.json"

GRAMMAR_VERSION = "pattern_candidates_extended_explicit_typed_pairwise"
COMPLETENESS_STATUS = "COMPLETE_EXPLICIT_LEGAL_PAIRWISE_GRAMMAR"
ACTION_ELIGIBLE = "ACTION_ELIGIBLE"
DESCRIPTIVE_ASSOCIATION = "DESCRIPTIVE_ASSOCIATION"
SAFE_CHAIN_COLUMNS = (
    "chain_id", "company_id", "chain_sequence", "chain_start_date", "chain_start_reason",
    "chain_end_date", "chain_window_status", "outcome_type", "outcome_round",
    "outcome_event_ids", "n_participant_rows", "investor_ids", "investor_raw_names",
    "n_exposure", "exposure_event_ids", "n_interface", "interface_event_ids",
)
SAFE_EXPOSURE_COLUMNS = ("event_id", "company_id", "event_date", "event_type")
SAFE_INTERFACE_COLUMNS = SAFE_EXPOSURE_COLUMNS + ("investor_id", "raw_investor_name")
FORBIDDEN_EVENT_COLUMNS = (
    "event_subtype", "title", "summary", "source_url", "metadata_json", "confidence", "source_type",
)
FORBIDDEN_INPUT_KINDS = (
    "entity/cohort tables", "predefined reference patterns, configuration, or coverage",
    "prior candidate, freeze, evaluation, or report artifacts",
    "ATE, p-value, recommendation, or outcome-label results",
)
THRESHOLD_REGISTRY = {
    "count": [0, 1, 2, 3, 5, 10, 20],
    "day": [0, 7, 14, 30, 60, 90, 180, 365, 730, 1095, 1825],
    "sequence_gap": [1, 7, 14, 30, 60, 90, 180, 365],
    "ratio": [0, 0.25, 0.5, 0.75, 1],
    "density": [0, 0.5, 1, 2, 4, 8, 12, 24],
    "position": [1, 2, 3, 5, 10],
}
DIMENSION_PRIORITY = {
    "typed_sequence": 0, "typed_timing": 1, "typed_position": 2, "typed_participant": 3,
    "typed_count": 4, "typed_presence": 5, "cadence": 6, "participant": 7,
    "count": 8, "share": 9, "history": 10, "round": 11, "duration": 12, "density": 13,
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


THRESHOLD_REGISTRY_SHA256 = digest_json(THRESHOLD_REGISTRY)


def canonical_rule(rule: dict[str, Any]) -> dict[str, Any]:
    op = rule["op"]
    result = json.loads(canonical_json(rule))
    if op == "compare" and result.get("cmp") in {"in", "not_in"}:
        result["right"]["literal"] = sorted(set(result["right"]["literal"]), key=canonical_json)
    if op == "sequence":
        result["same_values"] = sorted(set(result["same_values"]))
    if op == "all":
        children: list[dict[str, Any]] = []
        for raw in result["rules"]:
            child = canonical_rule(raw)
            children.extend(child["rules"] if child["op"] == "all" else [child])
        unique = {canonical_json(child): child for child in children}
        result["rules"] = [unique[key] for key in sorted(unique)]
    return result


def selector(family: str, event_type: str | None = None) -> dict[str, Any]:
    where = [] if event_type is None else [
        {"field": "event.event_type", "cmp": "eq", "value": event_type},
    ]
    return {"scope": "middle_events", "family": family, "where": where}


def compare(field: str, cmp: str, literal: Any = None) -> dict[str, Any]:
    result: dict[str, Any] = {"op": "compare", "left": {"field": field}, "cmp": cmp}
    if cmp not in {"is_null", "not_null"}:
        result["right"] = {"literal": literal}
    return result


def compare_value(left: dict[str, Any], cmp: str, literal: Any) -> dict[str, Any]:
    return {"op": "compare", "left": left, "cmp": cmp, "right": {"literal": literal}}


def rule_mask(rule: dict[str, Any], contexts: list[engine.ChainRuleContext]) -> int:
    bits = 0
    for index, context in enumerate(contexts):
        if engine.evaluate_rule(rule, context):
            bits |= 1 << index
    return bits


def company_mask(chain_bits: int, company_indexes: list[int]) -> int:
    bits = 0
    for chain_index, company_index in enumerate(company_indexes):
        if chain_bits & (1 << chain_index):
            bits |= 1 << company_index
    return bits


def mask_sha256(bits: int, width: int) -> str:
    return hashlib.sha256(bits.to_bytes((width + 7) // 8, "little")).hexdigest()


def _load_contract(path: Path, expected_family: str, safe_columns: tuple[str, ...]) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid schema contract {path}: {exc}") from exc
    if payload.get("format") != "pyarrow-schema-json-v1" or payload.get("event_family") != expected_family:
        raise ValueError(f"schema contract identity mismatch: {path}")
    fields = payload.get("fields")
    if not isinstance(fields, list) or any(not isinstance(item, dict) for item in fields):
        raise ValueError(f"schema contract fields malformed: {path}")
    by_name = {item.get("name"): item for item in fields}
    missing = sorted(set(safe_columns) - set(by_name))
    if missing:
        raise ValueError(f"schema contract missing safe fields for {expected_family}: {missing}")
    return {
        "format": payload["format"], "event_family": expected_family,
        "schema_version": payload.get("schema_version"),
        "safe_fields": {name: {"type": by_name[name].get("type"), "nullable": by_name[name].get("nullable")}
                        for name in safe_columns},
    }


def _type_compatible(actual: pa.DataType, declared: str) -> bool:
    normalized = str(actual).replace("string", "large_string")
    return normalized == declared or str(actual) == declared


def _validate_projection(table: pa.Table, contract: dict[str, Any], path: Path) -> None:
    if tuple(table.column_names) != tuple(contract["safe_fields"]):
        raise ValueError(f"unsafe or reordered projection for {path}: {table.column_names}")
    for field in table.schema:
        declared = contract["safe_fields"][field.name]
        if not _type_compatible(field.type, declared["type"]):
            raise ValueError(f"schema type drift for {field.name}: {field.type} != {declared['type']}")
        if field.nullable != declared["nullable"]:
            raise ValueError(f"schema nullability drift for {field.name}: {field.nullable} != {declared['nullable']}")


def read_inputs(paths: dict[str, Path], schema_paths: dict[str, Path]) -> tuple[
    dict[str, pa.Table], list[engine.ChainRuleContext], list[int], list[str], dict[str, Any]
]:
    columns = {"chains": SAFE_CHAIN_COLUMNS, "exposure": SAFE_EXPOSURE_COLUMNS, "interface": SAFE_INTERFACE_COLUMNS}
    contracts = {name: _load_contract(schema_paths[name], name, columns[name]) for name in columns}
    tables = {name: pq.read_table(paths[name], columns=list(columns[name])) for name in columns}
    for name in columns:
        _validate_projection(tables[name], contracts[name], paths[name])
    rows = {name: table.to_pylist() for name, table in tables.items()}
    lookup = engine.build_event_lookup(rows["exposure"], rows["interface"])
    chain_ids = [row["chain_id"] for row in rows["chains"]]
    if len(chain_ids) != len(set(chain_ids)):
        raise ValueError("duplicate chain_id")
    referenced: set[str] = set()
    for chain in rows["chains"]:
        if chain["chain_window_status"] not in engine.VALID_WINDOW_STATUSES:
            raise ValueError(f"invalid chain_window_status: {chain['chain_id']}")
        for family in ("exposure", "interface"):
            ids = chain[f"{family}_event_ids"]
            if int(chain[f"n_{family}"]) != len(ids) or len(ids) != len(set(ids)):
                raise ValueError(f"{family} count/reference mismatch: {chain['chain_id']}")
            for event_id in ids:
                event = lookup.get(event_id)
                if event is None or event["family"] != family or event["company_id"] != chain["company_id"]:
                    raise ValueError(f"invalid {family} reference {event_id}: {chain['chain_id']}")
                referenced.add(event_id)
        if chain["chain_window_status"] == "unknown_end_date" and (chain["n_exposure"] or chain["n_interface"]):
            raise ValueError(f"unknown-end chain contains middle events: {chain['chain_id']}")
    # Unreferenced physical rows never enter the observed type vocabulary or contexts.
    contexts = engine.build_chain_rule_contexts(rows["chains"], lookup)
    companies = sorted({context.chain["company_id"] for context in contexts})
    company_to_index = {value: index for index, value in enumerate(companies)}
    indexes = [company_to_index[context.chain["company_id"]] for context in contexts]
    return tables, contexts, indexes, companies, contracts


def _typed_value(kind: str, event_type: tuple[str, str], **options: Any) -> dict[str, Any]:
    family, literal = event_type
    payload = {"selector": selector(family, literal), **options}
    return {kind: payload}


def _anchor_facts(kind: str, event_type: tuple[str, str] | None = None) -> dict[str, Any]:
    return {
        "anchor_kind": kind, "typed_key": list(event_type) if event_type else None,
        "count_min": {}, "count_max": {}, "dated_min": 0,
        "requires_known_start": False, "requires_known_end": False,
        "participant_min": 0,
    }


def _set_type_min(facts: dict[str, Any], event_type: tuple[str, str], value: int) -> None:
    family, literal = event_type
    facts["count_min"] = {"total": value, family: value, f"type:{family}:{literal}": value}


def _event_anchor_rules(
    contexts: list[engine.ChainRuleContext], observed_types: list[tuple[str, str]],
    max_sequence_length: int, max_sequence_tuples_l2: int, max_sequence_tuples_l3: int,
    max_sequence_transitions: int,
) -> tuple[list[dict[str, Any]], dict[str, int], str]:
    raw: list[dict[str, Any]] = []
    sequence_ops = 0

    def add(rule: dict[str, Any], kind: str, event_type: tuple[str, str] | None,
            facts: dict[str, Any], taints: Iterable[str] = ()) -> None:
        raw.append({"rule": canonical_rule(rule), "kind": kind, "event_type": list(event_type) if event_type else None,
                    "dimension": "typed_" + ("presence" if kind in {"present", "absent"} else kind),
                    "facts": facts, "taints": sorted(set(taints))})

    for event_type in observed_types:
        family, literal = event_type
        selected = selector(family, literal)
        counts = [len(engine.select_events(selected, context)) for context in contexts]
        present_facts = _anchor_facts("present", event_type)
        _set_type_min(present_facts, event_type, 1)
        add({"op": "present", "selector": selected}, "present", event_type, present_facts,
            ["exit"] if family == "interface" and literal == "exit" else [])
        absent_facts = _anchor_facts("absent", event_type)
        absent_facts["count_max"] = {f"type:{family}:{literal}": 0}
        add({"op": "absent", "selector": selected}, "absent", event_type, absent_facts,
            ["exit"] if family == "interface" and literal == "exit" else [])
        maximum = max(counts, default=0)
        for cmp, thresholds in (("gte", (2, 3, 5, 10, 20)), ("lte", (1, 2, 3, 5, 10))):
            for threshold in thresholds:
                if threshold > maximum and cmp == "gte":
                    continue
                facts = _anchor_facts("count", event_type)
                if cmp == "gte":
                    _set_type_min(facts, event_type, threshold)
                else:
                    facts["count_max"] = {f"type:{family}:{literal}": threshold}
                add(compare_value({"event_count": selected}, cmp, threshold), "count", event_type, facts,
                    ["exit"] if family == "interface" and literal == "exit" else [])
        for relative, edge, taint in (("start", "first", ()), ("end", "last", ("end",))):
            for bound, cmp in (("min", "gte"), ("max", "lte")):
                expr = _typed_value("event_timing", event_type, relative_to=relative, edge=edge, bound=bound)
                values = [engine.evaluate_value(expr, context) for context in contexts]
                observed = [value for value in values if isinstance(value, int)]
                if not observed:
                    continue
                for threshold in THRESHOLD_REGISTRY["day"]:
                    if not min(observed) <= threshold <= max(observed):
                        continue
                    facts = _anchor_facts("timing", event_type)
                    _set_type_min(facts, event_type, 1)
                    facts["dated_min"] = 1
                    facts["requires_known_start"] = relative == "start"
                    facts["requires_known_end"] = relative == "end"
                    add(compare_value(expr, cmp, threshold), "timing", event_type, facts,
                        [*taint, *(["exit"] if family == "interface" and literal == "exit" else [])])
        for scope in ("family", "all_middle"):
            for edge in ("first_batch", "last_batch"):
                expr = _typed_value("event_position", event_type, scope=scope, edge=edge)
                facts = _anchor_facts("position", event_type)
                _set_type_min(facts, event_type, 1)
                facts["dated_min"] = 1
                add(compare_value(expr, "eq", True), "position", event_type, facts,
                    ["exit"] if family == "interface" and literal == "exit" else [])
        if family == "interface":
            expr = _typed_value("event_distinct_count", event_type, field="event.participant_key")
            values = [engine.evaluate_value(expr, context) for context in contexts]
            minimum, maximum_participants = min(values, default=0), max(values, default=0)
            for cmp in ("gte", "lte"):
                for threshold in THRESHOLD_REGISTRY["count"]:
                    if not minimum <= threshold <= maximum_participants:
                        continue
                    facts = _anchor_facts("participant", event_type)
                    if cmp == "gte":
                        _set_type_min(facts, event_type, threshold)
                        facts["participant_min"] = threshold
                    add(compare_value(expr, cmp, threshold), "participant", event_type, facts,
                        ["exit"] if literal == "exit" else [])

    tuple_sets: dict[int, set[tuple[tuple[str, str], ...]]] = {2: set(), 3: set()}
    for context in contexts:
        dated = [event for event in context.events if event.get("date_interval") is not None and event.get("event_type")]
        for length in range(2, max_sequence_length + 1):
            for chosen in itertools.combinations(dated, length):
                sequence_ops += 1
                if sequence_ops > max_sequence_transitions:
                    raise ValueError("INCOMPLETE_RESOURCE_BOUND: sequence witness/transition budget exceeded")
                if all(left["date_interval"].maximum < right["date_interval"].minimum
                       for left, right in zip(chosen, chosen[1:])):
                    tuple_sets[length].add(tuple((event["family"], str(event["event_type"]).strip()) for event in chosen))
    if len(tuple_sets[2]) > max_sequence_tuples_l2 or len(tuple_sets[3]) > max_sequence_tuples_l3:
        raise ValueError("INCOMPLETE_RESOURCE_BOUND: witnessed typed sequence tuple budget exceeded")
    gap_variants = [(None, None)] + [(value, None) for value in THRESHOLD_REGISTRY["sequence_gap"]] + [
        (None, value) for value in THRESHOLD_REGISTRY["sequence_gap"]
    ]
    for length in (2, 3):
        for typed_tuple in sorted(tuple_sets[length]):
            facts = _anchor_facts("sequence")
            multiplicity = Counter(typed_tuple)
            family_counts = Counter(family for family, _ in typed_tuple)
            facts["count_min"] = {"total": length, **{family: count for family, count in family_counts.items()},
                                  **{f"type:{family}:{literal}": count
                                     for (family, literal), count in multiplicity.items()}}
            facts["dated_min"] = length
            taints = ["exit"] if ("interface", "exit") in typed_tuple else []
            steps = [selector(family, literal) for family, literal in typed_tuple]
            for minimum, maximum in gap_variants:
                rule = {"op": "sequence", "steps": steps, "ordering": "strict_before",
                        "min_gap_days": minimum, "max_gap_days": maximum, "same_values": []}
                add(rule, "sequence", None, facts, taints)
                if all(family == "interface" for family, _ in typed_tuple):
                    same_facts = json.loads(canonical_json(facts))
                    same_facts["participant_min"] = 1
                    add({**rule, "same_values": ["event.participant_key"]}, "sequence", None, same_facts, taints)
    vocab = [[family, literal] for family, literal in observed_types]
    sequence_vocab = {str(length): [[list(item) for item in value] for value in sorted(tuple_sets[length])]
                      for length in (2, 3)}
    return raw, {"sequence_witness_operations": sequence_ops,
                 "witnessed_tuples_l2": len(tuple_sets[2]), "witnessed_tuples_l3": len(tuple_sets[3])}, digest_json(sequence_vocab)


def _field_value(context: engine.ChainRuleContext, field: str) -> Any:
    namespace, name = field.split(".", 1)
    return context.chain[name] if namespace == "chain" else context.derived[name]


def _structural_rules(contexts: list[engine.ChainRuleContext]) -> list[dict[str, Any]]:
    raw: list[dict[str, Any]] = []

    def add(rule: dict[str, Any], dimension: str, field: str, taints: Iterable[str] = ()) -> None:
        facts: dict[str, Any] = {"field": field, "cmp": rule.get("cmp"),
                                 "literal": rule.get("right", {}).get("literal")}
        raw.append({"rule": canonical_rule(rule), "dimension": dimension, "facts": facts,
                    "taints": sorted(set(taints))})

    round_values = sorted({_field_value(context, "derived.outcome_round_bucket") for context in contexts}, key=canonical_json)
    for value in round_values:
        # Current outcome stage is a decision-time condition, not an outcome
        # interpretation.  Keep same-round history separately tainted below.
        add(compare("derived.outcome_round_bucket", "in", [value]), "round", "derived.outcome_round_bucket")

    numeric: dict[str, tuple[str, str, tuple[str, ...]]] = {
        "derived.chain_duration_days": ("day", "duration", ("end",)),
        "chain.n_exposure": ("count", "count", ()), "chain.n_interface": ("count", "count", ()),
        "derived.n_middle": ("count", "count", ()),
        "derived.n_known_date_middle_events": ("count", "count", ()),
        "derived.n_ordered_batches": ("count", "cadence", ()),
        "derived.n_comparable_gaps": ("count", "cadence", ()),
        "derived.exposure_share": ("ratio", "share", ()), "derived.interface_share": ("ratio", "share", ()),
        "derived.events_per_chain_year": ("density", "density", ("end",)),
        "derived.batches_per_chain_year": ("density", "density", ("end",)),
        "derived.interface_investor_count": ("count", "participant", ()),
        "derived.middle_span_min": ("day", "cadence", ()), "derived.middle_span_max": ("day", "cadence", ()),
        "derived.min_interbatch_gap_min": ("day", "cadence", ()),
        "derived.min_interbatch_gap_max": ("day", "cadence", ()),
        "derived.max_interbatch_gap_min": ("day", "cadence", ()),
        "derived.max_interbatch_gap_max": ("day", "cadence", ()),
        "derived.n_prior_chains": ("position", "history", ()),
        "derived.company_chain_count": ("position", "history", ("future",)),
        "derived.prior_same_round_count": ("count", "history", ("same_round_history",)),
    }
    for field, (grid_name, dimension, taints) in numeric.items():
        values = [_field_value(context, field) for context in contexts]
        observed = [value for value in values if isinstance(value, (int, float)) and not isinstance(value, bool)]
        if observed:
            if dimension in {"share", "density"}:
                comparators = ("gte",)
            elif dimension == "cadence":
                comparators = ("lte",) if field.endswith("_max") else ("gte",)
            elif dimension == "history" and field != "derived.days_since_previous_same_round":
                comparators = ("gte",)
            else:
                comparators = ("gte", "lte")
            for threshold in THRESHOLD_REGISTRY[grid_name]:
                if min(observed) <= threshold <= max(observed):
                    for cmp in comparators:
                        add(compare(field, cmp, threshold), dimension, field, taints)
        if len(observed) != len(values) and field == "derived.chain_duration_days":
            add(compare(field, "is_null"), dimension, field, taints)
            add(compare(field, "not_null"), dimension, field, taints)
    return raw


def _compatibility(anchor: dict[str, Any], structural: dict[str, Any]) -> str | None:
    facts = anchor["facts"]
    sf = structural["facts"]
    field, cmp, value = sf["field"], sf["cmp"], sf["literal"]
    scope_for_field = {
        "chain.n_exposure": "exposure", "chain.n_interface": "interface", "derived.n_middle": "total",
        "derived.n_known_date_middle_events": "dated", "derived.interface_investor_count": "participant",
    }.get(field)
    if scope_for_field and cmp in {"gte", "lte"}:
        if scope_for_field == "dated":
            minimum = facts["dated_min"]
        elif scope_for_field == "participant":
            minimum = facts["participant_min"]
        else:
            minimum = facts["count_min"].get(scope_for_field, 0)
        if cmp == "lte" and minimum > value:
            return "UNSAT_COUNT_BOUND"
        if cmp == "gte" and minimum >= value:
            return "ANCHOR_IMPLIES_STRUCTURAL"
        if cmp == "lte" and scope_for_field in {"total", "exposure", "interface"}:
            typed_key = facts.get("typed_key")
            typed_max = facts["count_max"].get(
                f"type:{typed_key[0]}:{typed_key[1]}" if typed_key else "", None
            )
            if typed_max is not None and value <= typed_max:
                return "STRUCTURAL_IMPLIES_ANCHOR"
            if facts["anchor_kind"] == "absent" and value == 0 and typed_key and (
                scope_for_field == "total" or scope_for_field == typed_key[0]
            ):
                return "STRUCTURAL_IMPLIES_ANCHOR"
    if field == "derived.chain_duration_days" and cmp == "is_null" and facts["requires_known_start"]:
        return "UNSAT_NULLNESS"
    return None


def _catalog(
    raw: list[dict[str, Any]], prefix: str, contexts: list[engine.ChainRuleContext], company_indexes: list[int],
    chain_width: int, company_width: int, min_chain_support: int, min_company_support: int,
) -> tuple[list[dict[str, Any]], int]:
    kept: list[dict[str, Any]] = []
    pruned = 0
    seen: dict[str, dict[str, Any]] = {}
    for item in raw:
        canonical = canonical_rule(item["rule"])
        identity = {"grammar": GRAMMAR_VERSION, "thresholds": THRESHOLD_REGISTRY_SHA256,
                    "role": prefix, "rule": canonical}
        atom_id = prefix + digest_json(identity)[:24]
        bits = rule_mask(canonical, contexts)
        company_bits = company_mask(bits, company_indexes)
        if bits.bit_count() < min_chain_support or company_bits.bit_count() < min_company_support:
            pruned += 1
            continue
        record = {**item, "id": atom_id, "rule": canonical, "chain_bits": bits, "company_bits": company_bits,
                  "chain_support": bits.bit_count(), "company_support": company_bits.bit_count(),
                  "chain_mask_sha256": mask_sha256(bits, chain_width),
                  "company_mask_sha256": mask_sha256(company_bits, company_width)}
        prior = seen.get(atom_id)
        if prior is not None and prior["rule"] != canonical:
            raise ValueError(f"atom ID collision: {atom_id}")
        seen[atom_id] = record
    kept = [seen[key] for key in sorted(seen)]
    return kept, pruned


def _public_atom(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if key not in {"chain_bits", "company_bits"}}


def _pair_id(rule: dict[str, Any], vocab_hash: str) -> str:
    identity = GRAMMAR_VERSION + THRESHOLD_REGISTRY_SHA256 + vocab_hash + canonical_json(rule)
    return "B3P_" + hashlib.sha256(identity.encode()).hexdigest()[:24]


def _representative_key(pair: dict[str, Any]) -> tuple[Any, ...]:
    return (pair["track"], DIMENSION_PRIORITY.get(pair["event_dimension"], 99),
            DIMENSION_PRIORITY.get(pair["structural_dimension"], 99), canonical_json(pair["rule"]))


def _arg(args: argparse.Namespace, name: str, legacy: str | None, default: Any) -> Any:
    if hasattr(args, name):
        return getattr(args, name)
    if legacy and hasattr(args, legacy):
        return getattr(args, legacy)
    return default


def freeze(args: argparse.Namespace) -> dict[str, Any]:
    started = time.monotonic()
    paths = {"chains": Path(args.chain_path).expanduser().resolve(),
             "exposure": Path(args.exposure_path).expanduser().resolve(),
             "interface": Path(args.interface_path).expanduser().resolve()}
    schemas = {"chains": Path(args.chain_schema).expanduser().resolve(),
               "exposure": Path(args.exposure_schema).expanduser().resolve(),
               "interface": Path(args.interface_schema).expanduser().resolve()}
    output = Path(args.output_dir).expanduser().resolve()
    for path in [*paths.values(), *schemas.values()]:
        if not path.is_file():
            raise ValueError(f"missing input: {path}")
        if path == output or output in path.parents:
            raise ValueError(f"output/input alias is not allowed: {path}")
    if output.exists():
        raise ValueError(f"refusing to overwrite existing output directory: {output}")
    if not 2 <= args.max_sequence_length <= 3:
        raise ValueError("max sequence length must be 2 or 3")

    caps = {
        "max_observed_types": _arg(args, "max_observed_types", None, 32),
        "max_event_anchors": _arg(args, "max_event_anchors", "max_atoms", 250000),
        "max_supported_anchors": _arg(args, "max_supported_anchors", "max_supported_atoms", 20000),
        "max_structural_atoms": _arg(args, "max_structural_atoms", None, 2000),
        "max_supported_structural_atoms": _arg(args, "max_supported_structural_atoms", None, 1000),
        "max_sequence_tuples_l2": _arg(args, "max_sequence_tuples_l2", None, 1024),
        "max_sequence_tuples_l3": _arg(args, "max_sequence_tuples_l3", None, 8192),
        "max_legal_pairs": _arg(args, "max_legal_pairs", None, 5000000),
        "max_frozen_pairs": _arg(args, "max_frozen_pairs", None, 250000),
        "max_representatives": _arg(args, "max_representatives", "max_candidates", 60000),
        "max_sequence_transitions": _arg(args, "max_sequence_transitions", None, 50000000),
        "max_artifact_mib": _arg(args, "max_artifact_mib", None, 512.0),
        "max_wall_seconds": _arg(args, "max_wall_seconds", None, 1800.0),
        "max_rss_mib": _arg(args, "max_rss_mib", None, 8192.0),
    }
    tables, contexts, company_indexes, companies, contracts = read_inputs(paths, schemas)
    chain_width, company_width = len(contexts), len(companies)
    if not chain_width or not company_width:
        raise ValueError("cannot freeze an empty Chain domain")
    observed_types = sorted({
        (event["family"], str(event["event_type"]).strip())
        for context in contexts for event in context.events
        if event.get("event_type") is not None and str(event["event_type"]).strip()
    })
    if len(observed_types) > caps["max_observed_types"]:
        raise ValueError("INCOMPLETE_RESOURCE_BOUND: observed TYPE vocabulary exceeds cap")
    vocab_hash = digest_json([[family, literal] for family, literal in observed_types])
    raw_anchors, sequence_stats, sequence_vocab_hash = _event_anchor_rules(
        contexts, observed_types, args.max_sequence_length, caps["max_sequence_tuples_l2"],
        caps["max_sequence_tuples_l3"], caps["max_sequence_transitions"],
    )
    if len(raw_anchors) > caps["max_event_anchors"]:
        raise ValueError("INCOMPLETE_RESOURCE_BOUND: raw event anchors exceed cap")
    raw_structural = _structural_rules(contexts)
    if len(raw_structural) > caps["max_structural_atoms"]:
        raise ValueError("INCOMPLETE_RESOURCE_BOUND: raw structural coordinates exceed cap")
    anchors, anchor_support_pruned = _catalog(
        raw_anchors, "B3A_", contexts, company_indexes, chain_width, company_width,
        args.min_chain_support, args.min_company_support,
    )
    structural, structural_support_pruned = _catalog(
        raw_structural, "B3S_", contexts, company_indexes, chain_width, company_width,
        args.min_chain_support, args.min_company_support,
    )
    if len(anchors) > caps["max_supported_anchors"] or len(structural) > caps["max_supported_structural_atoms"]:
        raise ValueError("INCOMPLETE_RESOURCE_BOUND: support-retained component budget exceeded")

    full_chain = (1 << chain_width) - 1
    full_company = (1 << company_width) - 1
    cartesian = len(anchors) * len(structural)
    rejected = Counter()
    legal = support_pruned = constant_pruned = 0
    pairs: list[dict[str, Any]] = []
    ids: dict[str, str] = {}
    for anchor in anchors:
        for struct in structural:
            reason = _compatibility(anchor, struct)
            if reason:
                rejected[reason] += 1
                continue
            legal += 1
            if legal > caps["max_legal_pairs"]:
                raise ValueError("INCOMPLETE_RESOURCE_BOUND: legal pair coordinates exceed cap")
            chain_bits = anchor["chain_bits"] & struct["chain_bits"]
            company_bits = company_mask(chain_bits, company_indexes)
            if chain_bits in {0, full_chain} or company_bits in {0, full_company}:
                constant_pruned += 1
                continue
            if chain_bits.bit_count() < args.min_chain_support or company_bits.bit_count() < args.min_company_support:
                support_pruned += 1
                continue
            rule = canonical_rule({"op": "all", "rules": [anchor["rule"], struct["rule"]]})
            pair_id = _pair_id(rule, vocab_hash)
            prior_rule = ids.get(pair_id)
            if prior_rule is not None and prior_rule != canonical_json(rule):
                raise ValueError(f"candidate ID collision: {pair_id}")
            ids[pair_id] = canonical_json(rule)
            taints = sorted(set(anchor["taints"]) | set(struct["taints"]))
            track = DESCRIPTIVE_ASSOCIATION if taints else ACTION_ELIGIBLE
            pairs.append({
                "id": pair_id, "anchor_id": anchor["id"], "structural_id": struct["id"], "rule": rule,
                "track": track, "taints": taints, "event_dimension": anchor["dimension"],
                "structural_dimension": struct["dimension"], "chain_support": chain_bits.bit_count(),
                "company_support": company_bits.bit_count(), "chain_mask_sha256": mask_sha256(chain_bits, chain_width),
                "company_mask_sha256": mask_sha256(company_bits, company_width), "company_bits": company_bits,
            })
            if len(pairs) > caps["max_frozen_pairs"]:
                raise ValueError("INCOMPLETE_RESOURCE_BOUND: supported nonconstant frozen pairs exceed cap")
        if time.monotonic() - started > caps["max_wall_seconds"]:
            raise ValueError("INCOMPLETE_RESOURCE_BOUND: wall-time budget exceeded")
    if cartesian != legal + sum(rejected.values()):
        raise AssertionError("pairwise compatibility accounting is incomplete")
    pairs.sort(key=lambda item: item["id"])

    representative_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for pair in pairs:
        key = (pair["track"], pair["company_mask_sha256"])
        if key not in representative_by_key or _representative_key(pair) < _representative_key(representative_by_key[key]):
            representative_by_key[key] = pair
    representatives = sorted(representative_by_key.values(), key=lambda item: item["id"])
    if len(representatives) > caps["max_representatives"]:
        raise ValueError("INCOMPLETE_RESOURCE_BOUND: track-local company-mask representatives exceed cap")
    rep_id = {(item["track"], item["company_mask_sha256"]): item["id"] for item in representatives}
    for pair in pairs:
        pair["representative_id"] = rep_id[(pair["track"], pair["company_mask_sha256"])]
        pair["evaluation_basis"] = "COMPANY_MASK_EQUIVALENT"
        del pair["company_bits"]

    completeness = {
        "status": COMPLETENESS_STATUS,
        "statement": "Every support-retained event anchor × structural atom coordinate was explicitly compatibility-checked; every legal pair was bit-mask intersected and every support-retained nonconstant pair is frozen and evaluable.",
        "scope": "Observed referenced singleton event types, fixed grids, witnessed strict typed sequences of length 2-3, declared compatibility rules, support floors, and hard resource caps.",
        "not_claimed": "No subtype/free text/URL/metadata/quality or investor literal; no OR/NOT/type subsets/arbitrary thresholds/unwitnessed tuples/low-support or constant pairs; no ternary/deeper formula and no causal claim.",
    }
    grammar = {
        "kind": "complete_explicit_legal_pairwise_grammar", "grammar_semantic_version": GRAMMAR_VERSION,
        "ebnf": "RULE ::= ALL(EVENT_ANCHOR, STRUCTURAL_ATOM)", "composition": "EXACTLY_ONE_OF_EACH_NO_TERNARY",
        "threshold_registry": THRESHOLD_REGISTRY, "threshold_registry_sha256": THRESHOLD_REGISTRY_SHA256,
        "observed_type_vocabulary": [[family, literal] for family, literal in observed_types],
        "observed_type_vocabulary_sha256": vocab_hash, "witnessed_sequence_vocabulary_sha256": sequence_vocab_hash,
        "tracks": [ACTION_ELIGIBLE, DESCRIPTIVE_ASSOCIATION],
    }
    anchor_payload = {"schema_version": "1.0", "grammar": GRAMMAR_VERSION,
                      "anchors": [_public_atom(item) for item in anchors]}
    structural_payload = {"schema_version": "1.0", "grammar": GRAMMAR_VERSION,
                          "atoms": [_public_atom(item) for item in structural]}
    pair_payload = {"schema_version": "1.0", "grammar": GRAMMAR_VERSION, "completeness": completeness,
                    "pairs": pairs}
    representative_payload = {
        "schema_version": "1.0", "dedupe_scope": "TRACK_LOCAL_COMPANY_MASK",
        "representatives": [{"id": item["id"], "track": item["track"],
                             "company_mask_sha256": item["company_mask_sha256"]} for item in representatives],
        "candidate_to_representative": {item["id"]: item["representative_id"] for item in pairs},
    }
    frozen = {
        "config_schema_version": "1.0", "pattern_set_id": GRAMMAR_VERSION,
        "primary_order": [item["id"] for item in pairs],
        "patterns": [{
            "id": item["id"], "name": f"{item['event_dimension']} × {item['structural_dimension']}",
            "description": "Explicit label-blind typed-event × structural candidate.",
            "evaluation_status": "EVALUABLE", "not_evaluable_reason": None,
            "track": item["track"], "rule": item["rule"],
        } for item in pairs],
    }

    script_path, engine_path = Path(__file__).resolve(), Path(engine.__file__).resolve()
    projections = {"chains": list(SAFE_CHAIN_COLUMNS), "exposure": list(SAFE_EXPOSURE_COLUMNS),
                   "interface": list(SAFE_INTERFACE_COLUMNS)}
    inputs = {name: {"path": str(path), "sha256": digest_json(tables[name].to_pylist()),
                     "hash_scope": "canonical projected rows only", "rows": tables[name].num_rows,
                     "projected_columns": projections[name]} for name, path in paths.items()}
    counts = {
        "chains": chain_width, "companies_with_chains": company_width, "observed_types": len(observed_types),
        "raw_event_anchors": len(raw_anchors), "support_pruned_event_anchors": anchor_support_pruned,
        "supported_event_anchors": len(anchors), "raw_structural_coordinates": len(raw_structural),
        "support_pruned_structural_atoms": structural_support_pruned, "supported_structural_atoms": len(structural),
        "pairwise_cartesian_coordinates": cartesian, "legal_pair_coordinates": legal,
        "compatibility_rejected_coordinates": sum(rejected.values()),
        "compatibility_rejected_by_reason": dict(sorted(rejected.items())),
        "support_pruned_pairs": support_pruned, "constant_pruned_pairs": constant_pruned,
        "frozen_pairs": len(pairs), "track_local_representatives": len(representatives),
        "action_eligible_pairs": sum(item["track"] == ACTION_ELIGIBLE for item in pairs),
        "descriptive_association_pairs": sum(item["track"] == DESCRIPTIVE_ASSOCIATION for item in pairs),
        **sequence_stats,
    }
    manifest = {
        "generation_version": "extended_v1", "phase": "LABEL_BLIND_GENERATION_FREEZE",
        "runtime": {"python": platform.python_version(), "pyarrow": pa.__version__},
        "inputs": inputs,
        "schema_inputs": {name: {"path": str(path), "sha256": engine.sha256_file(path),
                                  "validated_contract": contracts[name]} for name, path in schemas.items()},
        "code_inputs": {"generator": {"path": str(script_path), "sha256": engine.sha256_file(script_path)},
                        "generic_rule_engine": {"path": str(engine_path), "sha256": engine.sha256_file(engine_path)}},
        "opened_paths": sorted(str(path) for path in [*paths.values(), *schemas.values(), script_path, engine_path]),
        "isolation_contract": {
            "generation_reads_outcome_labels": False, "generation_reads_reference_patterns": False,
            "generation_reads_prior_candidate_or_evaluation_results": False,
            "projected_columns": projections,
            "label_exclusion_rule": "only explicitly enumerated SAFE columns are projected",
            "forbidden_event_columns": list(FORBIDDEN_EVENT_COLUMNS), "forbidden_input_kinds": list(FORBIDDEN_INPUT_KINDS),
            "identity_fields_are_participant_equality_only": True,
            "cli_has_no_entity_reference_pattern_or_evaluation_option": True,
        },
        "grammar": grammar, "completeness": completeness,
        "support_policy": {"minimum_chains": args.min_chain_support, "minimum_companies": args.min_company_support,
                           "constant_pair_masks_published": False},
        "dedupe_policy": "track-local company mask; representatives cache evaluation but every pair receives a row",
        "evaluation_contract": {"delta": "caller_supplied", "all_tracks_evaluable": True,
                                "descriptive_causal_interpretation_allowed": False},
        "resource_limits": caps, "resource_observed": {"peak_rss_gate": "PASS_LE_MAX_RSS_MIB"},
        "counts": counts, "outputs": {},
    }
    rss_mib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    if rss_mib > caps["max_rss_mib"]:
        raise ValueError("INCOMPLETE_RESOURCE_BOUND: peak RSS exceeds cap")

    output.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=output.name + ".tmp-", dir=output.parent))
    try:
        payloads = {
            "event_anchor_catalog.json": anchor_payload,
            "structural_atom_catalog.json": structural_payload,
            "candidate_pairs.json": pair_payload,
            "candidate_representatives.json": representative_payload,
            "pattern_candidates_extended_frozen.json": frozen,
        }
        for name, payload in payloads.items():
            (temp / name).write_text(canonical_json(payload) + "\n")
        total_bytes = sum((temp / name).stat().st_size for name in payloads)
        if total_bytes > caps["max_artifact_mib"] * 1024 * 1024:
            raise ValueError("INCOMPLETE_RESOURCE_BOUND: semantic artifacts exceed size cap")
        manifest["resource_observed"]["semantic_artifact_bytes"] = total_bytes
        manifest["outputs"] = {name: {"sha256": engine.sha256_file(temp / name),
                                             "bytes": (temp / name).stat().st_size} for name in sorted(payloads)}
        (temp / "generation_manifest.json").write_text(canonical_json(manifest) + "\n")
        os.replace(temp, output)
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise
    return manifest


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--chain-path", required=True)
    result.add_argument("--exposure-path", required=True)
    result.add_argument("--interface-path", required=True)
    result.add_argument("--chain-schema", default=str(DEFAULT_CHAIN_SCHEMA))
    result.add_argument("--exposure-schema", default=str(DEFAULT_EXPOSURE_SCHEMA))
    result.add_argument("--interface-schema", default=str(DEFAULT_INTERFACE_SCHEMA))
    result.add_argument("--output-dir", required=True)
    result.add_argument("--max-sequence-length", type=int, default=3)
    result.add_argument("--min-chain-support", type=int, default=5)
    result.add_argument("--min-company-support", type=int, default=5)
    result.add_argument("--max-observed-types", type=int, default=32)
    result.add_argument("--max-event-anchors", type=int, default=250000)
    result.add_argument("--max-supported-anchors", type=int, default=20000)
    result.add_argument("--max-structural-atoms", type=int, default=2000)
    result.add_argument("--max-supported-structural-atoms", type=int, default=1000)
    result.add_argument("--max-sequence-tuples-l2", type=int, default=1024)
    result.add_argument("--max-sequence-tuples-l3", type=int, default=8192)
    result.add_argument("--max-legal-pairs", type=int, default=5000000)
    result.add_argument("--max-frozen-pairs", type=int, default=250000)
    result.add_argument("--max-representatives", type=int, default=60000)
    result.add_argument("--max-sequence-transitions", type=int, default=50000000)
    result.add_argument("--max-artifact-mib", type=float, default=512.0)
    result.add_argument("--max-wall-seconds", type=float, default=1800.0)
    result.add_argument("--max-rss-mib", type=float, default=8192.0)
    return result


def main() -> None:
    args = parser().parse_args()
    try:
        manifest = freeze(args)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(canonical_json(manifest["counts"]))
    print(f"wrote: {Path(args.output_dir).expanduser().resolve()}")


if __name__ == "__main__":
    main()
