# DEV004 Final Migration Preparation

**Decision: HOLD — mandatory complete-reference integrity fails.** No production cutover is authorized or performed.

Source: `202e30b4f843748926ea088aca988aea6ad9dc45`. All accepted ancestors listed in manifest.json remain present, including FLEX_STABLE_V2 `2d825bbcf27f802bd0bfcef90cedeba43cc968eb` (annotated tag peeled to commit). The three new DEV owner files are byte-identical. Existing runtime implementations and accepted functional evidence are retained; unrelated matrices were not repeated. The complete deployment manifest is **not** certified, and this composition is not a golden baseline.

## Six Byzantine imports: PASS

All six original membership rows, original event IDs, CONFIRMED/CERTAIN operator state and real confirmed-match payloads import exactly with `PRAGMA foreign_keys=ON`. No canonical identity event is manufactured or substituted; no ID is cleared, assignment dropped, or foreign key disabled.

The fresh disposable membership owner keeps the original five-column interface. A typed sidecar atomically maps non-null references to the real identity-event or confirmed-match namespace, using composite foreign keys for operation and match mint. Unknown, wrong-operation, wrong-mint and ambiguous parents fail closed. Existing membership writes populate the sidecar through triggers. Initialization is explicit disposable-only, repeat-safe, and refuses nonempty legacy replacement; this is not a live-schema migration or production installer. Applications opening a future typed database must use FK-enabled connections; complete runtime integration is not certified by this standalone import test.

**25 final provider-free controls pass**, including six exact imports, replay with zero duplicate insertions, FK checks, namespace and mutation guards, legacy NULL compatibility, repeat initialization, and reopening in a fresh interpreter. Synthetic identity controls are removed and are not canonical evidence. One earlier added-control fixture lacked a required default field; that fixture was corrected before the final successful run. Original accepted 100 creator / 616 Watchtower / 26 Deep / 11 frozen / freshness / Walkback / Birdeye evidence is reused, not represented as newly executed.

## Exact blocking reference

The first bounded retained-Core FK diagnostic returns `token_market_cap_peaks` rowid **13**, mint `BEy3GeKMC3J9dr2eabXYTUuD37YGiSnwWFq13hSgpump`, referencing missing `tracked_tokens`. An indexed V1 lookup `SELECT * FROM tracked_tokens WHERE mint=?` returns **zero rows**, using `sqlite_autoindex_tracked_tokens_1`. This is a retained-reference integrity failure, not an ATH numerical-tolerance failure. The diagnostic intentionally stops at the first violation; it does not assert this is the only one.

No tracked token is invented, retained peak removed, provenance changed or constraint bypassed. The full disposable migration rehearsal is **NOT RUN** because the mandatory full-reference gate fails; a six-row success is not a full-seed rehearsal. Paired transfer, rollback and restart evidence previously accepted remain valid within their recorded scope; they do not establish complete-seed acceptance here.

## Storage and production safety

Final successful qualification: free bytes `3786547200` before, `3786809344` after; both exceed 2,000,000,000 plus 67,108,864 reserve. Concurrent filesystem changes mean this difference is not attributable solely to the test. Disposable database 81,920 bytes; measured fixture allocation 81,920 before result metadata. Process file cap 8,000,000 bytes and database cap 1,024 pages, far below 500,000,000. Three bounded attempts created only compact fixtures; no full V1 copies, snapshots or raw provider retention. No ongoing storage producer or unbounded runtime path activated. Production mutations, provider calls, service actions, WebSockets, deletion and cutover: **0**.

ATH relative tolerance remains **0.1%**, valuation-only; original canonical values, provenance and identities unchanged. BIRDEYE authority, budgets and retries unchanged. New schema owner is DEV-only, not deployed; runtime deployment status QUALIFIED_NOT_DEPLOYED. No DEV_COMPLETE or production readiness claimed.

**Next action:** qualify the existing accepted compatibility/parent authority for this exact retained price-peak reference. Do not fabricate a tracked-token parent, delete evidence or broaden into historical acquisition. Resume the complete-seed rehearsal only when that mandatory reference is resolved under an accepted contract.
