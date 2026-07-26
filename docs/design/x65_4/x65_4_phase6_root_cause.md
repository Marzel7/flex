# X65.4 — Phase 6: Determine Root Cause

## Evaluation against each candidate cause

| Candidate cause | Fit | Evidence |
|---|---|---|
| **Creator-only ancestry walk** | **Confirmed — the primary cause** | Phase 2: `capture_provisioning_relationship()` (`provisioning_edges.py:150-205`) only ever writes a `SUBPROV_TO_CREATOR` edge when called with an already-known `creator` — it is invoked exclusively from the walkback success path, which resolves one mint's own creator lineage backward. No code path expands sideways to a subprov's other recipients. |
| **Sibling expansion omitted** | **Confirmed — the mechanism of the primary cause** | Phase 1: `wt_provisioning_edges`'s schema CHECK constraint (`provisioning_edges.py:50`) permits only `TREASURY_TO_SUBPROV`/`SUBPROV_TO_CREATOR` edge types — there is structurally no edge type to record a non-creator sibling recipient, even if the code wanted to. |
| **Traversal depth too shallow** | Partial contributor, not primary | The primary Fan-Out/Linear rule is 1-hop (subprov→creator); this is shallow, but depth is not actually the limiting factor here — even a deeper walk would still only ever discover creators (via further walkbacks), not siblings, because of the sibling-expansion gap above. Increasing depth alone would not fix the core issue. |
| **RPC limitations** | Rejected | The relevant fan-out evidence (`wt_candidate_websocket_watches`) is **already fully captured on-chain via existing RPC/WS calls in `_handle_subprov_tx()`** — no additional RPC capability is needed; the data already exists and sits unused. |
| **Temporal window too small** | Rejected | Phase 1 confirmed no temporal-window concept exists anywhere in the topology classifier at all — this is not a "window too small" problem, it is a "no window, and no alternative aggregation" problem. |
| **Recipient threshold too high** | Rejected | No recipient-count threshold exists in the classifier's Fan-Out/Linear rule (`> 1` distinct creator is already the lowest possible non-trivial threshold) — the issue is what is being counted (creators only), not the threshold value itself. |
| **Implementation defect** | **Confirmed — a second, compounding cause** | `funding_topology.py` was written to read only `wt_provisioning_edges`/`wt_active_subprov_sessions`/`watchtower_events`/walkback tables — a genuine implementation gap, since the richer `wt_candidate_websocket_watches` table (which directly answers the Fan-Out/Linear question with real evidence) already exists in the same database and is never consulted. This is not a detection gap (the data is captured); it is a classifier-implementation gap (the data is not read). |

## Root cause, precisely stated

**Two compounding causes, both confirmed by code and data, not
speculated:**

1. **Structural (schema + write-path)**: `wt_provisioning_edges` can
   only ever represent a subprov's relationship to wallets that
   *became confirmed creators* — there is no edge type, and no writer,
   for a subprov's broader outbound fan-out to non-creator sibling
   wallets. This is the creator-only ancestry walk the task
   hypothesized, confirmed exactly as described.

2. **Implementation gap (unused existing evidence)**: the real,
   already-captured fan-out evidence
   (`wt_candidate_websocket_watches`, populated live by
   `_handle_subprov_tx()` for every wrap-close destination a subprov
   produces) is never read by `funding_topology.py`. This table alone,
   with no new detection work, would have correctly classified 38 of
   43 confirmed WATCHTOWER launches as genuine Fan-Out (Phase 3/4).

## Code references supporting this conclusion

- `src/ops/provisioning_edges.py:47-63` — the `edge_type` schema
  constraint that structurally excludes non-creator edges.
- `src/ops/provisioning_edges.py:150-205` —
  `capture_provisioning_relationship()`, the sole writer, gated on
  `if subprov and creator:`.
- `src/ops/funding_topology.py:58-69` — `_subprov_sibling_counts()`,
  the read-side function whose `COUNT(DISTINCT to_wallet)` can only
  ever reflect creator-linked edges given the above.
- `src/core/ws_cascade.py:3684-3692` — `promote_to_subprov()` called
  with `creator=dests[0]["candidate"]`, only the first wrap-close
  destination, regardless of `len(dests)`.
- `src/core/ws_cascade.py:3696-3699` — `WRAP_CLOSE_FANOUT_DETECTED`
  event, proving `dest_count` is measured live but not propagated to
  the confirmed-creator edge.
- `src/core/ws_cascade_store.py:1861-1865` (`open_candidate_watch()`)
  — `subprov_fanout_count_at_capture`/`subprov_fanout_value_at_capture`,
  proving the real per-subprov fan-out accumulation already exists.
- Confirmed via direct grep: zero references to
  `wt_candidate_websocket_watches` anywhere in `funding_topology.py`.

## Classification

**Mixed: a structural detection-model gap (creator-only edge schema)
compounded by an implementation gap (existing fan-out evidence in a
sibling table never consulted).** Neither RPC limitations, temporal
windowing, nor recipient thresholds are contributing factors — this is
specifically a case of the classifier modeling creator ancestry when
richer operational-fan-out evidence already exists elsewhere in the
same database, unused.
