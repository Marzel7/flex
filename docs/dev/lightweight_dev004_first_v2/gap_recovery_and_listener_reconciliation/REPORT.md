# DEV004 Disk Recheck, Intake Gap Recovery and Listener Reconciliation

STATUS: PARTIAL — receipt disk floor PASS; installed-source authority and disposable composition compatibility PASS; complete birth/migration maintenance-gap recovery HOLD. No production/source deployment, services, provider requests, live WebSockets or production database mutations.

## Disk headroom

Initial available6309216256bytes (5.876GiB); final5235367936bytes (4.876GiB). Both exceed the installed2000000000-byte receipt floor; the old0.30GiB disk-floor blocker is superseded. Free space changed concurrently by about1GiB; this is not attributed to the compact fixtures without independent evidence. Final swap total5120MiB/used3922.06MiB/free1197.94MiB. No swap/storage actions taken. This clears the receipt floor only, not the full future cutover allocation budget or any unchanged10GiB snapshot-publication free-space default. Do not assume space is reserved.

## Installed correction authority — resolved

Installed checkout HEAD is bf1bdfc59b4acfed0338a493189d91d212bda50f with approved source overlays; its HEAD alone does not identify loaded source composition. Every listener-related file changed between FLEX_STABLE_V2 and accepted65da0e227727da5fb19a75950cae8de1c05bc9ab matches the installed bytes exactly:

- scripts/maintain_pumpportal_compact_receipts.py
- src/core/birth_retry_quarantine.py
- src/core/pumpfun_curve_listener.py
- src/core/pumpportal_birth_audit.py
- src/core/pumpportal_receipt_maintenance.py

Full SHA256 identities are in manifest.json. Accepted chain/records establish sharded receipts and indexed fail-closed retention (991896a8 /83f5c05f), quarantine and lifecycle correction, failure-only fixed diagnostics (a766d7e9), and nonfatal optional numeric parsing (65da0e22). The accepted deployment handoff for65da reports160 offline controls and bounded natural runtime acceptance; those historical results are context, not tests rerun here. The current manifest/object/source hashes were freshly checked. These differences are explained accepted corrections, not unknown production edits. The nine-file deployed Birdeye contract remains closed; no credential values or provider acquisition inspected.

DEV004 at2b13612ccc4a9ccef76b31d2c970ce486fbbda99 lacks four of the five changed-file contents; its birth_retry_quarantine helper already matches. Accepted65da is not ancestral to DEV004. The existing runtime candidate506fe25c is therefore NOT silently relabeled as containing these corrections.

## Disposable source reconciliation — qualified, not integrated

A reviewable prototype starts from current installed/accepted listener files and retains exactly DEV004's already-qualified _ensure_webhook_birth_queue_schema function. That function avoids the closed/reused connection boundary for fresh startup and is preserved verbatim from DEV004. All other listener function ASTs match installed source exactly. Receipt writer, quarantine helper, maintenance checker and CLI match accepted installed bytes exactly. This preserves receipt sharding/legacy lookup, cap/floor, atomic fsync/rename, fail-closed indexed dependencies, permanent-conflict quarantine/filtering, canonical-safe retry/consume predicates, sanitized diagnostics and optional numeric conversion handling.

