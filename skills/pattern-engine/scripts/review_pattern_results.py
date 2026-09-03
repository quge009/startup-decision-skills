#!/usr/bin/env python3
"""Review frozen extended Patterns without changing generation or evaluation.

The removal policy reads only frozen ASTs and generation metadata. Statistical
fields are attached only after the retained candidate IDs and deterministic
company-mask representatives have been frozen in memory.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

ALPHA = 0.05
RESULT_COLUMNS = ("RECOMMEND", "AVOID", "INDIFFERENT", "INCONCLUSIVE")
MATRIX_COLUMNS = ("Step", *RESULT_COLUMNS, "Total")
STEP_NAMES = (
    "Original results",
    "After removing invalid or uninterpretable Patterns",
    "After merging Patterns matching the same companies",
)
FREEZE_FILES = (
    "event_anchor_catalog.json",
    "structural_atom_catalog.json",
    "candidate_pairs.json",
    "candidate_representatives.json",
    "pattern_candidates_extended_frozen.json",
    "generation_manifest.json",
)

# This value, rather than evaluation data, is the complete review policy input.
POLICY = {
    "version": "review_pattern_results",
    "selection_inputs": ["frozen AST", "frozen pair metadata", "frozen component metadata"],
    "selection_forbidden_inputs": ["ATE", "p-value", "Result", "company outcome label"],
    "removal_precedence": [
        "exit_event",
        "event_type_other",
        "outcome_type",
        "round_unknown_or_other",
        "end_relative_timing",
        "duration",
        "future_only",
        "nullness_test",
        "data_quality_basis_or_known_date",
        "not_human_readable",
    ],
    "retained_concepts": [
        "round",
        "start-relative timing",
        "count",
        "participant",
        "cadence/density",
        "observed history/position",
    ],
    "global_merge_key": "company_mask_sha256",
    "representative_order": [
        "lowest AST complexity",
        "fewest rendered words",
        "shortest rendered text",
        "canonical AST",
        "candidate ID",
    ],
    "result_rule": {
        "alpha": ALPHA,
        "significant_positive": "RECOMMEND",
        "significant_negative": "AVOID",
        "significant_exact_zero": "INDIFFERENT",
        "equivalent_within_fixed_margin": "INDIFFERENT",
        "otherwise": "INCONCLUSIVE",
    },
}

FIELD_LABELS = {
    "chain.n_exposure": "Exposure event count",
    "chain.n_interface": "Interface event count",
    "derived.n_middle": "middle-event count",
    "derived.n_known_date_middle_events": "known-date middle-event count",
    "derived.n_ordered_batches": "ordered event-batch count",
    "derived.n_comparable_gaps": "comparable inter-batch gap count",
    "derived.exposure_share": "Exposure share of middle events",
    "derived.interface_share": "Interface share of middle events",
    "derived.events_per_chain_year": "middle events per chain-year",
    "derived.batches_per_chain_year": "event batches per chain-year",
    "derived.interface_investor_count": "distinct Interface participant count",
    "derived.middle_span_min": "minimum middle-event span in days",
    "derived.middle_span_max": "maximum middle-event span in days",
    "derived.min_interbatch_gap_min": "lower bound of the shortest inter-batch gap in days",
    "derived.min_interbatch_gap_max": "upper bound of the shortest inter-batch gap in days",
    "derived.max_interbatch_gap_min": "lower bound of the longest inter-batch gap in days",
    "derived.max_interbatch_gap_max": "upper bound of the longest inter-batch gap in days",
    "derived.n_prior_chains": "number of prior chains",
    "derived.company_chain_count": "company chain count",
    "derived.prior_same_round_count": "number of prior chains at the same round",
    "derived.chain_duration_days": "chain duration in days",
    "derived.outcome_round_bucket": "outcome round",
    "derived.outcome_round_known": "whether the outcome round is known",
    "chain.outcome_type": "outcome type",
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load JSON {path}: {exc}") from exc


def _walk(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _fields(rule: dict[str, Any]) -> set[str]:
    return {str(node["field"]) for node in _walk(rule) if isinstance(node.get("field"), str)}


def _event_types(rule: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for node in _walk(rule):
        if node.get("field") == "event.event_type" and node.get("cmp") == "eq":
            value = node.get("value")
            if isinstance(value, str):
                values.add(value.strip().casefold())
    return values


def _literal_values(value: Any) -> set[str]:
    if isinstance(value, list):
        return {str(item).strip().casefold() for item in value}
    return {str(value).strip().casefold()}


def _round_has_unknown_or_other(rule: dict[str, Any]) -> bool:
    for node in _walk(rule):
        if node.get("op") != "compare" or node.get("left", {}).get("field") != "derived.outcome_round_bucket":
            continue
        if _literal_values(node.get("right", {}).get("literal")) & {"unknown", "other"}:
            return True
    return False


def _has_end_relative_timing(rule: dict[str, Any]) -> bool:
    return any(node.get("relative_to") == "end" for node in _walk(rule) if "event_timing" not in node)


def _selector_text(selector: Any) -> str:
    if not isinstance(selector, dict) or selector.get("scope") != "middle_events":
        raise ValueError("unsupported event selector")
    family = selector.get("family")
    if family not in {"exposure", "interface"}:
        raise ValueError("unsupported event family")
    where = selector.get("where")
    if not isinstance(where, list) or len(where) != 1:
        raise ValueError("selector must contain one event type")
    condition = where[0]
    if (not isinstance(condition, dict) or condition.get("field") != "event.event_type" or
            condition.get("cmp") != "eq" or not isinstance(condition.get("value"), str)):
        raise ValueError("unsupported selector condition")
    event_type = condition["value"].strip().replace("_", " ")
    if not event_type:
        raise ValueError("empty event type")
    return f"{family.title()} {event_type} event"


def _format_literal(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and 0 <= value <= 1:
        return f"{value:g}"
    if isinstance(value, list):
        if not value:
            raise ValueError("empty literal list")
        return " or ".join(str(item).replace("_", " ") for item in value)
    if value is None or isinstance(value, (dict, tuple)):
        raise ValueError("unsupported literal")
    return str(value).replace("_", " ")


def _comparison_text(subject: str, cmp: Any, literal: Any) -> str:
    phrases = {"gte": "is at least", "lte": "is at most", "gt": "is greater than",
               "lt": "is less than", "eq": "is", "ne": "is not", "in": "is",
               "not_in": "is neither"}
    if cmp not in phrases:
        raise ValueError(f"unsupported comparator: {cmp}")
    return f"{subject} {phrases[cmp]} {_format_literal(literal)}"


def _value_subject(value: Any) -> str:
    if not isinstance(value, dict) or len(value) != 1:
        raise ValueError("unsupported comparison expression")
    if "field" in value:
        field = value["field"]
        if field not in FIELD_LABELS:
            raise ValueError(f"unrenderable field: {field}")
        return FIELD_LABELS[field]
    if "event_count" in value:
        return f"count of {_selector_text(value['event_count'])}s"
    if "event_distinct_count" in value:
        options = value["event_distinct_count"]
        if not isinstance(options, dict) or options.get("field") != "event.participant_key":
            raise ValueError("unsupported distinct-count field")
        return f"distinct participant count for {_selector_text(options.get('selector'))}s"
    if "event_timing" in value:
        options = value["event_timing"]
        if not isinstance(options, dict) or options.get("relative_to") not in {"start", "end"}:
            raise ValueError("unsupported event timing")
        if options.get("edge") not in {"first", "last"} or options.get("bound") not in {"min", "max"}:
            raise ValueError("unsupported event timing edge or bound")
        return (f"{options['bound']}imum days from chain {options['relative_to']} to the "
                f"{options['edge']} {_selector_text(options.get('selector'))}")
    if "event_position" in value:
        options = value["event_position"]
        if not isinstance(options, dict) or options.get("scope") not in {"family", "all_middle"}:
            raise ValueError("unsupported event-position scope")
        if options.get("edge") not in {"first_batch", "last_batch"}:
            raise ValueError("unsupported event-position edge")
        scope = "its event family" if options["scope"] == "family" else "all middle events"
        edge = options["edge"].replace("_", " ")
        return f"{_selector_text(options.get('selector'))} is in the {edge} among {scope}"
    raise ValueError("unsupported comparison expression")


def render_rule(rule: Any) -> str:
    if not isinstance(rule, dict):
        raise ValueError("rule is not an object")
    op = rule.get("op")
    if op == "all":
        children = rule.get("rules")
        if not isinstance(children, list) or len(children) < 2:
            raise ValueError("ALL must have at least two children")
        return " and ".join(f"({render_rule(child)})" for child in children)
    if op == "present":
        return f"has at least one {_selector_text(rule.get('selector'))}"
    if op == "absent":
        return f"has no {_selector_text(rule.get('selector'))}"
    if op == "compare":
        left = rule.get("left")
        cmp = rule.get("cmp")
        if cmp in {"is_null", "not_null"}:
            subject = _value_subject(left)
            return f"{subject} is {'missing' if cmp == 'is_null' else 'known'}"
        subject = _value_subject(left)
        literal = rule.get("right", {}).get("literal")
        if isinstance(left, dict) and "event_position" in left and cmp == "eq" and literal is True:
            return subject
        return _comparison_text(subject, cmp, literal)
    if op == "sequence":
        steps = rule.get("steps")
        if not isinstance(steps, list) or len(steps) not in {2, 3} or rule.get("ordering") != "strict_before":
            raise ValueError("unsupported sequence")
        text = " occurs before ".join(_selector_text(step) for step in steps)
        minimum, maximum = rule.get("min_gap_days"), rule.get("max_gap_days")
        if minimum is not None and maximum is not None:
            raise ValueError("sequence has two gap bounds")
        if minimum is not None:
            text += f" with at least {minimum} days between consecutive events"
        if maximum is not None:
            text += f" with at most {maximum} days between consecutive events"
        same = rule.get("same_values", [])
        if same:
            if same != ["event.participant_key"]:
                raise ValueError("unsupported sequence equality")
            text += " with the same participant"
        return text
    raise ValueError(f"unsupported rule operator: {op}")


def _expression_complexity(value: Any) -> int:
    if not isinstance(value, dict):
        return 10
    if "field" in value:
        return 1
    if "event_count" in value:
        return 2
    if "event_distinct_count" in value:
        return 3
    if "event_timing" in value or "event_position" in value:
        return 4
    return 10


def ast_complexity(rule: Any) -> int:
    if not isinstance(rule, dict):
        return 10_000
    op = rule.get("op")
    if op == "all":
        return 1 + sum(ast_complexity(child) for child in rule.get("rules", []))
    if op in {"present", "absent"}:
        return 2
    if op == "compare":
        return 1 + _expression_complexity(rule.get("left"))
    if op == "sequence":
        return (3 + len(rule.get("steps", [])) + int(rule.get("min_gap_days") is not None) +
                int(rule.get("max_gap_days") is not None) + 2 * len(rule.get("same_values", [])))
    return 10_000


def review_policy_decision(pair: dict[str, Any], anchor: dict[str, Any], structural: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return (primary removal reason, rendering), without statistical inputs."""
    rule = pair.get("rule")
    if not isinstance(rule, dict):
        return "not_human_readable", None
    event_types = _event_types(rule)
    fields = _fields(rule)
    taints = set(pair.get("taints", []))
    structural_dimension = pair.get("structural_dimension")

    if "exit" in event_types:
        return "exit_event", None
    if "other" in event_types:
        return "event_type_other", None
    if any("outcome_type" in field for field in fields):
        return "outcome_type", None
    if _round_has_unknown_or_other(rule):
        return "round_unknown_or_other", None
    if _has_end_relative_timing(rule):
        return "end_relative_timing", None
    if structural_dimension == "duration" or "derived.chain_duration_days" in fields:
        return "duration", None
    if "future" in taints or "derived.company_chain_count" in fields:
        return "future_only", None
    if any(node.get("op") == "compare" and node.get("cmp") in {"is_null", "not_null"} for node in _walk(rule)):
        return "nullness_test", None
    quality_tokens = ("quality", "basis", "known_date", "date_known", "date_precision", "window_status")
    if any(any(token in field.casefold() for token in quality_tokens) for field in fields):
        return "data_quality_basis_or_known_date", None

    # Component metadata is checked here so policy cannot silently use a stale or
    # mismatched dimension from a different freeze.
    if pair.get("event_dimension") != anchor.get("dimension"):
        return "not_human_readable", None
    if structural_dimension != structural.get("dimension"):
        return "not_human_readable", None
    try:
        rendered = render_rule(rule)
    except (KeyError, TypeError, ValueError):
        return "not_human_readable", None
    return None, rendered


