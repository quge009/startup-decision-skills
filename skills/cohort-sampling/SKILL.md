---
name: cohort-sampling
description: Build a bounded company cohort, create deterministic stratified train/validation splits, and sample an untouched labeled holdout while excluding previously used IDs. Use for reproducible research cohort construction and evaluation sampling.
---

# Cohort Sampling

Construct cohorts without hiding selection or reuse decisions in source code.
Inputs and outputs are always supplied explicitly.

## Workflow

1. Use `scripts/filter_cohort.py` to select the AI-company research cohort from
   source CSV chunks. Supply input directory, filename glob, output path, and
   founding-year bounds. The documented category/keyword rule and shared
   outcome-label mapping remain stable method choices.
2. Add any classification fields required for stratification outside this
   skill, then run `scripts/split_cohort.py`. Supply the stratum column,
   validation ratio, seed, and both output paths.
3. Use `scripts/sample_holdout.py` for an independent labeled holdout. Supply
   sample size, label and ID columns, seed, train ratio, output paths, and every
   prior sample through repeated `--exclude` arguments.
4. Record all CLI arguments and input hashes with the research run. Never tune
   sample composition after inspecting evaluation results.

`sample_holdout.py` allocates label quotas proportionally with deterministic
largest-remainder rounding. Both sampling tools keep singleton strata in the
training partition rather than producing an empty training stratum.

## Requirements

Python 3.10+; the scripts use only the standard library.
