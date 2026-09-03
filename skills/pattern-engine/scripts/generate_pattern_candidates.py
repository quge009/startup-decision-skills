#!/usr/bin/env python3
"""Freeze a label-blind, bounded event-chain Pattern candidate lattice.

Only explicitly projected, unlabeled Chain/event columns and schema contracts are
read.  The output is a compact finite grammar: supported atoms are explicit and
bounded canonical compositions are represented implicitly.  Use the separate
coverage-audit tool to load any reference Pattern configuration after freeze.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import platform
import shutil
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

import pyarrow as pa
import pyarrow.parquet as pq

import analyze_patterns as engine

RESEARCH_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CHAIN_SCHEMA = RESEARCH_ROOT / "schemas/chains_v0.3.schema.json"
DEFAULT_EXPOSURE_SCHEMA = RESEARCH_ROOT / "schemas/exposure_events_v0.3.schema.json"
DEFAULT_INTERFACE_SCHEMA = RESEARCH_ROOT / "schemas/interface_events_v0.3.schema.json"

CHAIN_COLUMNS = (
    "chain_id", "company_id", "chain_sequence", "chain_start_date",
    "chain_start_reason", "chain_end_date", "chain_window_status",
    "outcome_type", "outcome_round", "outcome_event_ids",
    "n_participant_rows", "investor_ids", "investor_raw_names",
    "n_exposure", "exposure_event_ids", "exposure_types", "n_interface",
    "interface_event_ids", "interface_types",
)
EXPOSURE_COLUMNS = ("event_id", "company_id", "event_date", "event_type", "event_subtype")
INTERFACE_COLUMNS = EXPOSURE_COLUMNS + ("investor_id", "raw_investor_name")
NUMERIC_FIELDS = (
    "chain.n_exposure", "chain.n_interface", "derived.n_middle",
    "derived.outcome_investor_count",
)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def candidate_id(rule: dict[str, Any]) -> str:
    return "C_" + digest_json(rule)[:24]


def selector(family: str, values: tuple[str, ...]) -> dict[str, Any]:
    return {
        "scope": "middle_events", "family": family,
        "where": [{"field": "event.event_type", "cmp": "in", "value": list(values)}],
    }


def canonical_rule(rule: dict[str, Any]) -> dict[str, Any]:
    """Normalize the declared grammar without using reference names or masks."""
    op = rule["op"]
    if op in {"present", "absent"}:
        result = json.loads(canonical_json(rule))
        predicate = result["selector"]["where"][0]
        if predicate["cmp"] == "eq":
            predicate["cmp"] = "in"
            predicate["value"] = [predicate["value"]]
        predicate["value"] = sorted(set(predicate["value"]))
        return result
    if op in {"sequence", "compare"}:
        result = json.loads(canonical_json(rule))
        if op == "compare" and result["left"].get("field") == "chain.outcome_type":
            right = result.get("right", {}).get("literal")
            if result["cmp"] == "eq":
                result["cmp"] = "in"
                result["right"] = {"literal": [right]}
            elif result["cmp"] == "ne":
                result["cmp"] = "not_in"
                result["right"] = {"literal": [right]}
            if result["cmp"] in {"in", "not_in"}:
                result["right"]["literal"] = sorted(set(result["right"]["literal"]))
        return result
    if op == "not":
        return {"op": "not", "rule": canonical_rule(rule["rule"])}
    children: list[dict[str, Any]] = []
    for raw in rule["rules"]:
        child = canonical_rule(raw)
        if child["op"] == op:
            children.extend(child["rules"])
        else:
            children.append(child)
    # A conjunction of absences on one event family is exactly absence of the union.
    if op == "all":
        merged: dict[str, set[str]] = defaultdict(set)
        retained = []
        for child in children:
            if child["op"] == "absent" and len(child["selector"]["where"]) == 1:
                pred = child["selector"]["where"][0]
                if pred["field"] == "event.event_type" and pred["cmp"] == "in":
                    merged[child["selector"]["family"]].update(pred["value"])
                    continue
            retained.append(child)
        children = retained + [
            {"op": "absent", "selector": selector(family, tuple(sorted(values)))}
            for family, values in sorted(merged.items())
        ]
    unique = {canonical_json(child): child for child in children}
    ordered = [unique[key] for key in sorted(unique)]
    return ordered[0] if len(ordered) == 1 else {"op": op, "rules": ordered}


def atom_group(rule: dict[str, Any]) -> str:
    op = rule["op"]
    if op in {"present", "absent"}:
        return f"event:{rule['selector']['family']}:{op}"
    if op == "sequence":
        return "sequence"
    field = rule["left"]["field"]
    return f"value:{field}"


def mask_hash(mask: int, width: int) -> str:
    return hashlib.sha256(mask.to_bytes((width + 7) // 8, "little")).hexdigest()


def rule_mask(rule: dict[str, Any], contexts: list[engine.ChainRuleContext]) -> int:
    mask = 0
    for index, context in enumerate(contexts):
        if engine.evaluate_rule(rule, context):
            mask |= 1 << index
    return mask


def company_mask(chain_mask: int, company_indexes: list[int]) -> int:
    result = 0
    for chain_index, company_index in enumerate(company_indexes):
        if chain_mask & (1 << chain_index):
            result |= 1 << company_index
    return result


def _subsets(values: tuple[str, ...], maximum: int) -> Iterable[tuple[str, ...]]:
    for size in range(1, min(len(values), maximum) + 1):
        yield from itertools.combinations(values, size)


def primitive_rules(
    contexts: list[engine.ChainRuleContext], domains: dict[str, tuple[str, ...]],
    max_subset_cardinality: int, max_sequence_length: int,
) -> Iterable[dict[str, Any]]:
    for family in ("exposure", "interface"):
        for values in _subsets(domains[family], max_subset_cardinality):
            selected = selector(family, values)
            yield {"op": "present", "selector": selected}
            yield {"op": "absent", "selector": selected}
    for values in _subsets(domains["outcome"], max_subset_cardinality):
        for comparator in ("in", "not_in"):
            yield {
                "op": "compare", "left": {"field": "chain.outcome_type"},
                "cmp": comparator, "right": {"literal": list(values)},
            }
    for value in domains["window_status"]:
        yield {
            "op": "compare", "left": {"field": "chain.chain_window_status"},
            "cmp": "eq", "right": {"literal": value},
        }
    for field in NUMERIC_FIELDS:
        namespace, name = field.split(".", 1)
        values = sorted({
            context.chain[name] if namespace == "chain" else context.derived[name]
            for context in contexts
            if (context.chain[name] if namespace == "chain" else context.derived[name]) is not None
        })
        for value in values:
            yield {
                "op": "compare", "left": {"field": field}, "cmp": "eq",
                "right": {"literal": value},
            }
            if value > 0:
                yield {
                    "op": "compare", "left": {"field": field}, "cmp": "gte",
                    "right": {"literal": value},
                }
    typed_steps = tuple((family, value) for family in ("exposure", "interface") for value in domains[family])
    for length in range(2, max_sequence_length + 1):
        for steps in itertools.product(typed_steps, repeat=length):
            selectors = [selector(family, (value,)) for family, value in steps]
            yield {
                "op": "sequence", "steps": selectors, "ordering": "strict_before",
                "min_gap_days": None, "max_gap_days": None, "same_values": [],
            }
            if all(family == "interface" for family, _ in steps):
                yield {
                    "op": "sequence", "steps": selectors, "ordering": "strict_before",
                    "min_gap_days": None, "max_gap_days": None,
                    "same_values": ["event.investor_id"],
                }


def read_inputs(paths: dict[str, Path]) -> tuple[dict[str, Any], list[engine.ChainRuleContext], list[int], list[str]]:
    columns = {"chains": CHAIN_COLUMNS, "exposure": EXPOSURE_COLUMNS, "interface": INTERFACE_COLUMNS}
    tables = {name: pq.read_table(paths[name], columns=list(columns[name])) for name in columns}
    rows = {name: table.to_pylist() for name, table in tables.items()}
    lookup = engine.build_event_lookup(rows["exposure"], rows["interface"])
    ordered = sorted(rows["chains"], key=lambda row: (row["company_id"], row["chain_sequence"], row["chain_id"]))
    contexts = [engine.build_chain_rule_context(row, lookup) for row in ordered]
    companies = sorted({row["company_id"] for row in ordered})
    company_to_index = {value: index for index, value in enumerate(companies)}
    indexes = [company_to_index[row["company_id"]] for row in ordered]
    return tables, contexts, indexes, companies


def freeze(args: argparse.Namespace) -> dict[str, Any]:
    started = time.monotonic()
    paths = {
        "chains": Path(args.chain_path).expanduser().resolve(),
        "exposure": Path(args.exposure_path).expanduser().resolve(),
        "interface": Path(args.interface_path).expanduser().resolve(),
    }
    schemas = {
        "chains": Path(args.chain_schema).expanduser().resolve(),
        "exposure": Path(args.exposure_schema).expanduser().resolve(),
        "interface": Path(args.interface_schema).expanduser().resolve(),
    }
    output = Path(args.output_dir).expanduser().resolve()
    for path in [*paths.values(), *schemas.values()]:
        if not path.is_file():
            raise ValueError(f"missing input: {path}")
        if output == path or output in path.parents:
            raise ValueError(f"output/input alias is not allowed: {path}")
    if output.exists():
        raise ValueError(f"refusing to overwrite existing output directory: {output}")
    if args.max_subset_cardinality < 1 or args.max_conjuncts < 1 or args.max_disjuncts < 1:
        raise ValueError("grammar bounds must be positive")
    if not 2 <= args.max_sequence_length <= 3:
        raise ValueError("max sequence length must be 2 or 3")

    tables, contexts, company_indexes, companies = read_inputs(paths)
    domains = {
        "exposure": tuple(sorted({event["event_type"] for context in contexts for event in context.events if event["family"] == "exposure"})),
        "interface": tuple(sorted({event["event_type"] for context in contexts for event in context.events if event["family"] == "interface"})),
        "outcome": tuple(sorted({context.chain["outcome_type"] for context in contexts})),
        "window_status": tuple(sorted({context.chain["chain_window_status"] for context in contexts})),
    }
    if not all(domains.values()):
        raise ValueError("cannot freeze grammar with an empty observed categorical domain")

    atom_rows = []
    raw_count = pruned_count = 0
    chain_width, company_width = len(contexts), len(companies)
    for raw_rule in primitive_rules(contexts, domains, args.max_subset_cardinality, args.max_sequence_length):
        raw_count += 1
        if raw_count > args.max_atoms:
            raise ValueError(f"INCOMPLETE_RESOURCE_BOUND: atom coordinates exceed --max-atoms={args.max_atoms}")
        rule = canonical_rule(raw_rule)
        chain_bits = rule_mask(rule, contexts)
        support = chain_bits.bit_count()
        if support < args.min_chain_support:
            pruned_count += 1
            continue
        company_bits = company_mask(chain_bits, company_indexes)
        atom_rows.append({
            "id": candidate_id(rule), "group": atom_group(rule), "rule": rule,
            "chain_support": support, "company_support": company_bits.bit_count(),
            "chain_mask_sha256": mask_hash(chain_bits, chain_width),
            "company_mask_sha256": mask_hash(company_bits, company_width),
        })
        if time.monotonic() - started > args.max_wall_seconds:
            raise ValueError(f"INCOMPLETE_RESOURCE_BOUND: exceeded --max-wall-seconds={args.max_wall_seconds}")
    by_id = {}
    for atom in atom_rows:
        prior = by_id.get(atom["id"])
        if prior is not None and prior["rule"] != atom["rule"]:
            raise ValueError(f"candidate ID collision: {atom['id']}")
        by_id[atom["id"]] = atom
    atoms = [by_id[key] for key in sorted(by_id)]

    # Immediate candidates retain one canonical representative per company exposure mask.
    representatives: dict[str, dict[str, Any]] = {}
    chain_classes: set[str] = set()
    for atom in atoms:
        chain_classes.add(atom["chain_mask_sha256"])
        key = atom["company_mask_sha256"]
        if key not in representatives or canonical_json(atom["rule"]) < canonical_json(representatives[key]["rule"]):
            representatives[key] = atom
    selected = sorted(representatives.values(), key=lambda item: item["id"])
    if len(selected) > args.max_candidates:
        raise ValueError(f"INCOMPLETE_RESOURCE_BOUND: company-mask representatives exceed --max-candidates={args.max_candidates}")

    grammar = {
        "kind": "bounded_atom_lattice",
        "candidate": "canonical ATOM | ANY(ATOM...) | ALL(ATOM..., optional ANY(ATOM...)); every normalized leaf must be in the atom catalog",
        "max_subset_cardinality": args.max_subset_cardinality,
        "max_conjuncts": args.max_conjuncts,
        "max_disjuncts": args.max_disjuncts,
        "max_sequence_length": args.max_sequence_length,
        "min_chain_support_per_atom": args.min_chain_support,
        "composition": {
            "all": "commutative, associative, idempotent conjunction; at most one ANY child",
            "any": "commutative, associative, idempotent disjunction of atoms",
            "same_group": "canonicalization may merge equivalent same-family absences; the normalized leaf must still satisfy atom bounds and exist in the catalog",
        },
        "atom_families": [
            "event_type subset presence/absence by observed family",
            "outcome_type observed-subset membership/non-membership",
            "observed window-status equality",
            "observed numeric equality and positive gte thresholds",
            "unbounded-gap strict event-type sequences, optionally same resolved interface investor",
        ],
        "excluded_dimensions": [
            "free-text event_subtype and identity values", "literal date thresholds",
            "non-null sequence gap thresholds", "unrestricted recursive Boolean formulas",
        ],
    }
    completeness = {
        "status": "COMPLETE_BOUNDED_COMPACT_GRAMMAR",
        "statement": (
            "The atom catalog contains every observed-domain atom satisfying the declared subset/sequence bounds and atom support floor. "
            "Together with the deterministic canonicalizer it compactly represents every canonical ATOM, bounded ANY-of-atoms, and bounded ALL-of-atoms-with-at-most-one-bounded-ANY formula whose normalized leaves remain within the declared bounds and frozen atom catalog. "
            "Only company-mask representatives are eagerly materialized; omitted compositions remain deterministically materializable from atom IDs."
        ),
        "not_claimed": "No claim is made for all mathematical formulas, excluded dimensions, atoms below the support floor, or formulas beyond the declared bounds.",
    }
    lattice = {
        "lattice_schema_version": "1.0", "lattice_id": "label_blind_finite_grammar",
        "grammar": grammar, "domains": {key: list(value) for key, value in domains.items()},
        "atoms": atoms, "completeness": completeness,
    }
    frozen = {
        "config_schema_version": "1.0", "pattern_set_id": "label_blind_candidates",
        "primary_order": [item["id"] for item in selected],
        "patterns": [{
            "id": item["id"], "name": f"Finite-grammar candidate {item['id']}",
            "description": f"Label-blind atom; group={item['group']}; company-mask representative.",
            "evaluation_status": "EVALUABLE", "not_evaluable_reason": None, "rule": item["rule"],
        } for item in selected],
    }

    script_path = Path(__file__).resolve()
    engine_path = Path(engine.__file__).resolve()
    input_meta = {
        name: {
            "path": str(path), "sha256": engine.sha256_file(path), "rows": tables[name].num_rows,
            "physical_columns": pq.read_metadata(path).num_columns, "projected_columns": list(
                CHAIN_COLUMNS if name == "chains" else EXPOSURE_COLUMNS if name == "exposure" else INTERFACE_COLUMNS
            ),
        } for name, path in paths.items()
    }
    generation_config = {
        "grammar": grammar,
        "projected_columns": {
            "chains": list(CHAIN_COLUMNS), "exposure": list(EXPOSURE_COLUMNS),
            "interface": list(INTERFACE_COLUMNS),
        },
        "observed_domains": {key: list(value) for key, value in domains.items()},
        "resource_limits": {
            "max_atoms": args.max_atoms, "max_candidates": args.max_candidates,
            "max_wall_seconds": args.max_wall_seconds,
        },
    }
    manifest = {
        "generation_contract": "label-blind-pattern-freeze-v1", "phase": "LABEL_BLIND_GENERATION_FREEZE",
        "runtime": {"python": platform.python_version(), "pyarrow": pa.__version__},
        "inputs": input_meta,
        "configuration_inputs": {
            "resolved_generation_config": {
                "content": generation_config, "sha256": digest_json(generation_config),
            },
        },
        "schema_inputs": {name: {"path": str(path), "sha256": engine.sha256_file(path)} for name, path in schemas.items()},
        "code_inputs": {
            "generator": {"path": str(script_path), "sha256": engine.sha256_file(script_path)},
            "rule_engine": {"path": str(engine_path), "sha256": engine.sha256_file(engine_path)},
        },
        "isolation_contract": {
            "labels_read": False, "reference_pattern_config_read": False,
            "chain_columns_read": list(CHAIN_COLUMNS),
            "forbidden_input_kinds": ["company outcome labels", "reference Pattern IDs/rules", "action-value results"],
        },
        # Destination and elapsed time are deliberately excluded: neither affects
        # generation semantics, and excluding them makes complete freeze reruns
        # byte-identical when inputs, code, environment, and bounds are unchanged.
        "parameters": {
            "max_subset_cardinality": args.max_subset_cardinality,
            "max_conjuncts": args.max_conjuncts,
            "max_disjuncts": args.max_disjuncts,
            "max_sequence_length": args.max_sequence_length,
            "min_chain_support": args.min_chain_support,
            "max_atoms": args.max_atoms,
            "max_candidates": args.max_candidates,
            "max_wall_seconds": args.max_wall_seconds,
        },
        "grammar": grammar, "completeness": completeness,
        "counts": {
            "chains": chain_width, "companies_with_chains": company_width,
            "raw_atom_coordinates": raw_count, "support_pruned_atoms": pruned_count,
            "supported_atoms": len(atoms), "distinct_chain_masks": len(chain_classes),
            "distinct_company_masks": len(representatives), "eager_frozen_candidates": len(selected),
        },
        "outputs": {},
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=output.name + ".tmp-", dir=output.parent))
    try:
        lattice_path = temp / "candidate_lattice.json"
        frozen_path = temp / "pattern_candidates_frozen.json"
        lattice_path.write_text(json.dumps(lattice, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
        frozen_path.write_text(json.dumps(frozen, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
        manifest["outputs"] = {
            lattice_path.name: {"sha256": engine.sha256_file(lattice_path), "bytes": lattice_path.stat().st_size},
            frozen_path.name: {"sha256": engine.sha256_file(frozen_path), "bytes": frozen_path.stat().st_size},
        }
        (temp / "generation_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n")
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
    result.add_argument("--max-subset-cardinality", type=int, default=8)
    result.add_argument("--max-conjuncts", type=int, default=4)
    result.add_argument("--max-disjuncts", type=int, default=3)
    result.add_argument("--max-sequence-length", type=int, default=3)
    result.add_argument("--min-chain-support", type=int, default=1)
    result.add_argument("--max-atoms", type=int, default=25000)
    result.add_argument("--max-candidates", type=int, default=10000)
    result.add_argument("--max-wall-seconds", type=float, default=900.0)
    return result


def main() -> None:
    args = parser().parse_args()
    try:
        manifest = freeze(args)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(manifest["counts"], sort_keys=True))
    print(f"wrote: {Path(args.output_dir).expanduser().resolve()}")


if __name__ == "__main__":
    main()
