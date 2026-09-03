---
name: event-chain-builder
description: Build schema-validated company timelines from funding, exposure, and investor-interface events, then materialize them as event chains. Use for comparable cross-source event-history research.
---

# Event Chain Builder — multi-source company event timeline

Build a per-company event timeline from three event families and chain them into
`chain:...` records. Each event family is materialized to schema-contract-typed
Arrow (PyArrow) parquet; the chain builder groups funding outcomes and attached
dated exposure/interface middle events into one chain row per outcome.

## Features

Three event builders read per-company JSON caches written by the matching v0.3
collectors and emit schema-contract-typed parquet:

- `build_funding_events.py` — funding outcomes (round / failed / withdrawn /
  acquired / ipo_* / company_closed). Explodes lead + co-investors into
  participant rows, matches investor IDs, resolves event claims, and tags a
  `fund:...` event_id per participant.
- `build_exposure_events.py` — exposure events (product_launch, customer_change,
  regulatory, supply_chain, ...). Emits `exp:...` event_ids.
- `build_interface_events.py` — investor-company relationship events
  (board_change, funding_participation, strategic_investment, ...). Resolves
  investor IDs, emits `int:...` event_ids.

`build_chains.py` chains them into `chain:...` rows. Each unique funding
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

`event_claim_resolution.py` groups rows by `event_id`, selects the uniquely
highest-confidence claim (earliest input order breaks ties), preserves every
raw claim in `metadata_json.claims`, and reports semantic conflicts. The three
event builders apply it before materialization; it may also be imported for
standalone claim normalization.

## Usage

```bash
# Build each event family from its v0.3 collector JSON cache.
python3 scripts/build_funding_events.py --entity-path ... --cache-dir ... --output-path ...
python3 scripts/build_exposure_events.py --entity-path ... --cache-dir ... --output-path ...
python3 scripts/build_interface_events.py --entity-path ... --cache-dir ... --output-path ...

# Chain them into chain:... records.
python3 scripts/build_chains.py --entity-path ... --funding-path ... \
  --exposure-path ... --interface-path ... --output-path ... \
  --company-label-column label --company-outcome-label-column outcome_label
```

Every builder requires `--entity-path`, `--cache-dir` (or the per-family source
paths), and `--output-path`; `--force-output` permits intentional replacement.
`build_chains.py`
takes `--entity-path --funding-path --exposure-path --interface-path
--output-path`; label-column flags default to the released v0.3 entity contract
but may be overridden for compatible tables.

## Requirements

`pandas`, `pyarrow` (parquet + table from_pylist). The scripts run network-free;
they consume caches already produced by the v0.3 collectors.

## Self-test

```bash
python3 -m py_compile \
  scripts/build_chains.py \
  scripts/build_funding_events.py \
  scripts/build_exposure_events.py \
  scripts/build_interface_events.py \
  scripts/schema_contract_loader.py || exit 1
```

The builders do not bundle a data sample (they need real parquet/cache inputs),
so parse-check the copied scripts and validations before running against a data
dir. `schema_contract_loader.py` is a pure library and is the safest unit to
exercise first with the four `schemas/*.schema.json` contracts.
