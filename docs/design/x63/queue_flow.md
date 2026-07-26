# x63 — Walkback Queue: End-to-End Execution Flow

All citations are `file:line` against the current repo state.

## 1. Literal call chain (migration → final storage)

```
pump.fun migration event detected
        │
        ▼
src/core/watchtower_attribution.py:store_migration()  (watchtower_attribution.py:122-147)
  writes migrated_tokens (neutral fact)
        │
        ▼
src/core/watchtower_attribution.py:146-147
  enqueue_migration(conn, mint=mint, creator=creator)
        │
        ▼
src/core/walkback_queue.py:enqueue_migration()  (walkback_queue.py:307-392)
        │
        ├─► classify_creator()  (walkback_queue.py:187-302)  — zero-RPC DB-only lookup
        │      cascades through: wt_ops_v2_wallets → wt_watchtower_launches →
        │      wt_wrap_close_candidates → wt_creator_birth_launch →
        │      wt_candidate_websocket_watches → watchtower_token_attribution →
        │      established-launcher check → CEX/relay check → FULL_WALKBACK fallback
        │
        ├─► INSERT OR IGNORE INTO wt_walkback_queue (walkback_queue.py:368-380)
        │      status/initial_outcome computed from walkback_class:
        │        LINK_ONLY* / OP_GRAPH_ROLE_MISMATCH / SELF_ROOTED_OPERATION → status='complete'
        │        SKIP                                                        → status='skipped'
        │        PARTIAL_* / FULL_WALKBACK (create-anchor valid)             → status='pending'
        │        FULL_WALKBACK (no valid create anchor)                     → status='waiting'
        │
        ├─► evaluate_and_enqueue_candidate()  (watchtower_candidates.py:118-189)
        │      X63 EPHEMERAL_WSOL_CREATOR_HANDOFF candidate detector — if it fires,
        │      sets wt_walkback_queue.priority=100 for this mint while status
        │      is pending/waiting (watchtower_candidates.py:170-174)
        │
        └─► if status is complete/skipped at enqueue time:
               materialize_outcome() (attribution_outcome.py) + sync_walkback_result()
               — row is DONE, worker never sees it
        │
        ▼  (only PARTIAL_* / FULL_WALKBACK rows reach here — the RPC-consuming path)
src/core/walkback_worker.py:run_loop()  (walkback_worker.py:1241-1328)
  infinite loop, INTERVAL_SEC=45s between iterations (walkback_worker.py:64,1328)
        │
        ├─► heartbeat write (walkback_worker.py:1298-1303)
        ├─► COUNT(*) pending rows with attempts < MAX_ATTEMPTS (walkback_worker.py:1306-1309)
        └─► if pending > 0: drain_batch(ops)  (walkback_worker.py:1153-1218)
                │
                ├─► SELECT up to BATCH_SIZE=8 candidate rows
                │     ORDER BY COALESCE(priority,0) DESC, enqueued_at ASC
                │     (walkback_worker.py:1160-1167)
                │
                ├─► for each row: _mark_running(ops, mint)  (walkback_worker.py:498-507)
                │     → deep_walkback.claim_with_lease()  (deep_walkback.py:231-240)
                │       atomic UPDATE ... WHERE status IN (pending, expired-running)
                │       — rowcount==1 required, else another worker already claimed it
                │
                ├─► _process_row(ops, row)  (walkback_worker.py:859-1054)
                │     dispatches on walkback_class:
                │       PARTIAL_TREASURY → 1-hop RPC from subprov (walkback_worker.py:874-890)
                │       PARTIAL_SUBPROV  → 1-hop RPC from creator (walkback_worker.py:892-913)
                │       FULL_WALKBACK    → hop1 (creator's funder) then hop2
                │                          (funder's funder), optionally deep-expanding
                │                          via _expand_unknown_upstream up to
                │                          DEEP_MAX_HOPS=8 (walkback_worker.py:915-1037)
                │     each hop calls _find_with_evidence → _find_funder_via_rpc, which
                │     performs the actual getSignaturesForAddress/getTransaction RPC work
                │     (walkback_worker.py:325-448)
                │
                └─► result written via one of:
                      _mark_complete()  (walkback_worker.py:573-630)
                      _mark_failed()    (walkback_worker.py:633-644)
                      _mark_exhausted() (walkback_worker.py:647-660)
                      each of these also calls materialize_outcome() and
                      sync_walkback_result()
        │
        ▼
time.sleep(INTERVAL_SEC) → loop repeats (walkback_worker.py:1328)
```

