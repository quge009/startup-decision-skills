# Dataset Index

Self-produced datasets from the business-predictive-model research are
published on Hugging Face. This index links each release to its HF dataset
card.

## Part 2 — Post-investment behavior (investor_behavior_analysis)

All tables below live in a single HF dataset repo
[`quge007/eventchain`](https://huggingface.co/datasets/quge007/eventchain),
exposed as eleven table-level configs:

| Config | Contents | Notes |
|---|---|---|
| `investors` | `investors_entity_v0.3` | 1,915 investors, 9 primary types |
| `investors_provenance` | `investors_provenance_v0.2` | investor field provenance |
| `funding_events` | `funding_events_chip_v0.3_clean_human_v0.2` | funding participant rows |
| `exposure_events` | `exposure_events_chip_v0.3_clean_human_v0.2` | externally observable company events |
| `interface_events` | `interface_events_chip_v0.4_clean_human_v0.2_identity_patch_v1` | investor/company interface events |
| `chains` | `chains_chip_v0.3_clean_human_v0.2_identity_patch_v1` | funding-level outcome chains |
| `companies` | `companies_entity_v0.2` | global canonical companies |
| `categories` | `category_v0.2` | company industry labels |
| `associated_entities` | `associated_entity_v0.1` | associated counterparties |
| `associated_provenance` | `associated_provenance_v0.1` | associated-entity field provenance |
| `chip_companies` | `companies_chip_subset_entity_v0.3` | 2,547-company FK target for chip events and chains |

> Schemas (`.spec.md` + `.schema.json`) ship alongside the dataset as the
> data dictionary (see `schemas/` in the HF repo).
>
> **License caveat**: datasets derive from public sources (SEC EDGAR,
> Wikidata CC0, company sites, news). They contain only aggregate /
> self-produced analysis fields, not redistributable third-party raw rows.
