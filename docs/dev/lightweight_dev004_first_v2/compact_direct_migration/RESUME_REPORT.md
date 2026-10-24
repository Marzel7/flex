# DEV004 — Resumed direct compact migration

STATUS: PASS — one bounded direct V1-to-compact-V2 qualification. Previous disk HOLD cleared. No production mutation, source clone, deletion, deployment or broader migration.

## Authority and disk gate

Initial recheck4,931,072,000bytes free exceeds2,067,108,864bytes (2GB floor+64MiB reserve). Existing harness source `eb9e1d35ea0b87ab38d59bb46a2b3fac458884ff` was reused unchanged; runtime candidate remains `0013ccbde314d0ad124f8ccd2963f15aff437519`. All three runtime trees still match that candidate. No previously accepted matrix was repeated.

The freeze read the current V1 sources only through read-only/query-only connections, taking the existing representative HXpDLpo…pump selectors and required schema/index metadata. No whole source hashing, copying, backup, snapshot or raw row intermediate. The manifest contains physical source identities, observed schema/header identities, exact selected-row digests, schemas, canonical price/finalization provenance, target physical identities, continuation rules, storage bounds and abort-before-activation rollback references. It does not falsely assert an atomic global Core/OPS snapshot.

Manifest: `resume_manifest.json`; SHA256 `5a1f308d8201d9b8df5a22fabd74388688b733e5a7cc6efe68df1163224499d3`. Freeze commit `c3818432` was pushed and its remote manifest bytes verified before execution. Exact local run: `/private/tmp/dev004-direct-compact-resume-45ed0080`; no existing directory/files were deleted. The earlier blocked directory remains empty.

## Direct migration and equivalence

37 distinct current V1 rows (Core6,OPS31),39,287encoded bytes were selected by52indexed bounded selectors and inserted into fresh compact target tables. Original values, canonical provenance, confidence, selected/alternative parents, quarantine, price facts/observations and null legacy event links all match the manifest's selected-row hashes exactly. The source databases total about48.286GiB, but only the selected rows and schema metadata were read; no table-wide acquisition dataset was migrated.

All11actual accepted reader comparisons PASS: treasury and SubProvider recognition, Watchtower route, creator classification, retained attribution outcome, competing parents, quarantine, price fact, price observations, OHLC reduction and creator-funding history. This preserves the source's distinctions, including dictionary-versus-membership authority and retained UNKNOWN outcomes; matching does not promote them into stronger claims.

Opening baseline/value/timestamp/exactness, canonical ATH, ATH time/evidence/finalization identity and provenance remain exact. V1/V2 ATH relative difference0%, within the accepted0.1%. The separate accepted historical reconstruction at0.0025616522235844718% remains valid; missing original candles are not recovered or invented. The accepted finalizer returns ALREADY_FINALIZED with0provider calls after reopen. BIRDEYE exclusivity and existing20global/4token/60s policy are unchanged; no credential or budget state is reset and no provider is invoked.

## Restart, idempotency and V1 independence

Exactly one qualification ran. First child exited71 immediately after one compact row commit and before cursor publication. A fresh process resumed at that same selector, verified the existing row instead of duplicating it, completed all52selectors and passed every field/reader comparison. Another explicit duplicate application inserted0rows.

Actual target cardinality is37: one durable pre-cursor row plus36 inserted on resume. The diagnostic cursor field inserted=36 counts resumed inserts, not total population; next_selector=52 is complete. Source/target row hashes and actual counts are the continuity authority.

The final verification used another fresh process whose SQLite audit rejects every production/V1 database open. The same11reader outputs and finalized-request suppression passed using V2 alone. Therefore the representative V2 read path has no permanent V1 database dependency. This does not claim every application consumer has been qualified by one launch.

Rollback for this scope is abort-before-activation: V1 remains authoritative and untouched; these two representative targets are never rebound to production. No source restoration/copy, retirement marker or unretire is needed. Existing paired transfer, journals, writer fences and full queue/price recovery qualifications remain preserved and were not rerun or weakened.

## Storage result

- Compact Core294,912bytes + OPS479,232bytes =774,144bytes total (0.738MiB).
- Execution allocation81,920→884,736bytes including authority/checkpoint/process metadata. Largest generated file479,232bytes.
- Execution free space4,933,214,208→4,932,685,824bytes; a later independent read4,931,059,712bytes. Every observation remains above2GB+64MiB. Concurrent filesystem changes are not all attributed to the test.
- Enforced8,000,000byte process single-file limit,1,024pages×4,096bytes per SQLite database,64MiB total fixture cap, <=64files; indexed128row/selector,64KB/row,2,048unique-row and2MB encoded-selection caps. All Python writes and SQLite targets restricted to this disposable root; source connections accepted only in explicit read-only mode. Network/credential-file access blocked. No new file approaches500,000,000bytes; raw retention=false; new unbounded growth paths0.
- Full source database copy bytes0; intermediate source-row snapshot bytes0; production mutations0; provider calls0.

The prior measured22.7MB reference pair/24.1MB including mappings remains the useful full-reference starting estimate. This774KB one-case result must not be extrapolated into a complete final operational size or a lifetime5GiB guarantee. The32MiB reference-pair/128MiB additional-space planning allowances in the retention report remain provisional, not newly certified by this sample. V1 is preserved as the current authority and temporary future migration/rollback reference, not a permanent dependency to duplicate into V2.

RUNTIME_DEPLOYMENT_REQUIRED=false — unchanged runtime; isolated test/configuration/evidence only. No live subscriptions, service actions, production writes, full V1 copies, cleanup/deletion or deployment occurred.

NEXT_ACTION: NONE — requested resumed bounded qualification complete. Stop. Any wider migration or deployment needs separate scope/authorization and exact selected-reference/current-state sizing; do not infer global cutover acceptance from this representative case.
