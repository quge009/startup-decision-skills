---
name: benchmark-validation
description: Label company outcomes, construct reproducible benchmark cohorts and holdouts, and compute precision-weighted evaluation statistics. Use when validating a startup proposal predictor against company outcomes.
---

# Benchmark Validation

Build the outcome-labelled benchmark used to validate a proposal predictor.
Keep ground-truth construction, sample selection, and reported metrics explicit
and reproducible.

## Workflow

1. Use `scripts/outcome_label.py` to map company records to the fixed eight-label
   outcome ontology and translate verdict/outcome pairs into evaluation cells.
2. Use `scripts/filter_cohort.py` to select a research cohort from source CSV
   chunks. Supply input directory, filename glob, output path, founding-year
   bounds, repeated `--category` values, and an optional `--keyword-regex`. The
   caller-defined category/keyword rule and shared
   outcome-label mapping remain stable method choices.
3. Add any classification fields required for stratification outside this
   skill, then run `scripts/split_cohort.py`. Supply the stratum column,
   validation ratio, seed, and both output paths.
4. Use `scripts/sample_holdout.py` for an independent labeled holdout. Supply
   sample size, label and ID columns, seed, train ratio, output paths, and every
   prior sample through repeated `--exclude` arguments.
5. Run `scripts/compute_f05_stats.py` on the completed result CSV to compute
   confusion-matrix counts, precision, recall, F0.5, and Wilson intervals.
6. Record all CLI arguments and input hashes with the research run. Never tune
   sample composition after inspecting evaluation results.

`outcome_label.py` defines the authoritative label and truth-class mapping.
`sample_holdout.py` allocates label quotas proportionally with deterministic
largest-remainder rounding. Both sampling tools keep singleton strata in the
training partition rather than producing an empty training stratum.

## Requirements

Python 3.10+; the scripts use only the standard library.
