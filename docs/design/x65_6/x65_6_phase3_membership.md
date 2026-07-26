# X65.6 — Phase 3: Define Membership

Reuses and finalizes the membership model designed in X65.5 Phase 3,
now specifically scoped as the exhaustive, mutually-exclusive
`campaign` classification described in Phase 2. No
detection logic is introduced or modified — every input below is
already-persisted evidence read by existing modules
(`src/ops/creator_identity.py`, `wt_watchtower_launches`,
`wt_candidate_websocket_watches`, `src/ops/treasury_resolution.py`).

## Decision model

```
For a given launch:

1. Is creator_identity == FRESH_CREATOR?
   NO  → OTHER_CAMPAIGN if any funding lineage exists at all,
         else UNCLASSIFIED
   YES → continue

2. Is there direct evidence of wrap-close provisioning reaching this
   creator (a wt_watchtower_launches row, OR a wt_attribution_outcomes
   evidence_json.subprovisioners entry backed by a real
   wt_active_subprov_sessions/wrap-close session)?
   NO  → OTHER_CAMPAIGN if other (non-wrap-close) funding lineage
         exists, else UNCLASSIFIED
   YES → WATCHTOWER (mandatory criteria satisfied)

3. [Confidence refinement, WATCHTOWER launches only —
    does not affect membership, only the displayed confidence tier]
   Check, independently and without excluding on absence:
     - SubProv fan-out observed (wt_candidate_websocket_watches,
       subprov produced >1 distinct recipient)
     - Single-use provisioner confirmed (wrap wallet funded exactly 1
       candidate, per available wt_candidate_websocket_watches history)
     - Provisioner not reused (wrap wallet used by exactly 1 subprov)
     - Treasury linkage exists (any treasury_resolution.py status other
       than UNRESOLVED/NO_SUBPROV)
     - Treasury is a KNOWN WATCHTOWER treasury (treasury_resolution.py
       status == KNOWN_TREASURY)
   → High: mandatory criteria + fan-out observed + (single-use AND not-reused confirmed)
   → Medium: mandatory criteria + at least one confidence signal present
   → Baseline: mandatory criteria only, no further corroborating evidence available
```

## Mandatory vs. optional (unchanged from X65.5, restated precisely for this integration)

| Evidence | Tier | Why |
|---|---|---|
| `creator_identity == FRESH_CREATOR` | **Mandatory** | The provisioning model is specifically about newly-funded creators |
| Wrap-close provisioning evidence reaching the creator | **Mandatory** | The one irreducible structural signature of the mechanism itself |
| SubProv fan-out (>1 recipient) | Optional, confidence-increasing | Strongest signal (X65.4: 88.4% of confirmed launches) but coverage-limited (X65.4 Phase 5) — must never exclude on absence |
| Single-use provisioner confirmed | Optional, confidence-increasing | Validated in 100% of checked cases (X65.4 Phase 3A) but partial coverage |
| Provisioner not reused | Optional, confidence-increasing | Same basis as above |
| Treasury linkage (any status) | Optional, confidence-increasing | Explicitly never gating, per Phase 4 |
| Known WATCHTOWER treasury (`KNOWN_TREASURY`) | Optional, confidence-increasing (highest treasury-related signal) | Strongest treasury-specific signal, but a launch with `UNKNOWN` or `NEW` treasury and strong fan-out/single-use evidence can still reach **High** confidence — treasury status is one input among several, not a required one |

## Why the model tolerates incomplete evidence by construction

Because only 2 of 7 candidate signals are mandatory, and the 5
remaining signals only ever raise confidence from a floor of
Baseline, a launch can never be excluded from `WATCHTOWER`
for lacking any signal beyond the two structural essentials. This
directly satisfies the task's explicit requirement ("Mandatory
evidence should remain intentionally minimal... never excludes
membership").

## No detection-logic changes

Every signal above is read from data already produced by existing,
unmodified modules — `enrich_creator_identity()`,
`_handle_subprov_tx()`'s existing writes to
`wt_candidate_websocket_watches`, and `resolve_treasury_for_cohort()`.
This phase defines a new **read-only classification function** (not
implemented in this task) that composes these existing reads; it adds
no new RPC, no new WS subscription, no new walkback logic, and changes
no existing table's write path.
