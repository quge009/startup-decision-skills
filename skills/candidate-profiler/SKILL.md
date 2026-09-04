---
name: candidate-profiler
description: Convert a free-text startup proposal into a structured five-dimension candidate card with founding-year metadata. Use to prepare a proposal for evidence-grounded market and moat assessment.
---

# Candidate Profiler

Convert a free-text proposal into `candidate_card.md`. Treat the proposal as the
primary evidence: do not add later company outcomes or perform web research.

## Workflow

1. Read `reference/candidate_card_template.md`.
2. Extract a four-digit founding year; use `0000` only when unrecoverable.
3. Produce the five-dimension candidate card without adding unsupported facts.
4. Validate with `scripts/validate_card.py`, then write atomically with
   `scripts/write_output.py`.

For field definitions, boundary rules, output schemas, edge cases, and examples,
read `reference/workflow.md`. Example inputs and outputs are under `examples/`.

## Requirements

Python 3.10+. Validation and writing helpers use only the standard library.
