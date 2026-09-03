---
name: post-investment-behavior
description: Derive auditable investor-company post-investment events and descriptive investor profiles by temporally joining investment evidence to interface events. Use for descriptive investor-behavior research, not causal inference or company-outcome prediction.
---

# Post-Investment Behavior

Analyze what is publicly observed after documented investment evidence. This
method is independent of event chains and does not use company outcome labels.

## Workflow

1. Supply investor entities, company entities, funding events, and
   identity-resolved Interface events using explicit CLI paths.
2. Run `scripts/analyze_post_investment_behavior.py --output-dir ...`.
3. Count an event as confirmed post-investment only when dated investment
   evidence precedes it; keep overlapping, unknown, absent, and later-only
   evidence states separate.
4. Report denominators and coverage with every type-level comparison. Treat the
   results as descriptive observed behavior, not investor effects, causal claims,
   or comprehensive activity histories.

Read the table contracts in `references/` when adapting inputs. The command
refuses to overwrite an existing output directory and writes event-level,
profile-level, and summary artifacts together for auditability.

## Requirements

Python 3.10+ and `pyarrow`.
