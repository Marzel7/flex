# Sprint X19.5 — WATCHTOWER Operational Validation & Attribution Audit

**Audit state:** complete, read-only  
**Fixed reconciliation window:** 2026-07-10 16:00:00 UTC through 2026-07-13 16:00:00 UTC  
**Primary stores:** `database/flex_complete_database.db` and `database/wt_ops_v2.db`  
**Canonical WATCHTOWER operator:** `04265d9f-6eb2-568c-a49e-9253091a4dbb`

No detector, RPC, websocket, database, attribution, identity, behaviour, assessment, forecast, route, or UI mutation was performed.

## Executive conclusion

The platform is detecting some WATCHTOWER launches, but the end-to-end primary mission is not operationally complete.

Four launches in the fixed 72-hour cohort reached a confirmed WATCHTOWER treasury through persisted lineage. Three were recorded by the strict live WATCHTOWER launch path; one was missed by that path and confirmed only by retrospective walkback. All four are explainable in Discovery, but none resolves to the canonical WATCHTOWER operator because the live confirmed-treasury registry and the canonical operator entity set are not reconciled. None of the four is present in Mission Control's current six-item feed.

The walkback worker also entered a persistent same-thread nested-write failure at 2026-07-13 15:03:20 UTC. Supervisor still reports the process as running, but its heartbeat stopped and its log continuously reports `NestedDatabaseWriteError`. This left recent work stranded as `running` or `pending`.

Therefore X20 should not begin on the assumption that the known-operator baseline is healthy.

## 1. Data-source ownership and counting rules

The audit keeps two populations separate:

1. **Platform launch cohort:** rows first observed by `token_analysis` during the fixed window. This is the broad launch denominator.
2. **Confirmed WATCHTOWER cohort:** rows whose persisted chain reaches `wt_confirmed_treasuries`. This is the relevant known-operator result set.

Stage definitions:

| Stage | Persisted proof |
|---|---|
| Launch seen | `token_analysis.analyzed_at` in the fixed window |
| Creator resolved | `pf_ws_creator` or `earliest_tx_creator` |
| Walkback started | matching `wt_walkback_queue.enqueued_at` |
| Walkback completed | queue status exactly `complete` |
| Sub-provisioner resolved | queue, strict launch, or attribution row contains a sub-provisioner |
| Treasury resolved | queue, strict launch, or attribution row contains a treasury |
| Treasury recognised | treasury exists in `wt_confirmed_treasuries` |
| Strict WATCHTOWER detection | mint exists in `wt_watchtower_launches` |
| Canonical operator resolved | recognised treasury exists in `operator_entities` for a canonical operator |
| Discovery visible | mint is identifiable by the current read-only Discovery composer |
| Mission Control visible | mint occurs in the current `/api/discovery/recent?limit=6` payload |

The funnel is deliberately sequential. A row cannot count at a later stage unless it passed every earlier stage.

## 2. Canonical 72-hour funnel

| Stage | Count | Loss from prior stage | Conversion |
|---|---:|---:|---:|
| Launches seen | 47,808 | — | 100.00% |
| Creator resolved | 47,788 | 20 | 99.96% |
| Walkbacks started after creator resolution | 397 | 47,391 | 0.83% of resolved creators |
| Walkbacks completed | 376 | 21 | 94.71% of started |
| Sub-provisioners resolved | 117 | 259 | 31.12% of completed |
| Treasuries resolved | 14 | 103 | 11.97% of resolved sub-provisioners |
| Known WATCHTOWER treasuries | 4 | 10 | 28.57% of resolved treasuries |
| Canonical WATCHTOWER operator | 0 | 4 | **0.00% of known treasuries** |

Non-sequential visibility measures:

| Surface | Count from 47,808-launch cohort | Interpretation |
|---|---:|---|
| Strict live WATCHTOWER launch records | 3 | Three live detections survived the strict path |
| Discovery-identifiable rows | 77 | Discovery can explain some partial chains before canonical resolution |
| Current Mission Control feed | 0 of the four confirmed WATCHTOWER rows | The current six recent items are unrelated unresolved walkbacks |

### First-loss classification for every launch

Each of the 47,808 launches was assigned exactly one first loss:

| First loss | Launches |
|---|---:|
| Creator unresolved | 20 |
| Walkback not started | 47,391 |
| Walkback incomplete: pending | 7 |
| Walkback incomplete: running | 13 |
| Walkback incomplete: skipped | 1 |
| Sub-provisioner unresolved | 259 |
| Treasury unresolved | 103 |
| Treasury unrecognised | 10 |
| Canonical operator unresolved | 4 |
| Complete canonical chain | **0** |

