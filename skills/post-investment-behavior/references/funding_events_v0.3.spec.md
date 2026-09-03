# Schema: `funding_events_chip_v0.3.parquet`

**Version:** v0.3.0

**Collector:** `scripts/collect_funding_events_v03.py`

**Builder:** `scripts/build_funding_events_v03.py`

**Company FK:** caller-supplied company entity table
**Machine-readable schema:** `schemas/funding_events_v0.3.schema.json` (authoritative machine contract loaded directly by the builder)

## Purpose and v0.2 changes

One row per participant in a chip-company funding-level outcome. v0.3 makes the company relationship stable and repairs implementation drift in v0.2:

- adds required `company_id` rather than relying on normalized-name remapping;
- materializes the previously specified but omitted `source_url` and `schema_version` columns;
- fixes Arrow types explicitly instead of pandas inference;
- formalizes terminal/failed outcome values already emitted by the collector;
- uses stable hashed event IDs based on company, event, date, and participant semantics.

## Arrow schema

| # | Column | Arrow type | Nullable | Contract |
|---:|---|---|:---:|---|
| 1 | `event_id` | `large_string` | No | PK; `fund:<sha1-prefix>` |
| 2 | `company_id` | `large_string` | No | FK to chip company entity v0.3 |
| 3 | `investor_id` | `large_string` | Yes | FK to investors entity v0.2 when matched |
| 4 | `investor_name` | `large_string` | Yes | Matched participant name |
| 5 | `raw_investor_name` | `large_string` | Yes | Participant/acquirer text from extraction |
| 6 | `role` | `large_string` | No | `lead`, `co`, `acquirer`, or `unknown` |
| 7 | `company_normalized_name` | `large_string` | No | Audit/migration key; not the primary FK |
| 8 | `company_name_raw` | `large_string` | No | Canonical company display name at build time |
| 9 | `round_name` | `large_string` | Yes | Funding round designation; null for terminal outcomes |
| 10 | `announce_date` | `large_string` | Yes | ISO/partial date string; null if unknown |
| 11 | `amount_usd` | `int64` | Yes | Round/deal/IPO amount in USD |
| 12 | `per_vc_amount_usd` | `int64` | Yes | Participant amount where known |
| 13 | `source_table` | `large_string` | No | `web_search_v02` for migrated cache or `web_search_v03` |
| 14 | `source_url` | `large_string` | Yes | Supporting evidence URL; null for legacy cache without URL provenance |
| 15 | `confidence` | `double` | No | Extraction confidence in `[0,1]` |
| 16 | `schema_version` | `large_string` | No | Constant `v0.3.0` |
| 17 | `investor_type` | `large_string` | Yes | Investor type when available |
| 18 | `event_type` | `large_string` | No | Funding-level outcome enum below |
| 19 | `event_subtype` | `large_string` | Yes | Round name or subtype |
| 20 | `position_change_shares` | `int64` | Yes | Reserved compatibility column |
| 21 | `position_value_usd` | `int64` | Yes | Reserved compatibility column |
| 22 | `data_source` | `large_string` | No | `<search_backend>+deepseek` |
| 23 | `metadata_json` | `large_string` | No | Raw extraction plus migration provenance |

Schema metadata contains `schema_version=v0.3.0` and `company_entity=companies_chip_subset_entity_v0.3.parquet`.

## `event_type` enum

`funding_round`, `funding_failed`, `funding_withdrawn`, `ipo_filed`, `ipo_completed`, `ipo_withdrawn`, `acquired`, `company_closed`.

## Cache compatibility and provenance

The first 166-company cache tranche was produced by `collect_funding_events_v02.py`. It is accepted as an explicit compatibility migration because its event fields are a subset of v0.3. Migrated rows use `source_table=web_search_v02`; unavailable `source_url` values remain null; `metadata_json.cache_migration` is `v0.2-compatible`. This does not claim that those caches were produced by the v0.3 collector. Future collection and retries use `collect_funding_events_v03.py`, which includes source URLs in the LLM evidence and records `source_type`.

The builder defaults to refusing an existing output. Replacement requires `--force-output`.
