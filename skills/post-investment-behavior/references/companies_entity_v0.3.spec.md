# Company entity input contract

The analysis requires one row per company with a unique, non-null `company_id`.
The table must also contain the company name field expected by the analyzer.
Every `company_id` referenced by funding or interface events must resolve here.

Company IDs are opaque stable identifiers. The method does not require a
particular industry, ID namespace, company count, or source database. Preserve
the source table and its entity-resolution provenance with every run.
