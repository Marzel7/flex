# X64.8 — Phase 10: Implementation Roadmap

Prioritized, actionable follow-on work derived from this audit. Nothing
in this document is implemented — it is a prioritization of future work,
each item still requiring its own explicitly-scoped approval before
execution, per this project's established pattern (audit → separately
authorized execution task, as in X64.7B preflight → X64.7C).

## Quick wins (low risk, high storage savings)

| Item | Storage reclaimed | Operational risk | Implementation effort | Dependencies |
|---|---|---|---|---|
| Remove `funder_networks` hot-DB copy | ~2.86GB | Low — archive copy verified as superset (42,314 vs 41,734 rows, all writes already redirected) | Low — a scoped deletion task following the X64.7C pattern (verify → delete → verify), likely reusable as-is | None — fully ready to schedule today |

## Medium-term improvements (archive tooling, retention jobs)

| Item | Storage reclaimed | Operational risk | Implementation effort | Dependencies |
|---|---|---|---|---|
| Confirm + prune completed `wt_subprov_sig_retry` / `wt_candidate_websocket_watches` rows | Unconfirmed, plausibly 100s of MB combined | Medium — must confirm terminal-state row split before any deletion | Medium — needs a status-distribution query first, then a scoped retry/watch-row purge job | A follow-up read-only query (not yet run in this audit) to size the safe target precisely |
| Build generalized archive tooling (based on `flex_investigation_archive.db` / `funder_networks` precedent) | Enables ~450MB+ of further archiving (Phase 6 candidates) | Low-Medium — archiving preserves data, lower risk than deletion, but cross-table archive candidates (`transfer_index` family) need care to preserve joinability | Medium — one clean table (`funder_networks`) already proved the pattern; generalizing to partial/time-boxed archival of `prediction_decision_context`/`wss_metrics` is new work | None blocking; can start independently of the quick win above |
| Automated retention/eviction for `rpc_response_cache` | Modest, unconfirmed | Low | Low-Medium — needs a TTL/eviction design, not just a one-off purge | A follow-up check of whether any eviction logic exists today (not confirmed in this pass) |

## Long-term architectural changes (partitioning, database split, cold storage)

| Item | Storage reclaimed | Operational risk | Implementation effort | Dependencies |
|---|---|---|---|---|
| Operational/historical backup split (Strategy B) | N/A (backup cost reduction, not live-DB storage reduction) | Low — matches existing `ATTACH DATABASE` pattern already in production use | Medium — requires a scheduled job, not just a manual script; needs the cleanup items above done first to avoid backing up soon-to-be-archived data twice | Quick win + medium-term archive tooling above, sequenced first |
| Time-boxed partial archival of `transfer_index` + funder-transfer tables | Potentially the largest long-term saving (544MB+ and growing at the fastest row-count rate of any table) | Medium — must scope to confirmed-attribution-only chains to avoid breaking re-evaluation of active investigations | High — cross-table joinability must be preserved in the archive; this is the most structurally complex candidate in this audit | Generalized archive tooling (above) proven on simpler single-table cases first |
| Incremental/WAL-based backup strategy (Strategy C) | N/A (backup efficiency, not live-DB storage) | Medium — new failure mode class (broken incremental chain) this project has no operational experience with | High — real engineering investment, no existing tooling to build from | Strategy B in production first; revisit only if backup-frequency requirements exceed what full-copy backups can sustain |

## Sequencing rationale

The ordering above is deliberate: the quick win (funder_networks
removal) is independent and should happen first regardless of anything
else, since it's the single highest-confidence, highest-value item found
in this entire audit. The medium-term items build the tooling and
evidence needed before any long-term architectural change is attempted —
in particular, building archive tooling on the *already-proven*
single-table case before attempting the structurally harder
multi-table `transfer_index` family archive avoids repeating this
project's own documented lesson (from prior "Hot DB retention plan"
work) that naive "looks unused/looks safe" judgments have been wrong
before without deeper dependency verification.
