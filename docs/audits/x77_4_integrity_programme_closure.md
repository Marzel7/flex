# X77.4 — Integrity Programme Closure

## Objective

Close the X76–X77 engineering programme with one final end-to-end audit,
confirming correctness across every subsystem this programme touched or
depended on, and issue a single push recommendation with justification.

## Programme scope recap

- **X76**: correctness programme (merge safety, identity projection,
  treasury governance, walkback self-healing, recovery diagnostics, live
  candidate detection).
- **X77.0**: identified the dominant write-contention cause (write lease
  held across RPC in `walkback_worker`'s `FULL_WALKBACK` branch).
- **X77.1**: fixed it — collect-then-persist transaction boundary,
  ~97% reduction in average lease-hold duration.
- **X77.2**: made `ws_cascade`'s background writer lossless under
  contention (durable retry queue, classified transient-vs-permanent).
- **X77.3**: soaked the fixes under real production load; found and fixed
  a second, independently pre-existing bug
  (`walkback_queue.ensure_schema()`'s lease leak) live; confirmed a third,
  pre-existing, still-open defect (`creator_funding_worker`'s permanent
  stall) that this programme does not fix.

## Final integrity audit

### 1. Merge Safety — CORRECT
`tests/test_x76_0_canonical_merge_safety.py`: 15/15 passing. No code in
this session's changeset touches merge logic.

### 2. Identity Projection — CORRECT
`tests/test_x76_1_operator_identity_projection.py`: 16/16 passing.
`operator_identity_governance.py`'s pre-existing, unrelated uncommitted
`_transition()` block (present since before this session, documented in
every prior X76.x/X77.x audit) remains untouched and unstaged.

### 3. Treasury Governance / Treasury Review — CORRECT
`tests/test_x76_2_treasury_review_audit_integrity.py`: 19/19 passing.
Empty diff on `treasury_review_workspace.py`. Live data confirms Treasury
Review candidate generation is active and healthy (5 new candidates in the
final soak hour, 40 in the final day — see X77.3).

### 4. Walkback — CORRECT (post-fix)
`tests/test_x77_1_walkback_transaction_boundary.py` (4/4),
`tests/test_ops_x21b_walkback_integration.py` (5/5),
`tests/test_x76_5_treasury_candidate_detection.py` (13/13),
`tests/test_x77_3_ensure_schema_lease_leak.py` (6/6) — all passing.
Live: 19 completions in the soak's final hour, average completion latency
2.526s, 0 stalled running jobs, 0 nested-write failures in the final hour.

### 5. Candidate Generation — CORRECT
`tests/test_x76_5a_walkback_candidate_health.py`: 16/16 passing.
`build_walkback_candidate_health()` reports **HEALTHY**, zero warnings, live,
right now. Discovery source correctly tracked
(`WALKBACK_RECURRING_FUNDER` confirmed on the GF7Y named-control read).

### 6. Discovery — CORRECT
`tests/test_discovery_workspace.py` (5/5),
`tests/test_ops_x21c_discovery_triage.py` (11/11) — passing. Not touched by
this session.

### 7. Mission Control — CORRECT, and proven accurate under a real incident
This is the strongest evidence in this closure: Mission Control's
Intelligence panel correctly surfaced CRITICAL during the X77.3 soak
(walkback stalled, funding worker stalled, snapshot stale) at the exact
moment `walkback_worker` was crash-looping and `creator_funding_worker` was
genuinely stuck — it did not show HEALTHY while either was broken. Once the
lease-leak was fixed and `walkback_worker` recovered, the SAME dashboard
correctly returned to HEALTHY with zero warnings, entirely without a code
change to the dashboard itself. Mission Control was not the thing that
needed fixing this programme; it was the instrument that proved the fixes
worked, and separately proved `creator_funding_worker`'s stall is real, not
a diagnostic artifact.

### 8. Operator Identity — CORRECT
`tests/test_x73_0_operator_identity_lifecycle.py`: 8/8 passing. Untouched.

### 9. Investigation Lifecycle — CORRECT
`tests/test_x69_1_evidence_reconciliation.py` (6/6),
`tests/test_x71_2_reconciliation_ui.py` (6/6) — passing. Untouched.

### 10. Dismissal — CORRECT
`tests/test_x75_4_investigation_dismissal.py` (6/6),
`tests/test_x75_5_investigation_trigger_provenance.py` (2/2) — passing.
GF7Y's own live state (`state='dismissed'`) confirms dismissal data
integrity end-to-end, not just test coverage.

### 11. Recovery — CORRECT, live-proven
`wt_walkback_recovery_events` correctly logged both self-kills that fired
during the X77.3 soak (`recovery_events_last_hour: 2` at time of
measurement), each with accurate lease-age/transaction-id/reason, each
followed by a `mark_restarted`/`mark_healthy` closing the loop
automatically on the next boot. Recovery is not theoretical this milestone
— it happened, twice, live, and was captured correctly.

### 12. Cross-process contention — CORRECT (X77.1 target), ONE OPEN DEFECT (X77.3 finding)
The specific contention path X77.1 targeted (`walkback_worker`'s
`FULL_WALKBACK` write-lease span) is fixed and holds under real load — see
X77.3 Finding 2. A second, independently pre-existing contention/reentrancy
defect (`walkback_queue.ensure_schema()`'s lease leak) was found and fixed
live during the same soak — X77.3 Finding 1. A third, pre-existing,
**still open** defect remains in `creator_funding_worker` — X77.3 Finding
3 — not fixed this programme, flagged for a dedicated follow-up.

### 13. Entity Graph — CORRECT
`wt_discovered_subprovs`/`wt_confirmed_treasuries` reads for all named
controls below returned correctly-shaped, internally consistent data with
no query errors, confirming the entity graph's read path is intact
post-soak.

### 14. Named controls
| Control | Result |
|---|---|
| WATCHTOWER (hub `DQyrAcCrDXQ7…`) | 2 walkback rows present, unchanged from X77.1's own validation |
| 3SW2 (`3SW2zquY2…`) | 13 walkback rows present, unchanged |
| B48k (`B48kNVXs4…`) | 43 walkback rows present, unchanged |
| C7Ha (`C7HaUt9CY…`) | 6 walkback rows present, unchanged |
| 3hJX | **NOT PRESENT** in `wt_walkback_queue`, `wt_discovered_subprovs`, or `wt_confirmed_treasuries` — checked directly by prefix across the whole programme (X77.1 and again here), consistently absent. Reported honestly, not fabricated. |
| GF7Y (`GF7YB1jGkt…`) | Present in `wt_discovered_subprovs`, `state='dismissed'`, `discovery_source='WALKBACK_RECURRING_FUNDER'` — read cleanly, confirms Dismissal + Discovery-source tracking both intact |

All present controls read identically to their pre-programme state — no
attribution, topology, or classification changed as a side effect of any
X77.x work.

## Regression summary (full closure sweep)

196 tests across the areas above, 195 passing, 1 pre-existing failure
(`test_x69_3_reconciliation_diagnostics.py::test_live_shadow_metrics_and_replays_are_clean`
— a hard-coded live-data-snapshot assertion that naturally drifts as the DB
grows; confirmed identically failing on `main` before any X77.x change, via
stash-and-compare). Not a regression.

## Remaining technical debt

1. **`creator_funding_worker` permanent stall** (BLOCKING) — see X77.3
   Finding 3. Dedicated follow-up milestone required. First question: is
   `asyncio.to_thread`'s default executor reusing OS worker threads while a
   stale `_thread_write_lease` from a prior, unrelated task survives on that
   thread. Do not weaken the write-lease guard or raise the self-kill
   threshold as a workaround — the guard is correct; the thread/task
   locality mismatch is the actual defect.
2. **The `ALTER TABLE ADD COLUMN` inside `try/except: pass` anti-pattern
   exists in ~40 files** across `src/` (grep count during X77.3
   investigation). Only `walkback_queue.py` was confirmed to actually leak
   the write lease (its `commit()` sat inside the `try`); the other 39 were
   not individually audited this programme — most likely follow the safer
   shape (`watchtower_candidates.ensure_schema`, confirmed non-leaking
   during X77.3 investigation, commits unconditionally once at function end
   rather than per-column). A dedicated low-priority audit pass would
   confirm none of the remaining 39 share `walkback_queue.py`'s specific
   defect shape.
3. **`3hJX` has no live entity** — not a defect, just noted for anyone
   reading forward: this control was named in the spec but has never had
   corresponding data in the ops DB at any point this programme measured it.

## Recommendation

**Split verdict, as this programme's own findings require:**

- **X77.1 (Walkback Transaction Boundary Optimisation): READY.**
  Validated in its own audit, reconfirmed stable under ~4h49m of real
  production contention in X77.3's authoritative soak window.
- **X77.2 (Lossless ws_cascade Write Handling): READY.**
  Validated in its own audit; the durable retry queue never accumulated a
  backlog throughout the entire authoritative soak window — the strongest
  possible live confirmation that contention on this write path stayed low
  and the loss it targets did not recur.
- **Overall X76–X77 platform stability: NOT READY TO PUSH.**
  `creator_funding_worker` has completed zero successful cycles across its
  entire observed uptime this programme — a live, ongoing, blocking defect
  in the creator-funding pipeline. It is independent of and unaffected by
  every fix shipped in X77.1/X77.2/X77.3 (confirmed via `git diff`: neither
  `creator_funding_worker.py` nor `realtime_creator_funding_extractor.py`
  were touched this session), but a programme-wide "the platform is stable"
  claim cannot be made while a core worker is fully non-functional.

The next milestone should be narrowly scoped to `creator_funding_worker`
alone, starting from the thread-local write-lease ownership hypothesis
documented in X77.3 Finding 3.

[x77_4_integrity_programme_closure.md](docs/audits/x77_4_integrity_programme_closure.md)
