# X65.8 — Phase 4: Root Cause

For every one of the 21 mismatches found in Phase 3, the cause is
identical — a single, precisely-located mechanism, not 21 separate
incidents.

## The mechanism

`_subprov_sibling_counts()` (`src/ops/funding_topology.py:58-69`) is
the function that decides Fan-Out vs. Linear:

```python
def _subprov_sibling_counts(ops_conn):
    """{subprov_wallet: distinct creator count} from wt_provisioning_edges'
    SUBPROV_TO_CREATOR edges..."""
    rows = ops_conn.execute(
        "SELECT from_wallet, COUNT(DISTINCT to_wallet) AS n "
        "FROM wt_provisioning_edges WHERE edge_type='SUBPROV_TO_CREATOR' "
        "GROUP BY from_wallet"
    ).fetchall()
    return {r[0]: r[1] for r in rows}
```

This counts **distinct creators**, sourced from a table
(`wt_provisioning_edges`) whose sole writer,
`capture_provisioning_relationship()` (`src/ops/provisioning_edges.py:150-205`),
only ever inserts a `SUBPROV_TO_CREATOR` edge when a creator is
**already known** (`if subprov and creator:`, line 195) — called
exclusively from the walkback success path. Per Phase 2, this table
covers only 2.3% of the confirmed-WATCHTOWER population, because these
launches were confirmed via the **live cascade**
(`wt_watchtower_launches`), a structurally separate write path that
never calls this function.

## Why this produces exactly the two mismatch patterns seen in Phase 3

- **`UNKNOWN` (20 of 21 mismatches)**: with zero `SUBPROV_TO_CREATOR`
  rows for the subprov, `_subprov_sibling_counts()` returns no entry
  for it. `classify_topology_for_launch()`'s own logic
  (`funding_topology.py:281-286`) correctly falls through to
  `UNKNOWN` when no sibling-count evidence exists and no walkback
  fallback evidence exists either — a logically consistent outcome
  *given its inputs*, but those inputs are simply absent for 90.7% of
  this population's true evidence source.
- **`LINEAR` (1 of 21 mismatches, `EGB4sv9ddN...`)**: this mint's
  subprov happens to have exactly **one** recorded
  `SUBPROV_TO_CREATOR` edge (from an unrelated walkback resolution that
  happened to touch the same subprov once) — `n_siblings == 1`
  triggers the `LINEAR` branch (`funding_topology.py:276-280`)
  confidently, even though the same subprov independently shows 25
  distinct recipients in `wt_candidate_websocket_watches`.

## Root cause classification (matching the task's example categories)

| Candidate cause | Applies? | Evidence |
|---|---|---|
| **Creator-only traversal** | **Yes — the primary cause** | `_subprov_sibling_counts()` counts creators exclusively; confirmed by the writer-side gate at `provisioning_edges.py:195` |
| **Incomplete provisioning graph** | **Yes — the underlying condition** | `wt_provisioning_edges` covers 2.3% of the confirmed-WATCHTOWER population (Phase 2) |
| **Missing sibling expansion** | **Yes — the structural gap** | `wt_provisioning_edges`'s schema CHECK constraint (`provisioning_edges.py:50`) has no edge type for non-creator siblings at all — this is not a missing query, it's a missing representation |
| **Evidence ignored** | **Yes — the fixable gap** | `wt_candidate_websocket_watches` already has 90.7% coverage of this exact population (Phase 2) and is never read anywhere in `funding_topology.py` (confirmed via grep, X65.4/X65.8 both) |
| **Outdated graph construction** | Partial | `wt_provisioning_edges` is not "outdated" in the sense of being stale or abandoned (it's actively written, Phase 2) — but its *design* (creator-only edges) predates the live cascade's much richer, already-existing candidate-watch data becoming available |
| **Threshold issue** | No | The `>1` fan-out threshold itself is not the problem — even a threshold of `>0` would fail identically, since the count is zero for 90.7% of this population regardless of threshold value |

## Conclusion

A single, well-understood mechanism — Topology's exclusive reliance on
a creator-only edge table with near-zero coverage of the
cascade-confirmed population — fully explains all 21 mismatches. This
matches X65.4's original finding exactly and is now reconfirmed
directly against Campaign's live, independently-computed output rather
than a standalone replay.
