---
name: candidate-classifier
description: Generate a structured candidate card, founding year, and business archetype from a free-text proposal. Use to prepare a proposal for evidence-grounded market and uniqueness assessment.
---

# Candidate Classifier

Convert a free-text proposal into `candidate_card.md` and `archetype.json`.
Treat the proposal as the primary evidence: do not add later company outcomes or
perform web research during classification.

## Workflow

1. Read `reference/candidate_card_template.md` and
   `reference/archetype_taxonomy.md`.
2. Extract a four-digit founding year; use `0000` only when unrecoverable.
3. Produce the five-dimension card and an auxiliary archetype classification.
   Archetype never short-circuits downstream evaluation.
4. Validate with `scripts/validate_card.py` and
   `scripts/validate_archetype.py`, then write atomically with
   `scripts/write_outputs.py`.

For field definitions, boundary rules, output schemas, edge cases, and examples,
read `reference/workflow.md`. Example inputs and outputs are under `examples/`.

## Requirements

Python 3.10+. Validation and writing helpers use only the standard library.
