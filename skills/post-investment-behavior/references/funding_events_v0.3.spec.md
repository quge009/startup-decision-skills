# Schema: funding events v0.3

**Version:** v0.3.0

**Collector:** `collect_funding_events.py` in the event-evidence-collector skill

**Builder:** `build_funding_events.py` in the event-chain-builder skill

**Company FK:** caller-supplied company entity table
**Machine-readable schema:** `schemas/funding_events_v0.3.schema.json` (authoritative machine contract loaded directly by the builder)

## Purpose and v0.2 changes

One row per participant in a company funding-level outcome.

- adds required `company_id` rather than relying on normalized-name remapping;
- materializes the previously specified but omitted `source_url` and `schema_version` columns;
- fixes Arrow types explicitly instead of pandas inference;
- formalizes terminal/failed outcome values already emitted by the collector;
- uses stable hashed event IDs based on company, event, date, and participant semantics.

## Arrow schema

| # | Column | Arrow type | Nullable | Contract |
|---:|---|---|:---:|---|
| 1 | `event_id` | `large_string` | No | PK; `fund:<sha1-prefix>` |
| 2 | `company_id` | `large_string` | No | FK to the supplied company entity table |
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

Schema metadata contains `schema_version=v0.3.0` and identifies the company entity contract.

## `event_type` enum

`funding_round`, `funding_failed`, `funding_withdrawn`, `ipo_filed`, `ipo_completed`, `ipo_withdrawn`, `acquired`, `company_closed`.

## Cache compatibility and provenance


The builder defaults to refusing an existing output. Replacement requires `--force-output`.
