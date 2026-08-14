# EB1.3G Fixture-Only Query Extractor

EB1.3G accepts only one verified immutable EB1.1H bundle directory and one explicitly injected SQLite fixture containing exactly `review_records(position, canonical_json)` and `proposal_records(position, canonical_json)`. It opens SQLite with URI `mode=ro`, enables and verifies `PRAGMA query_only`, checks the complete table/type/constraint allow-list, reads contiguous zero-based rows deterministically, enforces canonical JSON, and runs under an active deadline of at most 30 seconds.

The extractor enforces the EB1.3F ceilings: at most eight requirements, 64 review rows, 64 proposal rows, 262,144 combined JSON bytes, 64 selected proposals, one manifest, eight corpus lanes, and a 1 MiB immutable bundle. It reconstructs EB1.2A, verifies EB1.3C lineage, builds and replays EB1.3D, and assembles and replays EB1.3E.

Every input proposal is accounted as selected, excluded-not-ready, or unknown, with conflict and residual counts. Unknowns, conflicts, residuals, schema/limit/deadline/lineage/replay drift fail closed. A zero-selected input returns `NO_ELIGIBLE_PROPOSALS` with no fabricated manifest or corpus. All results remain `NON_EXECUTABLE_PLANNING_PROPOSAL` with both planning and execution authority false. Qualification uses frozen and ephemeral fixtures only and makes no production compatibility claim.