These categories sum to 47,808.

## 3. Confirmed WATCHTOWER reconciliation

The four rows that reached a confirmed treasury are the material 72-hour WATCHTOWER cohort.

| Mint | Observed UTC | Live strict detection | Creator | Walkback | Sub-provisioner | Confirmed treasury | Canonical operator | Discovery | Mission Control now |
|---|---|---|---|---|---|---|---|---|---|
| `G6k4…pump` | Jul 10 17:13:34 | **No** | Yes | Complete | `G893…hacD` | `9hGc…EZk4` | **No** | Yes | No |
| `C4TF…pump` | Jul 11 01:29:01 | Yes, `PROGRAM_LOGS` | Yes | Complete, conflicting outcome | `4SBR…uSSn` | `Dtwi…3p3u` | **No** | Yes | No |
| `EQ6q…pump` | Jul 11 05:59:44 | Yes, `PENDING_CREATE_RETRY` | Yes | Complete | `9e2H…PFN5` | `9hGc…EZk4` | **No** | Yes | No |
| `AwXt…pump` | Jul 11 11:15:52 | Yes, `PROGRAM_LOGS` | Yes | Complete | `HA71…UqZq` | `9hGc…EZk4` | **No** | Yes | No |

### Material detection omission

`G6k4…pump` is persisted proof of a launch missed by the strict live WATCHTOWER detector:

- broad launch observation: 2026-07-10 17:13:34 UTC;
- walkback enqueued: 17:13:35;
- walkback completed: 17:13:37;
- outcome: `WATCHTOWER_CONFIRMED`;
- matched sub-provisioner: `G893…hacD`;
- matched confirmed treasury: `9hGc…EZk4`;
- no `wt_watchtower_launches` row;
- no wrap-close candidate or candidate websocket-watch row for its creator.

The first missing stage is therefore live candidate/launch detection. Walkback, treasury resolution, and identity-to-WATCHTOWER-operation attribution succeeded afterwards.

Within the four confirmed rows, strict live detection coverage was 3/4, or 75%. This does not measure unknown operators; it measures only rows eventually proven against the existing confirmed-treasury registry.

## 4. Persisted walkback replay

Six recent known WATCHTOWER launches were replayed by joining existing records only. No RPC or detector was invoked.

| Mint | Persisted chain | Where the chain terminates | Classification |
|---|---|---|---|
| `G6k4…pump` | creator → `G893…` → `9hGc…` | Confirmed treasury; canonical operator missing | Detection issue, then operator mapping issue |
| `C4TF…pump` | creator → `4SBR…` → `Dtwi…` in strict launch record | Queue says `NO_ATTRIBUTION_FOUND`; Discovery masks this with the strict launch record; canonical operator missing | Persistence disagreement and operator mapping issue |
| `EQ6q…pump` | creator → `9e2H…` → `9hGc…` | Confirmed treasury; canonical operator missing | Operator mapping issue |
| `AwXt…pump` | creator → `HA71…` → `9hGc…` | Confirmed treasury; canonical operator missing | Operator mapping issue |
| `Ct2V…pump` | creator → `23aR…` → `9hGc…` | Confirmed treasury; canonical operator missing | Operator mapping issue |
| `Eeuj…pump` | strict launch says creator → `2soj…` → `DchJ…`; queue says creator → `5tzF…` and `LINEAGE_GAP` | Two persisted paths disagree; Discovery prefers the strict launch path; canonical operator missing | Persistence/lineage disagreement and operator mapping issue |

Additional persistence defects:

- `EQ6q…pump` and `AwXt…pump` have queue status `complete` but no `completed_at`. They cannot rank correctly in the recent Discovery/Mission Control feed.
- `C4TF…pump` has a full strict launch chain but the queue contains no sub-provisioner or treasury and records `NO_ATTRIBUTION_FOUND`.
- `Eeuj…pump` records different sub-provisioners in the launch and queue stores.
- Of 39 strict WATCHTOWER launches in the last 30 days, all 39 have creator, sub-provisioner, and confirmed-treasury resolution; **zero** reaches the canonical operator.

## 5. Current walkback-worker failure

Supervisor reports `walkback_worker` as running, but persisted and log evidence shows it stopped progressing:

- last heartbeat: 2026-07-13 15:03:20 UTC;
- the row claimed at 15:03:20 remains `running`;
- six later rows remain pending;
- twelve older rows in the fixed window are also stranded as `running`;
- the log repeatedly reports:

