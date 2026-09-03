---
name: evaluate-proposal
description: Evaluate a business proposal through candidate classification, time-bounded market and uniqueness research, evidence interpretation, scoring, and structured reasoning. Use for an end-to-end evidence-grounded startup assessment.
---

# Evaluate Proposal

Orchestrate the complete proposal workflow. Preserve intermediate artifacts so
the final decision remains auditable.

## Workflow

1. Save the proposal as `free_text.txt` in a new working directory.
2. Use `candidate-classifier` to create and validate the candidate card and
   archetype metadata.
3. Use `check-interpreter` to build uniform queries, retrieve evidence only
   within the founding-year window, and produce validated M/U checks.
4. Run `scripts/aggregate.py`; generate `reasoning.json` from the recorded
   evidence and validate it with `scripts/validate_reasoning_schema.py`.
5. Never skip M/U checks because of archetype. If required evidence or founding
   year is unavailable, follow the explicit fail/warn behavior rather than
   inventing values.

Read `reference/workflow.md` for artifact schemas, commands, scoring rules,
failure handling, and the final summary format.

## Requirements

Python 3.10+, the sibling `candidate-classifier` and `check-interpreter` skills,
and their documented retrieval credentials.
