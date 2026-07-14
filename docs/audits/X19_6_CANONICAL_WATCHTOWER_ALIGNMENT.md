# Sprint X19.6 — Canonical WATCHTOWER Alignment

**State:** implemented and live-validated  
**Canonical operator:** `04265d9f-6eb2-568c-a49e-9253091a4dbb` (`WATCHTOWER`)  
**Control invariant:** every confirmed WATCHTOWER treasury has exactly one active canonical owner.

## 1. Canonical identity reconciliation

The authoritative legacy fact remains `wt_confirmed_treasuries`. The reconciler does not discover or score identity. It accepts a treasury only when that row already exists and accepts the owner only when the reviewed canonical operator exists with:

- the fixed canonical operator ID;
- display name `WATCHTOWER`;
- status `CONFIRMED`.

For every confirmed treasury, one `operator_entities` membership is persisted with type `TREASURY`. A new `watchtower_identity_reconciliations` ledger stores one evidence snapshot and fingerprint per treasury. Existing active ownership by another operator raises `TreasuryOwnershipConflict`; it is never papered over with a second WATCHTOWER membership.

The operation is idempotent and executes through the managed operations-database writer. Runtime treasury confirmations call the same connection-bound reconciliation function before their existing commit, so registry confirmation and canonical membership share a transaction.

### Live result

| Measure | Result |
|---|---:|
| Confirmed treasuries | 58 |
| Canonical WATCHTOWER memberships | 58 |
| Reconciliation ledger rows | 58 |
| Unresolved confirmed treasuries | 0 |
| Duplicate active ownership | 0 |

## 2. Live attribution completion

The known-operator control path is now:

```text
Launch
  → persisted walkback/strict attribution
  → confirmed treasury
  → canonical WATCHTOWER
  → Discovery
  → Mission Control WATCHTOWER stream
  → Operator Intelligence
```

The rolling 72-hour live funnel after reconciliation reported four confirmed treasuries, four canonical operator resolutions, four Discovery-visible rows, and four Mission Control-visible rows. There is no loss between confirmed treasury and analyst visibility.

## 3. Walkback recovery and progress health

The nested schema acquisition was removed from the runtime path. Treasury-review schema initialization now uses the caller-owned connection, and the worker initializes that schema before claiming queue rows.

Worker startup also performs deterministic crash recovery:

- stale `running` jobs below the attempt ceiling return to `pending`;
- stale jobs at the ceiling become explicit `failed` rows;
- no active operation is retried by a lock loop.

The health model measures:

- queue depth;
- oldest pending age;
- completions per minute and per hour;
- average completion latency;
- heartbeat age;
- oldest running age;
- stalled running jobs;
- database-write failures.

The required rule is enforced: pending work plus zero completions per minute is unhealthy even if the process heartbeat exists.

### Live recovery result

- 13 abandoned `running` claims were requeued at restart;
- the first recovered batch completed 8 rows;
- subsequent persisted telemetry recorded 14 completions in the hour and 9 in the current minute;
- stalled running jobs: 0;
- nested write failures after restart: 0;
- worker status: `HEALTHY`.

## 4. Mission Control truth

`/api/discovery/recent` now owns independent, bounded streams:

1. Recent WATCHTOWER;
2. Recent Promotions;
3. Recent Emerging Operators;
4. Recent Reviews;
5. Recent Walkbacks;
6. Recent Discovery.

Mission Control renders those streams separately. A confirmed WATCHTOWER launch cannot be displaced by six unrelated walkback completions because recency is applied within each stream. Retrospective confirmed walkbacks are included even when the strict launch table missed the token.

## 5. Discovery Level 1

When a confirmed treasury has the reconciled canonical membership, Discovery now displays without expansion:

- `WATCHTOWER`;
- `Confirmed Treasury`;
- treasury confidence;
- canonical operator link;
- existing identity classes `Infrastructure reuse` and `Vanity family` when recorded;
- `Treasury confirmed`.

These labels are composed from existing operator evidence and the confirmed registry. Missing ownership remains missing rather than being inferred in the presentation layer.

## 6. Permanent control dataset

`tests/fixtures/watchtower_control_launches.json` freezes 30 persisted WATCHTOWER launch chains. The X19.6 test suite requires every row to have:

- creator;
- sub-provisioner;
- confirmed treasury;
- exactly one canonical WATCHTOWER resolution;
- Discovery Level 1 identity;
- Mission Control WATCHTOWER-stream visibility;
- Operator Intelligence linkage.

The same suite verifies reconciliation idempotency, conflict refusal, full-funnel cardinality, progress-based worker health, and deterministic stranded-claim recovery.

## 7. Operational dashboard

`/ops/discovery-assurance` now leads with the live rolling attribution control rather than the stale historical assurance denominator. Every stage shows:

- count;
- loss from the prior stage;
- conversion;
- source freshness;
- oldest stuck item timestamp;
- click-through destination.

The historical `wt_farm_launches` assurance remains available below and is explicitly labelled historical.

New read-only control APIs:

- `/api/ops-v2/watchtower-control`;
- `/api/ops-v2/walkback-health`;
- `/api/ops-v2/watchtower-attribution-funnel?hours=72`.

## 8. Verification

- 194 operator, promotion, observation, navigation, Discovery, UI, managed-write, and X19.6 tests passed.
- 30-launch permanent control dataset passed end to end.
- live identity cardinality passed `58 = 58 = 58`, with zero duplication.
- live control APIs returned HTTP 200.
- Mission Control and attribution-control pages returned HTTP 200.
- live worker recovery demonstrated persisted completions and current progress telemetry.

The canonical WATCHTOWER control case is now suitable as the reference baseline for X20.
