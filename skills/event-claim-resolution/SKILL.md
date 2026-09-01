---
name: event-claim-resolution
description: "Resolve a multi-source event claim set into a single canonical event record: pick the highest-confidence normalized canonical value per field, retain all raw claims for audit, and detect / report semantic conflicts across sources. Pure stdlib. Use for deduplicating or merging events (funding rounds, exposures, interactions) observed by multiple data sources. Trigger on: 'resolve event claims', 'merge multi-source events', 'event canonicalization'."
---

# Event Claim Resolution — multi-source event canonicalization

Merges an event observed by multiple sources into one canonical event,
keeping the full raw-claim trail for auditability and flagging field-level
conflicts.

## Behaviour

- `resolve_event_claims(rows, ...)` takes a list of raw claim rows for the
  same `event_id` and returns a canonical event dict.
- Per non-key field, the **normalized canonical value** is chosen by
  confidence / recency; the raw values are retained under a
  `raw_claims` structure so downstream audit can reconstruct every source.
- `_conflict_fields(rows)` reports which fields carry semantically
  inconsistent values across sources.

## Usage

```python
from event_claim_resolution_v03 import resolve_event_claims

canonical = resolve_event_claims(raw_claim_rows, event_id="evt-123")
# -> canonical canonicalized fields + raw_claims trail + conflicts
```

## Requirements

Pure Python stdlib (`collections`, `typing`). No external deps.
