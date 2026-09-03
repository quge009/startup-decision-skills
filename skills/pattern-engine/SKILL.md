---
name: pattern-engine
description: Freeze label-blind event-chain pattern candidates, audit grammar coverage, evaluate frozen candidates, review equivalent or uninterpretable patterns, and build an auditable registry. Use for retrospective operating-action analysis over company event chains; RECOMMEND and AVOID apply to actions, not companies.
---

# Pattern Engine

Discover and evaluate event-chain Patterns without allowing outcome labels to
influence candidate generation. Inputs are never mutated and derived artifacts
are written atomically.

## Workflow

1. Freeze the bounded grammar before reading labels:
   - `generate_pattern_candidates.py` builds the compact lattice.
   - `generate_pattern_candidates_extended.py` adds event anchors, structural
     atoms, and typed pairwise compositions.
2. Run `audit_pattern_candidates.py coverage` against any predefined
   reference Patterns only after the freeze. This tests method coverage without
   allowing reference outcomes into generation.
3. Run its evaluation mode with the fixed equivalence margin. Interpret
   `RECOMMEND` and `AVOID` as labels on operating actions represented by a
   Pattern, never as company labels or predictions.
4. Use `review_pattern_results.py` to remove invalid or
   uninterpretable candidates and merge candidates selecting the same companies.
   Supply freeze, evaluation, and output paths explicitly; use `--expected-count`
   only when reproducing a pinned run.
5. Use `build_pattern_registry.py --batch NAME=JSON ...`; supply universe
   identity, size, ordering, and reviewed batches explicitly.
6. Run `analyze_patterns.py` for a caller-supplied Pattern specification
   to materialize chain, company, investor, prevalence, identity-coverage, and
   run-manifest outputs.

Read the candidate-lattice, extended-candidate, and registry contracts under `schemas/`
before altering grammar limits, masks, ordering, or registry fields. Supply the
three event schemas explicitly to generation commands.

## Requirements

Python 3.10+ and `pyarrow`. Set `INVESTOR_BEHAVIOR_DATA_DIR` or pass every input
path explicitly. Candidate generation must remain label-blind; evaluation begins
only from a completed immutable freeze.
