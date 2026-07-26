# x63 — Integration Recommendations for EPHEMERAL_WSOL_CREATOR_HANDOFF Signal

This is the only forward-looking document in this audit set. Everything
below is a recommendation, not a description of current behavior.

## Important prior-art finding
`src/ops/watchtower_candidates.py` **already implements** an
`EPHEMERAL_WSOL_CREATOR_HANDOFF` candidate detector (`PRIMITIVE` constant,
`watchtower_candidates.py:17`), already wired into
`wt_walkback_queue.priority` via `HIGH_PRIORITY=100`
(`watchtower_candidates.py:19,170-174`), already called from inside
`enqueue_migration()` on both the fresh-insert and already-queued paths
(`walkback_queue.py:326-333, 382-385`). The queue's `ORDER BY
COALESCE(priority,0) DESC, enqueued_at ASC` (`walkback_worker.py:1165`)
already respects this column. **The integration point requested by this
task's context already exists in the codebase** — see `performance.md` for
the finding that it has not yet fired on any row in the current dataset
(all 6753 rows have `priority=0`), traced to a specific root cause:
`evaluate_and_enqueue_candidate` gates its own INSERT on
`classify_quick_birth_migration()` requiring a non-NULL `migrated_at`, but
it is called at/near CREATE time — before migration has happened or is
even knowable — so the gate fails on essentially every real invocation and
`wt_watchtower_candidates` stays empty (confirmed: 0 rows, against
3,052,976 rows of qualifying raw evidence in
`wt_candidate_websocket_watches`). This changes the framing of the
recommendations below: the existing call site (point #2) is not merely
"has not yet fired" — it is the reason nothing fires, and any fix must
address the timing gate itself, not just add volume through the same path.

If the actual need is "make this signal fire more often / earlier / on more
launches" rather than "build a new integration," the recommendations below
should be read as candidate places to strengthen or trigger the *existing*
mechanism rather than as a proposal for a parallel new one.

## Candidate insertion points, evaluated

1. **Before enqueue** (i.e. a pre-filter that changes `walkback_class` or
   skips enqueue entirely) — not recommended as a new addition. The
   classification step (`classify_creator`, `walkback_queue.py:187-302`) is
   already zero-RPC and purely DB-lookup driven; injecting a heavier
   detector here would slow every migration event, including the ~5.4% that
   are already `LINK_ONLY`/`SKIP` and need no walkback at all.

2. **During enqueue** (current implementation's actual location) — this is
   where `evaluate_and_enqueue_candidate` already runs. It is the natural
   point because it has access to the freshly-classified row and can gate
   the priority UPDATE on `status IN ('pending','waiting')`
   (`watchtower_candidates.py:171-172`), i.e. only affects rows that will
   actually reach the worker queue. This remains the best-fit insertion
   point for any refinement of the signal itself (e.g. adjusting what
   counts as a "quick birth → migration" window, or adding new handoff
   variants beyond `WSOL_WRAP_CLOSE`/`SEEDED_ACCOUNT_CLOSE`).

3. **At queue-selection time** (inside `drain_batch`'s SELECT) — not
   recommended for this signal specifically. The `ORDER BY priority DESC`
   clause already reads whatever value was set at enqueue time; recomputing
   the signal at selection time would mean re-deriving handoff evidence on
   every poll cycle (45s default) for every pending row, which is wasted
   work compared to computing it once at enqueue.

4. **Worker pre-processing** (inside `_process_row` before dispatch) — not
   a good fit; by this point the row has already been claimed and its
   position in the batch has already been decided, so any priority signal
   computed here can no longer influence ordering for *this* batch. Could
   only affect a *future* re-queue, which is a strictly worse trigger point
   than #2.

5. **Post-processing** (after `_mark_complete`) — not applicable for
   *prioritizing* this row (it's already done), but this is where
   `sync_walkback_result()` and `materialize_outcome()` already run, and
   where a completed walkback's evidence (e.g. a confirmed `WSOL_WRAP_CLOSE`
   discovered only during the RPC walk itself, not known at enqueue time)
   could seed **retroactive** priority-setting for *other still-pending*
   rows sharing the same wallet/creator pattern — this is not currently
   done anywhere observed in this audit.

## Recommended single best point
**#2 (during enqueue), reusing the existing `evaluate_and_enqueue_candidate`
mechanism, but only after fixing the `classify_quick_birth_migration`
timing gate at its call site.** As currently written, this integration
point cannot fire at CREATE time because it requires `migrated_at`, a
fact that by definition does not exist yet. Two non-mutually-exclusive
fixes, both scoped to `watchtower_candidates.py` and requiring no queue/
schema redesign:
- Treat `MISSING_MIGRATION` as evaluable at enqueue time (the primitive
  signal — the wrap-close handoff itself — is independent of whether the
  token later migrates; migration timing is presently used as a
  "quick pump" confirmation signal, not something the handoff detector
  should need). This would let the INSERT proceed on birth/create alone.
- Or, add a second, later call to `evaluate_and_enqueue_candidate` (or a
  dedicated re-evaluation) triggered from wherever `token_analysis.
  migrated_at` actually gets set (the migration listener/reconciler), so
  the full three-timestamp classification still applies but at a point
  where all three values can genuinely be non-NULL.

Absent one of these, point #2 remains: because it is the correct
architectural location once the gate is fixed —
- It is already the integration point in production code.
- It only touches rows before they reach `status='pending'`/`'waiting'`,
  so it can never race the worker's claim (`claim_with_lease`) or need to
  coordinate with an in-flight `running` row.
- It composes with the existing `ix_wbq_priority` index
  (`status, priority DESC, enqueued_at ASC`), so no schema or index change
  is needed to make a priority boost immediately effective in the next
  `drain_batch` poll.

## Can priority be extended without a full redesign?
**Yes.** The column, index, and ORDER BY clause are all already generic
(`priority INTEGER`, not a fixed enum of values) — a graduated priority
scheme (e.g. multiple tiers instead of the current binary
`0`/`HIGH_PRIORITY=100`) requires no schema change, only:
- Additional constants/logic inside `evaluate_and_enqueue_candidate` (or a
  sibling function called from the same enqueue-time hook) to compute a
  tier instead of a flat `100`.
- No change to `drain_batch`'s SELECT/ORDER BY at all — it already sorts by
  `priority DESC` generically.

The one architectural constraint worth flagging: `priority` is currently
only ever set (never read back and combined with other factors) by a single
writer (`watchtower_candidates.py:170-174`), and the UPDATE is
unconditional (`SET priority=?`, not `SET priority=MAX(priority,?)`)
(`watchtower_candidates.py:171`) — a second signal source writing to the
same column would silently overwrite this one's value rather than compose
with it, unless changed to a MAX/greatest-of pattern. Any second
prioritization source should be aware of this before writing to the same
column.
