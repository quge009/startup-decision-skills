# Investors entity v0.3

Investor v0.3 is a strictly additive extension of the authoritative `investors_entity_v0.2.parquet` snapshot. It contains exactly **1,915 rows** in this order:

1. the frozen **989 v0.2 rows**, preserved value-for-value and in their original order; then
2. the **926** entities released in `interface_counterparty_identity/v1/new_investors_entity.jsonl`.

The frozen v0.1 entity table is not part of the v0.3 entity universe and **no legacy entity row may be appended**. It may be read only as a pinned aid for conservatively mapping compatible v0.1 provenance into the v0.2 base. See `investors_provenance_v0.2.spec.md`.

## Columns and new rows

| # | Column | Arrow type | Arrow nullable | Meaning and release constraint |
|---:|---|---|:---:|---|
| 1 | `investor_id` | `large_string` | Yes | Stable investor PK; non-null and unique in this snapshot |
| 2 | `schema_version` | `large_string` | Yes | Row schema version inherited from the source generation |
| 3 | `name` | `large_string` | Yes | Canonical investor display name; non-null in this snapshot |
| 4 | `normalized_name` | `large_string` | Yes | Normalized name used for conservative exact matching |
| 5 | `website` | `large_string` | Yes | Investor website when supported |
| 6 | `domain` | `large_string` | Yes | Normalized website domain when supported |
| 7 | `location_country` | `string` | Yes | Country value from the bounded source set |
| 8 | `location_state` | `list<element: string>` | Yes | Zero or more state/region values |
| 9 | `location_city` | `list<element: string>` | Yes | Zero or more city values |
| 10 | `stage_focus` | `list<element: string>` | Yes | Reported investment-stage focus |
| 11 | `industry_focus` | `list<element: string>` | Yes | Reported industry focus |
| 12 | `founded_year` | `int32` | Yes | Investor founding year when known |
| 13 | `investor_type` | `list<element: string>` | Yes | One or more normalized investor types |
| 14 | `tier` | `int32` | Yes | Source-defined investor tier when available |
| 15 | `wikidata_qid` | `large_string` | Yes | Accepted Wikidata entity identifier |
| 16 | `findfunding_slug` | `large_string` | Yes | FindFunding source slug when available |
| 17 | `manual_seed_slug` | `large_string` | Yes | Stable manual-seed slug when available |
| 18 | `crunchbase_permalink` | `large_string` | Yes | Historical Crunchbase permalink when retained |
| 19 | `source_set` | `list<element: string>` | Yes | Sources contributing to the entity row |
| 20 | `first_seen_at` | `large_string` | Yes | First observation timestamp/date representation |
| 21 | `aum_usd_approx` | `int64` | Yes | Approximate assets under management when supported |
| 22 | `sec_cik` | `large_string` | Yes | SEC Central Index Key when resolved |

All fields retain the physical Arrow nullability of the frozen pandas-derived
base schema. Required snapshot properties, including non-null unique IDs, are
enforced as invariants below rather than encoded as non-null Arrow fields.

New rows use `schema_version=v0.3.0`, contain all 22 keys, use list-valued `investor_type`, and include `interface_recovery` in `source_set`. Accepted QIDs produce `wikidata:<QID>` IDs; without one, the stable namespace is `interface_recovery:<24-hex>`. Optional nulls mean no reliable source was available, not that fields were accidentally omitted.

## Deduplication and foreign keys

New-entity deduplication checks accepted QID and punctuation-insensitive exact canonical name against the pinned 989-row base and other new rows. Fuzzy similarity is never a merge rule.

Every published `EXISTING_INVESTOR` Interface binding references an ID in the authoritative 989-row base. Every published `NEW_INVESTOR` binding references one of the 926 appended package rows, including 58 stable-ID legacy-registry investors actually observed in Interface. No binding is silently suppressed.

## Invariants

- row count is exactly `989 + 926 = 1,915`;
- rows `[0:989]` equal the pinned v0.2 table value-for-value;
- no v0.1 entity row is appended;
- all investor IDs are unique;
- all materialized Interface investor foreign keys resolve to this table;
- the companion provenance table is partial for the frozen base and complete for the new `interface_recovery` fields, as specified separately.