```text
NestedDatabaseWriteError:
database=tracked
outer_command=walkback_worker.py:344 in _ops_conn
inner_command=walkback_worker.py:344 in _ops_conn
```

The triggering call path is:

```text
run_loop
  → _ops_conn
  → drain_batch
  → _process_row
  → unknown hop-2
  → _surface_treasury_review_lead
  → treasury_bank.add_walkback_hop2_lead
  → treasury_bank._ensure_schema_once
  → db_connect(OPS_DB_PATH) while the outer connection owns the write lane
```

After the first treasury-review-lead failure, subsequent outer-loop attempts fail at `_ops_conn`. This is a walkback write-architecture regression, not ordinary SQLite contention and not a reason to add retries or timeouts.

## 6. Detection-regression assessment

### What is proven

- The listener is not globally dead. In the fixed window the operations store contains 28,132 recent WATCHTOWER events, 395 webhook hits, 17,616 active-session updates, and 2,954 candidate-watch updates.
- Three `WATCHTOWER_LAUNCH_DETECTED` events occurred on Jul 11.
- One additional launch reached `WATCHTOWER_CONFIRMED` retrospectively without a strict launch row.
- The existing Discovery Assurance endpoint independently reports historical graph-coverage weakness: 619 known-sub-provisioner launches, only 6 wrap-close observations, a 1.0% known-graph discovery rate, and 112 `D3_no_wcc_observed` cases.
- That assurance dataset is stale for live validation: its `wt_farm_launches` source ends on 2026-06-30 and contains zero rows in this audit's 72-hour window.

### What is not proven

Persisted data alone cannot prove that every externally alleged post-Jul-11 launch belongs to WATCHTOWER. After the final strict detection at 2026-07-11 11:16:13 UTC, no later row in the current operations store reaches a confirmed treasury.

The broad store does contain launches by reused creators with old funding relationships to confirmed infrastructure. Those relationships date from March/April and are not current provisioning observations, so they are evidence candidates rather than sufficient WATCHTOWER identity proof. Their first missing stage is a current candidate/provisioning record.

### Root-cause classification

The two-day analyst-visible absence is not explained by a single total outage:

1. **Detection coverage issue:** at least one confirmed launch was absent from the strict live launch table; historical assurance already reports a large subscription/session gap.
2. **Walkback issue:** current worker progress stopped on re-entrant write acquisition.
3. **Operator issue:** confirmed live treasuries do not map to the canonical operator.
4. **UI issue:** Mission Control does not retain or rank the confirmed launches, and Discovery's Level 1 answer does not name WATCHTOWER.

## 7. Confirmed-treasury and canonical-operator reconciliation

The identity stores are bidirectionally inconsistent:

- `wt_confirmed_treasuries`: 58 rows;
- confirmed treasuries mapped to any canonical operator: 2;
- canonical WATCHTOWER operator entities: 9, all typed `TREASURY`;
- those nine present in `wt_confirmed_treasuries`: 2;
- distinct confirmed treasuries used by the last 30 days of strict WATCHTOWER launches: 6;
- those six mapped to canonical WATCHTOWER: 0.

Consequences:

- a currently active confirmed treasury such as `9hGc…EZk4` is correctly labelled confirmed by Discovery but has empty `operator_history`;
- a canonical operator entity such as `2ujR…FW6R` resolves to WATCHTOWER but is shown as an unconfirmed treasury candidate because it is absent from the confirmed registry;
- the system can say “confirmed WATCHTOWER launch” through legacy/operation provenance while being unable to navigate to the canonical WATCHTOWER actor.

## 8. Discovery UI audit

### Information that exists

- confirmed/candidate/unknown state is computed from `wt_confirmed_treasuries` and `wt_treasury_review`;
- confidence and resolution method exist on the treasury node;
- the Funding Walkback panel exposes `TREASURY` versus `TREASURY_CANDIDATE` and its confidence;
- token-level Attribution Chain contains a `WATCHTOWER_ATTRIBUTION` node when the strict launch or attribution store supports it;
- Promotion Lineage can show the canonical operator when `operator_entities` contains the entity.

### Information hidden below Level 1

- “WATCHTOWER launch attribution” is in the collapsed Attribution Chain, not the Level 1 answer;
- treasury confidence is in the open walkback/supporting record rather than beside the Level 1 identity claim;
- resolution method is supporting evidence rather than the initial treasury classification.

### Information missing for current live treasuries

