# X65.8 — Phase 6: Preserve Existing Behaviour

## Continues classifying non-WATCHTOWER launches correctly

The revised rule (Phase 5) only changes which evidence source is
consulted *first* at the existing Fan-Out/Linear decision point — it
does not change what happens for a launch whose subprov has **no**
`wt_candidate_websocket_watches` coverage at all (the common case for
walkback-only-resolved, non-cascade-confirmed launches, per Phase 2).
For those launches, the decision falls through to the exact same
`wt_provisioning_edges`-based logic that runs today (Phase 5's steps
5c/5d/5e) — completely unchanged in code and in outcome.

## Preserves existing Linear detection

`LINEAR` is reachable via the same three paths as today
(candidate-watch count = 1, provisioning-edge sibling count = 1,
walkback fallback with no observed branch, or treasury-direct-no-subprov)
— none of these paths are removed or altered in their own logic, only
reordered in priority. A launch that is genuinely Linear under today's
evidence remains Linear under the revised evidence, because a real
single-recipient subprov will show `candidate_watch_count=1` (if
covered) or fall through to the unchanged `wt_provisioning_edges` check
(if not).

## Preserves Mesh detection

`MESH`'s decision point (step 4, `_mesh_treasuries()`) is entirely
unchanged — it runs **before** the revised Fan-Out/Linear logic in the
decision order and does not read `wt_candidate_websocket_watches` at
all. No change proposed to this branch in this task.

## Preserves Unknown where evidence genuinely does not exist

A launch with **no** subprov, **no** treasury, **no**
`wt_candidate_websocket_watches` coverage, and **no**
`wt_provisioning_edges` coverage still correctly falls through to
`UNKNOWN` (Phase 5's step 5f / step 2's early-exit) — the revised
design adds an additional evidence check, it does not remove the final
fallback that protects against fabricating a classification when no
evidence exists anywhere.

## Only WATCHTOWER launches with validated operational fan-out migrate

Per Phase 3's replay, the 21 launches that migrate from
`UNKNOWN`/`LINEAR` to `FAN_OUT` under the revised design are precisely
the 21 launches that (a) Campaign already independently confirms as
`WATCHTOWER` (via its own, separately-computed evidence) and (b) show
real, substantial `wt_candidate_websocket_watches` fan-out (2 to 481
recipients). No non-WATCHTOWER launch is affected by this change,
because the revised evidence source
(`wt_candidate_websocket_watches`) has essentially zero coverage
outside the cascade-confirmed population (Phase 2's finding that
`OTHER_CAMPAIGN`/`UNCLASSIFIED` launches almost never appear in this
table, since it is populated exclusively by the live cascade's
wrap-close detector, which walkback-only launches never pass through).

This is not a coincidence requiring special-casing — it follows
directly from the evidence source's own natural coverage boundary, not
from any Topology-side reference to Campaign's output. Topology would
independently reach the same 21-launch migration set even if Campaign
did not exist, because both classifiers are drawing on the same
underlying fact (this specific subprov produced this many wrap-close
recipients) from the same table.
