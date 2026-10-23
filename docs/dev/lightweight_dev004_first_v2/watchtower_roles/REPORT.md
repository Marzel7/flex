# DEV004 — Watchtower treasury-role equivalence

DEV004_WATCHTOWER_ROLE_STATUS: PASS for the 99 demonstrated regressions. Overall V2 cutover remains HOLD. Of the five separately unqualified cases, one now has a verified witness; four remain HOLD. No production changes or runtime implementation.

Frozen authority: FLEX_STABLE_V2 peeled commit `2d825bbcf27f802bd0bfcef90cedeba43cc968eb`; immutable manifest SHA256 `f0d3d933109f3572023af677729feef4db6e432657c7b7095b5bae05f2821dc7`. Existing DEV004 branch at freshness correction `ace58aba292a7183cfef39e853a114ebbcbe8712`, price correction `20d1025e`, accepted price-worker `71b6e338`, and deployed nine-file Birdeye hashes preserved. The dormant price worker was not activated. Original operational fixtures remain unchanged.

FIXED_99_CASES: 99/99 now exactly match their retained V1 role-validator outputs. All 616 qualified frozen routes in the 621-route population match; five originally unqualified cases remain a separate ledger. `all_621_routes.json` contains exact comparisons. Actual installed `watchtower_registry_promotion.py` functions were used unchanged, with their source hash recorded. No source-pair scans were run against production: V1 comparisons use frozen outputs plus independently recovered indexed proof witnesses.

VERIFIED_ROLE_LINKS: Four shared sender/recipient pairs account for the 99 regressions. Chosen integer session IDs are 288523, 275385, 288639 and 288877. Each retained verified edge matches the active-session record's treasury, recipient and funding signature, is DIRECT_SOL_TRANSFER, and has LIVE_DIRECTIONAL_TRANSACTION provenance. All are historically EXPIRED; that state is preserved and no monitoring is armed. Thirteen indexed alternative witnesses across those pairs are documented, not silently recast as conflicting parents. The existing reader requires existence of a qualified role distinction, so one witness per pair is sufficient. All exact signatures, amounts, timing, queue/provisioning identities, entity confidence and provenance, selected/alternative edges and recognition records are in `role_results.json`.

The bounded lookup joins by meaning rather than name: `wt_active_subprov_sessions.id` is the integer `wt_lineage_verified_session_edges.session_id`; it is not the textual `wt_provisioning_sessions.session_id`. Candidate sessions are fetched through the existing subprov-wallet unique-index prefix, proofs through INTEGER PRIMARY KEY, quarantine through the source-table/source-row unique index, root policies through their primary key. Each source query rejects SCAN plans, has a 65-row failure cap and 0.5-second progress deadline. Source reads are URI mode=ro/query_only. No new source indexes or historical acquisition.

CONFLICTING_ROLE_EVIDENCE: Zero contradictory or quarantined selected role witnesses established for the 99. Existing competing-parent, excluded and negative evidence rows are preserved, not reclassified. Missing, reversed and non-direct synthetic proofs all retain TREASURY_EVIDENCE_CONFLICT. A small historical role-distinguishing transfer is not proof that its amount financed a later larger route.

Topology is preserved with distinct addresses: canonical treasury → upstream provisioning parent (`session.treasury`) → direct funding account (legacy `queue.subprov`/`session.subprov`) → creator → launch. Source column labels alone do not promote an upstream parent to a confirmed SubProvider. Exact source confidence/review states remain authoritative; no candidate was promoted or account collapsed.

INSUFFICIENT_RETAINED_EVIDENCE: Four of the separately unqualified cases have no active-session witness reached through the bounded indexed pair lookup. This is not a proof that no unindexed historical edge exists. Their treasury-role admission stays fail-closed. No role evidence was invented.

FIVE_UNQUALIFIED_CASES:

