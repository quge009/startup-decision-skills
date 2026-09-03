# Pattern candidate lattice contract

`generate_pattern_candidates.py` reads only the explicitly supplied chain,
exposure, and interface tables plus their schemas. Candidate generation must
not read company outcome labels or reference Pattern results.

The output directory contains `candidate_lattice.json`,
`pattern_candidates_frozen.json`, and `generation_manifest.json`. The
manifest pins source and output hashes. A run must refuse an existing output
directory and produce deterministic candidate IDs from canonical rules.

Limits on atom count, composition size, support, runtime, and candidate count
are caller-controlled CLI parameters and form part of the run provenance.
