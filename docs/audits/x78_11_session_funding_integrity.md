# X78.11 Session Funding Integrity & WATCHTOWER Contamination Audit

Audit frozen: 7 August 2026. Historical databases were queried read-only. No
historical session, Operator, treasury, launch membership, reconciliation result,
or governance decision was changed.

## Executive verdicts

| Area | Verdict | Basis |
|---|---|---|
| Forward session detector | **SAFE** | New ancestry requires an explicit parsed source and destination in the funding transaction. |
| Canonical WATCHTOWER | **PARTIALLY CONTAMINATED** | The canonical identity has independent/manual evidence, but 39 of 176 launch chains are not fully supportable without inherited session claims. |
| Historical sessions | **UNSAFE AS DIRECT LINEAGE** | The persisted screening universe mixes direct transfers, indirect senders and transactions with no directional edge to the stored child. |
| Platform impact | **CANONICAL IDENTITY IMPACT** | Session roots feed canonical adapters and attribution consumers, although this audit does not demote or rewrite any identity. |
| Historical repair | **QUARANTINE + INCREMENTAL REBUILD** | Preserve rows as context; admit them to lineage only after transaction replay establishes each direct edge. |

## A. Forward detector audit and correction

### Session-entry paths

The active paths capable of opening directional sessions were:

1. Treasury websocket processing in `Cascade._handle_treasury_tx`.
2. Recursive subprovider processing in `Cascade._handle_subprov_tx`.
3. Temporary-candidate promotion in `Cascade._temp_candidate_sweep`.
4. Capital-distributor promotion in `Cascade._handle_cdc_tx`.
5. Durable pending-session replay in `drain_pending_sessions`.
6. Enhanced webhook treasury outbounds in `_process_wt_infra_payload`.

The defect was in paths 1 and 2: sender loss plus another account's positive
balance delta could be promoted to ancestry. Paths 3–5 could replay the stored
claim without re-verifying its original transaction. The webhook path consumed a
direct `nativeTransfers` item, but could substitute a transaction-level net-flow
counterparty before opening the session.

The new shared contract, `_explicit_native_funding_transfers`, accepts only:

- parsed outer or inner System Program `transfer`/`transferWithSeed` where the
  claimed parent is the explicit source and the child is the explicit recipient;
- a WSOL `closeAccount` where the claimed parent owns the identified WSOL token
  account and directs the close proceeds to a distinct recipient.

A recipient balance delta is used to quantify a WSOL close only after ownership
and direction are explicit. It never establishes direction. Self-close, trading
gain, program co-occurrence, signing, and account-key presence cannot open a
session. Temp/CDC promotion replays the original funding signature. Pending
replay fails closed with `UNVERIFIED_DIRECTIONAL_EDGE`. The webhook uses its
literal `fromUserAccount -> toUserAccount` edge for lineage even when a separate
net-flow counterparty is useful for presentation.

Compact telemetry preserves rejected context through
`POSITIVE_DELTA_DESCENDANTS_REJECTED`; no new schema was introduced.

### Transaction controls

RPC replay of the five required negative classes produced no claimed false edge:

| Control | Result |
|---|---|
| WSOL self-close | Rejected |
| Self-signed trading/net gain | Rejected |
| Passive swap/program-account gain | Rejected |
| Reverse-direction transfer | Rejected in the claimed direction |
| Co-signed transaction | Claimed co-signer edge rejected; a different explicit transfer remained visible |

The positive control remained intact, with every hop proved by its own signature:

`69SN -> 9St6 -> 8CEy -> Bvv4 -> 5tzF`.

## B. Canonical WATCHTOWER audit

### Frozen census

| Measure | Current canonical state |
|---|---:|
| Launches / distinct mints | 176 / 176 |
| Creators | 176 |
| Launch treasury roots | 15 |
| Effective subproviders | 160 |
| Funding mechanisms | 153 WSOL close; 18 seeded-account close; 5 plain transfer |
| Creator extraction | 43 close destination; 133 walkback recovered |
| Operator identity treasury entities/assets | 69 / 69 |
| Operator evidence records | 5 |

The 69 identity treasury assets are not 69 independent transaction proofs. Many
are deterministic projections/backfills from `operator_entities`. The core
confirmed identity does retain independent legacy/manual evidence: repeated
infrastructure reuse across the initial entities/operations, vanity family,
matching funding templates and chain activity. Consequently this audit does not
invalidate or demote WATCHTOWER.

### Transaction-clean shadow reconstruction

Inherited `treasury_wallet` values from session rows were excluded unless their
stored signature independently proved the asserted direct edge.

| Measure | Current | Transaction-clean/supportable |
|---|---:|---:|
| Launches | 176 | 137 |
| Treasury roots | 15 | 13 |
| Effective subproviders | 160 | 140 |
| Creator edges | 176 | 149 |
| Treasury chains | 176 | 154 |

The 39 unsupported complete launch chains split into 22 treasury-chain gaps and
17 creator-evidence gaps. This means canonical membership is not wholly
independent of historical session evidence, but 137 launch topologies survive
the stricter contract.

### Treasury-family integrity

`clean` requires both a supportable treasury chain and creator edge for the
launch. `chain` reports explicit treasury-to-subprovider support even where the
creator side remains incomplete.

