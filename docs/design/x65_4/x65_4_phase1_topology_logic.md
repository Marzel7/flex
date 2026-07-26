# X65.4 — Phase 1: Document Current Topology Logic

Read-only. Traces where topology classification runs, exactly what
evidence each classification consumes, and produces a decision tree.

## Where classification is performed

**`src/ops/funding_topology.py`** — `classify_topology_for_launch()`
(pure function, per-launch) called from `build_topology_classification()`
(batch entry point, one pass per Discovery load over
`wt_attribution_outcomes`). This is the sole topology classifier;
`src/core/operation_dashboard_routes.py` and `src/core/token_behavior.py`
only consume/display its output (`LINEAR`/`MULTI_LEVEL_FAN_OUT`/`FAN_OUT`
/`MESH`/`UNKNOWN`), they do not compute it.

## Evidence sources consulted, per classification

| Classification | Table(s) read | What is actually counted |
|---|---|---|
| `MULTI_LEVEL_FAN_OUT` (walkback-derived variant) | `wt_walkback_edge_candidates` (`selection_status='SELECTED'`), `wt_walkback_queue.termination_reason_json` | Selected walkback hop chains; "fan-out" = a `parent` wallet appearing as the parent of >1 `wallet`/`child` across **already-selected** walkback edges — i.e. only wallets the walkback process itself chose to record as a parent-child pair while resolving a **specific mint's own lineage** |
| `MULTI_LEVEL_FAN_OUT` (session-lineage variant) | `watchtower_events` (`event_type='SUBPROV_SESSION_OPENED_WS'`, `payload.via='subprov_plain_xfer'`) | Whether the subprov itself is a recorded **child** of another subprov (a treasury→subprov→subprov→creator chain), not fan-out breadth at all |
| `MESH` | `wt_active_subprov_sessions` (`treasury_wallet` set ∩ `subprov_wallet` set) | Whether a treasury wallet also appears as a subprov wallet anywhere — a structural mesh signal, unrelated to fan-out breadth |
| `FAN_OUT` vs `LINEAR` (primary path) | `wt_provisioning_edges` filtered to `edge_type='SUBPROV_TO_CREATOR'`, grouped by `from_wallet`, `COUNT(DISTINCT to_wallet)` | **Distinct creators** a subprov is recorded as funding — `>1` → `FAN_OUT`, `=1` → `LINEAR` |
| `FAN_OUT` vs `LINEAR` (walkback fallback, when no `wt_provisioning_edges` evidence exists for the subprov) | `wt_walkback_edge_candidates`/`wt_walkback_queue` parent fan-out counts | Same "selected walkback parent" fan-out count as the `MULTI_LEVEL_FAN_OUT` walkback variant, just at depth 1 instead of ≥2 |
| `UNKNOWN` | (absence of any of the above) | No lineage evidence at all for the launch |

## Critical finding: `wt_provisioning_edges` cannot represent sibling (non-creator) recipients

`wt_provisioning_edges`'s schema (`src/ops/provisioning_edges.py:47-63`)
constrains `edge_type` to exactly two values:
`TREASURY_TO_SUBPROV` and `SUBPROV_TO_CREATOR`. There is **no edge
type for a subprov's non-creator sibling recipients** (the task's
Wallet A/B/C/D). The write side confirms this is not an oversight in
just the schema: `capture_provisioning_relationship()`
(`provisioning_edges.py:150-205`) only ever writes a
`SUBPROV_TO_CREATOR` edge when it is called with a **known creator**
(`if subprov and creator:`, line 195) — it is invoked exclusively from
the walkback success path, which by definition is walking **one
specific mint's own creator lineage** backward. It never queries or
records a subprov's other, non-creator outbound transfers. This means
`_subprov_sibling_counts()`
(`funding_topology.py:58-69`, `COUNT(DISTINCT to_wallet)` grouped by
`from_wallet`) can only ever count **how many different creators**
a subprov is known to have funded across **multiple separate mints'
walkbacks** — never the subprov's true single-provisioning-window
outbound fan-out (Wallet A/B/C/D + Creator, all as part of *one*
operational cycle).

## Where genuine sibling/fan-out evidence already exists but is unused

`src/core/ws_cascade.py`'s `_handle_subprov_tx()` (line 3498+) is the
live, real-time detector. On a wrap-close transaction with multiple
destinations (`dests`, line 3535), it:
- Logs `WRAP_CLOSE_FANOUT_DETECTED` with the true `dest_count=len(dests)`
  (line 3696-3699) — proving the system already measures multi-destination
  wrap-closes when they occur.