- the Level 1 label “Confirmed WATCHTOWER Treasury”;
- canonical operator `WATCHTOWER`;
- operator promotion lineage for all six treasuries used by the last 30 days of strict launches;
- the requested evidence summary (`Infrastructure reuse`, `Vanity family`, `Treasury confirmed`) as a coherent analyst answer.

Current direct-treasury Level 1 wording is only: “Treasury discovery is confirmed: confirmed treasury reached.” It distinguishes confirmation state, but not WATCHTOWER ownership.

## 9. Mission Control visibility and click path

Mission Control receives launch-related intelligence only through the six-item Discovery recent feed. At audit time all six items are generic unresolved walkback completions. None of the four confirmed WATCHTOWER launches is present.

Two of the three latest strict launches have `status='complete'` but `completed_at IS NULL`, so the feed cannot order them by completion time. The third has aged out of the six-item window.

Analyst workflow:

### Analyst does not already know the mint

There is no current Mission Control item for the four launches. The workflow is a dead end: the analyst cannot discover them from Mission Control.

### Analyst already knows the mint

```text
Mission Control search
  → Discovery Level 1: confirmed chain, but WATCHTOWER not named
  → expand Attribution Chain
  → WATCHTOWER launch attribution becomes visible
```

This requires navigation plus one disclosure action. Reaching the canonical WATCHTOWER operator is impossible from these rows because `operator_history` is empty.

## 10. Operational dashboard assessment

The existing `/ops/discovery-assurance` page is the closest permanent operational dashboard, but it is not the required live canonical funnel:

- it ends at wrap-close coverage rather than canonical operator and analyst visibility;
- its independent `wt_farm_launches` source stopped updating on Jun 30;
- it has no rolling 72-hour stage counts;
- it does not expose per-stage loss from launch through Mission Control.

The permanent funnel should use the stage definitions in section 1 and expose, for a fixed rolling window:

```text
Launches Seen
  → Creator Resolved
  → Walkbacks Started
  → Walkbacks Completed
  → Sub-Provisioners Resolved
  → Treasuries Resolved
  → Known Treasuries
  → Canonical Operators
  → Discovery Visible
  → Mission Control Visible
```

Each stage needs count, prior-stage loss, oldest incomplete item, and source freshness. This is a recommendation only; it was not implemented in this audit.

## 11. Prioritised remediation plan

| Priority | Classification | Evidence-backed remediation objective |
|---|---|---|
| P0 | Walkback issue | Remove the nested operations-database acquisition from treasury-review lead/schema handling; prove worker heartbeat and stranded-row recovery before trusting new walkbacks |
| P0 | Operator/identity issue | Reconcile authoritative confirmed treasuries with canonical `operator_entities`, preserving review provenance; prove all known WATCHTOWER launch treasuries resolve to the canonical actor |
| P1 | Detection issue | Reconcile the confirmed retrospective miss and the historical D3 coverage set against candidate/session creation to identify the exact subscription boundary loss |
| P1 | Persistence issue | Reconcile conflicting launch/queue lineage, require `completed_at` for completed rows, and prevent stale outcomes such as `NO_ATTRIBUTION_FOUND` beside a strict confirmed chain |
| P1 | Operational visibility | Replace or refresh the stale Discovery Assurance denominator and add the canonical rolling funnel with freshness and loss |
| P2 | UI issue | After identity reconciliation, make confirmed WATCHTOWER treasury and canonical operator explicit in Discovery Level 1 and retain confirmed launch events in Mission Control |

No retry, timeout, detector, identity-rule, database, or UI change is recommended as a substitute for those reconciliations.

## 12. Success-criteria answers

**Are WATCHTOWER launches actually being detected?**  
Partially. Three strict detections occurred in the window. A fourth launch was confirmed retrospectively but missed by the strict path.

**If not, where does the pipeline fail?**  
At least one failure is at live candidate/launch detection. Current new walkbacks then fail at re-entrant write acquisition. Confirmed treasuries finally fail at canonical operator mapping.

**If launches are detected, why are analysts not seeing them?**  
Mission Control's bounded recent feed contains unrelated unresolved walkbacks, completion timestamps are missing on two strict rows, and Discovery keeps WATCHTOWER identity below Level 1.

**Can an analyst immediately recognise a confirmed WATCHTOWER treasury during walkback?**  
No. They can recognise a confirmed treasury, but not canonical WATCHTOWER ownership. The operator mapping is absent for current live treasuries.

**Is the platform ready for X20 Emerging Operator Detection?**  
**No.** The known-operator control case does not reach the canonical operator, and the walkback worker is currently alive-but-not-progressing. X20 would make emerging-operator conclusions difficult to distinguish from known-operator pipeline failures.
