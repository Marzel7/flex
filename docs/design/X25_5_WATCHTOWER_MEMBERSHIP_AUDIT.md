# X25.5 — Audit: WATCHTOWER Membership Attribution

Status: INVESTIGATION ONLY. No code changed. All findings below are traced
through the actual source and confirmed by live queries against
`database/wt_ops_v2.db`.

---

## Phase 1 — Trace from the rendered wording to the database

The exact wording under audit:

> "This launch is part of a WATCHTOWER-tracked operation, established by
> retrospective walkback rather than live detection..."

**Rendering layer** — `templates/discovery.html:533-534`, inside
`detectionReconciliation(r)`:
```js
'WALKBACK_RECOVERED':'This launch is part of a WATCHTOWER-tracked operation, ...'
'PIPELINE_INCONSISTENCY':'This launch is part of a WATCHTOWER-tracked operation, ...'
```
`r` is `d.detection_reconciliation`, and the branch taken is keyed on
`r.classification`.

**API layer** — `src/discovery/service.py:534-543`, inside `_entity()`:
```python
_recon = classify_walkback_confirmed_launches(self.ops_db_path)
detection_reconciliation = next(
    (r for r in _recon.get("rows", []) if r.get("mint") == token), None
)
```

**Classifier** — `src/ops/detection_reconciliation.py:57-165`,
`classify_walkback_confirmed_launches()`. Its entire input population is:
```sql
SELECT ... FROM wt_provisioning_sessions WHERE source_mint IS NOT NULL AND source_mint != ''
```
For each such row, if no matching `wt_watchtower_launches` row exists (no
live/catchup detection ever recorded), the classifier checks whether a
`LIVE_ARMED` session covered the CREATE window and assigns
`WALKBACK_RECOVERED` (not covered) or `PIPELINE_INCONSISTENCY` (covered but
still missed).

**Source-of-truth table** — `wt_provisioning_sessions`. Populated by
`capture_provisioning_relationship()` in `src/ops/provisioning_edges.py:150`,
called from `_capture_provisioning_facts()` in
`src/core/walkback_worker.py:502`, which is in turn called from the
`FULL_WALKBACK` branch of the walkback worker's main loop (lines 695, 718).

## Phase 2 — Where does the conclusion actually originate?

**Not** from: canonical operator, Operation Identity (X25.4), attribution
outcome, or any heuristic scoring. **Directly and only** from: whether a
`wt_provisioning_sessions` row exists for the mint — which is itself
populated purely by the walkback worker having captured *any* funding
fragment (`treasury or subprov or creator` non-null) during a walk, per
`provisioning_edges.py:150-178`'s own docstring: *"Called from the walkback
success path with whatever subset of (treasury, subprov, creator) and their
funding evidence is known at that point... accepts partial data."*

Critically, the write is **unconditional on treasury confirmation**. The
walkback worker's own comment at `walkback_worker.py:711-716` states this
explicitly:

> "capture the observed treasury(hop2)->subprov(hop1)->creator relationship
> as an operation-agnostic fact, **REGARDLESS of whether hop2 is a
> confirmed treasury**... hop2 (if found) is whatever funded hop1, known or
> not."

So a `wt_provisioning_sessions` row — the sole gate for the
"WATCHTOWER-tracked operation" wording — is written even when the walk's
own final verdict is `LINEAGE_GAP` (hop2 found but not a confirmed
treasury) or even `NO_ATTRIBUTION_FOUND`.

## Phase 3 — Complete decision tree (actual logic, not idealized)

