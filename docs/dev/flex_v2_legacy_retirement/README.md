# Explicit predecessor retirement contract — DEV qualified only

Golden tag flex-v2-golden-2026-10-28 and source 8322e2c remain immutable.
This candidate does not publish production retirement evidence or delete files.

The existing paired guard continues to require physical current Core/OPS identity,
ACTIVE/TRANSFER ownership, generation, source seals, existing retirement markers
and the accepted auxiliary writer authority. Unconfigured validation is unchanged.

The existing pair owner can explicitly call publish_predecessor_retirement with
an authorization argument, while both original V1 files still exist and already
have verified .retired markers. It independently validates ACTIVE successor
seals, snapshots the exact original manifest bytes, preserves both original
V1 device/inode identities, and reestablishes metadata durability. It publishes
one immutable certificate at AUTHORITY_PATH.predecessor-retirement-v1.json.
It never connects to, copies or deletes a database. Both original files must
be stat-verifiable before publication; missing files never imply retirement.

Publication uses one bounded stage, fsync, exclusive immutable link, directory
fsync and destination readback. Interrupted/conflicting publication is refused;
only an explicit owner retry with independently verifiable original evidence
can finalize a matching stage. The function returns a pin only after completion.
At most one certificate and one stage exist per authority: 32 KiB each. Reads
are bounded and the existing 2 GB plus 64 MiB floor is preserved.

Subsequent validation requires BOTH explicit configuration bindings:
FLEX_DB_PREDECESSOR_RETIREMENT_MANIFEST and
FLEX_DB_PREDECESSOR_RETIREMENT_SHA256. File presence alone is not authorization.
Missing, corrupt, wrong-owner, stale, mixed, replaced or pending evidence fails
closed. The immutable historical manifest and source seals are checked; original
V1 retirement markers remain mandatory metadata and must not be deleted.
A reappearing V1 file must retain its recorded identity. Only the exact certified
original V1 predecessor may be absent; current/other V2 identities stay physical.

The same pin persists through fresh compact-pair recovery. Current Golden
recovery script and renderer bytes are unchanged. Its existing common environment
mapping carries both pins. Shared readers, the three active launchers and the
dormant launcher must be installed coherently before any file retirement.
The active and dormant launch boundaries normalize inherited FLEX_DB_PATH and
OPS_V2_DB_PATH aliases only after successful retirement-aware V2 validation.
This removes the listener's inherited V1 aliases without changing attribution.

The seven-day owner and its manifest schema remain unchanged. A later authorized
activation must pass both pins to the one existing scheduler command as well as
the three workers/recovery environment and any later dormant profile. Tests
prove bounded cleanup with both original fixture files absent. Dormant roles
remain stopped; no new schedule, database, ledger or infrastructure is added.

Next gate: read-only exact installed-source/configuration deployment preflight,
including retirement owner quiescence, all-reader bindings and source pins.
Certificate publication, worker reloads and deletion each require the later
explicit production authorization. Retain active pumpswap_tokens.db auxiliary,
V2 data, budgets, custody, receipts/journals and all retirement/seal metadata.
