#!/usr/bin/env python3
"""Build the single canonical CHIP Pattern registry from the three reviewed batches."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

RESULTS = ("RECOMMEND", "AVOID", "INDIFFERENT", "INCONCLUSIVE")
BATCHES = ("batch1", "batch2", "batch3")
ALPHA = 0.05
DELTA = 0.05
UNIVERSE = {
    "id": "chip_chain_companies_v0.3_663",
    "size": 663,
    "ordering": "company_id UTF-8 lexical ascending",
    "encoding": "integer bitset, bit i = ordered company i, little-endian bytes, ceil(width/8)",
}
EXPECTED_BATCH1 = {
    "P1": "INCONCLUSIVE", "P2": "RECOMMEND", "P3": "RECOMMEND",
    "P4": "INCONCLUSIVE", "P5": "INCONCLUSIVE", "P7": "INCONCLUSIVE",
    "P8": "AVOID",
}
EXPECTED_COUNTS = {
    "RECOMMEND": {"batch1": 2, "batch2": 5, "batch3": 9453},
    "AVOID": {"batch1": 1, "batch2": 0, "batch3": 74},
    "INDIFFERENT": {"batch1": 0, "batch2": 0, "batch3": 0},
    "INCONCLUSIVE": {"batch1": 4, "batch2": 0, "batch3": 13769},
}
EXPECTED_BATCH3_MATRIX = (208394, 97607, 23296)

HERE = Path(__file__).resolve()
ROOT = HERE.parent.parent
DEFAULT_BATCH1_CONFIG = ROOT / "configs/chip_patterns_v0.1.json"
DEFAULT_BATCH1_PREVALENCE = Path("/tmp/kiro_review_chip_baseline_20260827_111448/pattern_prevalence_sf_v0.2.json")
DEFAULT_BATCH2_FREEZE = Path("/tmp/kiro_review_chip_freeze_c_20260827_111448")
DEFAULT_BATCH2_PREVALENCE = Path("/tmp/chip_candidate_evaluation_delta005_freeze_20260827/pattern_prevalence_sf_v0.2.json")
DEFAULT_BATCH3_REVIEW = Path("/tmp/review_chip_batch3_results_v01_20260827T1654.json")
DEFAULT_REPORT = ROOT / "_REPORT.md"
DEFAULT_SCHEMA = ROOT / "schemas/chip_pattern_registry_v1.spec.md"


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                       allow_nan=False) + "\n").encode("utf-8")


def canonical_value_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical_value_bytes(value)).hexdigest()


def load_json(path: Path) -> Any:
    def reject_constant(token: str) -> None:
        raise ValueError(f"non-finite JSON constant {token} in {path}")

    with path.open(encoding="utf-8") as stream:
        return json.load(stream, parse_constant=reject_constant)


def classify_result(ate: float, p_value: float, p_tost: float | None) -> str:
    if p_value < ALPHA:
        if ate > 0:
            return "RECOMMEND"
        if ate < 0:
            return "AVOID"
        return "INDIFFERENT"
    if p_tost is not None and p_tost < ALPHA:
        return "INDIFFERENT"
    return "INCONCLUSIVE"


def _words(value: str) -> str:
    return value.replace("_", " ")


def _selector_text(selector: dict[str, Any]) -> str:
    family = selector.get("family")
    where = selector.get("where")
    if family not in {"exposure", "interface"} or not isinstance(where, list) or len(where) != 1:
        raise ValueError(f"unsupported Batch2 selector: {selector}")
    condition = where[0]
    if condition.get("field") != "event.event_type" or condition.get("cmp") not in {"eq", "in"}:
        raise ValueError(f"unsupported Batch2 selector condition: {condition}")
    values = condition.get("value")
    if not isinstance(values, list):
        values = [values]
    if not values or not all(isinstance(value, str) and value for value in values):
        raise ValueError(f"invalid Batch2 event-type selector: {condition}")
    rendered = " or ".join(_words(value) for value in values)
    return f"{family.title()} {rendered} event"


def render_human_rule(rule: dict[str, Any]) -> str:
    """Deterministically render the bounded Batch2 rule grammar without statistical inputs."""
    op = rule.get("op")
    if op in {"all", "any"}:
        children = rule.get("rules")
        if not isinstance(children, list) or not children:
            raise ValueError(f"{op} rule has no children")
        joiner = " and " if op == "all" else " or "
        return joiner.join(f"({render_human_rule(child)})" for child in children)
    if op in {"present", "absent"}:
        phrase = _selector_text(rule.get("selector", {}))
        return f"at least one {phrase} occurs" if op == "present" else f"no {phrase} occurs"
    if op == "compare":
        left = rule.get("left", {})
        field = left.get("field") if isinstance(left, dict) else None
        subjects = {
            "derived.n_middle": "middle-event count",
            "chain.n_exposure": "Exposure-event count",
            "chain.n_interface": "Interface-event count",
            "chain.chain_window_status": "Chain window status",
            "chain.outcome_type": "outcome type",
            "derived.outcome_investor_count": "unique outcome-investor count",
        }
        subject = subjects.get(field, _words(field) if isinstance(field, str) else None)
        if not subject:
            raise ValueError(f"unsupported Batch2 comparison subject: {left}")
        comparator = rule.get("cmp")
        comparator_text = {
            "eq": "equals", "ne": "does not equal", "gte": "is at least", "gt": "is greater than",
            "lte": "is at most", "lt": "is less than", "in": "is one of", "not_in": "is not one of",
        }.get(comparator)
        if comparator_text is None:
            raise ValueError(f"unsupported Batch2 comparator: {comparator}")
        literal = rule.get("right", {}).get("literal")
        if isinstance(literal, list):
            literal_text = ", ".join(_words(str(value)) for value in literal)
        else:
            literal_text = _words(str(literal))
        return f"{subject} {comparator_text} {literal_text}"
    if op == "sequence":
        steps = rule.get("steps")
        if not isinstance(steps, list) or len(steps) not in {2, 3}:
            raise ValueError("unsupported Batch2 sequence")
        text = " occurs strictly before ".join(_selector_text(step) for step in steps)
        if rule.get("same_values"):
            text += " for the same participant"
        return text
    raise ValueError(f"unsupported Batch2 rule operator: {op}")


def _markdown_table_after(text: str, marker: str) -> list[list[str]]:
    marker_at = text.find(marker)
    if marker_at < 0:
        raise ValueError(f"report marker not found: {marker}")
    lines = text[marker_at:].splitlines()
    start = next((index for index, line in enumerate(lines) if line.startswith("|")), None)
    if start is None:
        raise ValueError(f"report table not found after: {marker}")
    rows: list[list[str]] = []
    for line in lines[start:]:
        if not line.startswith("|"):
            if rows:
                break
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells and not all(set(cell) <= {"-", ":"} for cell in cells):
            rows.append(cells)
    if len(rows) < 2:
        raise ValueError(f"report table is empty after: {marker}")
    return rows


def _count(cell: str) -> int:
    match = re.search(r"\d[\d,]*", cell)
    if not match:
        raise ValueError(f"no integer in report cell: {cell}")
    return int(match.group().replace(",", ""))


def parse_batch2_review(report_path: Path) -> dict[str, Any]:
    text = report_path.read_text(encoding="utf-8")
    start = text.find("##### 4.8.1.2 Second batch: label-blind finite-grammar enumeration freeze")
    end = text.find("##### 4.8.1.3", start)
    if start < 0 or end < 0:
        raise ValueError("report Batch2 freeze section is missing or unbounded")
    section = text[start:end]

    shortlist_rows = _markdown_table_after(section, "The following five are frozen as the meaningful second-batch shortlist")
    if shortlist_rows[0][0] != "Candidate":
        raise ValueError("unexpected Batch2 shortlist header")
    shortlist: dict[str, str] = {}
    for row in shortlist_rows[1:]:
        match = re.fullmatch(r"`(C_[0-9a-f]+)`\s+(.+)", row[0])
        if not match or row[-1].replace("*", "") != "RECOMMEND":
            raise ValueError(f"invalid Batch2 shortlist row: {row}")
        candidate_id, name = match.groups()
        if candidate_id in shortlist:
            raise ValueError(f"duplicate Batch2 shortlist ID: {candidate_id}")
        shortlist[candidate_id] = name
    if len(shortlist) != 5:
        raise ValueError(f"Batch2 shortlist must contain exactly 5 IDs, got {len(shortlist)}")

    accounting_rows = _markdown_table_after(section, "The complete evaluated-result accounting is:")
    accounting = {row[0].replace("*", ""): _count(row[1]) for row in accounting_rows[1:]}
    required_accounting = {
        "Meaningful RECOMMEND shortlist": 5, "Other RECOMMEND": 1614, "AVOID": 16,
        "INCONCLUSIVE": 2008, "Insufficient support": 4,
        "Total eager representatives evaluated": 3647,
    }
    if accounting != required_accounting:
        raise ValueError(f"Batch2 accounting drifted: {accounting}")

    recommend_rows = _markdown_table_after(section, "The 1,614 non-shortlisted RECOMMEND candidates are partitioned")
    avoid_rows = _markdown_table_after(section, "The 16 AVOID candidates are likewise fully partitioned")
    reason_keys = {
        "Arbitrary multi-event categorical subset": "arbitrary_multi_event_subset",
        "Terminal/outcome or `exit` dimension": "terminal_outcome_or_exit",
        "Sequence without a unified interpretation": "sequence_without_unified_interpretation",
        "Redundant numeric threshold": "redundant_numeric_threshold",
        "Target-adjacent singleton": "target_adjacent_singleton",
        "Simple singleton not robust after multiplicity review": "simple_singleton_not_robust",
        "Low-support singleton": "low_support_singleton",
        "Tiny-support sequence": "tiny_support_sequence",
        "Extreme-imbalance absence": "extreme_imbalance_absence",
    }

    def reasons(rows: list[list[str]], total_label: str) -> dict[str, int]:
        result: dict[str, int] = {}
        for row in rows[1:]:
            label = row[0].replace("*", "")
            if label == total_label:
                continue
            key = reason_keys.get(label)
            if key is None or key in result:
                raise ValueError(f"unexpected or duplicate Batch2 exclusion reason: {label}")
            result[key] = _count(row[1])
        return result

    return {
        "shortlist": shortlist,
        "accounting": accounting,
        "other_recommend_reasons": reasons(recommend_rows, "Total other RECOMMEND"),
        "avoid_reasons": reasons(avoid_rows, "Total AVOID"),
    }


def _source(path: Path, digest: str, pointer: str, role: str) -> dict[str, str]:
    return {"artifact_path": str(path), "artifact_sha256": digest, "pointer": pointer, "role": role}


def _sorted_sources(items: list[dict[str, str]]) -> list[dict[str, str]]:
    return sorted(items, key=lambda item: (item["artifact_path"], item["pointer"], item["role"]))


def _support(row: dict[str, Any], chain_count: int | None) -> dict[str, Any]:
    return {
        "chain_count": chain_count,
        "company_count": row["taking_total"],
        "contingency_2x2": {
            "taking": {"SUCCESS": row["taking_success"], "FAILURE": row["taking_failure"],
                       "total": row["taking_total"]},
            "not_taking": {"SUCCESS": row["not_taking_success"], "FAILURE": row["not_taking_failure"],
                           "total": row["not_taking_total"]},
        },
    }


def _stats(row: dict[str, Any], *, batch3: bool = False) -> dict[str, Any]:
    return {
        "estimand": "SUCCESS_RISK_DIFFERENCE",
        "ate": row["ate_success"],
        "p": row["p_value"],
        "p_tost": row["p_tost"],
        "chi_square": row.get("chi_square"),
        "alpha": ALPHA,
        "equivalence_margin": DELTA,
        "source_evaluation_status": row.get("status") if batch3 else row.get("evaluation_status"),
    }


def _mask(mask_hash: str, member_count: int) -> dict[str, Any]:
    return {
        "sha256": mask_hash, "member_count": member_count,
        "universe_id": UNIVERSE["id"], "universe_size": UNIVERSE["size"],
        "ordering": UNIVERSE["ordering"], "encoding": UNIVERSE["encoding"], "origin": "source",
    }


def _inference(review_status: str) -> dict[str, Any]:
    return {
        "design": "OBSERVATIONAL_UNADJUSTED", "causal_claim_allowed": False,
        "multiple_testing_adjusted": False, "certainty": "DECISION_RULE_ONLY",
        "review_status": review_status,
    }


def _registered_entry(*, candidate_id: str, batch: str, name: str | None, meaning: str,
                      rule: dict[str, Any], result: str, row: dict[str, Any],
                      chain_count: int | None, company_mask: dict[str, Any] | None,
                      attributes: dict[str, Any], review_status: str,
                      sources: list[dict[str, str]], batch3: bool = False) -> dict[str, Any]:
    return {
        "id": candidate_id, "batch": batch, "name": name, "meaning": meaning,
        "rule": rule, "rule_sha256": sha256_value(rule), "result": result,
        "support": _support(row, chain_count), "stats": _stats(row, batch3=batch3),
        "company_mask": company_mask, "attributes": attributes,
        "inference": _inference(review_status), "source": _sorted_sources(sources),
    }


def _index_unique(items: list[dict[str, Any]], key: str, label: str) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    values: dict[str, dict[str, Any]] = {}
    indexes: dict[str, int] = {}
    for index, item in enumerate(items):
        item_id = item.get(key)
        if not isinstance(item_id, str) or item_id in values:
            raise ValueError(f"invalid or duplicate {label} ID: {item_id}")
        values[item_id], indexes[item_id] = item, index
    return values, indexes


def build_registry(*, batch1_config: Path = DEFAULT_BATCH1_CONFIG,
                   batch1_prevalence: Path = DEFAULT_BATCH1_PREVALENCE,
                   batch2_freeze_dir: Path = DEFAULT_BATCH2_FREEZE,
                   batch2_prevalence: Path = DEFAULT_BATCH2_PREVALENCE,
                   batch3_review: Path = DEFAULT_BATCH3_REVIEW,
                   report_path: Path = DEFAULT_REPORT,
                   schema_path: Path = DEFAULT_SCHEMA,
                   enforce_production_counts: bool = True) -> dict[str, Any]:
    paths = {
        "batch1_config": batch1_config.resolve(),
        "batch1_prevalence": batch1_prevalence.resolve(),
        "batch2_freeze": (batch2_freeze_dir / "chip_candidates_frozen_v0.1.json").resolve(),
        "batch2_lattice": (batch2_freeze_dir / "candidate_lattice_v0.1.json").resolve(),
        "batch2_prevalence": batch2_prevalence.resolve(),
        "batch3_review": batch3_review.resolve(),
        "report": report_path.resolve(), "generator": HERE, "schema": schema_path.resolve(),
    }
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise ValueError(f"missing registry inputs: {missing}")
    hashes = {name: sha256_file(path) for name, path in paths.items()}

    buckets = {result: {batch: [] for batch in BATCHES} for result in RESULTS}

    # Batch 1: checked-in definitions joined only to the explicitly reviewed baseline.
    config = load_json(paths["batch1_config"])
    prevalence1 = load_json(paths["batch1_prevalence"])
    definitions, definition_indexes = _index_unique(config.get("patterns", []), "id", "Batch1 definition")
    rows1, row1_indexes = _index_unique(prevalence1.get("pattern_average_action_value", []), "pattern", "Batch1 result")
    if set(definitions) != set(rows1) or set(definitions) != set(EXPECTED_BATCH1) | {"P6"}:
        raise ValueError("Batch1 definition/result IDs drifted from P1-P8")
    if prevalence1.get("equivalence_margin") != DELTA or prevalence1.get("ordered_observable_companies") != {"FAILURE": 232, "SUCCESS": 269}:
        raise ValueError("Batch1 reviewed cohort or delta drifted")
    for candidate_id in sorted(EXPECTED_BATCH1):
        definition, row = definitions[candidate_id], rows1[candidate_id]
        expected = EXPECTED_BATCH1[candidate_id]
        if row.get("result") != expected or classify_result(row["ate_success"], row["p_value"], row["p_tost"]) != expected:
            raise ValueError(f"Batch1 reviewed Result drifted for {candidate_id}")
        entry = _registered_entry(
            candidate_id=candidate_id, batch="batch1", name=definition["name"],
            meaning=f'{definition["name"]}: {definition["description"]}', rule=definition["rule"],
            result=expected, row=row, chain_count=None, company_mask=None,
            attributes={"source_track": None, "event_dimension": None, "structural_dimension": None,
                        "patterns_with_same_company_mask": None}, review_status="FROZEN_REVIEWED",
            sources=[
                _source(paths["batch1_config"], hashes["batch1_config"],
                        f"/patterns/{definition_indexes[candidate_id]}", "definition"),
                _source(paths["batch1_prevalence"], hashes["batch1_prevalence"],
                        f"/pattern_average_action_value/{row1_indexes[candidate_id]}", "statistics"),
            ],
        )
        buckets[expected]["batch1"].append(entry)
    p6_definition, p6_row = definitions["P6"], rows1["P6"]
    if p6_definition.get("evaluation_status") != "NOT_EVALUABLE" or p6_definition.get("rule") is not None or p6_row.get("result") is not None:
        raise ValueError("Batch1 P6 no longer has the expected non-evaluable contract")
    p6 = {
        "id": "P6", "batch": "batch1", "name": p6_definition["name"],
        "meaning": f'{p6_definition["name"]}: {p6_definition["description"]}',
        "rule": None, "rule_sha256": None, "result": None, "support": None, "stats": None,
        "company_mask": None,
        "attributes": {"source_evaluation_status": "NOT_EVALUABLE",
                       "registry_status": "NOT_OBSERVABLE",
                       "mapping_reason": p6_definition["not_evaluable_reason"]},
        "inference": _inference("FROZEN_REVIEWED"),
        "source": _sorted_sources([
            _source(paths["batch1_config"], hashes["batch1_config"],
                    f'/patterns/{definition_indexes["P6"]}', "definition"),
            _source(paths["batch1_prevalence"], hashes["batch1_prevalence"],
                    f'/pattern_average_action_value/{row1_indexes["P6"]}', "status"),
        ]),
    }

    # Batch 2: exactly the five IDs parsed from the frozen report shortlist.
    review2 = parse_batch2_review(paths["report"])
    freeze2 = load_json(paths["batch2_freeze"])
    lattice2 = load_json(paths["batch2_lattice"])
    prevalence2 = load_json(paths["batch2_prevalence"])
    definitions2, definition2_indexes = _index_unique(freeze2.get("patterns", []), "id", "Batch2 definition")
    atoms2, atom2_indexes = _index_unique(lattice2.get("atoms", []), "id", "Batch2 lattice atom")
    rows2, row2_indexes = _index_unique(prevalence2.get("pattern_average_action_value", []), "pattern", "Batch2 result")
    if len(rows2) != 3647 or prevalence2.get("equivalence_margin") != DELTA:
        raise ValueError("Batch2 evaluation count or delta drifted")
    evaluation_counts = Counter(row.get("result") or "INSUFFICIENT_COHORT_DATA" for row in rows2.values())
    if evaluation_counts != Counter({"RECOMMEND": 1619, "AVOID": 16, "INCONCLUSIVE": 2008,
                                     "INSUFFICIENT_COHORT_DATA": 4}):
        raise ValueError(f"Batch2 evaluation distribution drifted: {evaluation_counts}")
    for candidate_id, reviewed_name in sorted(review2["shortlist"].items()):
        definition = definitions2.get(candidate_id)
        atom = atoms2.get(candidate_id)
        row = rows2.get(candidate_id)
        if definition is None or atom is None or row is None:
            raise ValueError(f"Batch2 shortlist join failed: {candidate_id}")
        if definition.get("rule") != atom.get("rule") or row.get("result") != "RECOMMEND":
            raise ValueError(f"Batch2 rule/result join disagrees: {candidate_id}")
        if row.get("taking_total") != atom.get("company_support"):
            raise ValueError(f"Batch2 support join disagrees: {candidate_id}")
        meaning = render_human_rule(definition["rule"])
        meaning = meaning[0].upper() + meaning[1:] + "."
        entry = _registered_entry(
            candidate_id=candidate_id, batch="batch2", name=reviewed_name, meaning=meaning,
            rule=definition["rule"], result="RECOMMEND", row=row,
            chain_count=atom["chain_support"], company_mask=_mask(atom["company_mask_sha256"], atom["company_support"]),
            attributes={"source_track": None, "event_dimension": atom.get("group"),
                        "structural_dimension": None, "patterns_with_same_company_mask": None},
            review_status="MEANINGFUL_SHORTLIST",
            sources=[
                _source(paths["report"], hashes["report"], f"§4.8.1.2/shortlist/{candidate_id}", "selection"),
                _source(paths["batch2_freeze"], hashes["batch2_freeze"],
                        f"/patterns/{definition2_indexes[candidate_id]}", "definition"),
                _source(paths["batch2_lattice"], hashes["batch2_lattice"],
                        f"/atoms/{atom2_indexes[candidate_id]}", "support_and_mask"),
                _source(paths["batch2_prevalence"], hashes["batch2_prevalence"],
                        f"/pattern_average_action_value/{row2_indexes[candidate_id]}", "statistics"),
            ],
        )
        buckets["RECOMMEND"]["batch2"].append(entry)

    excluded = {
        "batch2": {
            "evaluated_total": 3647, "registered_total": 5, "excluded_total": 3642,
            "by_evaluation_result": {
                "other_RECOMMEND": 1614, "AVOID": 16, "INCONCLUSIVE": 2008,
                "INSUFFICIENT_COHORT_DATA": 4,
            },
            "other_recommend_reasons": review2["other_recommend_reasons"],
            "avoid_reasons": review2["avoid_reasons"],
            "source": _sorted_sources([
                _source(paths["report"], hashes["report"], "§4.8.1.2/excluded-accounting", "selection"),
                _source(paths["batch2_freeze"], hashes["batch2_freeze"], "/patterns", "definition"),
                _source(paths["batch2_prevalence"], hashes["batch2_prevalence"],
                        "/pattern_average_action_value", "statistics"),
            ]),
        }
    }

    # Batch 3: the review artifact is the sole selection source; never revisit frozen pairs.
    review3 = load_json(paths["batch3_review"])
    matrix = review3.get("three_step_matrix")
    if not isinstance(matrix, list) or tuple(row.get("Total") for row in matrix) != EXPECTED_BATCH3_MATRIX:
        raise ValueError("Batch3 three-step matrix drifted")
    if sha256_value(review3.get("policy")) != review3.get("policy_sha256"):
        raise ValueError("Batch3 review policy hash disagrees")
    final3 = review3.get("final_candidates")
    if not isinstance(final3, list) or len(final3) != 23296:
        raise ValueError("Batch3 review must contain exactly 23,296 final candidates")
    candidates3, candidate3_indexes = _index_unique(final3, "candidate_id", "Batch3 final candidate")
    if len(candidates3) != len(final3):
        raise AssertionError("unreachable duplicate Batch3 candidate")
    for candidate_id in sorted(candidates3):
        candidate = candidates3[candidate_id]
        row = candidate.get("statistics")
        rule, result, meaning = candidate.get("rule"), candidate.get("result"), candidate.get("pattern")
        if not isinstance(rule, dict) or result not in RESULTS or not isinstance(meaning, str) or not meaning.strip():
            raise ValueError(f"invalid Batch3 final candidate: {candidate_id}")
        if classify_result(row["ate_success"], row["p_value"], row["p_tost"]) != result:
            raise ValueError(f"Batch3 Result disagrees with statistics: {candidate_id}")
        entry = _registered_entry(
            candidate_id=candidate_id, batch="batch3", name=None, meaning=meaning, rule=rule,
            result=result, row=row, chain_count=row["chain_support"],
            company_mask=_mask(candidate["company_mask_sha256"], row["company_support"]),
            attributes={"source_track": candidate.get("source_track"),
                        "event_dimension": candidate.get("event_dimension"),
                        "structural_dimension": candidate.get("structural_dimension"),
                        "patterns_with_same_company_mask": candidate.get("patterns_with_same_company_mask")},
            review_status="THREE_STEP_FINAL", batch3=True,
            sources=[_source(paths["batch3_review"], hashes["batch3_review"],
                             f"/final_candidates/{candidate3_indexes[candidate_id]}", "selection_definition_statistics_mask")],
        )
        buckets[result]["batch3"].append(entry)

    for result in RESULTS:
        for batch in BATCHES:
            buckets[result][batch].sort(key=lambda item: item["id"])

    by_result = {}
    by_batch = {batch: 0 for batch in BATCHES}
    for result in RESULTS:
        row_counts = {batch: len(buckets[result][batch]) for batch in BATCHES}
        for batch, count in row_counts.items():
            by_batch[batch] += count
        by_result[result] = {**row_counts, "total": sum(row_counts.values())}
    counts = {
        "by_result": by_result, "registered_by_batch": by_batch,
        "registered_total": sum(by_batch.values()),
        "special_statuses": {"NOT_OBSERVABLE": {"batch1": 1, "batch2": 0, "batch3": 0, "total": 1}},
        "excluded": {"batch2": 3642},
    }

    artifact_roles = {
        "batch1_config": "batch1_definition", "batch1_prevalence": "batch1_reviewed_statistics",
        "batch2_freeze": "batch2_definition", "batch2_lattice": "batch2_support_and_mask",
        "batch2_prevalence": "batch2_statistics", "batch3_review": "batch3_reviewed_selection",
        "report": "batch2_human_review_freeze", "generator": "registry_generator", "schema": "registry_specification",
    }
    artifacts = [
        {"name": name, "path": str(paths[name]), "sha256": hashes[name],
         "bytes": paths[name].stat().st_size, "role": artifact_roles[name]}
        for name in sorted(paths)
    ]
    snapshot_match = re.search(r"(20260827T1654)", paths["batch3_review"].name)
    registry = {
        "registry_schema_version": "1.0", "registry_id": "chip_pattern_registry_v1",
        "source_snapshot": snapshot_match.group(1) if snapshot_match else paths["batch3_review"].stem,
        "result_order": list(RESULTS), "batch_order": list(BATCHES),
        "result_buckets": buckets,
        "special_statuses": {"NOT_OBSERVABLE": {"batch1": [p6], "batch2": [], "batch3": []}},
        "excluded_summary": excluded,
        "counts": counts,
        "batch_summaries": {
            "batch1": {"reviewed_patterns": 8, "registered": 7, "not_observable": 1},
            "batch2": {"evaluated": 3647, "registered": 5, "excluded": 3642},
            "batch3": {"three_step_matrix": matrix,
                       "audit_appendix": review3.get("audit_appendix")},
        },
        "provenance": {
            "artifacts": artifacts,
            "batch3_policy_sha256": review3["policy_sha256"],
            "batch3_review_input_sha256": review3.get("input_sha256"),
            "statistical_method": {
                "estimand": "SUCCESS_RISK_DIFFERENCE",
                "difference_test": "pooled two-proportion z test",
                "equivalence_test": "TOST", "alpha": ALPHA, "equivalence_margin": DELTA,
                "observable_cohort": {"SUCCESS": 269, "FAILURE": 232, "total": 501},
            },
            "canonical_encoding": {
                "charset": "UTF-8", "sort_keys": True, "ensure_ascii": False,
                "allow_nan": False, "separators": [",", ":"], "trailing_lf": True,
            },
            "determinism": "No timestamp, random value, directory discovery, or output-path field is recorded.",
        },
    }
    validate_registry(registry, expected_counts=EXPECTED_COUNTS if enforce_production_counts else None,
                      verify_artifacts=True)
    return registry


def _assert_finite(value: Any, path: str = "$") -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"non-finite number at {path}")
    if isinstance(value, dict):
        for key, child in value.items():
            _assert_finite(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_finite(child, f"{path}[{index}]")


def validate_registry(registry: dict[str, Any], *, expected_counts: dict[str, dict[str, int]] | None = EXPECTED_COUNTS,
                      verify_artifacts: bool = False) -> None:
    _assert_finite(registry)
    if registry.get("result_order") != list(RESULTS) or registry.get("batch_order") != list(BATCHES):
        raise ValueError("registry order contract drifted")
    buckets = registry.get("result_buckets")
    if not isinstance(buckets, dict) or set(buckets) != set(RESULTS) or len(buckets) != len(RESULTS):
        raise ValueError("result_buckets must contain exactly the four Results")
    seen: set[str] = set()
    calculated = {result: {} for result in RESULTS}
    artifact_catalog = {item["path"]: item for item in registry.get("provenance", {}).get("artifacts", [])}
    if len(artifact_catalog) != len(registry.get("provenance", {}).get("artifacts", [])):
        raise ValueError("duplicate provenance artifact path")
    for result in RESULTS:
        if (not isinstance(buckets[result], dict) or set(buckets[result]) != set(BATCHES)
                or len(buckets[result]) != len(BATCHES)):
            raise ValueError(f"{result} must contain exactly batch1/batch2/batch3")
        for batch in BATCHES:
            entries = buckets[result][batch]
            if [entry.get("id") for entry in entries] != sorted(entry.get("id") for entry in entries):
                raise ValueError(f"{result}/{batch} entries are not sorted by ID")
            calculated[result][batch] = len(entries)
            for entry in entries:
                candidate_id = entry.get("id")
                if not isinstance(candidate_id, str) or candidate_id in seen:
                    raise ValueError(f"invalid or duplicate registry ID: {candidate_id}")
                seen.add(candidate_id)
                if entry.get("batch") != batch or entry.get("result") != result:
                    raise ValueError(f"entry bucket mismatch: {candidate_id}")
                if not isinstance(entry.get("meaning"), str) or not entry["meaning"].strip():
                    raise ValueError(f"entry has no human meaning: {candidate_id}")
                rule = entry.get("rule")
                if not isinstance(rule, dict) or entry.get("rule_sha256") != sha256_value(rule):
                    raise ValueError(f"entry rule/hash is invalid: {candidate_id}")
                support, stats = entry.get("support"), entry.get("stats")
                if not isinstance(support, dict) or not isinstance(stats, dict):
                    raise ValueError(f"entry support/stats is invalid: {candidate_id}")
                table = support.get("contingency_2x2", {})
                taking, control = table.get("taking", {}), table.get("not_taking", {})
                for arm_name, arm in (("taking", taking), ("not_taking", control)):
                    if arm.get("SUCCESS") + arm.get("FAILURE") != arm.get("total"):
                        raise ValueError(f"2x2 {arm_name} does not conserve: {candidate_id}")
                if taking.get("total") + control.get("total") != 501:
                    raise ValueError(f"2x2 cohort is not 501: {candidate_id}")
                if support.get("company_count") != taking.get("total"):
                    raise ValueError(f"company support disagrees with taking total: {candidate_id}")
                chain_count = support.get("chain_count")
                if chain_count is not None and chain_count < support["company_count"]:
                    raise ValueError(f"chain support is below company support: {candidate_id}")
                expected_ate = taking["SUCCESS"] / taking["total"] - control["SUCCESS"] / control["total"]
                if not math.isclose(stats.get("ate"), expected_ate, rel_tol=0.0, abs_tol=1e-15):
                    raise ValueError(f"ATE disagrees with 2x2: {candidate_id}")
                for key in ("p", "p_tost"):
                    number = stats.get(key)
                    if not isinstance(number, (int, float)) or isinstance(number, bool) or not math.isfinite(number) or not 0 <= number <= 1:
                        raise ValueError(f"invalid {key}: {candidate_id}")
                if classify_result(stats["ate"], stats["p"], stats["p_tost"]) != result:
                    raise ValueError(f"statistics classify outside bucket: {candidate_id}")
                mask = entry.get("company_mask")
                if mask is not None:
                    if not re.fullmatch(r"[0-9a-f]{64}", str(mask.get("sha256"))):
                        raise ValueError(f"invalid company mask hash: {candidate_id}")
                    if mask.get("member_count") < support["company_count"] or mask.get("universe_size") != 663:
                        raise ValueError(f"company mask metadata disagrees: {candidate_id}")
                for source in entry.get("source", []):
                    artifact = artifact_catalog.get(source.get("artifact_path"))
                    if artifact is None or artifact.get("sha256") != source.get("artifact_sha256"):
                        raise ValueError(f"source is absent from artifact catalog: {candidate_id}")
    if expected_counts is not None and calculated != expected_counts:
        raise ValueError(f"registered Result counts drifted: {calculated}")

    special = registry.get("special_statuses")
    if not isinstance(special, dict) or set(special) != {"NOT_OBSERVABLE"} or len(special) != 1:
        raise ValueError("special_statuses must contain exactly NOT_OBSERVABLE")
    if (set(special["NOT_OBSERVABLE"]) != set(BATCHES)
            or len(special["NOT_OBSERVABLE"]) != len(BATCHES)):
        raise ValueError("NOT_OBSERVABLE must contain exactly the three batches")
    special_entries = sum((special["NOT_OBSERVABLE"][batch] for batch in BATCHES), [])
    if len(special_entries) != 1 or special_entries[0].get("id") != "P6":
        raise ValueError("P6 must be the sole NOT_OBSERVABLE entry")
    p6 = special_entries[0]
    if any(p6.get(key) is not None for key in ("rule", "rule_sha256", "result", "support", "stats", "company_mask")):
        raise ValueError("P6 rule/result/support/stats/mask must all be null")
    if "P6" in seen:
        raise ValueError("P6 must not appear in a Result bucket")
    seen.add("P6")

    excluded = registry.get("excluded_summary", {}).get("batch2", {})
    by_evaluation = excluded.get("by_evaluation_result", {})
    if sum(by_evaluation.values()) != excluded.get("excluded_total") or excluded.get("registered_total") + excluded.get("excluded_total") != excluded.get("evaluated_total"):
        raise ValueError("Batch2 excluded totals do not conserve")
    if sum(excluded.get("other_recommend_reasons", {}).values()) != by_evaluation.get("other_RECOMMEND"):
        raise ValueError("Batch2 RECOMMEND exclusion reasons do not conserve")
    if sum(excluded.get("avoid_reasons", {}).values()) != by_evaluation.get("AVOID"):
        raise ValueError("Batch2 AVOID exclusion reasons do not conserve")

    counts = registry.get("counts", {})
    expected_by_result = {result: {**calculated[result], "total": sum(calculated[result].values())} for result in RESULTS}
    expected_by_batch = {batch: sum(calculated[result][batch] for result in RESULTS) for batch in BATCHES}
    if counts.get("by_result") != expected_by_result or counts.get("registered_by_batch") != expected_by_batch or counts.get("registered_total") != sum(expected_by_batch.values()):
        raise ValueError("top-level registered counts do not conserve")
    matrix = registry.get("batch_summaries", {}).get("batch3", {}).get("three_step_matrix", [])
    if expected_counts is not None and tuple(row.get("Total") for row in matrix) != EXPECTED_BATCH3_MATRIX:
        raise ValueError("Batch3 matrix does not conserve expected stages")
    if verify_artifacts:
        for artifact in artifact_catalog.values():
            path = Path(artifact["path"])
            if not path.is_file() or sha256_file(path) != artifact["sha256"] or path.stat().st_size != artifact["bytes"]:
                raise ValueError(f"provenance artifact changed or disappeared: {path}")


def write_registry(registry: dict[str, Any], output_path: Path) -> None:
    output_path = output_path.expanduser().resolve()
    tmp_root = Path("/tmp").resolve()
    if output_path != tmp_root and tmp_root not in output_path.parents:
        raise ValueError("registry output must be under /tmp")
    if output_path.exists():
        raise ValueError(f"refusing to overwrite existing registry: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_bytes(registry)
    fd, temp_name = tempfile.mkstemp(prefix=output_path.name + ".tmp-", dir=output_path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, output_path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--output", type=Path, required=True, help="new canonical JSON path")
    result.add_argument("--batch1-config", type=Path, required=True)
    result.add_argument("--batch1-prevalence", type=Path, required=True)
    result.add_argument("--batch2-freeze-dir", type=Path, required=True)
    result.add_argument("--batch2-prevalence", type=Path, required=True)
    result.add_argument("--batch3-review", type=Path, required=True)
    result.add_argument("--report", type=Path, required=True)
    result.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    return result


def main() -> None:
    args = parser().parse_args()
    registry = build_registry(batch1_config=args.batch1_config,
                              batch1_prevalence=args.batch1_prevalence,
                              batch2_freeze_dir=args.batch2_freeze_dir,
                              batch2_prevalence=args.batch2_prevalence,
                              batch3_review=args.batch3_review,
                              report_path=args.report, schema_path=args.schema)
    write_registry(registry, args.output)
    payload_hash = hashlib.sha256(canonical_bytes(registry)).hexdigest()
    print(json.dumps({"output": str(args.output.resolve()), "sha256": payload_hash,
                      "registered_total": registry["counts"]["registered_total"]}, sort_keys=True))


if __name__ == "__main__":
    main()