```
Walkback worker runs FULL_WALKBACK for a mint
  │
  ├─ hop1 = funder of creator (via RPC)
  │    └─ _capture_provisioning_facts(treasury=None, subprov=hop1, creator)
  │         → wt_provisioning_sessions row written (session-level fact:
  │           subprov leg only) REGARDLESS of hop1's confirmation status
  │
  ├─ if hop1 is a known subprov (wt_discovered_subprovs):
  │    outcome = WATCHTOWER_CONFIRMED if hop1's treasury is set, else LINEAGE_GAP
  │    (mark_complete; wt_walkback_queue.intelligence_outcome set)
  │
  ├─ else if hop1 is a known treasury: outcome = WATCHTOWER_CONFIRMED
  │
  ├─ else: hop2 = funder of hop1 (via RPC)
  │    └─ _capture_provisioning_facts(treasury=hop2, subprov=hop1, creator)
  │         → wt_provisioning_sessions row written (BOTH legs)
  │           REGARDLESS of whether hop2 is a confirmed treasury
  │         (walkback_worker.py:711-716, explicit in the code's own comment)
  │
  │    ├─ if hop2 is a known treasury: outcome = WATCHTOWER_CONFIRMED
  │    └─ else: outcome = LINEAGE_GAP  ← wt_provisioning_sessions row STILL EXISTS
  │
  ▼
Later, Discovery resolves this mint:
  │
  ├─ classify_walkback_confirmed_launches() scans ALL wt_provisioning_sessions
  │    rows with source_mint set — NOT filtered by intelligence_outcome,
  │    NOT filtered by treasury confirmation
  │
  ├─ no wt_watchtower_launches row exists for this mint (walk-only, no live
  │    detection) → check wt_active_subprov_sessions for LIVE_ARMED coverage
  │
  ├─ not LIVE_ARMED at CREATE time → classification = WALKBACK_RECOVERED
  │
  ▼
Discovery renders: "This launch is part of a WATCHTOWER-tracked operation..."
```

**Empirical confirmation against the live database** (read-only queries,
no writes performed):

| `wt_walkback_queue.intelligence_outcome` for mints with a `wt_provisioning_sessions` row | Count | % |
|---|---|---|
| `LINEAGE_GAP` | 278 | 97.5% |
| `WATCHTOWER_CONFIRMED` | 6 | 2.1% |
| `NO_ATTRIBUTION_FOUND` | 1 | 0.4% |
| **Total** | **285** | 100% |

Concretely reproduced for one real mint:
`DYn5EjqneA22LvND9VXtaaYztHYVcy9ZPZg6xhvWpump` — `wt_walkback_queue`
records `intelligence_outcome = 'LINEAGE_GAP'`, `treasury = None` (the walk
never found ANY confirmed treasury — only an unconfirmed subprov and
creator). Running `classify_walkback_confirmed_launches()` against this
exact mint returns `classification: 'WALKBACK_RECOVERED'`, which Discovery
renders as "This launch is part of a WATCHTOWER-tracked operation."

## Phase 4 — Is this wording justified?

**No — it is not justified for the overwhelming majority (97.5%) of the
mints it currently applies to.**

The wording asserts operation *membership* — that this launch belongs to a
tracked WATCHTOWER operation. But the sole persisted gate for emitting that
wording (`wt_provisioning_sessions.source_mint IS NOT NULL`) requires
nothing more than "the walkback worker managed to identify at least one
funding-chain fragment," which explicitly includes cases where:
- no confirmed treasury was ever found (`treasury = NULL` in the session
  row itself, or a hop2 that failed the `_is_known_treasury` check), and
- the walk's own authoritative verdict, `wt_walkback_queue.intelligence_outcome`,
  is `LINEAGE_GAP` or `NO_ATTRIBUTION_FOUND` — both of which are explicitly
  *not* a WATCHTOWER-confirmed outcome by the walkback worker's own
  classification logic (`_mark_complete(ops, mint, "LINEAGE_GAP", ...)` is
  the *rejection* path, contrasted directly against
  `_mark_complete(ops, mint, "WATCHTOWER_CONFIRMED", ...)` a few lines above
  it in the same function).

In other words: **the classifier conflates "a partial funding fragment was
observed during a walk" with "this launch belongs to a confirmed WATCHTOWER
operation."** These are different facts. `wt_provisioning_sessions` was
designed (per its own docstring in `provisioning_edges.py`) as an
"operation-agnostic," "append-only," non-attributional fact table — it was
never meant to imply confirmed membership on its own. `detection_reconciliation.py`
uses its mere existence as if it were proof of exactly that.

**The correct gate**, if this wording is to remain honest, would need to
additionally require that the mint's `wt_walkback_queue.intelligence_outcome`
(or equivalently, its resolved `wt_attribution_outcomes.outcome_type`) is
actually `WATCHTOWER_CONFIRMED` / `CANONICAL_OPERATOR_REACHED` — not merely
that a `wt_provisioning_sessions` row happens to exist. As implemented
today, 278 of 285 mints (97.5%) reaching this wording have a walk that
explicitly ended in `LINEAGE_GAP`, meaning the platform's own walkback
worker declined to confirm a treasury, yet Discovery still tells the
analyst the launch "is part of a WATCHTOWER-tracked operation."

No fix is proposed or implemented here per the sprint's explicit scope —
this document is the investigation and verdict only.
