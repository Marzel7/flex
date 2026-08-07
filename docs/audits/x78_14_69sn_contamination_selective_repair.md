# X78.14 — 69SN Contamination Scope & Selective Repair

## Verdict

The 69SN repair is complete and selective. No historical session, transaction fact, canonical launch, Operator asset, attribution decision or reconciliation result was deleted or rewritten.

The repair separates three things which were previously conflated:

- explicit transaction evidence;
- valid indirect ancestry;
- a historical session's inherited `treasury_wallet` label.

All historical 69SN session roots are now forensic context unless the session's own transaction explicitly proves `69SN → subprovider`. A durable root policy applies the same fail-closed rule to newly arriving sessions, eliminating a race with the live writer. No other treasury has this policy.

## Repair totals

| Result | Rows |
|---|---:|
| Raw 69SN sessions preserved | 207,116 |
| Direct-transaction-proven session rows | 0 |
| Valid indirect Binance ancestry retained as context | 1 |
| Proven inherited/session-only contamination | 42 |
| Unresolved inherited roots quarantined from Tier 1 | 207,073 |
| Tier-1 eligible 69SN session roots after repair | 0 |
| Historical rows deleted | 0 |
| Canonical rows changed | 0 |

The 42 proven-invalid rows are the 38 frozen Binance-branch failures plus four wider cached transaction replays where the explicit sender differs from stored 69SN. The unresolved count includes the two named Binance controls and every other 69SN session for which no cached matching directional edge exists. `UNRESOLVED` does not mean disproved; it means the row cannot establish Tier-1 lineage.

## Evidence classification

| Class | Current result | Treatment |
|---|---|---|
| A — Direct transaction proven | No 69SN session row in the cached replay | Eligible only after insertion into the explicit verified-edge registry |
| B — Indirect transaction proven | One audited 69SN→…→5tzF chain | Preserved as contextual multi-hop evidence; flattened session excluded |
| C — Inherited session only | 42 proven invalid rows | Quarantined from Tier-1 lineage |
| D — Temporal heuristic only | Former 132-launch latest-session bucket | Removed from directional presentation |
| E — Structural/context only | Events, co-occurrence, historical clusters and monitoring records | Preserved; cannot create an edge |
| F — Manual/governance | Confirmed treasury, Operator asset, seven canonical launches | Preserved unchanged for later governance review |
| G — Unresolved | 207,073 session roots | Preserved as context, excluded until transaction proof exists |

## Complete operational persistence census

The operational database contained the following material uses. Counts are a current, moving-platform snapshot; monitoring/event rows can increase while the listener runs.

| Table / field | Rows | Semantic role | Classification / action |
|---|---:|---|---|
| `wt_active_subprov_sessions.treasury_wallet` | 207,116 | Historical inherited session root | B/C/G; raw preserved, Tier-1 excluded |
| `wt_watchtower_launches.treasury_wallet` | 7 | Canonical launch attribution | F; unchanged |
| `watchtower_token_attribution.matched_treasury` | 7 | Frozen attribution projection | F; unchanged |
| `wt_launch_audit.treasury` | 7 | Historical launch audit | E/F; unchanged |
| `wt_confirmed_treasuries.treasury` | 1 | Manual/legacy governance registry | F; unchanged |
| `operator_entities.entity_address` | 1 | Operator entity | F; unchanged |
| `operator_identity_assets.asset_value` | 1 | Governed identity asset | F; unchanged |
| `operator_identity_events.payload_json` | 1 embedded | Immutable governance history | F; unchanged |
| `attribution_evidence.subject_wallet` | 1 | Evidence subject | E/F; unchanged |
| `wt_walkback_edge_candidates.candidate_parent` | 1 | Explicit transaction candidate | A where selected/replayed; preserved |
| `wt_walkback_queue` treasury/funder | 8 / 1 | Walkback result/context | A/E/G per row; unchanged |
| `wt_ops_v2_edges.from_wallet` | 280 | Persisted historical graph edge | Mixed legacy provenance; unchanged, not used to release quarantine |
| `wt_ops_v2_forward_walks.trigger_wallet` | 286 | Historical traversal context | E; unchanged |
| `wt_discovered_subprovs.treasury` | 382 | Historical discovery projection | E/G; unchanged |
| `wt_temp_provision_candidates.treasury` | 403 | Temporary candidate context | E/G; unchanged |
| `wt_candidate_websocket_watches.treasury_wallet` | 322 | Monitoring/watch context | E; unchanged |
| `wt_ecosystem_exchange_interactions.treasury_wallet` | 326 | Historical ecosystem presentation copy | E/G; unchanged |
| `wt_fanout_events.treasury_wallet` | 124 | Historical event context | E/G; unchanged |
| `wt_pending_session_writes.treasury` | 29 | Pending/replay claim | G until directional verification |
| `wt_provisioning_candidate_workflow.session_treasury` | 2 | Workflow context | G; lineage readers now use eligible view |
| `wt_swarm_buys.treasury_wallet` | 9 | Monitoring context | E; unchanged |
| `watchtower_events` wallet/related/payload | 239 / 3,919 / 5 | Event history | E; unchanged |
| `wt_webhook_hits.wallet_address` | 6,833 | Raw observation history | E; unchanged |

