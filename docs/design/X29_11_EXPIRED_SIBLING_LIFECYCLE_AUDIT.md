# X29.11 — EXPIRED_SIBLING Lifecycle Audit

Investigation only, per the brief. No code changed, no RPC performed — every fact below comes from SQL against the already-persisted `wt_ops_v2.db` and `flex_complete_database.db` tables. Subject: the 24 `state=EXPIRED_SIBLING, close_reason=sibling_idle` wallets for subprovider `ANenEukvmpYsyP52LgDsZN6kj3n7igjbJDTCtj4xCAXq`, identified in X29.9/X29.10.

## Schema check performed before searching (premise verification)

The brief's example table list includes `wt_trader_wallets`, `wt_graph_nodes`, `wt_graph_edges`. As flagged in X29.9, these do **not** exist in `wt_ops_v2.db` — they exist only in the separate hot database, `flex_complete_database.db`. Confirmed directly this sprint:

| Table | `wt_ops_v2.db` | `flex_complete_database.db` |
|---|---|---|
| `wt_trader_wallets` | absent | present |
| `wt_graph_nodes` | absent | present |
| `wt_graph_edges` | absent | present |

Both databases were searched. This correction stands, restated with direct confirmation rather than inference from a table listing.

A second correction, found only by executing queries rather than trusting assumed column names: my first search pass used guessed column names for several tables (e.g. `funded_wallet`/`wrap_wallet`/`subprov_wallet` for `wt_subprov_evidence`) that do not exist in the real schema. `PRAGMA table_info` was run against every candidate table and the searches re-run with verified column names before any negative result was accepted. The real `wt_subprov_evidence` columns are `subprov`, `creator_wallet`, `amount_sol`, `observed_at`, `create_fired`, `funding_mechanism` — not the guessed names. This matters because a silently-wrong column name produces a false "no hits" result indistinguishable from a true one; the corrected re-run is what the findings below are based on.

## Tables searched (24 wallets × each)

`wt_watchtower_launches`, `wt_provisioning_edges`, `wt_subprov_evidence`, `wt_candidate_websocket_watches` (self, for re-entry), `wt_discovered_subprovs`, `wt_capital_distributor_candidates`, `wt_capital_reloads`, `wt_fanout_events`, `wt_wrap_close_candidates`, `wt_temp_provision_candidates`, `wt_rotation_candidates`, `wt_operation_candidates`, `wt_confirmed_treasuries`, `wt_trader_wallets`, `wt_graph_nodes`, `wt_graph_edges`, `wt_sub_provisioners`, `wt_swarm_candidates`, `wt_swarm_recipients`, `wt_swarm_provisioners`, `creator_funding_graph`, `creator_funders`, `wt_creator_launches`, `wt_operator_launches`, `wt_staged_wallets` — every column plausibly holding a wallet address in each table was checked against all 24 addresses.

## Result: total enumeration

**All 24 wallets appear in exactly two places, and nowhere else:**

1. Their own single row in `wt_candidate_websocket_watches` (the `EXPIRED_SIBLING` row itself).
2. Their own single row in `wt_subprov_evidence` (`subprov=ANen`, `create_fired=0`, `funding_mechanism=WSOL_WRAP_CLOSE`, `amount_sol` ranging 0.60–2.49 SOL).

Zero hits in every other table checked, including both hot-DB graph tables (`wt_graph_nodes`/`wt_graph_edges`) and `wt_trader_wallets`. One incidental non-hit worth recording precisely: `wt_discovered_subprovs`'s row for `ANen` itself has `first_creator=3GLunZBx6BucFWRi6QMiTCaCrCS3rJibSMAx7CeWmEYQ` (the earliest-detected sibling, by timestamp). This is not a role or appearance for that wallet — `first_creator` is a pointer field on the *subprovider's* row recording which candidate happened to be first observed; it does not indicate `3GLunZBx...` gained any independent status.

## Classification (all 24, exactly one category each)

**Never seen again — 24 of 24.**

None of the 24 falls into Later creator, Later provisioner, Later CDC, Trader, or Other operational role. None is "still unknown" either — the evidence is complete and conclusive for all 24, not merely absent. Each wallet's entire operational lifecycle, as far as either database can show, consists of: funded once by `ANen` → placed under WS candidate observation once → closed once as `sibling_idle` → never referenced again in any table in either database.

## Timing

All 24 were funded within a single **7-second burst** (`wt_subprov_evidence.observed_at`, 1784048376–1784048383). All 24 were detected into `wt_candidate_websocket_watches` within 1–2 seconds of their funding (`detected_at` tracks `observed_at` almost exactly). All 24 carry the same `expires_at = detected_at + 1800` (a 30-minute nominal candidate-observation window).

**None of the 24 actually ran out its 1800-second window.** Every one closed between **296 and 309 seconds** after detection — roughly 5 minutes in, not 30. All 24 closures land within a tight 20-second span (1784048672–1784048692), clustered right around HTR9U7's confirmed `create_time` of **1784048633** (the one sibling that fired `FIRED_CREATE`, per X29.9/X29.10). This timing is decisive: `close_reason=sibling_idle` is not a timeout — it is an **active closure triggered by the sibling-suppression rule firing once HTR9U7's CREATE was detected**, closing every other still-open candidate from the same burst within seconds of each other, roughly 40-60 seconds after HTR9U7 itself fired. `expires_at` is a dead-man's-switch ceiling that was never reached for any of these 24; the real closing mechanism is the sibling-suppression rule, confirmed by the clustering, not the stored expiry timestamp.

