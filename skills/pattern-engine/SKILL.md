---
name: pattern-engine
description: "Configuration-driven pattern recognition engine over entity event data. `analyze_chip_patterns_v01.py` is an analysis-only derived layer that reads versioned production Parquet tables (entity, investor, exposure, interface, chains), validates input contracts plus an external JSON Pattern specification, and writes auditable chain / company / investor / Pattern artifacts atomically (never mutating inputs). `generate_chip_pattern_candidates_v02.py` freezes a label-blind candidate grammar by projecting SAFE_* columns, validating checked-in schemas, and publishing a new directory atomically. Pure pyarrow engine, env-var overridable data dir, strongly-validating CLI. Use for CHIP pattern analysis, candidate generation, and config-driven event-pattern recognition on investor/company event chains. Trigger on: 'analyze CHIP patterns', 'run pattern engine', 'generate pattern candidates', 'evaluate event patterns'."
---

# Pattern Engine — configuration-driven CHIP pattern recognition

Deterministic, configuration-driven pattern recognition over entity event
data. The engine never mutates its inputs; every artifact is written to a
fresh output directory.

## What it does

Two standalone scripts share a data-root default that is overridable via the
`INVESTOR_BEHAVIOR_DATA_DIR` environment variable (falling back to
`~/investor-behavior-analysis`):

### `analyze_chip_patterns_v01.py` (analysis core)

Reads five versioned production Parquets:

- entity (companies) table
- investor table
- exposure events
- interface events
- chain table

It validates the input contract (company label consistency, chain window
status, event references, column requirements) and an external JSON Pattern
specification (`--pattern-spec-path` / `--pattern-config`), then derives:

- `chain_ordered_features_v0.2.parquet`
- `company_chain_profiles_v0.2.parquet`
- `investor_interface_profiles_v0.2.parquet`
- `interface_identity_rows_v0.2.parquet`
- `pattern_prevalence_sf_v0.2.json`
- `interface_identity_coverage_v0.2.json`
- `summary_v0.2.md`
- `chip_patterns_resolved_v0.1.json`
- `run_manifest_v0.2.json`

The output directory must not already exist (the engine refuses to overwrite),
and it must not alias any input. Because every artifact is produced in a temp
directory and `os.replace`d into place, a partial write never leaves a partial
output behind.

### `generate_chip_pattern_candidates_v02.py` (candidate grammar freezer)

Label blind. It opens only the three supplied v0.3 tables (chains, exposure,
interface), projects the `SAFE_*` columns, validates three caller-supplied JSON
schemas, and publishes a frozen candidate grammar to a new `--output-dir`
(required). No outcome labels or prior pattern/results are accepted.

## Usage

```bash
# Analyze CHIP patterns from entity event data.
# Defaults read Parquets under $INVESTOR_BEHAVIOR_DATA_DIR (or ~/investor-behavior-analysis).
INVESTOR_BEHAVIOR_DATA_DIR=/path/to/data \
python3 scripts/analyze_chip_patterns_v01.py \
  --entity-path ... --investor-path ... \
  --exposure-path ... --interface-path ... --chain-path ... \
  --output-dir ./out \
  --pattern-spec-path configs/chip_patterns_v0.1.json \
  --equivalence-margin 0.05
```

```bash
# Freeze a label-blind candidate grammar from the three v0.3 tables.
INVESTOR_BEHAVIOR_DATA_DIR=/path/to/data \
python3 scripts/generate_chip_pattern_candidates_v02.py \
  --chain-path ... --exposure-path ... --interface-path ... \
  --chain-schema ... --exposure-schema ... --interface-schema ... \
  --output-dir ./candidate-out \
  --max-sequence-length 3 --min-chain-support 5 --min-company-support 5
```

Each CLI flag in both scripts remains fully functional; only the data-root
default is env-var overridable.

## Data location

Set `INVESTOR_BEHAVIOR_DATA_DIR` to the directory containing the versioned
Parquet tables when it is not the default `~/investor-behavior-analysis`.
Individual input paths can still be passed explicitly per CLI flag.

## Requirements

- Python 3.10+ (uses `from __future__ import annotations`, `dict[str, ...]` typing)
- `pyarrow` (Parquet read/write)
- `_common.py` (provides `make_slug` for the analyzer)

The engine itself is deterministic and validates its inputs and config
strictly; the `run_manifest_v0.2.json` records script/config/input sha256s,
pyarrow version, and git provenance for auditability.

The release does not bundle the study's exhaustive registry, 501-company analysis
subset, or frozen pattern config. Users must supply a compatible config and the
schema contracts shipped with the companion dataset; this skill reproduces the
engine, not the paper's full pattern-analysis dataset.

## Self-test

Both scripts parse-compile cleanly:

```bash
python3 -m py_compile scripts/analyze_chip_patterns_v01.py
python3 -m py_compile scripts/generate_chip_pattern_candidates_v02.py
```

Sanity-check the CLIs agree on defaults without touching data:

```bash
python3 scripts/generate_chip_pattern_candidates_v02.py --help
python3 scripts/analyze_chip_patterns_v01.py --help
```

Run each against a real (or synthetic) set of versioned Parquet tables before
relying on derived artifacts for downstream decisions.