| Treasury | Launches | Clean | Supported chains | Verdict |
|---|---:|---:|---:|---|
| DchJ… | 58 | 47 | 47 | Partially dependent |
| 9hGc… | 24 | 23 | 23 | Partially dependent |
| Dtwi… | 17 | 16 | 17 | Independently supported treasury chain |
| Fkcc… | 16 | 16 | 16 | Independently supported |
| Cgwr… | 10 | 2 | 9 | Creator evidence incomplete |
| 4231… | 10 | 10 | 10 | Independently supported |
| 5nTJ… | 9 | 9 | 9 | Independently supported |
| 43PK… | 7 | 5 | 5 | Partially dependent |
| yUpm… | 7 | 0 | 7 | Treasury chain supported; creator evidence incomplete |
| 69SN… | 7 | 1 | 2 | Genuine evidence retained; mostly unresolved in canonical launch projection |
| 9gv9… | 5 | 5 | 5 | Independently supported |
| EFKV… | 3 | 3 | 3 | Independently supported |
| 3sSt… | 1 | 0 | 1 | Creator evidence incomplete |
| alternate 43PK… | 1 | 0 | 0 | Unresolved |
| G2CQ… | 1 | 0 | 0 | Unresolved |

For the named controls, 4231 and EFKV survive fully. 69SN has real directional
evidence—including the four-hop positive control—but that evidence must remain
separate from the frozen Binance contamination and does not prove the other five
current canonical launch chains.

Mechanism labels were retained only as observations. A historical
`WSOL_WRAP_CLOSE` default was not treated as independent ancestry evidence.

## C. Historical screening and stratified replay

The live database grew slightly between the supplied baseline and audit freeze.
The frozen screen contained **215,470** WSOL-close-labelled rows (not 215,470
confirmed corrupt rows), 14 roots and 101,955 distinct children. Of those,
206,880 carried 69SN as inherited root. There were no missing signatures, no
`funding_time > detected_at` rows, and no root-equals-child rows. No row had an
exact same-signature root-to-child corroboration in the separate persisted
provisioning-edge table.

A quantile/time-stratified RPC sample covered every root (59 transactions):

| Replay class | Rows | Share |
|---|---:|---:|
| Stored root is explicit direct sender | 30 | 50.8% |
| Explicit sender exists but differs from stored root | 16 | 27.1% |
| No directional edge to stored child | 13 | 22.0% |

This is evidence of a mixture: genuine direct funding, potentially valid
multi-hop activity whose parent edge was flattened, and contamination. It is not
evidence that all rows are corrupt, nor that 69SN genuinely controls 96% of the
universe. The concentration is consistent with root-inheritance amplification
plus real operational activity.

### Six stored Operations exposed

All six were `FORMING`; none was modified. Samples are shown as
`root-direct / different-sender / no-edge`.

| Root | Operation | Sessions | Children | Stratified sample |
|---|---|---:|---:|---|
| DchJ… | 69af7941… | 3,585 | 3,068 | 2 / 2 / 1 |
| Dtwi… | 9868e8dd… | 587 | 529 | 2 / 1 / 2 |
| 9hGc… | 4135d67d… | 124 | 109 | 3 / 1 / 1 |
| 43PK… | 8c73b9a0… | 55 | 32 | 5 / 0 / 0 |
| Cgwr… | de6473a7… | 2 | 2 | 2 / 0 / 0 |
| 41iv… | c7f182da… | 1 | 1 | 1 / 0 / 0 |

The sample supports exposure, not a precise whole-population corruption rate.

## D. Downstream dependency map

| Classification | Consumers |
|---|---|
| Safe when used only for monitoring/lifecycle | Cascade subscription targeting, active-session health/counts, scheduler cadence |
| Presentation-only risk | `operation_dashboard_routes`, `operation_intelligence`, Operational Role/session topology renderers |
| Attribution risk | `treasury_resolution`, `funding_topology`, `campaign_classification`, `ecosystem_intelligence`, `emerging_operator_service`, `provisioning_candidates_workflow`, Discovery/Investigation projection, Treasury Review, temporal launch allocation, `evidence_reconciliation`, `detection_reconciliation` |
| Canonical-identity risk | `watchtower_canonical_adapters` session-root fallback, canonical launch/operator asset projection, operator similarity paths when session ancestry is treated as direct |

The risk classification describes dependency, not proof that each consumer has
already emitted an incorrect result.

## E. Repairability and required provenance

- **SAFE_TO_RECONSTRUCT:** stored signatures whose parsed transaction directly
  proves the asserted sender and recipient.
- **REQUIRES_RPC_REPLAY:** signatures present but insufficiently decomposed in
  persisted evidence, including sender/root mismatches that may represent a real
  parent edge.
- **CONTEXT_ONLY:** co-occurrence, shared swaps/programs, co-signing and temporal
  association.
- **UNRESOLVABLE:** missing/unavailable transaction bodies or ambiguous ownership.
- **DO_NOT_USE_AS_LINEAGE:** self-close and no-directional-edge rows.

Future relationship provenance must store root identity separately from direct
sender and recipient, parent session/edge, signature, relationship type, ancestry
depth, evidence source, mechanism, transaction timestamp, and observed-versus-
inherited status.

Recommended next milestone: quarantine inherited session roots from authoritative
lineage, replay/rebuild incrementally, retain context-only rows for analysis, and
promote an edge only when its own transaction satisfies the directional contract.
No historical mass repair was performed in X78.11.

## Regression record

- 148 passed; 2 failed across the focused and cross-layer selections.
- Unrelated failure: `test_x71_2_reconciliation_ui` expects the exact legacy
  fixture name `B48k / Dv34 Family`; that name is absent from the current fixture.
- Unrelated failure: `test_x69_3_reconciliation_diagnostics` contains frozen live
  metric totals (for example 192 agreements and 4 review populations); the current
  database reports 238 and 7 respectively.
- Runtime after restart: `ws_cascade`, `watchtower_api`, and
  `watchtower_listener` were RUNNING; Registry and Discovery returned HTTP 200.
  `/healthz` returned 503 because it reports several pre-existing stale worker
  heartbeats, while its database check remained `ok`.