- But `promote_to_subprov()` (line 3684-3692) is called with
  `creator=dests[0]["candidate"]` — **only the first destination**,
  unconditionally, regardless of `len(dests)`.
- Every destination (including `dests[0]`) is separately persisted to
  `wt_candidate_websocket_watches` via `store.open_candidate_watch()`
  (line 3712), which tracks a running `subprov_fanout_count_at_capture`/
  `subprov_fanout_value_at_capture` **per subprov, accumulated across its
  entire lifetime of observed wrap-close destinations** — this is the
  actual, already-persisted evidence of a subprov's real outbound
  fan-out breadth.
- **`funding_topology.py` never reads `wt_candidate_websocket_watches`
  at all** (confirmed via direct grep — zero references). The richest
  available fan-out evidence source is completely disconnected from
  the topology classifier.

Separately, `_handle_subprov_tx()`'s plain-transfer branch (no
wrap-close destinations found, lines 3538-3631) does iterate every
account key in the transaction's balance deltas to detect
sub-subprov candidates — but this only fires when there is **no**
wrap-close destination at all, and only feeds the `MULTI_LEVEL_FAN_OUT`
sub-subprov-lineage signal (via `SUBPROV_SESSION_OPENED_WS`/
`parent_subprov`), not a sibling-breadth signal.

## Decision tree (as currently implemented)

```
For a given launch (mint):

1. Is there a selected walkback edge chain with depth ≥ 2 AND a parent
   wallet that fans out to >1 child (among SELECTED walkback edges only)?
   → MULTI_LEVEL_FAN_OUT

2. Else, is there no subprov AND no treasury evidence at all?
   → UNKNOWN

3. Else, is the involved subprov itself a recorded child of another
   subprov session (SUBPROV_SESSION_OPENED_WS, via=subprov_plain_xfer)?
   → MULTI_LEVEL_FAN_OUT

4. Else, is the involved treasury also recorded as a subprov elsewhere
   (wt_active_subprov_sessions treasury∩subprov overlap)?
   → MESH

5. Else, if a subprov is known:
   a. Does wt_provisioning_edges record >1 DISTINCT CREATOR for this
      subprov (SUBPROV_TO_CREATOR edges, across ALL mints)?
      → FAN_OUT (n_siblings > 1)
   b. Does it record exactly 1 creator?
      → LINEAR (n_siblings == 1)
   c. No SUBPROV_TO_CREATOR edge recorded for this subprov at all —
      fall back to selected-walkback parent fan-out at depth ≥ 1:
      does the immediate walkback parent fan out to >1 child?
      → FAN_OUT (walkback fallback)
      else → LINEAR (walkback fallback, no observed branch)
      else (no walkback evidence either) → UNKNOWN

6. Else, if only a treasury is known (no subprov at all):
   → LINEAR ("treasury_direct_no_subprov")

7. Else → UNKNOWN
```

## Traversal depth, thresholds, windows — as actually implemented

| Parameter | Value used today |
|---|---|
| Graph traversal depth | Effectively 1 hop (subprov→creator) for the primary Fan-Out/Linear rule; up to 2 hops for the walkback-derived Multi-Level variant; no traversal beyond a subprov's *recorded creator set*, ever |
| Branching criteria | `COUNT(DISTINCT to_wallet)` on `SUBPROV_TO_CREATOR` edges — creators only, never non-creator siblings |
| Fan-out threshold | `> 1` distinct creator (i.e., 2+ creators funded by the same subprov, observed across separate mints/walkbacks) |
| Temporal window | None — no time-bounded "provisioning window" concept exists anywhere in this classifier; `wt_provisioning_edges` accumulates across all time via `first_observed_by_flex`/`last_observed_by_flex`, with no windowing applied at query time |
| Amount similarity checks | None — `funding_amount_sol` is stored per edge but never compared/clustered by the topology classifier |
| RPC/DB sources | Exclusively local SQLite reads (`wt_ops_v2.db`) — zero RPC calls anywhere in `funding_topology.py` (confirmed by inspection: no RPC-related imports) |

This directly answers Phase 2's question in advance: the current
implementation only ever recognizes fan-out when a subprov is
independently linked to **more than one creator** through separate,
already-resolved walkbacks — it has no mechanism to recognize a
single subprov's fan-out to non-creator sibling wallets within one
provisioning cycle, even though that exact evidence
(`wt_candidate_websocket_watches`) already exists in the database,
unused.
