# Pattern registry contract

`build_pattern_registry.py` combines one or more reviewed JSON batches into a
canonical registry. Each record requires `pattern_id`, `rule`, and one action
assessment: `RECOMMEND`, `AVOID`, `INDIFFERENT`, or `INCONCLUSIVE`.

The caller supplies a universe ID, positive universe size, ordering rule, and
named batch inputs. Pattern IDs must be globally unique. Records are sorted by
UTF-8 pattern ID; source files are pinned by SHA-256; output is written
atomically and never overwrites an existing file. Assessments describe the
represented operating action, not a company.
