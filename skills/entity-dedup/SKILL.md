---
name: entity-dedup
description: Deduplicate company and investor names into canonical entities with stable IDs, deterministic normalization, and evidence-based equivalence rules. Use for entity merging in startup and investment research.
---

# Entity Dedup — Canonical company & investor entity keys

Deterministic deduplication of raw company and investor entity names into
canonical entities with stable, reproducible IDs, plus the slug-keyed
`investor_id` lookup map used by downstream ingest passes.

## Company entity pipeline (`build_company_entity.py`)

Builds `companies_entity_v0.2.parquet` from per-source `edges_by_source`
parquet files.

- **Dedup key**: `company_normalized_name`, further normalized by
  `normalize_company_name` — lowercase, punctuation stripped, and trailing
  legal suffixes (`inc`, `corp`, `ltd`, `plc`, `llc`, `lp`, `sa`, `ag`,
  `nv`, `bv`, `co`, `company`, `corporation`, `limited`, `group`,
  `holdings`, `international`, `global`, `industries`) popped repeatedly.
- **Optional manual alias rules**: `--manual-aliases <yaml>` accepts
  project-specific canonical-name and alias groups. No manual aliases are
  applied by default.
- **Garbage filtering**: `_is_garbage` rejects pure-numeric / year-like,
  UI-navigation keywords (Japanese/Chinese), leaked pandas Series repr,
  VC portfolio-page metadata concatenated into names, and sentence-like
  descriptions.
- **company_id generation**: `co:XXXX-XXXX:<slug>`, sequence by
  first-encounter order across edges files, slug from `make_slug(canonical_name)`.
- CUSIP is extracted from `metadata_json` when present; `edge_count` reports
  how many edges reference the company.

### Usage

```bash
python3 scripts/build_company_entity.py --edges-dir <dir> --out <path>
# Optional, for a separately maintained research-specific mapping:
python3 scripts/build_company_entity.py --edges-dir <dir> --out <path> \
  --manual-aliases <aliases.yaml>
```

| Flag | Requirement | Meaning |
|---|---|---|
| `--edges-dir` | required | Directory of per-source edge Parquets |
| `--out` | required | Output Parquet path |
| `--manual-aliases` | none | Optional project-specific canonical-name and alias YAML |

## Investor merge pipeline (`merge_investors.py`)

Merges the per-source extracted parquets (`findfunding` / `wikidata` /
`manual_seed`, plus optional `url_verify`) into canonical entity +
provenance tables and a coverage report.

- **Dedup**: union–find over five equivalence rules — 1) same wikidata QID,
  2) same domain (aggregator platforms like AngelList/LinkedIn/Crunchbase
  skipped; `url_verify` rows only merge via rule 3), 3) same `normalized_name`
  AND ≥1 shared `location_state`, 4) same `findfunding_slug`,
  5) same `manual_seed_slug`.
- **Canonical value selection**: confidence-weighted per source+field
  (`SOURCE_CONFIDENCE`), tie-broken by `SOURCE_PRIORITY`; list fields union
  across sources; per-source ID fields preserved independently.
- **Outputs**: `investors_entity_v0.1.parquet` (canonical), a long-format
  `investors_provenance_v0.1.parquet` (per field + source contribution with
  confidence and evidence URL), and `reports/step1_coverage_v0.1.md`
  (row-count corridor + marquee-name sanity, type distribution, field
  completeness, cross-source overlap, QID conflicts).

### Usage

```bash
python3 scripts/merge_investors.py --out-dir <dir>
# Optional sanity checks for a separately maintained research universe:
python3 scripts/merge_investors.py --out-dir <dir> --universe <universe.yaml>
```

| Flag | Requirement | Meaning |
|---|---|---|
| `--out-dir` | required | Root output directory; reads `extracted/investors_by_source/` below it |
| `--universe` | none | Optional row-count corridor and expected-name sanity-check YAML |

## Investor ID lookup map (`_common.py`)

`load_investor_id_map()` loads the canonical entity table and returns
`{make_slug(name): investor_id}` — the shared lookup so downstream ingest
scripts reuse the canonical `investor_id` instead of generating their own.

All command inputs and outputs are passed explicitly. The shared lookup helper
uses one environment variable:

| Environment variable | Requirement | Used by |
|---|---|---|
| `INVESTOR_ENTITY_PATH` | Required when using the lookup helper | `_common.py` `load_investor_id_map()` |

The normalizers live in `_common.py`: `make_slug` (accent-transliterating,
CJK-preserving), `normalize_name` (16-token trailing strip list for investor
names), `normalize_website` / `extract_domain` (scheme normalization + public
suffix), `normalize_stage` / `normalize_stage_list` (10-value stage enum), and
`to_list`.

## Requirements

- Python 3.10+ (`from __future__ import annotations`).
- `pandas` and `pyarrow` (both scripts + `_common.py`).
- `pyyaml` when using an optional universe or manual-alias configuration.

## Self-test

```bash
python3 -m py_compile scripts/_common.py scripts/merge_investors.py scripts/build_company_entity.py
```

All three modules must compile clean. Then smoke-test CLI parsing:
`python3 scripts/merge_investors.py --help` and
`python3 scripts/build_company_entity.py --help` should both print usable
flag documentation and exit 0.
