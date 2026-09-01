---
name: event-chain-builder
description: "Build a multi-source event timeline for a company (funding, exposure, interface) and chain events into chain:... records with schema-contract-driven Arrow typing. Use for assembling cross-source event history and outcome-level chain materialization for startup prediction / VC analysis. Trigger on: 'build event timeline', 'chain funding and exposure events', 'materialize chains v0.3'."
---

# Event Chain Builder — multi-source company event timeline

Build a per-company event timeline from three event families and chain them into
`chain:...` records. Each event family is materialized to schema-contract-typed
Arrow (PyArrow) parquet; the chain builder groups funding outcomes and attached
dated exposure/interface middle events into one chain row per outcome.

## Features

Three event builders read per-company JSON caches written by the matching v0.3
collectors and emit schema-contract-typed parquet:

- `build_funding_events_v03.py` — funding outcomes (round / failed / withdrawn /
  acquired / ipo_* / company_closed). Explodes lead + co-investors into
  participant rows, matches investor IDs, resolves event claims, and tags a
  `fund:...` event_id per participant.
- `build_exposure_events_v03.py` — exposure events (product_launch, customer_change,
  regulatory, supply_chain, ...). Emits `exp:...` event_ids.
- `build_interface_events_v03.py` — investor-company relationship events
  (board_change, funding_participation, strategic_investment, ...). Resolves
  investor IDs, emits `int:...` event_ids.

`build_chains_v03.py` chains them into `chain:...` rows. Each unique funding
outcome (company, outcome_type, round_name, announce_date) becomes one chain end;
participant rows collapse into a single chain row, and dated exposure/interface
events falling in the preceding temporal window are attached to it. Undated
middle events stay in their source tables and are reported as unassigned
diagnostics.

All four build against the JSON schema contracts in `schemas/` via
`schema_contract_loader.py`, which converts `pyarrow-schema-json-v1` documents
into `pa.Schema` plus an ordered event-type enum. Builders refuse to overwrite an
existing output unless `--force-output` is passed, and fail loud on missing
inputs, unknown company_ids, unexpected schema versions, or unsupported outcome
types.

## Usage

```bash
# Point DATA_ROOT / the individual paths at your data dir (defaults to
# ~/investor-behavior-analysis).
export INVESTOR_BEHAVIOR_DATA_DIR=/your/data/investor-behavior-analysis

# Build each event family from its v0.3 collector JSON cache.
python3 scripts/build_funding_events_v03.py
python3 scripts/build_exposure_events_v03.py
python3 scripts/build_interface_events_v03.py

# Chain them into chain:... records.
python3 scripts/build_chains_v03.py
```

Every builder accepts `--entity-path`, `--cache-dir` (or the per-family source
paths), `--output-path`, and `--force-output` overrides. `build_chains_v03.py`
takes `--entity-path --funding-path --exposure-path --interface-path
--output-path`.

Module-level paths default to `$INVESTOR_BEHAVIOR_DATA_DIR`
(`$HOME/investor-behavior-analysis` if unset); set the env var or pass explicit
`--*` flags to use a different location.

## Requirements

`pandas`, `pyarrow` (parquet + table from_pylist). The scripts run network-free;
they consume caches already produced by the v0.3 collectors.

## Self-test

```bash
python3 -m py_compile \
  scripts/build_chains_v03.py \
  scripts/build_funding_events_v03.py \
  scripts/build_exposure_events_v03.py \
  scripts/build_interface_events_v03.py \
  scripts/schema_contract_loader.py || exit 1
```

The builders do not bundle a data sample (they need real parquet/cache inputs),
so parse-check the copied scripts and validations before running against a data
dir. `schema_contract_loader.py` is a pure library and is the safest unit to
exercise first with the four `schemas/*.schema.json` contracts.