- `2RDV1zyUpJ9RG9Q4aqoB5gSHrT6goM149b95ktCTpump`: VERIFIED_ROLE_LINK via shared session 288877; current V2 is eligible. Its formerly interrupted V1 result is not relabelled as a historical exact comparison.
- `2NjuvDtoj4HqSNGqMYBs4wBotq1e92gyHCCyEPKZpump`
- `2Nu94jFVHUE7MWVsS6V9zfqYi9js8UYd93yydkbvpump`
- `2UKkazisyXfWUyLT4eqCgvEV6KNaxqwkHU49htQppump`
- `2YGcLWQ5NZ5qijs9UNBoFcAWREYa1Hpc6QwouAPPpump`

The last four are INSUFFICIENT_RETAINED_EVIDENCE/HOLD, not demonstrated V1/V2 regressions. Their exact Fkcc treasury/recipient predicates are in the case manifest. Stop at this evidence boundary; no RPC research.

V2_REFERENCE_ROWS_ADDED: 10 rows: four verified edges, four exact supporting session records and two existing discovered-SubProvider records required to preserve installed recognition. Existing active-session table schema and eligible-session view are reused in the disposable fixture; no runtime or production schema change. Every original row in all 55 existing OPS tables remains byte-value equivalent; Core is byte-identical. All four support sessions pass the existing eligible-session relation. No queue leases, retries, memberships, quarantine or review states were rewritten.

Fixture: `/private/tmp/dev004-watchtower-roles-qualified`. Core 8,970,240 bytes; OPS 13,754,368 bytes; total 22,724,608 bytes and 28,464 rows. OPS length rises 12,288 bytes; added canonical JSON payload is 5,848 bytes. These are fixture measurements, not physical APFS recovery or a prediction of final V2 size. `storage.json` records hashes. Each generated file is capped below 16,000,000 bytes; SQLite copies use max_page_count 3840; fixed finite test matrix, zero new unbounded growth paths, no new raw acquisition. Fixtures were copied from the compact DEV seed, never production databases.

V1_V2_ROLE_EQUIVALENCE: Installed known-treasury and known-SubProvider readers match live read-only source results for all 219 involved wallets. False/rejected recognition remains false. Four synthetic future-launch controls admit the same known role pattern with distinct creator/funder roles; they qualify role admission, not a full new funding investigation.

WATCHTOWER_ATTRIBUTION_EQUIVALENCE: 99/99 ROLE_NORMALIZED_ROUTE_VERIFIED dictionaries match, including creator/subprov/treasury role, mechanism and signature predicates. Existing attribution outputs, parent candidates and review evidence remain unchanged. This closes the role-link regression only; it does not assert full Watchtower historical reconstruction.

PROVIDER_FREE_TEST_RESULTS: PASS: 616 qualified route comparisons; 219 wallet role comparisons; three negative-role controls; four future-role controls; original 11 frozen comparisons; all 26 Deep diagnostic cases; eight intake/recovery controls; 13 price/candidate checks (including the previous accepted functional checks); 12 freshness groups including fresh-interpreter restart with zero extra assessments. Opening fallback provenance remains exact, canonical price facts unchanged, ATH relative difference 0.0025616522% is below accepted 0.1%, BIRDEYE authority and all nine installed credential hashes unchanged. No providers, WebSockets or service actions.

REMAINING_CUTOVER_BLOCKERS: Four separate unqualified role cases; creator-history decision coverage; full bounded Walkback investigation/queue cutover reconciliation; prospective unfinished acquisition/restart and complete dormant price composition; accepted API/UI/monitoring projection coverage; final file-growth and cutover/rollback acceptance. These remain the previously scoped gates, not newly discovered failures. Potential Operations freshness and this 99-case role defect are closed in DEV; production/V1 remains authoritative.

RUNTIME_DEPLOYMENT_REQUIRED=false: test/data qualification only; no executable runtime source changed. No deployment, service restart, production writes, deletion or cutover authorized or performed.

NEXT_ACTION: Keep the four evidence-limited cases on HOLD. The next bounded functional qualification is creator-history classification for the already identified affected populations, using explicit V1/V2 decisions and minimum retained inputs; no broad seed audit or historical RPC acquisition. Separate authorization is required for that next objective.
