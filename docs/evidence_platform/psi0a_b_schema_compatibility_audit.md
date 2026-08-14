# PSI0A-B Schema Compatibility Audit

PSI0A-B compares exact caller-supplied relation, column-affinity and index-prefix requirements with explicitly mapped SQLite sources. Connections use URI `mode=ro`, a 250 ms lock timeout and verified `PRAGMA query_only`. Only `sqlite_schema`, `table_info`, `index_list` and `index_info` metadata are read; production rows and EB evidence are never extracted.

The immutable result binds the PSI0A-A boundary, database file names, complete observed column/index metadata, per-relation schema digests, discrepancies, counts and exact replay. Missing relations, required columns, affinity mismatches or index prefixes produce `SCHEMA_INCOMPATIBLE`; unknown source mappings and boundary drift fail closed.
