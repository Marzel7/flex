# X65.8 — Phase 5: Design Updated Topology Logic

Design only — no code changes in this task. Designs a revised
Fan-Out/Linear rule for `classify_topology_for_launch()` that consumes
the same richer evidence Campaign already uses
(`wt_candidate_websocket_watches`), while remaining a fully independent
classifier.

## Architecture constraint (from the task, honored exactly)

```
Observed Evidence
        │
        ├── Campaign
        └── Topology
```

**Never**:
```
Observed Evidence
        │
Campaign
        │
Topology
```

Topology's revised logic reads `wt_candidate_websocket_watches`
directly — the same underlying SQLite table Campaign's
`_fanout_evidence_for_subprovs()` (`src/ops/campaign_classification.py`)
also reads — but Topology does **not** call any Campaign function, does
not read `records[mint]["campaign"]`, and does not depend on
`build_campaign_classification()` having run first or at all. Both
classifiers independently query the same table and reach their own,
separately-computed conclusion. This is the same relationship
`funding_topology.py` and `operational_behaviour_tags.py` already have
today (both independently read from overlapping evidence, X29.1) — not
a new pattern.

## Revised Fan-Out/Linear rule

Current (`_subprov_sibling_counts()`, `funding_topology.py:58-69`):
```python
def _subprov_sibling_counts(ops_conn):
    # COUNT(DISTINCT to_wallet) grouped by from_wallet,
    # from wt_provisioning_edges WHERE edge_type='SUBPROV_TO_CREATOR'
```

Revised — add a new, independent evidence function (a new function in
`funding_topology.py`, not a call into `campaign_classification.py`):

```python
def _subprov_candidate_watch_counts(ops_conn):
    """{subprov_wallet: distinct candidate_wallet count} from
    wt_candidate_websocket_watches -- every recorded wrap-close
    destination a subprov has ever produced, creator or not. This is
    the SAME table Campaign's own fan-out check reads
    (src/ops/campaign_classification.py), queried independently here
    with Topology's own SQL -- no cross-module call, no shared
    function, no dependency on Campaign having run."""
    if not _table_exists(ops_conn, "wt_candidate_websocket_watches"):
        return {}
    rows = ops_conn.execute(
        "SELECT subprov_wallet, COUNT(DISTINCT candidate_wallet) AS n "
        "FROM wt_candidate_websocket_watches GROUP BY subprov_wallet"
    ).fetchall()
    return {r[0]: r[1] for r in rows}
```

## Revised decision order within `classify_topology_for_launch()`

The existing decision tree (X65.8 Phase 1) is preserved in full — this
is an **additive priority insertion**, not a rewrite of the whole
function:

```
1. MULTI_LEVEL_FAN_OUT (walkback-depth variant)      [UNCHANGED]
2. No lineage evidence at all → UNKNOWN               [UNCHANGED]
3. MULTI_LEVEL_FAN_OUT (sub-subprov session variant)  [UNCHANGED]
4. MESH                                               [UNCHANGED]
5. If a subprov is known:
   a. NEW: Does wt_candidate_websocket_watches record
      >1 distinct candidate_wallet for this subprov?
      → FAN_OUT, derived_from="candidate_watch_count=<n>"
   b. NEW: Does it record exactly 1?
      → LINEAR, derived_from="candidate_watch_count=1"
   c. [EXISTING, now a FALLBACK rather than primary] Does
      wt_provisioning_edges record >1 distinct creator?
      → FAN_OUT, derived_from="wt_provisioning_edges_sibling_count=<n>"
   d. [EXISTING fallback] Does it record exactly 1?
      → LINEAR, derived_from="wt_provisioning_edges_sibling_count=1"
   e. [EXISTING fallback] walkback-parent-fanout check
      → FAN_OUT / LINEAR as today
   f. [EXISTING] No evidence anywhere → UNKNOWN
6. Treasury-direct-no-subprov → LINEAR                [UNCHANGED]
7. UNKNOWN                                             [UNCHANGED]
```

The only change is **which evidence source is consulted first** at
step 5 — `wt_candidate_websocket_watches` (90.7% coverage of the
cascade-confirmed population, Phase 2) is promoted ahead of
`wt_provisioning_edges` (2.3% coverage of the same population, but
still the better-covering source for walkback-only-resolved launches,
per Phase 2's overlap finding) — rather than replacing it.

## Why this is additive, not a replacement

`wt_provisioning_edges` is retained as step 5c/5d, unconditionally
reachable whenever `wt_candidate_websocket_watches` has no data for a
given subprov (the common case for walkback-only-resolved launches,
per Phase 2). No existing evidence source is discarded; the new source
is only consulted **first**, with the old source remaining exactly as
useful as it is today for the population it already serves well.

## Confidence model (new, optional addition — not required by the task, but a natural fit)

The task does not require a confidence tier for Topology, and none is
proposed as mandatory here — but if desired, one MAY be added,
following the exact fingerprint-signal pattern already used for
`derived_from` (a free-text provenance string), without introducing a
`confidence` field of Campaign's own naming or shape. This is left as
an optional, separately-decidable addition for Phase 8's implementation
plan, not designed further here, since the task's Phase 6/7 focus is
strictly on correcting the FAN_OUT/LINEAR/UNKNOWN assignment itself.

## No new evidence source, no new detection

`wt_candidate_websocket_watches` already exists, is already populated
live by the unmodified `_handle_subprov_tx()` detector (X65.4 Phase 1),
and is already read by the unmodified Campaign classifier. This design
adds a second, independent reader of the same already-existing table —
zero new tables, zero new detection logic, zero new RPC.