No runtime source files were edited. Prototype files under prototype/*.py.txt are evidence only; they have not been installed, activated or substituted for a new immutable executable candidate. Its source hashes and precise composition boundary are recorded in manifest.json/summary.json. Source integration/sealing and any eventual deployment still require the normal future candidate gates; the previous uncertainty about which installed corrections must be retained is closed.

## Provider-free compatibility

67 targeted tests PASS across the accepted CREATE numeric-parser and receipt-failure suites, including actual SQLite/TrackedConnection readback success, missing row, closed connection, disk-floor refusal and busy paths. All source reads/writes use synthetic fixture paths; network/credential-file/subprocess/unmanaged-thread guards remain active, with only exact baseline Git blobs supplied from retained objects and required asyncio test executor/shutdown threads allowed. Existing fresh-schema startup evidence is reused because the chosen startup function and quarantine helper are exact qualified source; no full daemon startup or67-to160 coverage expansion is claimed.

One original test asserting that ONLY numeric parsing changed is intentionally excluded because this prototype composes multiple already-accepted corrections. It is replaced by exact AST assertions: startup==DEV004; every other installed function==prototype. Initial pytest setup attempted out-of-fixture temp/null-log destinations and was blocked; redirected to fixture-local files. Five readback cases were initially blocked by the restrictive test-thread guard; after narrowly permitting their asyncio executor/shutdown threads, only those five were rerun and passed. The aggregate67 unique tests all pass; no failed application behavior is hidden. Maximum fixture file661817bytes; RLIMIT_FSIZE16000000 and SQLite page limits retained. No outside-fixture DB opens observed. No receipt retirement/maintenance command executed.

## Birth/migration gap recovery — HOLD with demonstrated limit

Received-event durability and unseen-event recovery are separate contracts:

- Existing compact receipts, birth/migration persistence queues, file fallbacks and Core→OPS handoff retries can retain/retry events already observed before interruption. They cannot contain events never delivered while the listener is stopped. This task did not read or mutate live receipts/queues.
- PumpPortal subscriptions have no supplied resume cursor; PumpSwap logsSubscribe likewise is not accepted replay. Installed code explicitly documents disconnect-gap event loss.
- The birth RPC reconciler requests the latest configured100 program signatures per sweep with no before/until pagination. It has an in-memory _birth_reconciler_last_seen_sig, advanced before transaction acquisition/persistence. No durable full-gap completion identity is established.
- The migration RPC reconciler also requests the latest configured100 signatures with no pagination. Persistent migration dedup helps returned signatures, but does not prove that the entire interruption interval was enumerated. Its completeness docstring is not evidence of a full-interval guarantee.
- Current listener launcher sets both PUMPFUN_BIRTH_RECONCILER_ENABLED=0 and LISTENER_MIGRATION_RECONCILER_ENABLED=0, and disables the Helius birth websocket fallback. No flag changed, reconciler activated or provider queried.

Five provider-free controls execute the actual accepted reconciler loop bodies with synthetic RPC/transaction sinks: a150-event gap yields100 unique birth recoveries and100 unique migration recoveries after two instantaneous sweeps, with50 outside the single page; no pagination parameters are used. A failed synthetic birth transaction acquisition still advances the memory anchor; the second sweep does not retry those100. A new actor resets that anchor. These are controlled counterexamples to complete-gap/restart coverage, not claims that real150-event gaps occurred or that every returned on-chain signature is a birth/migration. Actual transport requests=0. Test source hash matches installed listener751d40d1… . No gap duration/event-rate bound sufficient to ensure a single page covers maintenance has been accepted.

Therefore the existing mechanisms provide partial/received-event recovery, not the required complete evidence-preserving maintenance-gap contract. It cannot receive PASS merely because the single-page code is idempotent or a short pause is proposed. No provider calls can or need be made to demonstrate this structural limit, and no new recovery engine is implemented.

## Revised blocker state / next action

1. Old disk-floor blocker: CLEARED at observation; recheck before any future operation and budget all transient/rollback growth.
2. Installed corrections: authority and offline merged-prototype compatibility QUALIFIED; runtime candidate integration/sealing NOT PERFORMED. Preserve current installed source, never overwrite it with unmodified506fe25c.
3. Complete intake-gap evidence preservation: HOLD. Decide the explicit maintenance-gap acceptance contract before scheduling a pause: complete bounded interval/restart recovery must be qualified, or bounded evidence loss must be explicitly accepted. No such loss acceptance is inferred here.

NEXT_ACTION: Resolve that single intake-gap acceptance decision. Keep production running and unchanged; do not stop intake, enable provider acquisition, deploy the prototype or reopen online discovery-epoch work. Existing final-state/paired-binding/rollback and ongoing storage gates remain separate and unchanged.

RUNTIME_DEPLOYMENT_REQUIRED=false — provider-free source reconciliation qualification and documentation only. Production/V1 credentials, schema, source, configuration and services unchanged. No new discovery predicates, historical replay, deletion or infrastructure.
