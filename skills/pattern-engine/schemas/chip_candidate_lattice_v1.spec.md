# Label-Blind CHIP Candidate Lattice v1

**Status:** bounded candidate-generation and post-freeze audit contract
**Scripts:** `scripts/generate_chip_pattern_candidates_v01.py`, `scripts/audit_chip_pattern_coverage_v01.py`

## Separation of phases

Generation projects only the Chain/event columns listed in its manifest. It does not project either company-label column, accept a reference-Pattern option, load `configs/chip_patterns_v0.1.json`, or compute action-value statistics. Values are derived from observed, Chain-referenced `event_type`, `outcome_type`, window-status, and numeric/count values. Schema files and all input files are hashed. Focused tests inspect the generator source and manifest for this boundary.

The separate `coverage` command first validates the frozen output hashes and production-input hashes, then loads the reference config. The separate `evaluate` command may read labels only after the same freeze checks. Neither command modifies the freeze.

## Complete finite grammar

The freeze explicitly catalogs every atom that meets the configured atom support floor within these dimensions and bounds:

- presence and absence of every non-empty observed `event_type` subset per Exposure/Interface family, up to `max_subset_cardinality`;
- membership and non-membership in every non-empty observed `outcome_type` subset up to that bound;
- equality to each observed Chain window status;
- equality to each observed value, and `gte` at each positive observed value, for `chain.n_exposure`, `chain.n_interface`, `derived.n_middle`, and `derived.outcome_investor_count`;
- strict sequences of observed singleton event types from length 2 through `max_sequence_length`; gaps are unbounded, and all-Interface sequences also have a same-non-null-`investor_id` variant.

A canonical candidate is an atom, an `ANY` of at most `max_disjuncts` atoms, or an `ALL` of at most `max_conjuncts` children where at most one child is a bounded `ANY` of atoms. Boolean children are flattened, sorted, and deduplicated; same-family event absences under `ALL` merge into one absence over the union. Every normalized leaf must still satisfy the atom bounds/support floor and exist in the frozen catalog. Candidate IDs are `C_` plus the first 24 hexadecimal digits of SHA-256 over canonical JSON.

This is a **compact lattice**, not literal formula expansion. `candidate_lattice_v0.1.json` explicitly stores all supported atoms and represents every bounded canonical composition above whose normalized leaves remain in the catalog implicitly. `chip_candidates_frozen_v0.1.json` eagerly stores one deterministic representative per distinct company-any mask for immediate analysis. The manifest reports raw coordinates, support-pruned atoms, distinct Chain masks, and company-mask representatives. Given identical inputs, code, runtime, and generation bounds, all three freeze artifacts are byte-identical across reruns even when the destination directory differs; destination and elapsed wall time are intentionally excluded because they are not generation semantics.

Completeness is exactly `COMPLETE_BOUNDED_COMPACT_GRAMMAR`: all supported atoms and all declared bounded compositions are represented. It does **not** claim all mathematical formulas, unsupported atoms, free-text subtype/identity predicates, literal date thresholds, bounded sequence gaps, or unrestricted recursive Boolean expressions. Exceeding atom, eager-candidate, or wall-time limits fails with `INCOMPLETE_RESOURCE_BOUND` before a freeze directory is published.

## Coverage criterion

For each evaluable reference rule, the audit canonicalizes it using the frozen grammar, proves that every leaf atom exists in the frozen atom catalog, deterministically materializes its candidate ID, and evaluates both the original and materialized rules. Recovery requires both:

1. exact equality of the bitset over all Chains; and
2. exact equality of the company-any bitset over dated/evaluable Chains.

A non-evaluable reference is reported as `NOT_OBSERVABLE`; no false mask is invented. Mask-equivalent eager candidates are diagnostic and AST identity is not required.

## Reusable commands

From this research directory:

```bash
PY=python3
$PY scripts/generate_chip_pattern_candidates_v01.py \
  --output-dir /tmp/chip_candidate_freeze
$PY scripts/audit_chip_pattern_coverage_v01.py coverage \
  --freeze-dir /tmp/chip_candidate_freeze \
  --output /tmp/chip_candidate_coverage.json --markdown
$PY scripts/audit_chip_pattern_coverage_v01.py evaluate \
  --freeze-dir /tmp/chip_candidate_freeze --delta .08 \
  --output /tmp/chip_candidate_evaluation_delta008.json
```

Generated `/tmp` artifacts are verification outputs, not formal derived releases.
