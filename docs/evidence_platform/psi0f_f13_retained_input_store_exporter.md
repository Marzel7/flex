# PSI0F-F13 retained-input store and exporter

PSI0F-F13 implements the safe-local bridge designed in F12. A fixture publisher
accepts the complete caller-injected F9 input set plus explicit review metadata,
constructs and replays F9 in memory first, and then writes one new isolated
SQLite store. It cannot reuse or overwrite a destination.

The store retains a manifest, ordered cohort, full evaluation summaries,
normalized runtime projections, full candidate payloads, explicit nomination
dispositions, and ordered disposition members. Canonical payload bytes and
digests are retained for every structured row. Each review has a closed
non-personal reviewer class, closed reason codes, and a unique logical sequence.
Discovery lifecycle remains separate and cannot produce `PROPOSED` or
`SUPPORTED` authority. Every table rejects update and delete.

The exporter accepts one explicit store path and retention identity. It rejects
symlinks and SQLite companion files, opens URI `mode=ro`, enforces
`PRAGMA query_only`, validates the exact seven-table and fourteen-trigger schema,
and executes seven bounded queries. It verifies canonical payloads, digests,
coverage, review metadata, ordered membership, the retained manifest, the F9
bundle, and the reconstructed F5 source identity. It returns the bundle only in
memory and writes no file.

Qualification uses temporary fixture databases only. PSI0F-F13 does not capture
real EP3 or EP4 records, create human dispositions, open a production or retained
database, publish a real F9 bundle or F5 source, invoke EB0.4H, publish a PSI0F
surface, access a provider, mutate a service, deploy, or activate anything.