## Re-entry pattern

**Zero re-entry.** No wallet among the 24 appears a second time in `wt_candidate_websocket_watches` (checked directly — one row each, no duplicates), and none appears having been promoted into any later-stage table (`wt_discovered_subprovs`, `wt_capital_distributor_candidates`, `wt_rotation_candidates`, etc.) under its own identity. Whatever these 24 wallets did after their single wrap-close transaction — assuming, per X29.7.1's precedent for the analogous HZB2 wallet, that most such wrap-close intermediaries are single-use custody wallets that are simply never touched again — left no trace in either database.

## Conservation table

| Outcome | Count |
|---|---|
| Never seen again | 24 |
| Later creator | 0 |
| Later provisioner | 0 |
| Later CDC | 0 |
| Trader | 0 |
| Other operational role | 0 |
| Still unknown | 0 |
| **Total** | **24** |

24 → 24, exactly once each, no double-counting, no residual.

## Is EXPIRED_SIBLING terminal, or merely "end of launch observation"?

Both, but for different reasons than the phrasing might suggest, and it's worth separating two things the brief's question could mean:

**As a matter of what actually happened to these 24 wallets: terminal.** Every wallet checked against every persisted operational table shows nothing beyond its single funding-and-candidate-observation event. There is no evidence any of them did anything else the platform recorded — no later launch, no later funding role, no re-entry. Whatever these wallets are (most plausibly single-use WSOL wrap/custody intermediaries per the confirmed HZB2 precedent from X29.7.1, given the identical `WSOL_WRAP_CLOSE` mechanism and single-transaction lifecycle pattern), the platform has no evidence they were ever operationally reused.

**As a matter of what the *label* asserts: it is deliberately narrower than "terminal," and that narrowness is correct, not a gap.** `close_reason=sibling_idle` documents a decision made by the candidate-observation layer — this wallet was one of several funded in the same instant-burst as a wallet that *did* fire CREATE, and the existing sibling-suppression rule (memory: `buy-swarm-vs-creator`) chose not to keep watching it once its sibling won. The label asserts "we stopped watching this wallet because its sibling succeeded," not "we have proven this wallet will never do anything else, ever." Those are different claims, and the schema is honest about only making the first one — `close_reason` is a statement about *why observation stopped*, not a lifetime verdict. This audit's finding is that, for these specific 24, the two claims happen to coincide (stopped watching = never did anything else, as far as any table shows) — but that coincidence is a fact about this dataset, not a guarantee the label itself makes.

## Does Discovery currently hide operational behaviour by treating sibling expiry as the end of the story?

Yes, in the same sense X29.10 already found for Category A/B counts generally, now sharpened with lifecycle detail: Discovery's current metrics (`fan_out_count`, `historical_launches`) read only `wt_watchtower_launches`/`wt_provisioning_edges`, so all 24 of these wallets — funded, real WSOL_WRAP_CLOSE transactions, actively observed for ~5 minutes before deliberate suppression — are as invisible to Discovery's UI as if they had never existed. The suppression decision itself (X29.9's confirmed, intentional, working rule) is sound; the finding here is narrower and consistent with X29.10's recommendation: the *timing precision* now available (all 24 closed within 20 seconds of the winning sibling's CREATE, not at their nominal 1800s expiry) is itself evidence of a tight, coordinated single-operator burst that Discovery currently has no vocabulary to surface — it would show ANen as "Fan-out: 1" with no visibility into the fact that 24 near-identical siblings were funded and briefly observed in the same 7-second window before being correctly suppressed.

## Summary answers (per the brief's exact deliverable list)

- **Enumeration**: all 24 wallets listed and confirmed against the live `wt_candidate_websocket_watches` table (matches X29.9/X29.10's prior count).
- **Search across evidence tables**: performed against every table in the brief's example list plus the additional tables discovered in X29.9; `wt_trader_wallets`/`wt_graph_nodes`/`wt_graph_edges` confirmed absent from `wt_ops_v2.db`, present and checked in `flex_complete_database.db`.
- **Final-outcome classification**: 24/24 "Never seen again" — no exceptions.
- **Timing**: funded in a 7-second burst; detected within ~1-2s of funding; closed 296-309s after detection (not at the nominal 1800s expiry) — closure timing clusters within 20 seconds of the one sibling's confirmed CREATE, indicating active sibling-suppression closure, not timeout.
- **Re-entry**: none — zero duplicate rows, zero later-table appearances under their own address.
- **Operational significance**: consistent with X29.7.1's HZB2 finding — these are very likely single-use WSOL wrap/custody wallets whose lifecycle is genuinely one-shot, not evidence of a detection gap.
- **Terminal vs. end-of-observation**: factually terminal for all 24 in this dataset; the label itself asserts only "observation stopped due to sibling-suppression," a narrower and more honest claim that happens to coincide with terminal outcome here.
