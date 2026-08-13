# EB1.0F Frozen Stack and Extractor Preflight

Verdict: `FROZEN_ELIGIBILITY_STACK_COMPLETE_EXTRACTOR_BOUNDARY_DEFINED`.

EB1.0A–E form a complete frozen projection from exact verified-bundle summary documents to immutable eligibility manifests and one four-lane corpus.

The fixture-only EB1.0G source must be one dependency-injected SQLite database with exactly `bundle_summary_documents(stage, document_kind, canonical_json)` and `eb0_1_revision(engineering_revision)`. It must contain exactly run/accounting-or-aggregate/hashes documents for EB0.1, EB0.2 and EB0.4, and run/manifest/hashes for EB0.3. Stage/document pairs are unique. JSON must be canonical. SQLite is opened `mode=ro` with verified `query_only`, exact schema allow-lists, an active deadline no greater than 30 seconds, exactly 12 document rows, one revision row, and a 1 MiB total JSON ceiling.

The extractor must call EB1.0C then EB1.0D–E, account for every required stage/document, and fail closed on extras, omissions, duplicate rows, malformed/noncanonical JSON, schema drift, deadline/size breach, adapter rejection or replay failure. It makes no production compatibility claim and performs no entity linkage or analytics.
