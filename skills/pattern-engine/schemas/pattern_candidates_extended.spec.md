# Extended Pattern candidate contract

`generate_pattern_candidates_extended.py` expands the label-blind grammar with
typed event anchors, structural atoms, sequences, and legal pairwise
compositions. It writes `pattern_candidates_extended_frozen.json` with a
generation manifest and supporting catalogs.

Generation and evaluation are physically separate. Outcome labels may be read
only by `audit_pattern_candidates.py evaluate` after all freeze hashes pass.
Every resource bound is explicit and recorded. No dataset-specific expected
candidate count is part of this reusable contract.
