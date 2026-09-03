# CHIP Candidate Batch 3 v1 — Explicit Typed Pairwise Specification

**Generator:** `scripts/generate_chip_pattern_candidates_v02.py`
**Generic evaluator:** `scripts/analyze_chip_patterns_v01.py`
**Post-freeze evaluator:** `scripts/audit_chip_pattern_coverage_v01.py evaluate`

## 1. Label-blind generation boundary

Generation opens only three explicitly supplied v0.3 Parquets and their three checked-in machine schema contracts. It performs no glob/directory discovery.

Safe projections are exactly:

- Chain: `chain_id, company_id, chain_sequence, chain_start_date, chain_start_reason, chain_end_date, chain_window_status, outcome_type, outcome_round, outcome_event_ids, n_participant_rows, investor_ids, investor_raw_names, n_exposure, exposure_event_ids, n_interface, interface_event_ids`.
- Exposure: `event_id, company_id, event_date, event_type`.
- Interface: `event_id, company_id, event_date, event_type, investor_id, raw_investor_name`.

Labels (`company_label_v4`, `company_outcome_label_v4`, `label_v4`), P1–P8/reference configs, Batch2/prior candidates/freezes/evaluations/reports, ATE/p-values/recommendations, and entity/cohort tables are forbidden. Event subtype, free text, URL, metadata, confidence and source type are not projected. Interface identity fields are used only for participant equality/count and never become literals. The observed `TYPE` vocabulary contains only non-empty `(family,event_type)` singletons among rows referenced by current Chains; unreferenced physical rows do not enlarge it.

The manifest records safe projections, opened paths, projected-row/schema/code hashes, observed type and witnessed-sequence vocabulary hashes, forbidden columns/input kinds, caps and completeness accounting. Changing only forbidden physical columns or adding a historical result file cannot change freeze bytes.

## 2. Complete finite grammar

```ebnf
TYPE          ::= observed singleton (exposure|interface, event_type)
RULE          ::= ALL(EVENT_ANCHOR, STRUCTURAL_ATOM)

EVENT_ANCHOR  ::= TYPE_PRESENT(TYPE) | TYPE_ABSENT(TYPE)
                | TYPE_COUNT(TYPE, CMP, COUNT_GRID)
                | TYPE_START_TIMING(TYPE, min, gte, DAY_GRID)
                | TYPE_START_TIMING(TYPE, max, lte, DAY_GRID)
                | TYPE_END_TIMING(TYPE, min, gte, DAY_GRID)
                | TYPE_END_TIMING(TYPE, max, lte, DAY_GRID)
                | TYPE_POSITION(TYPE, family|all_middle, first_batch|last_batch)
                | TYPED_SEQUENCE(TYPED_STEP{2,3}, GAP_SPEC, SAME_PARTICIPANT?)
                | TYPE_DISTINCT_PARTICIPANTS(INTERFACE_TYPE, CMP, COUNT_GRID)
TYPED_STEP    ::= selector(family=TYPE.family, event.event_type eq TYPE.event_type)
GAP_SPEC      ::= unbounded | minimum(SEQUENCE_GAP_GRID) | maximum(SEQUENCE_GAP_GRID)
SAME_PARTICIPANT ::= event.participant_key; all-Interface sequences only

STRUCTURAL_ATOM ::= ROUND | DURATION | FAMILY_OR_TOTAL_COUNT
                  | SHARE_OR_DENSITY | PARTICIPANT_COUNT
                  | BATCH_CADENCE_OR_GAP | CHAIN_POSITION_OR_HISTORY
```

There are no standalone event/structural candidates, `ANY`, `NOT`, ternary combinations, or implicit composition in v1. Every published candidate is exactly one typed anchor and one non-type structural atom. Presence is the sole event-count `>=1` form and absence the sole event-count `=0` form; typed event-count coordinates therefore omit `gte 1` and `lte 0`.

Fixed grids: count `[0,1,2,3,5,10,20]`; days `[0,7,14,30,60,90,180,365,730,1095,1825]`; sequence gap `[1,7,14,30,60,90,180,365]`; ratio `[0,.25,.5,.75,1]`; density `[0,.5,1,2,4,8,12,24]`; position `[1,2,3,5,10]`. Non-sequence numeric coordinates intersect these grids with the unlabeled observed range.

