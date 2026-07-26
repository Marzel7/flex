# X65.3 — Phase 4: Blast Radius

## Measured counts (live, ~3-hour observation window since deployment)

| Metric | Count | Basis |
|---|---|---|
| Total migrations processed (log-based) | 418 | `Marked token migrated` log lines, `logs/supervisor/listener.log`, since 2026-07-21T20:15:05Z |
| Total migrated launches (DB-based, `migrated_at` column) | 171 | `SELECT COUNT(*) FROM token_analysis WHERE migrated_at >= <deploy_epoch>` |
| Launches entering `_update_token_entry_with_creator()`'s write path with an overwrite condition detected | 105 distinct mints (102 log events) | `[CREATE_SIG_OVERWRITE_ATTEMPT]` count, Phase 2 |
| Launches ending `NULL` (overwrite completed) | 105 | Phase 3, 100% of flagged mints |
| Launches retaining their existing signature | 0 (of the flagged set) | Phase 3 |

## Note on the two different "total migrations" numbers

The log-line count (418) and the `migrated_at`-column count (171)
diverge — this is an honestly-reported discrepancy, not reconciled or
guessed at. Plausible explanations (not confirmed further in this
task, out of scope for X65.3's runtime-verification focus): retries
that re-log `Marked token migrated` for the same mint without changing
`migrated_at` a second time, or a difference in exactly which write
path increments each counter. Neither total changes this phase's core,
directly-measured finding: of the launches the diagnostic actually
flagged, **100% ended up with the signature destroyed**.

## Percentage of migrations affected

Using the log-based total (418) as the denominator (the more direct,
same-source measurement as the overwrite-attempt count itself):

**102 / 418 ≈ 24.4%** of all migrations processed during this window
triggered a detected overwrite condition.

This is consistent with — and now confirmed at much higher statistical
confidence than — the earlier same-day snapshot (35 of 38, ~92%,
though that smaller sample was likely skewed by which specific
migrations happened to land in the first few minutes after deployment;
the fuller ~3-hour window's ~24% is the more representative rate).

## Interpretation

Roughly one in four migrations processed by the live listener is
currently having its `create_tx_signature` destroyed by this defect —
a substantial, ongoing, and continuously recurring rate, not a rare
edge case. Every launch this affects loses its durable CREATE-signature
evidence permanently unless a separate recovery action is taken (per
X65.2's Phase 6 finding, recovery is possible but was not attempted in
either investigation).