Additional legacy/live-database occurrences were retained, including direct transaction stores (`creator_funders`, `funder_incoming_transfers`, `funder_outgoing_transfers`, `wt_graph_edges`) and contextual analytics/prediction snapshots. These do not receive authority merely from containing the address. Exact transaction tables remain available as independent evidence.

## Binance contamination

All 41 historical rows whose stored shape implied `69SN → 5tzF` are excluded from direct lineage:

- 38: proven invalid inherited ancestry;
- 2: unresolved and quarantined;
- 1: valid indirect ancestry, preserved only as the multi-hop chain below.

The live Binance 2 profile now shows no 69SN upstream relationship and no 69SN launch bucket. Its 204 launches remain intact. The previous 132-launch temporal attribution disappeared; current eligible presentation is:

| Eligible upstream context | Launches |
|---|---:|
| DchJ… | 136 |
| 9hGc… | 49 |
| 4231… | 19 |
| EFKV… | 0 |

The total remains 204/204. Underlying 5tzF→creator provisioning edges were not modified.

## Valid positive control

The transaction-proven chain remains preserved:

`69SN → 9St6 → 8CEy → Bvv4 → 5tzF`

Each arrow is independently supported by its own transaction signature in the X78.11/X78.13 transaction evidence. The chain establishes valid indirect ancestry. It does not establish a direct `69SN → 5tzF` edge and does not seed any other 69SN relationship.

## Downstream enforcement

Tier-1 consumers now use `wt_lineage_eligible_sessions`, including:

- Discovery/Investigation population projection;
- Treasury resolution and Treasury Review inputs;
- campaign classification;
- ecosystem intelligence;
- funding topology and boundary analytics;
- provisioning candidate verification;
- detection reconciliation;
- canonical adapters;
- Operation Intelligence, Operational Role and infrastructure launch buckets.

Lifecycle and monitoring readers may continue using raw session history because those uses do not assert ancestry.

The eligibility view excludes an explicitly quarantined row and also enforces `wt_lineage_root_policies`. For 69SN, a new session is excluded automatically unless `wt_lineage_verified_session_edges` contains the exact same session ID, sender, recipient and signature. This prevents live inserts from bypassing the repair.

## Canonical safety

No automatic WATCHTOWER governance change was made.

- Seven canonical launches still carry 69SN in the historical canonical table.
- X78.11 found two transaction-supportable treasury chains among those seven, one of which also had complete creator support.
- Five canonical claims remain evidence-unavailable/unsupported after session-root quarantine.
- The Operator identity asset and confirmed-treasury row remain untouched.

These are findings for a later explicit governance review, not an automatic demotion.

## Validation

- Raw session count was unchanged across the settled repair run.
- Binance direct eligible sessions: 0.
- 69SN Tier-1 eligible inherited sessions: 0.
- Binance profile: HTTP/API payload retained 204 launches and omitted 69SN from upstream/split presentation.
- API and listener were restarted/running after the reader change.
- Focused and cross-layer regression: 163 passed, 3 unrelated legacy-UI expectation failures.
- The three failures predate X78.14: obsolete Timeline label, legacy promotion URL, and retired sidebar operations URL.

## Final accounting

- 69SN transaction relationships retained: the four explicit edges in the known positive chain and all other raw transaction facts.
- 69SN session relationships quarantined: 207,116.
- Proven-invalid inherited relationships: 42.
- Relationships removed from directional presentation: all unverified/flattened 69SN session roots.
- Launch attributions removed from heuristic presentation: the 132-launch Binance bucket.
- Canonical rows removed: 0.
- Canonical claims independently supportable: 2/7 treasury chains (1/7 complete launch chain).
- Canonical claims now lacking independent complete support: 5/7, pending governance review.
