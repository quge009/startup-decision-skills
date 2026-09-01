---
name: cohort-sampling
description: "Build benchmark cohorts from raw Crunchbase / opensporks dumps: (1) filter a 2.87M-row dump into the AI-startup cohort_v2 (Tier 1 AI-category allow-list OR reverse-keyword recall + Tier 3 founded 2010-2026, outcome_label attached), (2) A4 stratified 70/30 train-val split by archetype_primary, and (3) loop-4 pool-1 sampler that draws exactly 4000 labeled-only rows (seed 45) from untouched cohort_v2 with a per-sub-label 7:3 train/val split for frozen F0.5 benchmark holdout. Pure stdlib. Use for benchmark cohort construction and train/val sampling in startup-prediction / VC calibration work. Trigger on: 'filter cohort rows', 'split cohort train val', 'sample pool 1', 'build benchmark training/validation set'."
---

# Cohort Sampling — benchmark cohort building blocks

Three deterministic, stdlib-only step scripts that build the labeled
training / validation cohorts used for F0.5 calibration:

| Script | Step | What it does |
|---|---|---|
| `cb_step_f_filter_cohort_v2.py` | Filter v2 | Streams the full opensporks/Crunchbase dump and emits `cohort_v2.csv` applying Tier 1 (AI category allow-list OR reverse-keyword recall) + Tier 3 (founded 2010-2026), attaching `outcome_label` | 
| `cb_step_j_a4_split.py` | A4 split | Stratified 70% train / 30% val split of `cohort_v2_classified.csv` by `archetype_primary` (seed 42, reproducible) |
| `cb_step_ck_loop4_pool1_sampler.py` | Loop 4 pool 1 sampler | Draws exactly 4000 labeled rows from untouched `cohort_v2` (seed 45, excluding prior pool 1 ids), split 7:3 train/val per sub-label for a frozen unbiased holdout |

## Filter v2 — `cb_step_f_filter_cohort_v2.py`

Applies ONLY Tier 1 and Tier 3 per charter v5 §1 Task A. The v1 Tier 2
(funding stage), Tier 4 (description length), and Tier 5 (outcome !=
AMBIGUOUS) filters are deliberately dropped — outcome classification is
delegated to `cb_outcome_label.outcome_label_mapping` (from the
`outcome-labeling` skill), which assigns every row one of 8 outcome labels.

Input: raw hugginface-format chunks `crunchbase_*.csv` under
`<CB_DATA_HOME>/.data/pipeline_benchmark/crunchbase_hf_raw`.
Output: `cohort_v2.csv` under the filtered dir, one row per cohort member with
columns `id, name, website, short_description, categories, founded_on,
operating_status, ipo_status, last_funding_type, last_funding_at,
funding_total, growth_insight_description, locations, permalink, url,
outcome_label, tier1_match_path`.

## A4 split — `cb_step_j_a4_split.py`

Stratified train/val split of the classified cohort. Rows are grouped by
`archetype_primary`, shuffled deterministically (seed 42), and each archetype
bucket is split 70% train / 30% val. Edge case: an archetype with a single row
goes entirely to train (val needs ≥ 1 row). Validation prints per-archetype and
per-outcome_label counts for both halves, plus the archetype-positive subset
that drives A5/A6 evaluation.

## Loop 4 pool 1 sampler — `cb_step_ck_loop4_pool1_sampler.py`

Frozen unbiased holdout for the baseline generalization test (See
`_AUDIT_2026-07-06.md §20.5`). 4000 labeled rows scaled from pool 1's 396
sub-label fractions (x10.1), excluding the 396 pool-1 ids already touched by
iter 3 / iter 5, plus a clean-cards constraint (every sampled id must have
`workspace_a5/working/<id>/cleaned_card.md`). Each sub-label is sampled with
`random.Random(45)` then independently split 7:3 to preserve the labeled
distribution in both halves. Dry-run by default; `--write` persists.

## Usage

```bash
# Step F — filter the raw dump into cohort_v2.csv (prints funnel + label dist)
python3 scripts/cb_step_f_filter_cohort_v2.py

# Step J — stratified train/val split of the classified cohort
python3 scripts/cb_step_j_a4_split.py

# Step CK — pool 1 sampler dry-run (default, writes nothing)
python3 scripts/cb_step_ck_loop4_pool1_sampler.py
# persist the train/val CSVs
python3 scripts/cb_step_ck_loop4_pool1_sampler.py --write
```

All paths default to `<home>/.data/pipeline_benchmark/...`. Override the base
with `CB_DATA_HOME`, or pin individual paths:

- `CB_RAW_DIR`, `CB_FILTERED_DIR` — filter v2 input / output dirs.
- `CB_A4_INPUT`, `CB_A4_TRAIN`, `CB_A4_VAL` (or `CB_FILTERED_DIR` for all three) — A4 split paths.
- `CB_INPUT_CSV`, `CB_POOL1_ITER3`, `CB_POOL1_ITER5`, `CB_CLEAN_A5`, `CB_OUT_TRAIN`, `CB_OUT_VAL` — loop 4 sampler paths (also exposed as `--input-csv`, `--pool1-iter3`, `--pool1-iter5`, `--clean-a5`, `--out-train`, `--out-val`).

## Requirements

Pure Python stdlib (`csv`, `re`, `random`, `argparse`, `collections`,
`pathlib`, `os`, `sys`). No external deps. `cb_step_f_filter_cohort_v2.py`
additionally imports `cb_outcome_label.outcome_label_mapping` / `VALID_LABELS`
— copy it in alongside the script (shipped in the `outcome-labeling` skill).
The loop 4 sampler's clean-cards constraint requires the A5.2
`cleaned_card.md` workspace output to be present.

## Self-test

Each script parses and runs against the paths it is given:

```bash
python3 -m py_compile scripts/cb_step_f_filter_cohort_v2.py \
  scripts/cb_step_j_a4_split.py scripts/cb_step_ck_loop4_pool1_sampler.py
```

`cb_step_ck_loop4_pool1_sampler.py` self-checks on every dry-run: it fails fast
(non-zero exit) if any sub-label pool is short of its target, and prints a
stratum x split verification table so the 4000/2801/1199 row counts and 7:3
per-sub-label ratios are auditable before `--write`.