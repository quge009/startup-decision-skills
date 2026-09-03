# Interface events v0.4

Version v0.4.0 is strictly additive to the physical v0.3 source schema: it appends nullable `associated_entity_id` and non-null `counterparty_resolution_status`. All original columns retain their source Arrow types and order. `event_id`, `raw_investor_name`, event date/type/subtype, title, summary, source fields, confidence, scraped timestamp, and metadata JSON retain their values and semantics. The row `schema_version` and schema metadata become `v0.4.0`.

## Arrow schema

| # | Column | Arrow type | Nullable | Contract |
|---:|---|---|:---:|---|
| 1 | `event_id` | `large_string` | No | PK; stable Interface event identifier |
| 2 | `company_id` | `large_string` | No | FK to the supplied company entity table |
| 3 | `investor_id` | `large_string` | Yes | FK to investor entity v0.3 when resolved as an investor |
| 4 | `raw_investor_name` | `large_string` | Yes | Counterparty text retained from extraction |
| 5 | `event_date` | `large_string` | Yes | ISO or partial event date |
| 6 | `event_type` | `large_string` | No | Interface event enum below |
| 7 | `event_subtype` | `large_string` | Yes | Optional event refinement |
| 8 | `title` | `large_string` | No | Short event title; may be empty but not null |
| 9 | `summary` | `large_string` | No | Event summary; may be empty but not null |
| 10 | `source_url` | `large_string` | Yes | Supporting evidence URL |
| 11 | `source_type` | `large_string` | No | Retrieval/source class |
| 12 | `confidence` | `double` | No | Extraction confidence in `[0,1]` |
| 13 | `scraped_at` | `large_string` | No | Source retrieval timestamp |
| 14 | `schema_version` | `large_string` | No | Constant `v0.4.0` |
| 15 | `metadata_json` | `large_string` | No | Raw extraction and materialization metadata |
| 16 | `associated_entity_id` | `large_string` | Yes | FK to associated entity v0.1 for a resolved non-investor counterparty |
| 17 | `counterparty_resolution_status` | `large_string` | No | Identity-resolution status enum below |

Schema metadata records `schema_version=v0.4.0` and the exact company,
investor, and associated-entity FK targets.

## Controlled enums

`event_type` is one of `activist_action`, `board_change`, `exit`,
`funding_participation`, `major_holder_change`, `other`,
`secondary_transaction`, `service_provider`, or `strategic_investment`.

`counterparty_resolution_status` is one of `EXISTING_INVESTOR`,
`NEW_INVESTOR`, `ASSOCIATED_ENTITY`, `AMBIGUOUS`, or `UNRESOLVED`.

## Identity-resolution rules

Statuses are `EXISTING_INVESTOR`, `NEW_INVESTOR`, `ASSOCIATED_ENTITY`, `AMBIGUOUS`, and `UNRESOLVED`. Investor statuses require `investor_id` and prohibit `associated_entity_id`; associated status requires `associated_entity_id` and prohibits `investor_id`; ambiguous/unresolved prohibit both. `investor_id` references investor entity v0.3 and `associated_entity_id` references associated entity v0.1.

Rows outside the 2,952-row identity overlay retain existing investor IDs. Existing mapped rows receive `EXISTING_INVESTOR`; unnamed/unmapped rows receive `UNRESOLVED`. This status assignment does not infer a counterparty identity.

An `EXISTING_INVESTOR` binding is publishable only when its ID exists in the authoritative 989-row v0.2 base. Confirmed investors present only in the pinned legacy registry are explicitly released as `NEW_INVESTOR` rows in the v0.3 extension; materialization never silently changes overlay status or IDs.