## 2. Zero-RPC "LINK_ONLY" fast path

For a row classified `LINK_ONLY` / `LINK_ONLY_GRAPH` / `OP_GRAPH_ROLE_MISMATCH` /
`SELF_ROOTED_OPERATION` / `SKIP`, the entire lineage is already known from
existing tables (`wt_ops_v2_wallets`, `wt_watchtower_launches`,
`wt_wrap_close_candidates`, etc.). `enqueue_migration` marks the row
`complete`/`skipped` at insert time (`walkback_queue.py:356-359`) and the
worker never touches it — `drain_batch`'s SELECT only matches
`status='pending'` or an expired `status='running'` lease
(`walkback_worker.py:1163`), so these rows are excluded by construction.

## 3. What "successful completion" writes

Traced writes from a completed `PARTIAL_*`/`FULL_WALKBACK` row
(`_mark_complete`, `walkback_worker.py:573-630`):
1. `wt_walkback_queue` — `status='complete'`, `intelligence_outcome`, `subprov`,
   `treasury` (COALESCE, never overwrites), `rpc_used`, `completed_at`.
2. `watchtower_token_attribution` — only if `confirmed_subprov` or `treasury`
   is set (`walkback_worker.py:590-604`), upsert on mint.
3. `wt_discovered_subprovs` — via `_ensure_subprov_lead` only for
   `LINEAGE_GAP` outcome with an unconfirmed subprov (`walkback_worker.py:608-619`).
4. `wt_treasury_review` — via `_surface_treasury_review_lead` →
   `treasury_bank.add_walkback_hop2_lead` for `LINEAGE_GAP` funder leads
   (`walkback_worker.py:621-625`) and for unknown hop-2 candidates during
   `FULL_WALKBACK` deep expansion (`walkback_worker.py:1016-1017`).
5. `wt_attribution_outcomes` (+ `wt_unknown_infrastructure_registry`) — via
   `materialize_outcome()` in `src/ops/attribution_outcome.py:603` (called at
   the end of every `_mark_*` function).
6. `wt_watchtower_candidates` — via `sync_walkback_result()` in
   `watchtower_candidates.py:238-266` (called at end of every `_mark_*`),
   mirrors final status/outcome into the candidate row only if one exists.
7. `wt_provisioning_sessions` / `wt_provisioning_edges` — via
   `_capture_provisioning_facts()` → `capture_provisioning_relationship()`
   in `src/ops/provisioning_edges.py`, called during the `FULL_WALKBACK`
   branch at hop1 confirmation (`walkback_worker.py:964-968`) and at hop2
   (`walkback_worker.py:990-997`) — append-only evidence, never attribution.
8. `wt_walkback_atomic_flows` / `wt_walkback_edge_candidates` — via
   `deep_walkback.persist_edge_candidate` / `persist_atomic_flows`, called
   inside `_find_funder_via_rpc` for every candidate considered, not just the
   winner (`walkback_worker.py:428-444`).

`wt_confirmed_treasuries` is **only read** by the worker (`_is_known_treasury`,
`walkback_worker.py:467-470`) — never written by any code path traced in
`walkback_worker.py` or `walkback_queue.py`. Promotion into
`wt_confirmed_treasuries` happens in `src/core/treasury_bank.py`, a separate
human/webhook-driven promotion path, not part of the walkback completion flow.
