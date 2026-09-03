#!/usr/bin/env python3
"""Post-freeze audit/evaluation for label-blind Pattern candidate lattices.

`coverage` is the only path that loads a reference Pattern configuration.  It
materializes canonical candidates from the already-frozen atom lattice and
requires exact Chain and observable-company exposure equality.  `evaluate`
reads labels only after freeze and computes company-level action-value counts.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import pyarrow.parquet as pq

import analyze_patterns as engine
import generate_pattern_candidates as generator
import generate_pattern_candidates_extended as extended_generator

RESEARCH_ROOT = Path(__file__).resolve().parent.parent


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load JSON {path}: {exc}") from exc


def load_freeze(freeze_dir: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    freeze_dir = freeze_dir.expanduser().resolve()
    is_extended = (freeze_dir / "generation_manifest.json").is_file()
    if is_extended:
        manifest_path = freeze_dir / "generation_manifest.json"
        lattice_path = freeze_dir / "candidate_pairs.json"
        frozen_path = freeze_dir / "pattern_candidates_extended_frozen.json"
    else:
        manifest_path = freeze_dir / "generation_manifest_v0.1.json"
        lattice_path = freeze_dir / "candidate_lattice_v0.1.json"
        frozen_path = freeze_dir / "pattern_candidates_frozen.json"
    manifest, lattice, frozen = map(_load_json, (manifest_path, lattice_path, frozen_path))
    for name, metadata in manifest.get("outputs", {}).items():
        path = freeze_dir / name
        if not path.is_file():
            raise ValueError(f"freeze integrity failure: missing {name}")
        actual = engine.sha256_file(path)
        if actual != metadata["sha256"]:
            raise ValueError(f"freeze integrity failure for {name}: {actual} != {metadata['sha256']}")
    if manifest["phase"] != "LABEL_BLIND_GENERATION_FREEZE":
        raise ValueError("input is not a label-blind generation freeze")
    expected = (extended_generator.COMPLETENESS_STATUS if is_extended else "COMPLETE_BOUNDED_COMPACT_GRAMMAR")
    if manifest["completeness"]["status"] != expected:
        raise ValueError("cannot audit an incomplete candidate grammar")
    return manifest, lattice, frozen


def read_contexts(manifest: dict[str, Any], *, with_labels: bool) -> tuple[list[engine.ChainRuleContext], list[str], list[str | None]]:
    paths = {name: Path(item["path"]) for name, item in manifest["inputs"].items()}
    is_extended = manifest.get("generation_version") == "extended_v1"
    source = extended_generator if is_extended else generator
    for name, path in paths.items():
        if is_extended:
            columns = manifest["inputs"][name]["projected_columns"]
            actual = extended_generator.digest_json(pq.read_table(path, columns=columns).to_pylist())
        else:
            actual = engine.sha256_file(path)
        if actual != manifest["inputs"][name]["sha256"]:
            raise ValueError(f"post-freeze input hash drift for {name}: {actual}")
    chain_columns = list(source.SAFE_CHAIN_COLUMNS if is_extended else source.CHAIN_COLUMNS)
    label_field = "company_label_v4"
    if with_labels:
        chain_columns.append(label_field)
    chains = pq.read_table(paths["chains"], columns=chain_columns).to_pylist()
    exposure_columns = source.SAFE_EXPOSURE_COLUMNS if is_extended else source.EXPOSURE_COLUMNS
    interface_columns = source.SAFE_INTERFACE_COLUMNS if is_extended else source.INTERFACE_COLUMNS
    exposure = pq.read_table(paths["exposure"], columns=list(exposure_columns)).to_pylist()
    interface = pq.read_table(paths["interface"], columns=list(interface_columns)).to_pylist()
    lookup = engine.build_event_lookup(exposure, interface)
    ordered = sorted(chains, key=lambda row: (row["company_id"], row["chain_sequence"], row["chain_id"]))
    contexts = engine.build_chain_rule_contexts(ordered, lookup) if is_extended else [
        engine.build_chain_rule_context(row, lookup) for row in ordered
    ]
    companies = [row["company_id"] for row in ordered]
    labels = [row.get(label_field) for row in ordered]
    return contexts, companies, labels


def leaves(rule: dict[str, Any]) -> Iterable[dict[str, Any]]:
    if rule["op"] in {"all", "any"}:
        for child in rule["rules"]:
            yield from leaves(child)
    else:
        yield rule


def validate_materializable(rule: dict[str, Any], lattice: dict[str, Any]) -> list[str]:
    bounds = lattice["grammar"]
    op = rule["op"]
    if op == "any":
        if not 2 <= len(rule["rules"]) <= bounds["max_disjuncts"]:
            raise ValueError(f"ANY arity outside frozen bound: {len(rule['rules'])}")
        if any(child["op"] in {"all", "any"} for child in rule["rules"]):
            raise ValueError("frozen grammar permits only atoms inside ANY")
    elif op == "all":
        if not 2 <= len(rule["rules"]) <= bounds["max_conjuncts"]:
            raise ValueError(f"ALL arity outside frozen bound: {len(rule['rules'])}")
        any_children = [child for child in rule["rules"] if child["op"] == "any"]
        if len(any_children) > 1:
            raise ValueError("frozen grammar permits at most one ANY child")
        for child in rule["rules"]:
            if child["op"] == "all":
                raise ValueError("nested ALL must be flattened")
            if child["op"] == "any":
                validate_materializable(child, lattice)
    atom_ids = {item["id"] for item in lattice["atoms"]}
    missing = []
    result = []
    for atom in leaves(rule):
        atom_id = generator.candidate_id(atom)
        result.append(atom_id)
        if atom_id not in atom_ids:
            missing.append(atom_id)
    if missing:
        raise ValueError(f"formula requires atoms absent after freeze/support pruning: {sorted(set(missing))}")
    return sorted(set(result))


def bits_for(rule: dict[str, Any], contexts: list[engine.ChainRuleContext]) -> int:
    return generator.rule_mask(rule, contexts)


def observable_company_bits(chain_bits: int, contexts: list[engine.ChainRuleContext], companies: list[str]) -> tuple[int, list[str]]:
    observable = sorted({
        company for company, context in zip(companies, contexts)
        if context.chain["chain_window_status"] == "dated"
    })
    index = {company: position for position, company in enumerate(observable)}
    result = 0
    for chain_index, (company, context) in enumerate(zip(companies, contexts)):
        if context.chain["chain_window_status"] == "dated" and chain_bits & (1 << chain_index):
            result |= 1 << index[company]
    return result, observable


def coverage(args: argparse.Namespace) -> dict[str, Any]:
    freeze_dir = Path(args.freeze_dir)
    manifest, lattice, frozen = load_freeze(freeze_dir)
    contexts, companies, _ = read_contexts(manifest, with_labels=False)
    # Deliberately occurs only after freeze and integrity/input-hash validation.
    reference_path = Path(args.reference_config).expanduser().resolve()
    reference = engine.load_pattern_config(reference_path)

    frozen_rules = [item["rule"] for item in frozen["patterns"]]
    frozen_masks: dict[int, list[str]] = {}
    for item, rule in zip(frozen["patterns"], frozen_rules):
        frozen_masks.setdefault(bits_for(rule, contexts), []).append(item["id"])

    rows = []
    all_pass = True
    for definition in reference.patterns:
        if definition.evaluation_status != "EVALUABLE" or definition.rule is None:
            rows.append({
                "reference_pattern": definition.id, "status": "NOT_OBSERVABLE",
                "reason": definition.not_evaluable_reason, "candidate_id": None,
                "chain_exact": None, "company_exact": None,
            })
            continue
        canonical = generator.canonical_rule(definition.rule)
        try:
            atom_ids = validate_materializable(canonical, lattice)
            candidate_bits = bits_for(canonical, contexts)
            reference_bits = bits_for(definition.rule, contexts)
            candidate_companies, observable = observable_company_bits(candidate_bits, contexts, companies)
            reference_companies, _ = observable_company_bits(reference_bits, contexts, companies)
            chain_exact = candidate_bits == reference_bits
            company_exact = candidate_companies == reference_companies
            status = "RECOVERED_EXACT" if chain_exact and company_exact else "MISMATCH"
            equivalent = sorted(frozen_masks.get(reference_bits, []))
            row = {
                "reference_pattern": definition.id, "status": status,
                "candidate_id": generator.candidate_id(canonical),
                "candidate_rule": canonical, "atom_ids": atom_ids,
                "materialization": "post_freeze_from_frozen_atom_lattice",
                "chain_exact": chain_exact, "company_exact": company_exact,
                "reference_chain_support": reference_bits.bit_count(),
                "candidate_chain_support": candidate_bits.bit_count(),
                "reference_company_support": reference_companies.bit_count(),
                "candidate_company_support": candidate_companies.bit_count(),
                "observable_company_count": len(observable),
                "eager_frozen_mask_equivalent_ids": equivalent,
                "eager_frozen_mask_equivalent_count": len(equivalent),
            }
        except ValueError as exc:
            status = "NOT_RECOVERED"
            row = {
                "reference_pattern": definition.id, "status": status,
                "error": str(exc), "candidate_id": None,
                "chain_exact": False, "company_exact": False,
            }
        all_pass &= status == "RECOVERED_EXACT"
        rows.append(row)
    result = {
        "audit_version": "v0.1", "mode": "POST_FREEZE_REFERENCE_COVERAGE",
        "freeze_dir": str(freeze_dir.expanduser().resolve()),
        "freeze_manifest_sha256": engine.sha256_file(freeze_dir / "generation_manifest_v0.1.json"),
        "reference_config": {"path": str(reference_path), "sha256": reference.sha256},
        "criterion": "exact equality of Chain mask and dated/evaluable-company-any mask",
        "result": "PASS" if all_pass else "FAIL", "patterns": rows,
        "counts": dict(Counter(row["status"] for row in rows)),
    }
    write_result(result, Path(args.output), coverage_markdown(result) if args.markdown else None)
    if not all_pass:
        raise ValueError("coverage audit failed; see output")
    return result


def coverage_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Pattern Candidate Post-Freeze Coverage Audit", "",
        f"Result: **{result['result']}**", "",
        "Criterion: exact Chain exposure and dated/evaluable-company-any exposure masks.", "",
        "| Reference | Status | Candidate | Chain support | Company support |",
        "|---|---|---|---:|---:|",
    ]
    for row in result["patterns"]:
        if row["status"] == "NOT_OBSERVABLE":
            lines.append(f"| {row['reference_pattern']} | NOT_OBSERVABLE | — | — | — |")
        else:
            lines.append(
                f"| {row['reference_pattern']} | {row['status']} | `{row.get('candidate_id') or '—'}` | "
                f"{row.get('candidate_chain_support', '—')} | {row.get('candidate_company_support', '—')} |"
            )
    lines += ["", "Materialized candidates are audit outputs; the label-blind freeze is not modified.", ""]
    return "\n".join(lines)


def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    if abs(float(args.delta) - 0.05) > 1e-12:
        raise ValueError("Batch3 evaluation requires --delta .05")
    freeze_dir = Path(args.freeze_dir).expanduser().resolve()
    manifest, lattice, frozen = load_freeze(freeze_dir)
    pair_metadata = {item["id"]: item for item in lattice["pairs"]}
    if manifest.get("generation_version") != "extended_v1":
        raise ValueError("evaluate is reserved for the Batch3 explicit pair freeze")
    contexts, companies, labels = read_contexts(manifest, with_labels=True)
    company_labels: dict[str, str] = {}
    observable = set()
    for company, label, context in zip(companies, labels, contexts):
        if company in company_labels and company_labels[company] != label:
            raise ValueError(f"inconsistent label for {company}")
        company_labels[company] = str(label)
        if context.chain["chain_window_status"] == "dated":
            observable.add(company)
    cohort = sorted(company for company in observable if company_labels[company] in {"SUCCESS", "FAILURE"})
    cohort_set = set(cohort)
    success_total = sum(company_labels[company] == "SUCCESS" for company in cohort)
    failure_total = len(cohort) - success_total
    representative_payload = _load_json(freeze_dir / "candidate_representatives.json")
    candidate_to_rep = representative_payload["candidate_to_representative"]
    definitions = {item["id"]: item for item in frozen["patterns"]}
    representative_ids = sorted(set(candidate_to_rep.values()))
    metrics_by_rep: dict[str, dict[str, Any]] = {}
    for representative_id in representative_ids:
        definition = definitions[representative_id]
        chain_bits = bits_for(definition["rule"], contexts)
        taking = {
            company for index, (company, context) in enumerate(zip(companies, contexts))
            if context.chain["chain_window_status"] == "dated" and chain_bits & (1 << index)
        } & cohort_set
        st = sum(company_labels[company] == "SUCCESS" for company in taking)
        ft = len(taking) - st
        sn, fn = success_total - st, failure_total - ft
        base: dict[str, Any] = {
            "status": "INSUFFICIENT_PATTERN_SUPPORT" if not taking or len(taking) == len(cohort) else "EVALUATED",
            "taking_success": st, "taking_failure": ft, "taking_total": len(taking),
            "not_taking_success": sn, "not_taking_failure": fn, "not_taking_total": len(cohort) - len(taking),
        }
        if base["status"] == "EVALUATED":
            base.update(engine.action_value_metrics(st, ft, sn, fn, 0.05))
        metrics_by_rep[representative_id] = base
    rows = []
    for definition in frozen["patterns"]:
        candidate_id = definition["id"]
        representative_id = candidate_to_rep[candidate_id]
        track = definition["track"]
        row = {
            "candidate_id": candidate_id, "track": track, "representative_id": representative_id,
            "evaluation_basis": "COMPANY_MASK_EQUIVALENT",
            "chain_support": pair_metadata[candidate_id]["chain_support"],
            "company_support": pair_metadata[candidate_id]["company_support"],
            **metrics_by_rep[representative_id],
        }
        if track == extended_generator.DESCRIPTIVE_ASSOCIATION:
            action_result = row.pop("result", None)
            row["result"] = {
                "RECOMMEND": "POSITIVE_ASSOCIATION", "AVOID": "NEGATIVE_ASSOCIATION",
                "INDIFFERENT": "EQUIVALENT_WITHIN_DELTA", "INCONCLUSIVE": "INCONCLUSIVE",
                None: None,
            }[action_result]
            row["causal_interpretation_allowed"] = False
        else:
            if row.get("result") == "INDIFFERENT":
                row["result"] = "INCONCLUSIVE"
            row["causal_interpretation_allowed"] = True
        rows.append(row)
    if len(rows) != len(frozen["patterns"]) or len({row["candidate_id"] for row in rows}) != len(rows):
        raise AssertionError("every frozen Batch3 pair must receive exactly one evaluation row")
    result_counts = Counter(row.get("result") or row["status"] for row in rows)
    result = {
        "evaluation_version": "extended_v1", "mode": "POST_FREEZE_LABELLED_TWO_TRACK_EVALUATION",
        "delta": 0.05, "statistics_algorithm": "two_proportion_chi_square_plus_TOST_v1",
        "candidate_count": len(rows), "representative_computations": len(metrics_by_rep),
        "observable_cohort": {"SUCCESS": success_total, "FAILURE": failure_total},
        "result_counts": dict(sorted(result_counts.items())), "candidates": rows,
    }
    write_result(result, Path(args.output), None)
    return result


def write_result(result: dict[str, Any], path: Path, markdown: str | None) -> None:
    path = path.expanduser().resolve()
    if path.exists():
        raise ValueError(f"refusing to overwrite output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=path.stem + ".tmp-", dir=path.parent))
    try:
        temp_json = temp_dir / path.name
        temp_json.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n")
        if markdown is not None:
            (temp_dir / (path.stem + ".md")).write_text(markdown)
        os.replace(temp_json, path)
        if markdown is not None:
            os.replace(temp_dir / (path.stem + ".md"), path.with_suffix(".md"))
        temp_dir.rmdir()
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="command", required=True)
    audit = sub.add_parser("coverage", help="load reference rules only after freeze and verify exact masks")
    audit.add_argument("--freeze-dir", required=True)
    audit.add_argument("--reference-config", required=True)
    audit.add_argument("--output", required=True)
    audit.add_argument("--markdown", action="store_true")
    scoring = sub.add_parser("evaluate", help="evaluate eager frozen candidates using labels after freeze")
    scoring.add_argument("--freeze-dir", required=True)
    scoring.add_argument("--delta", type=float, default=0.05)
    scoring.add_argument("--output", required=True)
    return result


def main() -> None:
    args = parser().parse_args()
    try:
        if args.command == "coverage":
            result = coverage(args)
            print(json.dumps({"result": result["result"], "counts": result["counts"]}, sort_keys=True))
        else:
            if not 0.0 < args.delta < 1.0:
                raise ValueError("--delta must be between zero and one")
            result = evaluate(args)
            print(json.dumps({"candidate_count": result["candidate_count"], "result_counts": result["result_counts"]}, sort_keys=True))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
