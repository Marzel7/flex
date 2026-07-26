# X65.2 — Phase 4: Pipeline Coverage Analysis

Full ingestion pipeline map (birth → attribution), and the first stage
at which evidence disappears for each of the 12 `UNRESOLVED` launches.
Read-only; synthesizes Phases 2 and 3.

## Full pipeline (as it exists today)

```
1. PumpPortal WS "create" event received
   → pumpfun_curve_listener.py:10798, tx_type == "create"
   → _portal_vsol[mint] populated (creator cached in memory)
   → _insert_bonding_curve_token(mint, creator, ..., create_tx_signature=sig)
       → INSERT ... ON CONFLICT DO UPDATE into token_analysis
       → on success: log [PREMIG_BIRTH_SEED], then caller logs [PUMPPORTAL] 🟢 Birth
   → _ensure_pf_ws_creator(mint, reason="birth") fired as background task
       → portal fast-path or RPC-validated path resolves pf_ws_creator

2. (Parallel/independent) On-chain program-log CREATE observation
   → handle_birth() (line 6112), 3 live call sites
   → _write_create_ledger_durable() → wt_create_ledger_pending → wt_create_event_ledger

3. Realtime creator-funding extraction (per docs/CLAUDE.md)
   → triggered at pumpfun_curve_listener.py:1728, gated on a successful birth
   → realtime_creator_funding_extractor.py: extract_funding_for_new_token()
   → populates creator_funders (creator_address, funder_address, amount_sol, tx_sig)

4. Funder-transfer extraction
   → triggered at line 1734, for each row from step 3
   → funder_incoming_extractor.py: extract_for_creator()
   → populates funder_incoming_transfers

5. Sub-provider / treasury lineage (separate subsystem, WS-driven)
   → ws_cascade daemon watches confirmed SUB_PROV wrap-close fan-out
   → populates wt_active_subprov_sessions, wt_provisioning_edges

6. Walkback queue (backstop, RPC-capable, can run independent of steps 1-5)
   → wt_walkback_queue: FULL_WALKBACK / LINK_ONLY / SKIP / PARTIAL
   → can independently recover a funder_wallet and even a create_anchor_signature
   → writes wt_attribution_outcomes (terminal_entity, outcome_type)

7. Topology / operational intelligence derivation
   → operational_intelligence.py reads creator_funders / wt_provisioning_edges /
     wt_active_subprov_sessions (NOT wt_attribution_outcomes directly for topology)
   → produces topology=UNKNOWN when no funding-edge evidence exists,
     regardless of whether step 6 completed
```

## Coverage matrix — first stage where evidence disappears

| Mint | Step 1 (birth persist) | Step 2 (ledger) | Step 3 (creator_funders) | Step 5 (subprov) | Step 6 (walkback) | First gap |
|---|---|---|---|---|---|---|
| B3Fq8SqBtsxsWw... | ✗ signature lost (no log evidence retained) | ✗ 0 rows | ✗ 0 rows | ✗ 0 rows | ✓ complete | **Step 1** |
| CmoCuZ9J2YT1QH... | ✗ signature lost (birth logged, seed missing) | ✗ 0 rows | ✗ 0 rows | ✗ 0 rows | ✓ complete | **Step 1** |
| HHcXBLbnuSWdYi... | ✗ signature lost | ✗ 0 rows | ✗ 0 rows | ✗ 0 rows | ✓ complete | **Step 1** |
| EQZfBpWpQc5BEU... | ✗ signature lost | ✗ 0 rows | ✗ 0 rows | ✗ 0 rows | ✓ complete | **Step 1** |
| DpTtRHY6PSuxxJ... | ✗ signature lost | ✗ 0 rows | ✗ 0 rows | ✗ 0 rows | ✓ complete | **Step 1** |
| CvP9vVUCpoDuMd... | ✗ signature lost | ✗ 0 rows | ✗ 0 rows | ✗ 0 rows | ✓ complete | **Step 1** |
| 4WfoYERYFw3AQW... | ✗ signature lost | ✗ 0 rows | ✗ 0 rows | ✗ 0 rows | ✓ complete | **Step 1** |
| EDNvjVDjKVfRsq... | ✗ signature lost | ✗ 0 rows | ✗ 0 rows | ✗ 0 rows | ✓ complete | **Step 1** |
| 71TKvknpvwRcjd... | ✗ signature lost (no log evidence retained) | ✗ 0 rows | ✗ 0 rows | ✗ 0 rows | ✓ complete | **Step 1** |
| c5Zye8yFd1AGrS... | ✗ signature lost | ✗ 0 rows | ✗ 0 rows | ✗ 0 rows | ✓ complete | **Step 1** |
| 9Mn2t7yX2TmSSM... | ✗ signature lost | ✗ 0 rows (queue recovered one independently, not propagated) | ✗ 0 rows | ✗ 0 rows | ✓ complete, `CREATE_ANCHORED`/`VALID` | **Step 1** (partially recoverable — see Phase 6) |
| FzNgpR11RYACas... | ✗ signature lost | ✗ 0 rows | ✗ 0 rows | ✗ 0 rows | ✓ complete | **Step 1** |

**All 12 launches share the identical first-gap stage: Step 1, the
birth-time persistence of `create_tx_signature`.** Every downstream
stage's gap (Step 2 ledger, Step 3 creator_funders) is a direct,
fully-explained consequence of Step 1's gap — none of steps 2-5 show
any independent failure of their own; they simply never had a trigger
condition (a persisted CREATE signature / successful birth) to act on.
Step 6 (walkback) is the only stage that runs independently of Step 1
and it succeeds for all 12, terminating correctly at
`INSUFFICIENT_EVIDENCE` because the funder wallets it finds have no
further indexed lineage regardless of the Step 1 gap.

## Why this is a single root cause, not twelve separate incidents

The uniformity is the key finding: 12/12 launches fail at exactly the
same stage, with the same signature (`pf_ws_creator` set,
`create_tx_signature` NULL, `[PREMIG_BIRTH_SEED]` absent from logs).
This rules out random/incidental causes (RPC timeouts, one-off DB
locks, transient network errors would be expected to produce a mixed
pattern across 12 independent launches spanning 6 days) and points to
a systemic, reproducible condition in the birth-insert call path —
consistent with Phase 2's finding that `sig` was empty/falsy at the
point `_insert_bonding_curve_token()` was called, for reasons Phase 5
investigates further.
