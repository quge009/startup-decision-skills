---
name: leakage-scan
description: "Scan startup candidate cards for outcome leakage — phrases that reveal post-founding outcomes (IPO/exit, acquisition, shutdown, late funding rounds, retrospective outcome language) which would leak the SUCCESS/FAILURE label to a downstream model. Scans a proposal description (primary) + LLM Notes (secondary) + structural card fields, scores each card HIGH/MED/LOW severity, and writes per-row + per-category JSON + markdown reports. Pure stdlib, self-contained. Use for data-quality gating before training/evaluating startup-prediction pipelines. Trigger on: 'scan cards for leakage', 'leakage check', 'outcome leakage scan', 'post-founding outcome leak'."
---

# Leakage Scan — outcome leakage over company cards

Scans candidate company cards for outcome-related leakage that would trivially
reveal the SUCCESS/FAILURE label to a downstream model. This is a data-quality
gate, not a predictor.

## Why

Card text is scraped from *current* sources (Crunchbase categories + website
body text), so it reflects current state rather than founding-era state. Cards
may therefore contain post-founding outcome information (IPO tickers,
acquisition language, defunct-domain markers, late funding rounds, ...) that
already encodes the answer the pipeline is supposed to predict.

## What it detects

Six leakage categories, scanned with deterministic regex (no LLM):

| # | Category | Signals |
|---|---|---|
| 1 | Public / IPO | ticker symbols (`NASDAQ: ABCD`), "publicly traded", "listed on", IPO year, share price, `Public/private: public` |
| 2 | Acquisition / merger | "acquired by", "wholly owned by", "now part of", "rebranded as", "merged with" |
| 3 | Shutdown / defunct | "shut down", "no longer active", "domain for sale", "went out of business", "ceased operations" |
| 4 | Post-founding funding | Series B/C/D... rounds, "$X raised", "unicorn", billion-dollar valuation |
| 5 | Post-founding temporal | years > founding_year + 2 mentioned as events |
| 6 | Retrospective / observed outcome | LLM Notes stating the outcome directly ("has since been acquired", "exit via IPO", "recent shutdown") |

## Scan scope

- **Primary** — "Original proposal description" (the raw text the pipeline consumes)
- **Secondary** — LLM-generated "Notes" sections (may reflect outcome knowledge)
- **Structural** — card metadata fields (Public/private, founding year)

Each card gets a weighted severity score and a HIGH / MED / LOW bucket:
structural + explicit proposal leaks weight highest; LLM retrospection weights lowest.

## Usage

```bash
# Cards live under  $CARDS_DIR/<card_id>/candidate_card.md
# Pool CSVs live under  $POOL_1_DIR/a5_train_results_v15a_iter*.csv
python3 scripts/cb_step_ar_leakage_scan.py
```

Config is via environment variables (module-level constants, all overridable):

| Env var | Default | Meaning |
|---|---|---|
| `POOL_1_DIR` | `~/_data/pipeline_benchmark/crunchbase_filtered` | Directory holding the pool-1 result CSVs |
| `CARDS_DIR` | `~/workspace_a4_cards/working` | Root dir of candidate cards (`<id>/candidate_card.md`) |
| `LEAKAGE_OUT_JSON` | `/tmp/leakage_scan_output.json` | Per-row leakage flags + aggregate stats (JSON) |
| `LEAKAGE_OUT_MD` | `/tmp/leakage_scan_report.md` | Human-readable summary + HIGH-severity samples (markdown) |

## Output

- **JSON** — per-row leakage flags, severity score + reasons, severity by truth
  and by outcome, top pattern-hit ranks.
- **Markdown** — severity distribution, severity-by-outcome table, and the
  top-10 HIGH-severity flagged cards per truth class with their matched phrases.

Cards with a missing `candidate_card.md` are counted as missing (not scored).

## Requirements

Pure Python stdlib (`csv`, `json`, `os`, `re`, `sys`, `pathlib`,
`collections`). No external dependencies. The card format and CSV columns are
project-specific (`candidate_card.md`, `outcome_label`); adapt the defaults to
your own paths via the env vars above.

## Self-test

`python3 -m py_compile scripts/cb_step_ar_leakage_scan.py` confirms the script
parses. A full side-by-side run against a small reference card set is the
regression check — the pattern tables and severity weighting are fixed
deterministic constants; do not alter them without re-validating precision.
