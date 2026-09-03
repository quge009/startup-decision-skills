---
name: leakage-scan
description: Scan company-card text and metadata for post-founding outcome signals that could reveal evaluation labels. Use as a deterministic data-quality gate before training or evaluating a company assessment workflow.
---

# Leakage Scan

Detect outcome information in material that is supposed to represent only the
decision-time view. This is a quality-control tool, not a predictor.

## Workflow

1. Supply one or more result CSVs through repeated `--results-csv`. Each row
   must contain `id`, `name`, and `outcome_label`.
2. Supply `--cards-dir`; each card is read from
   `<cards-dir>/<id>/candidate_card.md`.
3. Supply `--output-json` and `--output-markdown` explicitly.
4. Review HIGH and MED findings before evaluation. Missing cards are reported,
   never treated as clean.

The deterministic rules cover public listing, acquisition, shutdown, later
funding, post-founding dates, retrospective language, and structural status
fields. The JSON preserves row-level matches; the Markdown summarizes severity
and representative findings.

```bash
python3 scripts/scan_outcome_leakage.py \
  --results-csv results.csv --cards-dir cards \
  --output-json leakage.json --output-markdown leakage.md
```

## Requirements

Python 3.10+; standard library only.
