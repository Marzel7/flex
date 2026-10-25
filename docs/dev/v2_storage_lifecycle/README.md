# V2 lifecycle DEV qualification

Status: **HOLD for golden-baseline freeze; QUALIFIED_NOT_DEPLOYED for the focused DEV owner.**

The owner disposes only schema-qualified expired latest-value RPC cache and disposable WSS telemetry strictly older than seven days. Everything else remains protected. Existing cache readers preserve cache-miss semantics without eager age deletion in the paired V2 profile. Legacy listener maintenance is disabled in that profile before opening a database; its accounting and two-day deletion path is not retained as a competing owner. Legacy unpaired behaviour is unchanged.

No source, schema, cron or database is changed in production. No duplicate packaging, historical data, unknown table or index is deleted. Already-retired legacy registrations remain retired.

## Invocation and authority

Use the exact listener package and `python -B -m src.ops.v2_storage_lifecycle --manifest ABSOLUTE_PATH --sha256 SHA256 --initialize` once against explicitly authorized disposable stores. Subsequent invocations use `--once --quiet`. `render_schedule` produces one cron definition; it does not install it. Manifest keys are exactly `version`, `generation`, `core`, `ops`, `pair_manifest`, `pair_authority`, `pair_sha256`, `lock`. Values must be physical absolute paths, a SHA-bound pair and ACTIVE matched generation. No default database or V1 fallback exists. The command installs the kernel file cap before opening stores.

A complete production runtime manifest is deliberately not frozen: see the precise outstanding contracts in qualification.json. Do not install this source with a terminal-aware recovery renderer still pinned to the previous listener hash.

## Verification

31 provider-free controls pass: 6/7/8-day boundaries, unexpired cache, ten protected families, bounded continuation, crash before/after commit, duplicate execution, concurrent writer priority, pressure refusal, trigger protection, page reuse, matched-pair disposable recovery, and all three packaged cache/legacy-maintenance variants.

Physical fixture result: 219 pages before and after deletion; 192 free pages after deletion; 219 pages after equivalent reinsertion, zero free pages. No VACUUM or manual checkpoint. This demonstrates page reuse, not sustained production growth bounds.

Per-step writer lease and SQL instruction budgets bound cooperative work, not absolute lock duration or WAL size. The kernel cap fails closed below 500 MB. Aggregate storage, receipt/log sustainability and approved dormant packaging remain explicit freeze blockers. Existing canonical/attribution/CREATE/Birdeye/terminal evidence and budgets are outside the mutation allowlist.

RUNTIME_DEPLOYMENT_GATE=QUALIFIED_NOT_DEPLOYED. This is isolated DEV qualification; production activation is not authorized.