def _selection_key(pair: dict[str, Any], rendered: str) -> tuple[Any, ...]:
    return (ast_complexity(pair["rule"]), len(rendered.split()), len(rendered),
            canonical_json(pair["rule"]), pair["id"])


def classify_result(row: dict[str, Any]) -> str:
    if row.get("status") != "EVALUATED":
        raise ValueError(f"candidate {row.get('candidate_id')} is not EVALUATED")
    ate, p_value, p_tost = row.get("ate_success"), row.get("p_value"), row.get("p_tost")
    if not isinstance(ate, (int, float)) or isinstance(ate, bool):
        raise ValueError(f"candidate {row.get('candidate_id')} has invalid ATE")
    if not isinstance(p_value, (int, float)) or isinstance(p_value, bool):
        raise ValueError(f"candidate {row.get('candidate_id')} has invalid p-value")
    if p_value < ALPHA:
        if ate > 0:
            return "RECOMMEND"
        if ate < 0:
            return "AVOID"
        return "INDIFFERENT"
    if isinstance(p_tost, (int, float)) and not isinstance(p_tost, bool) and p_tost < ALPHA:
        return "INDIFFERENT"
    return "INCONCLUSIVE"


def _source_result_as_four(value: Any) -> str:
    if value in RESULT_COLUMNS:
        return str(value)
    aliases = {
        "POSITIVE_" + "ASSOCIATION": "RECOMMEND",
        "NEGATIVE_" + "ASSOCIATION": "AVOID",
        "EQUIVALENT_WITHIN_DELTA": "INDIFFERENT",
    }
    if value not in aliases:
        raise ValueError(f"unknown source Result value: {value}")
    return aliases[value]


