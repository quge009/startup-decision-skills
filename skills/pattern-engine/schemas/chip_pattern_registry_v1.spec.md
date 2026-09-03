# CHIP Pattern Registry v1 specification

## 1. Authority and scope

`chip_pattern_registry_v1` is the single authoritative, machine-readable registry for the three reviewed CHIP Pattern batches. Its checked-in release path, relative to the research directory, is `artifacts/chip_pattern_registry_v1.json`. It is generated; entries must not be edited by hand and no companion manifest is authoritative. All provenance needed to reproduce or audit an entry is embedded in the registry.

The production generator is `scripts/generate_chip_pattern_registry_v01.py`. A build is restricted to a new path under `/tmp`; existing output is never overwritten. After review, those canonical bytes are copied byte-identically to the checked-in release path and the source/release SHA-256 values must match. The generator reads only these selection authorities:

1. Batch 1: `configs/chip_patterns_v0.1.json` joined to the explicitly supplied reviewed baseline `pattern_prevalence_sf_v0.2.json`.
2. Batch 2: the five IDs parsed from `_REPORT.md` §4.8.1.2, joined to the explicitly supplied `chip_candidates_frozen_v0.1.json`, `candidate_lattice_v0.1.json`, and fixed-`delta=.05` `pattern_prevalence_sf_v0.2.json`. No other Batch 2 candidate is registered.
3. Batch 3: only `final_candidates` from the explicitly supplied `review_chip_batch3_results_v01` artifact. The registry never reopens the frozen pair universe to select alternatives.

## 2. Canonical encoding

Canonical bytes are UTF-8 JSON with recursively sorted object keys, `ensure_ascii=false`, `allow_nan=false`, compact separators `,` and `:`, and exactly one trailing LF. There is no generated timestamp, random value, discovered input path, or output-path field. A rule SHA-256 is over the same canonical JSON encoding without the trailing LF. Two builds from identical source bytes plus identical generator/spec bytes must be byte-identical.

## 3. Top-level object

The object has these fields:

- `registry_schema_version`: exactly `"1.0"`.
- `registry_id`: exactly `"chip_pattern_registry_v1"`.
- `source_snapshot`: deterministic source label, not a build timestamp.
- `result_order`: exactly `RECOMMEND`, `AVOID`, `INDIFFERENT`, `INCONCLUSIVE`.
- `batch_order`: exactly `batch1`, `batch2`, `batch3`.
- `result_buckets`: exactly the four Result keys; each contains exactly the three batch arrays.
- `special_statuses`: exactly `NOT_OBSERVABLE`, with three batch arrays. P6 is the sole special entry.
- `excluded_summary`: aggregate-only accounting for the 3,642 non-registered Batch 2 candidates.
- `counts`: counts derived from arrays, including Result × batch, registered totals, special statuses, and excluded totals.
- `batch_summaries`: Batch 1/2 accounting and the original Batch 3 three-step review matrix/audit appendix.
- `provenance`: artifact catalog and hashes, Batch 3 policy/input hashes, statistical method, cohort, canonical encoding, and determinism statement.

No Result or special-status key outside the fixed sets is permitted. Every Result × batch array is sorted by `id` using Unicode code-point order. IDs are globally unique across registered and special entries.

## 4. Registered entry

Every entry in `result_buckets` is self-contained:

```json
{
  "id": "P2",
  "batch": "batch1",
  "name": "5+ investors with no recorded Exposure/Interface action",
  "meaning": "5+ investors ...: P1 conditions ...",
  "rule": {},
  "rule_sha256": "64 lowercase hex",
  "result": "RECOMMEND",
  "support": {
    "chain_count": null,
    "company_count": 67,
    "contingency_2x2": {
      "taking": {"SUCCESS": 55, "FAILURE": 12, "total": 67},
      "not_taking": {"SUCCESS": 214, "FAILURE": 220, "total": 434}
    }
  },
  "stats": {
    "estimand": "SUCCESS_RISK_DIFFERENCE",
    "ate": 0.32780796478437313,
    "p": 5.487947620230258e-7,
    "p_tost": 0.9999999347353214,
    "chi_square": 25.08424281634801,
    "alpha": 0.05,
    "equivalence_margin": 0.05,
    "source_evaluation_status": "EVALUABLE"
  },
  "company_mask": null,
  "attributes": {},
  "inference": {},
  "source": []
}
```

