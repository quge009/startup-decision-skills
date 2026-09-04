---
name: evaluate-proposal
description: Evaluate a startup proposal through candidate profiling, time-bounded market and moat research, evidence interpretation, scoring, and structured reasoning. Use for an end-to-end evidence-grounded startup assessment.
---

# Evaluate Proposal

Orchestrate the complete proposal workflow. Preserve intermediate artifacts so
the final decision remains auditable.

## Workflow

1. Save the proposal as `free_text.txt` in a new working directory.
2. Use `candidate-profiler` to create and validate the candidate card.
3. Use `market-check` and `moat-check` independently to build uniform queries,
   retrieve evidence only within the founding-year window, and produce
   validated M/U checks.
4. Run `scripts/aggregate.py`; generate `reasoning.json` from the recorded
   evidence and validate it with `scripts/validate_reasoning_schema.py`.
5. If required evidence or founding year is unavailable, follow the explicit
   fail/warn behavior rather than inventing values.

Read `reference/workflow.md` for artifact schemas, commands, scoring rules,
failure handling, and the final summary format.

## Requirements

Python 3.10+, the sibling `candidate-profiler`, `market-check`, and `moat-check`
skills, and their documented retrieval credentials.