def _statistical_signature(row: dict[str, Any], result: str) -> tuple[Any, ...]:
    keys = (
        "status", "taking_success", "taking_failure", "not_taking_success", "not_taking_failure",
        "taking_total", "not_taking_total", "taking_success_rate", "not_taking_success_rate",
        "ate_success", "ate_failure", "chi_square", "p_value", "p_tost", "company_support",
    )
    return (result, *(row.get(key) for key in keys))


def _statistics(row: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "status", "chain_support", "company_support", "taking_success", "taking_failure",
        "not_taking_success", "not_taking_failure", "taking_total", "not_taking_total",
        "taking_success_rate", "not_taking_success_rate", "ate_success", "ate_failure",
        "chi_square", "p_value", "p_tost",
    )
    return {key: row.get(key) for key in keys}


def _matrix_row(name: str, counts: Counter[str]) -> dict[str, Any]:
    row: dict[str, Any] = {"Step": name}
    row.update({result: int(counts[result]) for result in RESULT_COLUMNS})
    row["Total"] = sum(row[result] for result in RESULT_COLUMNS)
    if tuple(row) != MATRIX_COLUMNS:
        raise AssertionError("matrix columns drifted")
    return row


def _validate_manifest(freeze_dir: Path, manifest: dict[str, Any], input_hashes: dict[str, str]) -> None:
    outputs = manifest.get("outputs")
    if not isinstance(outputs, dict):
        raise ValueError("generation manifest has no outputs map")
    for name, metadata in outputs.items():
        if name not in input_hashes or not isinstance(metadata, dict):
            raise ValueError(f"generation manifest output is missing: {name}")
        if metadata.get("sha256") != input_hashes[name]:
            raise ValueError(f"generation manifest hash mismatch: {name}")
    expected = manifest.get("counts", {}).get("frozen_pairs")
    if not isinstance(expected, int):
        raise ValueError("generation manifest has no frozen pair count")
    if not (freeze_dir / "candidate_pairs.json").is_file():
        raise ValueError("candidate pair artifact is missing")