`meaning` is always non-empty. Batch 1 meaning is deterministically formed from the checked-in name and description. Batch 2 meaning is rendered deterministically from the frozen rule; the reviewed report supplies its display name. Batch 3 meaning is copied verbatim from review field `pattern`, which is the reviewer's rendered Pattern.

`rule` is copied from the applicable frozen machine artifact, never reconstructed from display text. Batch 1 intentionally leaves `chain_count` and `company_mask` null rather than treating company support as Chain support or fabricating a mask. Batch 2 and Batch 3 carry source Chain support and company-mask metadata. A non-null mask always identifies the 663-company universe, lexical company-ID ordering, and bitset encoding. `company_mask.member_count` is the source mask count over all 663 companies and can exceed `support.company_count`, which is the taking count within the fixed 501-company evaluable cohort; it must never be smaller.

`inference` always declares `OBSERVATIONAL_UNADJUSTED`, `causal_claim_allowed=false`, `multiple_testing_adjusted=false`, and `DECISION_RULE_ONLY`. A Result is a fixed rule decision on the 501 observable companies, not a causal or multiplicity-adjusted finding.

Each `source` element carries `artifact_path`, `artifact_sha256`, `pointer`, and `role`; it must resolve to an identically hashed item in `provenance.artifacts`. Source references are sorted by `(artifact_path, pointer, role)`.

## 5. P6 and excluded Batch 2 candidates

P6 is normalized from source `NOT_EVALUABLE` to registry status `NOT_OBSERVABLE`. It appears only in `special_statuses.NOT_OBSERVABLE.batch1`. Its `rule`, `rule_sha256`, `result`, `support`, `stats`, and `company_mask` are all null. Its attributes retain the source status and mapping reason. P6 must never appear in `INCONCLUSIVE` and must never receive an all-false mask.

The other 3,642 evaluated Batch 2 candidates are not emitted individually. `excluded_summary.batch2` conserves `5 + 3642 = 3647`; its evaluation classes sum to 3,642, its mutually exclusive non-shortlisted RECOMMEND reasons sum to 1,614, and its AVOID reasons sum to 16.

## 6. Required production counts

| Result | Batch 1 | Batch 2 | Batch 3 | Total |
|---|---:|---:|---:|---:|
| RECOMMEND | 2 | 5 | 9,453 | 9,460 |
| AVOID | 1 | 0 | 74 | 75 |
| INDIFFERENT | 0 | 0 | 0 | 0 |
| INCONCLUSIVE | 4 | 0 | 13,769 | 13,773 |
| Registered | 7 | 5 | 23,296 | 23,308 |

Special count: Batch 1 `NOT_OBSERVABLE=1`. Batch 3 preserves the three-step totals `208394 -> 97607 -> 23296` and final distribution `9453 / 74 / 0 / 13769` in Result order.

## 7. Semantic validation

Generation fails before publication unless all conditions hold:

- `entry.batch` and `entry.result` agree with their containing buckets.
- Registered rules are non-null and their canonical hashes match; P6's nullable fields remain null.
- IDs are globally unique and arrays are sorted.
- Every 2×2 arm conserves SUCCESS + FAILURE, both arms total 501, and taking total equals company support.
- A non-null Chain count is at least evaluable company count; a non-null 663-company mask member count is at least evaluable company count.
- `ate` equals taking SUCCESS rate minus not-taking SUCCESS rate within absolute tolerance `1e-15`.
- `p` and `p_tost` are finite and within `[0,1]`; all JSON numbers are finite.
- Reclassification by fixed alpha `.05`, ATE sign, and TOST `.05` equals the containing Result.
- Batch 2 has exactly the five report IDs and all definition/statistics/support joins agree.
- Batch 3 has exactly 23,296 unique reviewed final candidates and the expected matrix/distribution.
- Every count and exclusion subtotal conserves its parent total.
- Every consumed file still matches the path, size, and SHA-256 embedded in provenance immediately before output is written.

The output is first fully constructed and validated, then written to a temporary sibling, `fsync`ed, and atomically renamed.
