# Dataset Index

Self-produced datasets from the business-predictive-model research are
published on Hugging Face. This index links each release to its HF dataset
card.

## Part 2 — Post-investment behavior (investor_behavior_analysis)

All tables below live in a single HF dataset repo
[`quge007/investor-behavior-dataset`](https://huggingface.co/datasets/quge007/investor-behavior-dataset)
(private), exposed as four configs:

| Config | Contents | Notes |
|---|---|---|
| `entity` | `investors_entity_v0.3`, `investors_provenance_v0.2` | 1,915 investors, 9 types |
| `events` | funding / exposure / interface events (clean_human v0.2, identity_patch v1) | chip-startup scope |
| `chains` | `chains_chip_v0.3` (clean_human v0.2, identity_patch v1) | funding-level outcome chains |
| `companies` | `companies_entity_v0.2`, `category_v0.2`, `edges_long_v0.1`, `associated_*` | entity + industry + edges |

> Schemas (`.spec.md` + `.schema.json`) ship alongside the dataset as the
> data dictionary (see `schemas/` in the HF repo).
>
> **License caveat**: datasets derive from public sources (SEC EDGAR,
> Wikidata CC0, company sites, news). They contain only aggregate /
> self-produced analysis fields, not redistributable third-party raw rows.
