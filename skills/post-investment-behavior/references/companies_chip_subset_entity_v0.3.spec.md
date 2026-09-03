# companies_chip_subset_entity_v0.3 — Chip Startup Universe Company Entity Table

**Purpose**: Canonical entity table for the 2,547 chip startups in the frozen
research universe. Provides a stable `company_id` and `slug` for
each company, used as FK by event collection scripts (`collect_*_events_v02.py`)
and downstream chain analysis. Extends the v0.2 entity schema with outcome
labels from Step-10.2 web verification.

**File**: `companies_chip_subset_entity_v0.3.parquet`

**Source**: the frozen chip-startup research universe (source fields: `name`,
`outcome_label_v4`, `label_v4`)

**Build script**: `scripts/build_company_chip_entity_v03.py`

## Schema (one row per unique company)

| Column                 | Type   | Nullable | Description |
|------------------------|--------|----------|-------------|
| `company_id`             | string | no       | `co:chip-NNNN:<slug>` — unique within this table |
| `company_canonical_name` | string | no       | Original name from `chip_startup_universe.csv` |
| `company_normalized_name`| string | no       | Lowercased, punctuation-stripped, legal suffixes removed |
| `company_dedup_key`      | string | no       | Same as `company_normalized_name` (used for dedup matching) |
| `cusip`                  | string | yes      | Always null (chip startups are mostly private) |
| `edge_count`             | int64  | no       | 0 initially; updated by event collection scripts |
| `slug`                   | string | no       | URL-safe slug from `make_slug(name)`, unique |
| `outcome_label_v4`       | string | no       | Consolidated outcome: v3 if available, else v2 |
| `label_v4`               | string | no       | `SUCCESS`, `FAILURE`, or `AMBIGUOUS` |

## Deduplication

Input CSV may contain duplicate company names (same `slug` after normalization).
The build script keeps the first occurrence and drops subsequent duplicates,
logging the dropped rows.

## ID format

`co:chip-{seq:04d}:{slug}` — the `chip` namespace avoids collision with
`companies_entity_v0.2.parquet` IDs (which use `co:{NNNN}-{NNNN}:{slug}`).

## Relationship to companies_entity_v0.2

These two tables are **disjoint** by design. Only ~60 companies appear in both
(matched by normalized_name). The chip subset entity is purpose-built for
Step-10 event chain analysis and does not merge into the v0.2 table.