Structural round categories use the fixed normalizer; `pre_seed` and `seed` are distinct. Current-stage `derived.outcome_round_bucket` and, if used by this grammar, `derived.outcome_round_known` are decision-time stage conditions and do not carry descriptive taint. `outcome_type` category combinations are excluded. Structural comparator directions are finite and non-synonymous: family/total/known-date/participant counts and duration use `gte/lte`; duration alone also has `is_null/not_null`; shares and duration-derived densities use `gte`; ordered-batch/count and lower cadence bounds use `gte`, while upper cadence bounds use `lte`; `n_prior_chains`, `company_chain_count` and `prior_same_round_count` use `gte`. `n_prior_chains` is the true rank ordinal; raw non-contiguous `chain_sequence` is not treated as an ordinal structural threshold. `round_occurrence_index`, `is_last_chain`, and `chain_position_bucket` are omitted because they are deterministic summaries of retained history coordinates. Final `company_chain_count` is future-tainted; same-round history remains descriptive because it uses prior outcome history, independently of the current-stage round bucket.

## 3. Typed event semantics

`event_count(selector)` counts all referenced matching rows, including undated rows. It never substitutes a family-level first exposure/interface statistic for a typed statistic.

`event_timing(selector, relative_to, edge, bound)` selects only the requested singleton type. Start timing uses the type's first overlap-aware batch relative to an exact Chain start; a `founding` sentinel returns null. End timing uses its last batch relative to a known end; unknown end returns null. Partial dates remain closed intervals and are never replaced by midpoints. Ordering comparisons against null are false.

`event_position(selector, scope, edge)` is true when a matching typed event belongs to the first/last overlap-aware batch. `family` constructs batches only from that concrete family; `all_middle` uses all middle events. It does not impose an event-ID tie order or claim a unique first event.

`event_distinct_count(selector,event.participant_key)` is Interface-only. Every row independently uses non-empty resolved `investor_id` first, otherwise NFKC + casefold + whitespace-normalized raw name. Rows with no identity do not contribute and null never equals null.

Typed sequences preserve step order, use distinct events, and require `previous.maximum < next.minimum`. Guaranteed minimum gap is `next.minimum - previous.maximum`; maximum interval gap is `next.maximum - previous.minimum`. A coordinate has no bound, one minimum, or one maximum—not arbitrary min/max pairs. Same-participant sequences require every selected key to be non-null and equal. Only typed tuples witnessed at least once under strict ordering in unlabeled contexts are generated; L2/L3 vocabulary hashes are frozen.

Cross-Chain context order is `(company_id, chain_sequence, chain_id)`. `n_prior_chains`, first/early/last position and same-round occurrence are rank ordinals over actual prior records and remain correct when `chain_sequence` values are non-contiguous.

## 4. Compatibility and explicit materialization

Every support-retained anchor × structural coordinate is visited. Before mask intersection, semantic facts reject contradictions or redundant pairs with stable codes:

- `UNSAT_COUNT_BOUND`: anchor minimum total/family/dated/participant support exceeds a structural upper bound.
- `UNSAT_NULLNESS`: start-relative typed timing requires an exact start and therefore cannot coexist with null duration on a dated Chain.
- `ANCHOR_IMPLIES_STRUCTURAL`: anchor minimum already guarantees a structural lower bound.
- `STRUCTURAL_IMPLIES_ANCHOR`: a total/family upper bound already guarantees a typed upper-bound/absence anchor.

Sequence facts include step multiplicities and dated-event minima, but do not falsely imply one ordered batch per step. Same-participant implies one non-null participant, not one distinct participant per step. All other matrix cells are legal.

For every legal coordinate, pair mask is the integer bitset `anchor_bits & structural_bits`; AST evaluation is not repeated per pair. Company-any masks are derived from Chain bits. Constant and below-floor pair masks are pruned. The accounting invariant is:

`support_retained_anchor_count × support_retained_structural_count = legal_pair_coordinates + compatibility_rejected_coordinates`.

Every surviving pair stores both component IDs, canonical two-child `ALL` AST, track, taints, supports and Chain/company mask hashes. IDs are `B3P_ + sha256(grammar_version + threshold_hash + observed_type_vocab_hash + canonical_AST)[:24]`, with collision checks. JSON is UTF-8, sorted-key, compact, finite-number and deterministic.