def review(freeze_dir: Path, evaluation_path: Path, output_path: Path, *,
           expected_count: int | None = None, overwrite: bool = False) -> dict[str, Any]:
    freeze_dir = freeze_dir.expanduser().resolve()
    evaluation_path = evaluation_path.expanduser().resolve()
    output_path = output_path.expanduser().resolve()
    if output_path.exists() and not overwrite:
        raise ValueError(f"refusing to overwrite existing output: {output_path}")
    paths = {name: freeze_dir / name for name in FREEZE_FILES}
    paths["evaluation"] = evaluation_path
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise ValueError(f"missing review inputs: {missing}")
    input_hashes = {name: sha256_file(path) for name, path in paths.items()}
    manifest = _load_json(paths["generation_manifest.json"])
    _validate_manifest(freeze_dir, manifest, input_hashes)

    anchors_payload = _load_json(paths["event_anchor_catalog.json"])
    structural_payload = _load_json(paths["structural_atom_catalog.json"])
    anchors = {item["id"]: item for item in anchors_payload.get("anchors", [])}
    structural = {item["id"]: item for item in structural_payload.get("atoms", [])}
    if len(anchors) != len(anchors_payload.get("anchors", [])) or len(structural) != len(structural_payload.get("atoms", [])):
        raise ValueError("duplicate component ID in frozen catalogs")

    pair_payload = _load_json(paths["candidate_pairs.json"])
    pairs = pair_payload.get("pairs")
    if not isinstance(pairs, list):
        raise ValueError("candidate pair artifact has no pairs list")
    pair_count = len(pairs)
    manifest_count = manifest.get("counts", {}).get("frozen_pairs")
    if pair_count != manifest_count:
        raise ValueError(f"pair count disagrees with manifest: {pair_count} != {manifest_count}")
    if expected_count is not None and pair_count != expected_count:
        raise ValueError(f"production pair count mismatch: {pair_count} != {expected_count}")

    remaining_ids: set[str] = set()
    retained_mask_by_id: dict[str, str] = {}
    selected_by_mask: dict[str, dict[str, Any]] = {}
    retained_per_mask: Counter[str] = Counter()
    deletion_counts: Counter[str] = Counter()
    for pair in pairs:
        candidate_id = pair.get("id")
        if not isinstance(candidate_id, str) or candidate_id in remaining_ids:
            raise ValueError(f"invalid or duplicate candidate ID: {candidate_id}")
        remaining_ids.add(candidate_id)
        anchor = anchors.get(pair.get("anchor_id"))
        struct = structural.get(pair.get("structural_id"))
        if anchor is None or struct is None:
            raise ValueError(f"candidate {candidate_id} references an unknown component")
        reason, rendered = review_policy_decision(pair, anchor, struct)
        if reason is not None:
            deletion_counts[reason] += 1
            continue
        if rendered is None:
            raise AssertionError("retained candidate has no rendering")
        mask = pair.get("company_mask_sha256")
        if not isinstance(mask, str) or len(mask) != 64:
            raise ValueError(f"candidate {candidate_id} has an invalid company mask")
        retained_mask_by_id[candidate_id] = mask
        retained_per_mask[mask] += 1
        candidate = {
            "candidate_id": candidate_id,
            "company_mask_sha256": mask,
            "pattern": rendered,
            "rule": pair["rule"],
            "source_track": pair.get("track"),
            "event_dimension": pair.get("event_dimension"),
            "structural_dimension": pair.get("structural_dimension"),
            "selection_key": _selection_key(pair, rendered),
        }
        prior = selected_by_mask.get(mask)
        if prior is None or candidate["selection_key"] < prior["selection_key"]:
            selected_by_mask[mask] = candidate

    del pairs, pair_payload, anchors_payload, structural_payload, anchors, structural
    gc.collect()

    evaluation = _load_json(evaluation_path)
    rows = evaluation.get("candidates")
    if not isinstance(rows, list) or evaluation.get("candidate_count") != pair_count or len(rows) != pair_count:
        raise ValueError("evaluation does not contain exactly one row per frozen candidate")
    if evaluation.get("delta") != 0.05:
        raise ValueError("evaluation delta must be exactly .05")

    original_counts: Counter[str] = Counter()
    retained_counts: Counter[str] = Counter()
    group_signatures: dict[str, tuple[Any, ...]] = {}
    group_results: dict[str, str] = {}
    group_statistics: dict[str, dict[str, Any]] = {}
    for row in rows:
        candidate_id = row.get("candidate_id")
        if candidate_id not in remaining_ids:
            raise ValueError(f"duplicate or unknown evaluation candidate: {candidate_id}")
        remaining_ids.remove(candidate_id)
        result = classify_result(row)
        if _source_result_as_four(row.get("result")) != result:
            raise ValueError(f"source Result disagrees with p/sign rule: {candidate_id}")
        original_counts[result] += 1
        mask = retained_mask_by_id.get(candidate_id)
        if mask is None:
            continue
        retained_counts[result] += 1
        signature = _statistical_signature(row, result)
        prior_signature = group_signatures.get(mask)
        if prior_signature is not None and prior_signature != signature:
            raise ValueError(f"same company mask has inconsistent statistics: {mask}")
        if prior_signature is None:
            group_signatures[mask] = signature
            group_results[mask] = result
            group_statistics[mask] = _statistics(row)
    if remaining_ids:
        raise ValueError(f"evaluation is missing {len(remaining_ids)} frozen candidates")
    if set(selected_by_mask) != set(group_signatures):
        raise ValueError("retained company-mask groups are incomplete after evaluation")

    merged_counts = Counter(group_results.values())
    matrices = [
        _matrix_row(STEP_NAMES[0], original_counts),
        _matrix_row(STEP_NAMES[1], retained_counts),
        _matrix_row(STEP_NAMES[2], merged_counts),
    ]
    if any(row["Total"] != sum(row[result] for result in RESULT_COLUMNS) for row in matrices):
        raise AssertionError("Result columns do not conserve row totals")
    if matrices[0]["Total"] != pair_count:
        raise AssertionError("original matrix row does not conserve the frozen pair count")

    final_candidates = []
    for mask, candidate in selected_by_mask.items():
        result = group_results[mask]
        output_candidate = {key: value for key, value in candidate.items() if key != "selection_key"}
        output_candidate.update({
            "result": result,
            "statistics": group_statistics[mask],
            "patterns_with_same_company_mask": retained_per_mask[mask],
        })
        final_candidates.append(output_candidate)
    final_candidates.sort(key=lambda item: (item["company_mask_sha256"], item["candidate_id"]))

    retained_count = len(retained_mask_by_id)
    policy_hash = hashlib.sha256(canonical_json(POLICY).encode()).hexdigest()
    result = {
        "review_version": POLICY["version"],
        "matrix_columns": list(MATRIX_COLUMNS),
        "three_step_matrix": matrices,
        "audit_appendix": {
            "deletion_reason_counts": {reason: int(deletion_counts[reason])
                                       for reason in POLICY["removal_precedence"]},
            "removed_pattern_count": sum(deletion_counts.values()),
            "retained_pattern_count": retained_count,
            "global_company_mask_count": len(selected_by_mask),
            "merged_pattern_count": retained_count - len(selected_by_mask),
            "same_mask_statistical_consistency": "PASS",
        },
        "final_candidates": final_candidates,
        "policy": POLICY,
        "policy_sha256": policy_hash,
        "input_sha256": dict(sorted(input_hashes.items())),
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=output_path.name + ".tmp-", dir=output_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, sort_keys=False, indent=2, allow_nan=False)
            stream.write("\n")
        os.replace(temp_name, output_path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise
    return result


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument("--freeze-dir", type=Path, required=True)
    command.add_argument("--evaluation", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    command.add_argument("--expected-count", type=int)
    command.add_argument("--overwrite", action="store_true")
    return command


def main() -> None:
    args = parser().parse_args()
    result = review(args.freeze_dir, args.evaluation, args.output,
                    expected_count=args.expected_count, overwrite=args.overwrite)
    print(canonical_json({
        "output": str(args.output.resolve()),
        "policy_sha256": result["policy_sha256"],
        "three_step_matrix": result["three_step_matrix"],
        "audit_appendix": result["audit_appendix"],
    }))


if __name__ == "__main__":
    main()
