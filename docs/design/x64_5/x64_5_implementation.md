# X64.5 — Implementation Summary

## Code changes

### 1. `src/ops/anchor_reconciliation.py` (new module)
Zero-RPC reconciliation and self-healing logic:
- `ensure_schema(conn)` — additive `ALTER TABLE` for 5 new columns on
  `wt_walkback_queue` (`anchor_lookup_attempts`, `last_anchor_lookup_at`,
  `anchor_recovered_at`, `anchor_recovery_source`, `anchor_lookup_state`)
  plus a new `wt_anchor_reconciliation_log` audit table. Never alters or
  drops an existing column.
- `classify_stuck_row(live_conn, mint)` — the Phase 2 classification
  logic (`RECOVERABLE_VALID_ANCHOR` / `ANCHOR_PRESENT_INVALID` /
  `ANCHOR_STILL_MISSING` / `AMBIGUOUS_MULTIPLE_ROWS` / `MINT_NOT_FOUND`),
  using the actual production `valid_signature()` from
  `src/core/deep_walkback.py` — no re-derived validation rule.
- `dry_run_report(ops_conn, live_conn)` — Phase 2/7 read-only population
  audit, zero writes.
- `reconcile_waiting_create_anchors(ops_conn, live_conn, dry_run=False)`
  — Phase 3 idempotent reconciliation. For each recoverable row: persists
  the recovered signature, flips `create_anchor_audit_state` to `VALID`,
  advances `path_state` to the existing `CREATE_ANCHORED` state (the same
  state `enqueue_migration()` itself would have set had the anchor been
  visible at insert time), returns `status` to `pending` so the row
  becomes visible to `drain_batch`'s normal SELECT, and logs one
  `wt_anchor_reconciliation_log` row. Preserves `attempts`, `enqueued_at`,
  `creator`, `mint`, and every existing evidence column untouched — never
  writes `subprov`/`treasury`/any attribution table.
- `recheck_single_row_anchor(ops_conn, live_conn, mint)` — Phase 4 worker
  self-healing hook, single-row variant.

**On the `CREATE_ANCHOR_RECOVERED` state name** (Phase 3's literal
instruction: `reason = CREATE_ANCHOR_RECOVERED`): `deep_walkback.py`'s
`PATH_STATES` is a closed `frozenset` enforced by `set_path_state()`'s own
`ValueError` guard — adding a new enum value there is a schema/contract
change beyond this task's stated "additive only" scope, and no existing
call site needed it changed. Instead: `path_state` advances to the
existing `CREATE_ANCHORED` value (the correct, already-defined state for
"this row now has a valid anchor"), and the *reason/timestamp/source* the
task actually asks to preserve is captured via the new
`anchor_recovery_source`/`anchor_recovered_at` columns plus the dedicated
`wt_anchor_reconciliation_log` table — giving full traceability without
inventing an enum value outside the existing contract. The module-level
constant `RECONCILED_REASON = "CREATE_ANCHOR_RECOVERED"` still exists in
the module for callers/log messages that want the literal reason string.

### 2. `src/core/walkback_queue.py` (Phase 5/6 — enqueue-time hardening)
`enqueue_migration()`: when `live_conn` is `None` (the actual shape of
both real production call sites — see `x64_5_anchor_race_audit.md` Phase
1), the function now opens its own short-lived read-only connection to
`LIVE_DB_PATH` before attempting the `creator_funding_queue`/
`token_analysis` anchor lookup, and closes it immediately after — mirroring
the exact pattern `src/ops/watchtower_candidates.py`'s
`evaluate_and_enqueue_candidate()` already uses for the identical reason.
This closes the gap at its source for the common case (signature already
committed to `creator_funding_queue` by the time `enqueue_migration()`
runs) without changing either fragile call site's own connection-passing
signature — `store_migration()`'s and the curve-listener fallback's
broader transaction lifecycles are untouched.

This does **not** fully solve a genuine race (signature not yet committed
at the exact microsecond `enqueue_migration()` runs) — that remaining gap
is closed by the worker-side reconciliation pass below (Phase 6's
"Option C — eventual consistency, mandatory reliable reconciliation").

### 3. `src/core/walkback_worker.py` (Phase 4 — worker self-healing)
`run_loop()`'s main cycle now runs a self-healing anchor-reconciliation
pre-pass, immediately before the existing `pending`-count check, using a
short-lived read-only live-DB connection. Follows the exact
non-essential-maintenance pattern already established for
`recover_stalled_running_jobs`/`finalize_exhausted_pending` in the same
function: a lock-contention `OperationalError` is logged and skipped (the
next cycle retries), any other exception still propagates. This is what
makes rows recoverable even when the true underlying cause is a genuine
race rather than the simpler "`live_conn` never supplied" case — every
worker cycle re-checks every `WAITING_FOR_CREATE_ANCHOR` row against
`creator_funding_queue` at zero RPC cost.