Company-mask dedupe is track-local. It selects one representative per `(track, company_mask)` for computation caching; no candidate is removed from the formal freeze. Every candidate maps to a representative and later receives its own evaluation row.

Completeness status is **`COMPLETE_EXPLICIT_LEGAL_PAIRWISE_GRAMMAR`**. It covers exactly the safe observed singleton vocabulary, fixed grids, declared typed/structural atoms, compatibility rules, witnessed strict L2/L3 tuples, support floors and hard caps. It does not claim subtype/text/quality/identity-literal, type subsets, arbitrary thresholds, unwitnessed tuples, low-support/constants, ternary/deeper formulas, unreferenced events, all mathematical formulas, or causal effects.

## 5. Tracks and evaluation

Tracks are fixed:

- `ACTION_ELIGIBLE`
- `DESCRIPTIVE_ASSOCIATION`

Current-stage `derived.outcome_round_bucket` and `derived.outcome_round_known` do not taint a pair: Seed/Series/etc. may condition an action-eligible typed event. Same-round prior history, `chain.outcome_type`, end-relative timing, duration/duration-density, future-final-position, or an `(interface,exit)` anchor/sequence step taints the full pair descriptive. Exit is descriptive only; it is never action-eligible. Other typed presence/count/start timing/position/sequence, current counts/shares, Interface participants, cadence/gaps and strictly prior non-round history are action-eligible. No other field is promoted by this exception.

Both tracks occur in `chip_candidates_batch3_frozen_v1.json`; every Pattern is `EVALUABLE` and records `track`. Evaluation is physically post-freeze: it verifies every manifest output and projected source hash before loading `company_label_v4`. Delta is fixed to `.05`; another value fails. One metrics computation is cached per track-local representative and copied to each mapped candidate with `evaluation_basis=COMPANY_MASK_EQUIVALENT`.

Every row records track, taking/not-taking SUCCESS/FAILURE counts, frozen Chain/company support, ATE, p-value and `p_tost` when both cohorts exist. Action results are `RECOMMEND | AVOID | INCONCLUSIVE`; an equivalence result remains visible through `p_tost` but does not add a fourth action directive. Descriptive rows set `causal_interpretation_allowed=false` and use only `POSITIVE_ASSOCIATION | NEGATIVE_ASSOCIATION | EQUIVALENT_WITHIN_DELTA | INCONCLUSIVE`; they never emit RECOMMEND/AVOID.

## 6. Artifacts and caps

Artifacts:

- `event_anchor_catalog_batch3_v1.json`
- `structural_atom_catalog_batch3_v1.json`
- `candidate_pairs_batch3_v1.json`
- `candidate_representatives_batch3_v1.json`
- `chip_candidates_batch3_frozen_v1.json`
- `generation_manifest_batch3_v1.json`
- post-freeze `candidate_ate_batch3_delta005.json`

Defaults: observed TYPE ≤32; raw anchors ≤250,000; retained anchors ≤20,000; raw/retained structural atoms ≤2,000/1,000; witnessed L2/L3 tuples ≤1,024/8,192; legal pairs ≤5,000,000; frozen pairs ≤250,000; track-local representative cache entries ≤60,000 (calibrated from the production domain; never cross-track deduped); sequence operations ≤50,000,000; semantic artifacts ≤512 MiB; wall time ≤1,800 s; peak RSS ≤8 GiB. Any breach raises `INCOMPLETE_RESOURCE_BOUND`, removes the temporary directory and publishes nothing. Output directories must be new; source/output aliasing and overwrite are forbidden.

## 7. Verification commands

```bash
PY=python3
$PY scripts/generate_chip_pattern_candidates_v02.py --output-dir /tmp/chip_batch3_freeze_a
$PY scripts/generate_chip_pattern_candidates_v02.py --output-dir /tmp/chip_batch3_freeze_b
$PY scripts/audit_chip_pattern_coverage_v01.py evaluate \
  --freeze-dir /tmp/chip_batch3_freeze_a --delta .05 \
  --output /tmp/candidate_ate_batch3_delta005.json
```

These commands read production inputs and write only new `/tmp` paths. They do not modify `/data` and do not update `_REPORT.md` or `_PROGRESS.md`.
