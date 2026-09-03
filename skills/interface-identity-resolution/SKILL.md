---
name: interface-identity-resolution
description: Validate reviewed counterparty-name overlays and materialize investor or associated-entity identities onto interface events with pinned inputs and fail-closed provenance checks. Use after interface-event construction and before investor-level behavior analysis.
---

# Interface Identity Resolution

Resolve the named counterparty in an interface event without silently treating
every organization as an investor. Outcomes distinguish existing investors, new
investors, associated non-investor entities, ambiguous names, and unresolved
names.

## Workflow

1. Preserve the source Interface Parquet unchanged.
2. For rows with empty `raw_investor_name`, prepare a reviewed recovery overlay
   with exact excerpts and offsets, then validate/materialize it using
   `apply_interface_name_recovery.py`.
3. Run `init_identity_package.py` with all four source Parquets to create a
   fail-closed package: every discovered name starts `UNRESOLVED`. Review its
   decisions and event overlays without changing their source bindings. Pin
   every input and package file by hash.
4. Run `materialize_interface_identity.py` with explicit source
   interface, investor entity/provenance, package, and output paths.
5. Accept output only if coverage, foreign keys, event bindings, file locks, and
   schema contracts pass. Never promote `AMBIGUOUS` or `UNRESOLVED` records merely
   to raise coverage.

The schemas under `schemas/` define the overlay contracts. These scripts
materialize reviewed decisions; they do not perform adjudication themselves.

## Requirements

Python 3.10+ and `pyarrow==25.0.0`. The two materializers intentionally publish
only to new paths below `/tmp`; copy reviewed bytes to their release destination
after all checks pass. The package initializer may write to any new directory.