## Historical impact — live production run

Per the task's Phase 7 instruction, a dry-run report was produced first
([x64_5_backfill_dry_run.md](x64_5_backfill_dry_run.md)), then the real
reconciliation was executed against the live databases:

```
examined: 352
recovered: 310
skipped:   42  (all MINT_NOT_FOUND — genuinely no creator_funding_queue row)
conflicts:  0
```

Verified directly against the live DB after the run: the canonical mint
`H55qUAeK313XyTrhxeMVQgBrogdGG9biyAVfmDQipump` now shows
`status='pending'`, `create_anchor_signature='Tt3yP2SNaXG4gNWAmduUBCDbpmV26RErBQrzDLSZuZuqv28m4Kez3m6f82RJnvCUov8jPqHn2LhkCYxwwLfSP6b'`,
`create_anchor_audit_state='VALID'`, `path_state='CREATE_ANCHORED'`,
`attempts=0` — matching Phase 8's expected result exactly.

**Idempotency confirmed live**: a second `reconcile_waiting_create_anchors()`
call immediately after the first examined only the remaining 42
genuinely-missing rows and recovered 0 — the 310 already-recovered rows
were correctly excluded (no longer `status='waiting'`), no duplicate log
rows, no re-write.

`wt_anchor_reconciliation_log` now has 310 rows, one per recovered mint,
each carrying `mint`, `creator`, `recovered_signature`, `original_state`,
`recovery_source`, `recovery_timestamp` — exactly the fields the task's
Phase 3 "reconciliation event" requirement specifies.

## Phase 8 — canonical regression case, walked end-to-end

After reconciliation released the row, it was processed through the real
`_process_row()` walkback logic (single-row, not a full batch, to keep
RPC usage minimal and targeted at exactly this task's validation
requirement — 12 RPC credits used):

```
pre:  status=pending, subprov=None, treasury=None, attempts=0
post: status=complete, intelligence_outcome=LINEAGE_GAP,
      subprov=9JXUiZhYVzaxY1aDdNyvpkiFTZNr54WEeVhQvxKfKrn9,
      funding_mechanism=WSOL_WRAP_CLOSE, funder_amount_sol=1.112039,
      rpc_used=12
```

The walkback correctly reached hop1 (`9JXUiZhYVzaxY1…`), classified it as
a genuine `WSOL_WRAP_CLOSE` disposable-subprov handoff (`evidence_level=STRICT`
per the earlier X64 fix's own logging), and — since hop2 was not found
(`UPSTREAM_UNRESOLVED`) — correctly promoted it to a `LINEAGE_GAP`
discovery lead rather than collapsing it to `NO_ATTRIBUTION_FOUND`,
exactly per the X64 evidence-preservation fix implemented earlier in this
session.

**Does `H55qUAeK…pump` recover a lineage consistent with treasury
`4231KLYipwRTmFdQ6ZBa1H4Jf3EpfF62Gzg6DWHWvhPZ`? No.** The disposable
subprov this walkback actually surfaced (`9JXUiZhYVzaxY1…`) is a
**different wallet** from `HXMUxU94Zs2hGHW6r4odBiCTMxkzjV7YGJHAMYdTPFRY`
(the hop1 wallet already on record for `HHcXBLbn…`/`7nxHcmxb…`, the
launch this session's earlier X64.3/X64.4 audits examined in connection
with `4231KLYi…`). Checked directly: `9JXUiZhYVzaxY1…` does not appear in
any `watchtower_events` row (neither as `wallet_address` nor
`related_wallet`) — zero connection to `4231KLYi…` or to any of its known
downstream/upstream wallets. **This is the expected, correct outcome, not
a failure of this task**: `H55qUAeK…pump` and `HHcXBLbn…pump` are two
entirely different launches with two different creators and two different
disposable subprovs; nothing in this session ever established or claimed
that `H55qUAeK…pump` specifically traces to `4231KLYi…` — the treasury was
supplied as "a validation target, not a value to inject into the result,"
per the task's own explicit instruction, and the walk was never steered
toward it. The genuine, honestly-observed result is a new, independent
`LINEAGE_GAP` disposable-subprov lead, unconnected to the treasury named
in the task.
