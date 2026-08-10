# X78.13 — Production Write-Lane, Creator Funding & Ingestion Health Recovery

Date: 2026-08-10  
Branch: `classification-attribution-axis`  
Baseline HEAD: `a4f030a63378df7af65713d7eae6c0d58be20035`

## Outcome

The audit found and repaired five independent write-lane/lifecycle defects, but
production did not meet the preconditions for a 30-minute readiness window.
The window was therefore not started.

Final production sample:

- API, listener, Creator Funding and Creator Resolution processes were running.
- API health requests succeeded, but connection refusal was intermittently
  observed during contention.
- PumpPortal and PumpSwap were connected; listener queues were empty and recent
  births/migrations were persisted.
- Database pressure returned to `AT_RISK`; write p99 was 18,439.11 ms.
- Creator Funding remained `STALLED`: heartbeat age 179 seconds, 17,437 pending,
  and zero completed jobs in the current batch. Jobs were advancing, but repeated
  90-second extraction timeouts and non-terminating cleanup prevented the required
  completion proof.
- Operational Intelligence remained stale: watch pipeline age 4,113,027 seconds;
  Creator Resolution age 284 seconds; 27 recent migrated tokens lacked creators.
- WAL was 24.8 MB and not critically pinned.

## Proven defect ledger

| Defect | Evidence | Classification | Repair | Status |
|---|---|---|---|---|
| Listener symbol write abandoned a connection after an exception | live owner `pumpfun_curve_listener.py:164`, `tok_work_0`, multi-minute global blockage | LONG_WRITE_HOLDER / STALE_LEASE_STATE | managed read/write connections with guaranteed close | repaired |
| API price schema setup closed only on success | live owner `price_service.py:339`, gunicorn executor thread, repeated 60-second victims | LONG_WRITE_HOLDER / STARTUP_DDL_CONTENTION | unconditional close around schema setup | repaired |
| Listener schema retry leaked a partially acquired connection | retry changed from cross-process timeout to same-thread `NestedDatabaseWriteError` | PERMANENT_SELF_NESTING | unconditional startup connection close | repaired |
| Autocommit/no-op DDL retained the serializer lease | listener failed on the second `CREATE IF NOT EXISTS` after a successful first statement | STALE_LEASE_STATE / PERMANENT_SELF_NESTING | release tracked lane when a write statement leaves no SQLite transaction | repaired |
| First PumpPortal startup timeout was immediately fatal | `1 consecutive failures, 999.0min since last connection` | WEBSOCKET_FATAL policy defect | before first connection require ten consecutive failures; preserve three-minute rule after connection | repaired |
| Risk scoring held DDL lease across context reads | live owner `risk_scoring_builder.py:246` while building creator context | LONG_WRITE_HOLDER | commit schema/infra setup before read-only context construction | repaired |
| Creator Resolution held schema writes across full-population scans | live owner `creator_resolution_queue.py:51` for 40–60 seconds | LONG_WRITE_HOLDER / transaction-boundary defect | commit after schema maintenance and reuse verified schema during bulk enqueue | repaired, short validation only |
| Creator Funding extraction cleanup survives cancellation | repeated `did not finish cleanup within 10s`; 90-second jobs took up to 197.7 seconds; batch heartbeat stayed stale | RPC_BOUND / unresolved cleanup defect | no speculative repair made | active blocker |
| Operational Intelligence watch pipeline stale | age remained above 4.1 million seconds after upstream recovery | independent or downstream worker failure | not patched without causal proof | active blocker |

## Process and restart evidence

- API: deployed to PID 30675. The earlier unexplained respawn cannot be assigned
  a definitive historical cause; current startup contention was directly tied to
  the price-service lease path.
- Listener: several controlled deployments were required as independent defects
  became observable. The final run reached schema-ready state, connected both
  websocket sources and persisted live events. Earlier restarts included both
  nested startup writes and an independently proven PumpPortal fatal-policy bug.
- Creator Funding: controlled restart loaded the repaired shared lane and risk
  scoring boundary. It advanced through jobs but failed the completion/heartbeat gate.
- Creator Resolution: controlled restart loaded the shared lane and scan-boundary
  corrections. No production data or queue state was deleted.

## Validation

Focused regression results:

- 25/25 passed: X78.13 lease/boundary tests plus listener retry, reconnect,
  unlocked-release and price singleton coverage.
- 18/18 passed earlier: risk boundary, X78.8 infra separation, X78.7 query
  optimization and X78.13 listener/API lease tests.
- 22/22 passed earlier: listener/API lifecycle and reconnect group.

The broad regression command was not reported as successful because it did not
complete reliably; only completed targeted results are claimed.

## Readiness gates

Failed gates:

1. Database pressure was `AT_RISK` at the final sample.
2. Creator Funding did not demonstrate multiple completions.
3. Creator Funding heartbeat was stale.
4. Live birth rate remained materially below the 17.87/min control despite
   connected feeds and recent flow.
5. Operational Intelligence freshness was not improving.
6. API request availability was not continuously stable during contention.

Consequently, no 30-minute clock was started and no Evidence Platform action was
taken.

## Verdict

Production Health: **E — NEW_DEFECT_FOUND**  
Evidence Activation: **HEALTH_REPAIR_REQUIRED**  
Acquisition: **HOLD_ACQUISITION**

