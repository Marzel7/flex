# MC1.0 — Mission Control Capability & Severity Model

Design document. No implementation in this document. Freezes the model;
implementation follows in a separate milestone after review.

**Status: APPROVED WITH AMENDMENTS.** This revision incorporates all four
required amendments from design review: (1) rate-based detection is now
primary, elapsed-silence is fallback; (2) birth/migration thresholds are
explicitly deferred to production-data-derived tuning at implementation
time — no placeholder constant may ship as a permanent default; (3)
incident persistence is gated on an explicit operational-justification
decision, not assumed; (4) every capability now exposes an evidence
count (`N/M signals`) alongside its status. The capability hierarchy,
incident grouping, severity vocabulary, API contract, and UI direction
were approved as originally written and are unchanged below except where
an amendment specifically modifies them.

Scope: UI and alerting logic only. No backend behaviour changes, no
ingestion changes. This document proposes a new **derived layer** on top
of the existing `/api/health/full` subsystem data — every existing field
is retained unchanged; nothing here requires removing or renaming
anything currently read by `templates/system_health_dashboard.html` or
any other consumer.

---

## 1. Problem Statement (as observed in the current implementation)

`/api/health/full` (`src/core/main.py:24875-25438`) computes 7
independent subsystem blocks — `ingestion`, `price_worker`,
`cascade_infrastructure`, `cascade_activity`, `database`, `api`,
`intelligence` — each with its own ad-hoc status enum
(`HEALTHY`/`DEGRADED`/`DOWN`/`STALE`/`AT_RISK`/`CRITICAL`/`OFFLINE`/
`STOPPED`/`STALLED`/`IDLE`/`ACTIVE`/`WATCHING`/`CONNECTED`/`RETRYING`/
`PEAK-ONLY`/`UNKNOWN`, no shared vocabulary across blocks) and its own
elapsed-time thresholds (300s, 600s, 120s, 90s, 3600s — no shared
concept of "expected event rate"). A single `top` status
(`HEALTHY`/`DEGRADED`/`AT_RISK`/`DOWN`) is derived via one large
`if/elif` chain over these 7 independent statuses (lines 25402-25423).

This produces exactly the fragmentation problem the charter describes:
during a live ingestion outage, an operator sees `ingestion.pumpportal =
"RETRYING"`, `ingestion.last_birth_age_secs` large,
`ingestion.last_migration_age_secs` large, and
`intelligence.funding_worker_status = "STALLED"` as four separate facts
requiring manual correlation, rather than one incident.

None of the 7 blocks currently models "expected event rate" — thresholds
are fixed elapsed-time constants (e.g. `mig_age > 600`), not compared
against a configured expectation like "births should occur continuously,
many per minute."

---

## 2. Design Principle

**Severity is determined by operational capability lost, not individual
subsystem status.** A capability is a user-facing operational function
("is live chain ingestion working?"). Subsystems are the mechanisms that
implement a capability and the diagnostic signals that explain *why* a
capability is degraded — they do not, on their own, define severity.

This is implemented as a **derived layer that reads the existing 7
subsystem blocks and re-groups them** — it does not replace or duplicate
their underlying measurement logic. `ingestion`, `price_worker`,
`cascade_infrastructure`, `cascade_activity`, `database`, `api`, and
`intelligence` keep computing exactly what they compute today; a new
`capabilities` block and a new `incidents` block sit on top, computed
last, from data already gathered.

---

## 3. Capability Hierarchy

```
Platform
 └── Live Ingestion           (birth detection, migration detection, listener connectivity)
      └── Creator Funding      (funding extraction queue + worker)
           └── Operational Intelligence  (creator resolution, watch pipeline, attribution)
                └── WATCHTOWER           (cascade infra/activity — provisioning-hub detection)
 └── Infrastructure            (database, API/Gunicorn — cross-cutting, not downstream of ingestion)
```

**Rationale for this shape**, derived directly from what's already
measured:

- `intelligence.funding_worker_status`/`funding_queue_*` and
  `intelligence.crq_*`/`watch_pipeline_*` are currently both folded into
  one `intelligence` block, but they are two different capabilities with
  different consumers (funding extraction vs. creator resolution/attribution)
  and different failure signatures (X78's own investigations treated
  them as separate systems throughout). Splitting them into **Creator
  Funding** and **Operational Intelligence** matches how they're already
  operated and debugged (see `docs/audits/x78_*` — every X78 milestone
  targeted `creator_funding_worker` specifically, never "intelligence" as
  a whole).
- `cascade_infrastructure`/`cascade_activity` map directly to
  **WATCHTOWER** — they are already named for it in the existing code
  (`ws_cascade` heartbeat, treasury/subprov subscription counts).
- `ingestion` (PumpPortal/PumpSwap connectivity, birth/migration
  freshness, listener log health) maps to **Live Ingestion** — this is
  the capability the charter is specifically about.
- `database` and `api` are **Infrastructure** — cross-cutting concerns
  that any capability can be blocked by, not a capability of their own
  and not strictly downstream of Live Ingestion (the database can be
  under pressure while ingestion itself is fine, and vice versa — X78.13A
  measured exactly this: DB reported CRITICAL while the actual mechanism
  was an application-level hold, independent of ingestion health).
- `price_worker` does not map cleanly to any of the charter's named
  capabilities. It's proposed as its own capability, **Price Tracking**,
  sitting alongside Live Ingestion rather than downstream of it (a token
  can be actively priced even during a brief ingestion gap, and the
  existing code already treats `PEAK-ONLY` as an intentionally-degraded,
  non-alarming state — folding it into another capability would either
  hide it or make it drag down an unrelated capability's severity).

**Degradation propagation**: loss of an upstream capability degrades
(not necessarily equals) downstream capabilities' *displayed* severity
context, per the charter's "Loss of an upstream capability should
automatically degrade downstream capabilities" instruction — but a
downstream capability's own CRITICAL status, if independently true, is
never *suppressed* by an upstream problem. Concretely: if Live Ingestion
is CRITICAL, Creator Funding's card shows a "degraded by: Live Ingestion"
annotation and its own severity floor is raised to at least WARNING even
if its own signals look healthy in isolation (no new work is arriving to
process) — but if Creator Funding *also* has its own independent
CRITICAL signal (e.g. heartbeat genuinely stopped), that is displayed at
full severity, not masked by the upstream annotation.

---

## 4. Capability Health Calculation

Each capability's health is a pure function of its member subsystems'
**already-computed** fields — no new measurement, only new interpretation.
Per the charter's explicit list, health considers (mapped to what each
capability already has available):

| Factor | Live Ingestion | Creator Funding | Operational Intelligence | WATCHTOWER | Infrastructure | Price Tracking |
|---|---|---|---|---|---|---|
| Availability | `pumpportal`/`pumpswap` connection state | `funding_worker_status` | `crq_worker_age_secs` presence | `cascade_infra.status` | `api.gunicorn_alive` | `worker_alive` |
| Freshness | `last_birth_age_secs`, `last_migration_age_secs` | `funding_worker_heartbeat_age_secs` | `watch_pipeline_age_secs`, `crq_worker_age_secs` | `cascade_infra.heartbeat_age_secs` | — | `last_peak_update_age_secs`, `last_snapshot_write_age_secs` |
| Latency | `listener_log_age_secs` | — | `crq_avg_runtime_secs`/`crq_p95_runtime_secs` | — | `database.p99_wait_ms` | — |
| Forward progress | birth/migration timestamps advancing | `funding_queue_oldest_pending_age_secs` (X78.16's rank-based signal, once exposed via `--status`) | `crq_resolution_efficiency` | `cascade_activity.last_launch_age_secs` | — | — |
| Backlog growth | `birth_queue_pending`, `migration_queue_pending` | `funding_queue_pending` trend | `creator_queue_pending` trend | — | `database.serializer_queue_depth` | — |
| Heartbeat | (listener log age is the ingestion process's own liveness signal) | `wt_worker_heartbeat['creator-funding']` | `wt_worker_heartbeat['creator-resolution']`, `['watch-pipeline']` | `wt_worker_heartbeat['ws_cascade']` | (self — this IS the API process) | (peak/snapshot writes are the heartbeat) |
| Expected event rate | **NEW — see §5** | — (funding demand is derived from ingestion, not its own expected rate) | — | — | — | — |

Nothing in this table requires a new database query or a new measurement
— every cell already exists as a field in the current `/api/health/full`
response. The capability layer's job is purely to read these existing
fields and classify.

---

## 5. Expected Event Rate

**Amendment 1 (design review): rate-based detection is the PRIMARY
signal. Elapsed-time silence is a FALLBACK, not a co-equal trigger.**
The original draft treated silence and rate as two independent triggers
where either alone escalates. Review correctly identified that on a
platform where births normally occur continuously (many per minute), a
severe rate *collapse* is a materially earlier and more informative
signal than waiting for total silence — by the time births have gone
fully silent for 30+ minutes, the platform has already been degraded for
a long time at a lower (but still abnormal) rate. Rate-based detection
catches that earlier window; silence-based detection exists only as a
safety net for the (unusual, but possible) case where rate computation
itself is unavailable or degenerate.

**New configuration, no new measurement mechanism.** `ingestion` already
computes `last_birth_age_secs` and `last_migration_age_secs` from
existing timestamp columns (`token_analysis.analyzed_at`,
`token_analysis.migrated_at`). The new work is (a) computing an
**observed rate over a rolling window** (count of births/migrations in
the last N minutes ÷ N) and comparing it against a *historically-derived
expected rate*, and (b) retaining elapsed-silence purely as a fallback
for when the rate computation cannot run.

```python
# Primary signal — rate collapse relative to a historically-derived baseline.
RATE_WINDOW_MIN = int(os.environ.get("MC_RATE_WINDOW_MIN", "15"))
RATE_CRITICAL_RATIO = float(os.environ.get("MC_RATE_CRITICAL_RATIO", "0.1"))   # <10% of expected
RATE_WARNING_RATIO = float(os.environ.get("MC_RATE_WARNING_RATIO", "0.5"))    # <50% of expected

# EXPECTED_BIRTHS_PER_MIN / EXPECTED_MIGRATIONS_PER_MIN are NOT hardcoded
# constants (see Amendment 2) -- they are computed at runtime from a
# rolling historical baseline, e.g. a trailing 7-day (or configurable)
# median observed rate over the same RATE_WINDOW_MIN granularity,
# excluding windows already flagged as an incident (so a real outage
# doesn't quietly lower the platform's own baseline and mask the next
# one). Implementation detail (baseline query shape, exclusion logic,
# recompute cadence) is deferred to implementation time -- this design
# freezes only the CONTRACT: expected rate is a computed, self-updating
# baseline, never a fixed number shipped in code.

# Fallback signal — only consulted if the rate computation itself
# returns None/insufficient-data (e.g. fewer than MC_RATE_MIN_SAMPLES
# historical windows available to establish a baseline at all, such as
# shortly after this feature first deploys).
BIRTH_SILENCE_FALLBACK_SEC = int(os.environ.get("MC_BIRTH_SILENCE_FALLBACK_SEC", "5400"))       # 90 min, matches the charter's own worked example
MIGRATION_SILENCE_FALLBACK_SEC = int(os.environ.get("MC_MIGRATION_SILENCE_FALLBACK_SEC", "5400"))  # 90 min, matches the charter's own worked example
```

Per Amendment 2, `RATE_CRITICAL_RATIO`/`RATE_WARNING_RATIO`/`RATE_WINDOW_MIN`
are the only values this design ships as fixed defaults (they are
dimensionless ratios/window sizes, not absolute rate constants, so they
don't carry the same risk of being wrong for this specific platform's
actual traffic). The baseline rate itself (`EXPECTED_BIRTHS_PER_MIN`
etc.) is never a shipped constant — see Amendment 2 below.

**Evaluation order (primary → fallback), per capability signal:**

1. Compute `observed_rate = count(events in last RATE_WINDOW_MIN) / RATE_WINDOW_MIN`.
2. Compute `expected_rate` from the historical baseline (Amendment 2).
3. If `expected_rate` is available (sufficient history exists):
   `observed_rate / expected_rate < RATE_CRITICAL_RATIO` → CRITICAL;
   `< RATE_WARNING_RATIO` → WARNING. **This is the primary, normal-case
   path.**
4. Only if `expected_rate` is unavailable (insufficient history — e.g.
   first deployment, or the baseline window itself was mostly incident
   time and got excluded down to too few samples): fall back to
   `last_birth_age_secs > BIRTH_SILENCE_FALLBACK_SEC` → CRITICAL. This
   fallback exists so the capability is never silently unmonitored
   merely because a baseline hasn't been established yet — it is
   deliberately coarser (matching the charter's own literal 90-minute
   example) since it has no rate context to be more precise with.

Migrations use the identical primary/fallback shape with their own
baseline and fallback constant (migrations are far rarer than births —
only a fraction of launches migrate — so their baseline and fallback are
computed/configured independently, never derived from the birth rate).

---

## 6. Critical Ingestion Rules

Per the charter, both conditions independently escalate **Live
Ingestion** to CRITICAL — updated per Amendment 1 to state the
primary/fallback relationship explicitly:

- **Births**: primary — observed birth rate collapses below
  `RATE_CRITICAL_RATIO` of the historically-derived expected rate over
  `RATE_WINDOW_MIN`. Fallback (only if no baseline exists yet) — no
  births for `> BIRTH_SILENCE_FALLBACK_SEC` (default 5400s/90min).
- **Migrations**: primary — observed migration rate collapses below
  `RATE_CRITICAL_RATIO` of the historically-derived expected rate.
  Fallback — no migrations for `> MIGRATION_SILENCE_FALLBACK_SEC`
  (default 5400s/90min, matching the charter's example).

Per Amendment 2, **no absolute rate or silence-duration constant in
this section is permitted to ship as a permanent production default** —
the fallback constants above are safety-net values only, expected to be
rarely or never the active trigger once a real baseline exists; the
actual escalation behavior in production is governed by the
historically-derived baseline, tuned at implementation/deployment time
against real observed traffic (see Amendment 2).

If both births and migrations fire simultaneously, per the charter's
explicit instruction, **this produces exactly one incident, not two** —
see §7.

Live Ingestion's overall status is the max severity of: PumpPortal/PumpSwap
connection state (`RETRYING`/`STALE` already computed by `ingestion`),
the primary/fallback rate-collapse evaluation above, and the existing
`birth_queue_pending`/`migration_queue_pending` backlog thresholds
(already present in `ingestion`, reused unchanged).

---

## 7. Incident Grouping Rules

An **incident** is a named, capability-scoped grouping of one or more
correlated signals, replacing today's practice of rendering each
subsystem's status as an independent card.

**Grouping algorithm** (deterministic, no ML/heuristic scoring):

1. Compute each capability's own severity (§4/§6) from its member
   subsystems.
2. If **Live Ingestion** severity is CRITICAL or WARNING, open **one**
   incident scoped to Live Ingestion. Its `impact` list is the specific
   triggered conditions (e.g. "No births for 95 minutes", "No migrations
   for 95 minutes") — only conditions that actually fired, not every
   possible condition.
3. Attach **contributing signals** to that same incident from any
   subsystem whose current status is abnormal AND whose subsystem
   belongs to (or is causally upstream of) the capability that opened
   the incident. Concretely, for a Live-Ingestion incident: `pumpportal
   == RETRYING`, `pumpswap == RETRYING`/`STALE`, `funding_worker_status
   != RUNNING` (funding demand is downstream of ingestion — no new
   creators to fund if nothing is being born), `database.serializer_queue_depth`
   elevated if correlated in time. This is exactly the charter's worked
   example.
4. **One incident per capability**, not per subsystem or per triggered
   condition — if Live Ingestion has 4 abnormal signals, that is still 1
   incident with 4 listed impacts/contributing-signals, never 4 separate
   incidents.
5. If multiple *different* capabilities are independently critical at
   the same time (e.g. Live Ingestion CRITICAL *and* Infrastructure
   CRITICAL for an unrelated database reason), those remain **separate**
   incidents — grouping only collapses signals *within* one capability's
   causal chain, it does not force unrelated capabilities into one
   incident merely because they're concurrent. (This directly reflects
   X78.13A's finding: a DB-CRITICAL alert and a funding-worker stall
   were, on investigation, two different mechanisms — automatically
   merging them would have been actively misleading.)
6. Incident membership is **recomputed on every poll**, not maintained
   as mutable state — a signal that clears is simply absent from the next
   computation. Timeline tracking (§9) is the only piece requiring
   persisted state.

---

## 8. Severity Levels

Four levels, one shared vocabulary across the whole capability layer
(replacing the current 15+ inconsistent per-subsystem enum values for
severity-classification purposes — the underlying subsystem fields keep
their existing specific enums unchanged, since those carry diagnostic
detail the capability layer's 4-level summary intentionally discards):

| Level | Meaning | Color |
|---|---|---|
| `HEALTHY` | Operating normally | green |
| `WARNING` | Degraded but capability still delivering, or a downstream capability affected by an upstream issue | yellow |
| `CRITICAL` | Capability not delivering its function | red |
| `UNKNOWN` | Insufficient data to classify (mirrors existing per-subsystem `UNKNOWN` handling — fail-soft, never crashes the endpoint) | gray |

Platform-level status is the maximum severity across all capabilities —
this replaces today's bespoke `top` if/elif chain (§9's example response
shows the shape).

---

## 8A. Capability Evidence (Amendment 4)

**Every capability exposes an evidence count alongside its status,
never a bare status alone.** Review correctly identified that "CRITICAL"
or "WARNING" on its own doesn't tell an operator whether that
classification rests on one flaky timer or multiple independent,
corroborating observations — and a capability-first model that hides
that distinction risks operators either over-trusting a thin signal or
under-trusting a well-corroborated one.

Each capability computes a fixed, enumerated set of independent signal
checks (drawn directly from §4's table for that capability — no new
measurements, just naming each existing check as a discrete pass/fail
evidence item) and reports how many are currently abnormal:

```
Live Ingestion — CRITICAL — Evidence 4/4 signals
  ✓ Birth rate collapse (observed 0.02/min vs expected 2.1/min baseline)
  ✓ Migration rate collapse (observed 0.00/min vs expected 0.11/min baseline)
  ✓ PumpPortal retrying
  ✓ Listener log stale
```

versus, e.g., a thinner case that should visibly read as thinner:

```
Creator Funding — WARNING — Evidence 1/4 signals
  ✓ Heartbeat elevated (140s, threshold 120s)
  ✗ Queue backlog growth: normal
  ✗ Oldest eligible age: normal
  ✗ Worker status: RUNNING
```

**Evidence signals are the same abnormal/normal checks already
implicit in each capability's status derivation (§4/§6)** — this
section does not invent new checks, it requires that every check
counted toward a status decision is also individually surfaced with its
pass/fail state, not collapsed into an opaque single verdict. The
denominator (`M` in `N/M`) is the fixed number of signal checks defined
for that capability; the numerator (`N`) is how many are currently
abnormal. A capability with `0/M` abnormal signals is HEALTHY by
definition — there is no case where status and evidence count disagree,
since evidence count is what status is computed from, not a separate
parallel judgment.

This is included in the API contract (§10) as a `signals` array (an
ordered list of `{name, abnormal: bool, detail}` per capability) that
was previously specified only as an opaque dict — see §10's revision.

---

## 9. Incident Timeline

For every CRITICAL (and, optionally, WARNING) capability incident, track:

- `first_detected_at` — first poll at which this capability's severity
  reached its current level. Whether this survives across polls via a
  new table or is computed statelessly depends on the persistence
  decision in §11 (Amendment 3) — this section states what must be
  displayed, not how it must be stored.
- `current_duration_secs` — `now - first_detected_at`.
- `recovered_at` — set when the capability returns to HEALTHY; the
  incident record is kept (not deleted) for a bounded retention window so
  "recovered 4 minutes ago" is displayable, then aged out.
- `root_contributing_signals` — a snapshot of which specific subsystem
  fields triggered escalation, captured **at the moment of first
  detection** (not recomputed retroactively), so the displayed root cause
  reflects what was actually true when the incident started, even if
  intermediate signals have since changed.

No manual interpretation is required to answer "when did this start and
is it still happening" — this is the literal data needed for that, no UI
logic required beyond formatting.

---

## 10. API Contract Additions (non-breaking)

`/api/health/full`'s existing response shape is unchanged. Two new
top-level keys are added:

```jsonc
{
  "platform": "WATCHTOWER",
  "status": "CRITICAL",              // existing key, now driven by the capability layer instead of the old if/elif chain
  "ts": 1786270000,
  "subsystems": { /* ... unchanged, all 7 existing blocks, all existing fields ... */ },

  // NEW:
  "capabilities": {
    "live_ingestion": {
      "status": "CRITICAL",
      "degraded_by": null,                 // capability name, if downstream and degraded by an upstream incident
      "evidence": { "abnormal": 4, "total": 4 },   // Amendment 4 -- N/M evidence count
      "signals": [
        {
          "name": "birth_rate_collapse",
          "abnormal": true,
          "detail": "observed 0.02/min vs expected 2.10/min baseline (primary signal)"
        },
        {
          "name": "migration_rate_collapse",
          "abnormal": true,
          "detail": "observed 0.00/min vs expected 0.11/min baseline (primary signal)"
        },
        {
          "name": "pumpportal_connection",
          "abnormal": true,
          "detail": "RETRYING"
        },
        {
          "name": "listener_log_freshness",
          "abnormal": true,
          "detail": "listener_log_age_secs=612 (>300s stale threshold)"
        }
      ]
    },
    "creator_funding": {
      "status": "WARNING",
      "degraded_by": "live_ingestion",
      "evidence": { "abnormal": 0, "total": 4 },
      "signals": [
        { "name": "heartbeat_freshness", "abnormal": false, "detail": "107s (threshold 120s)" },
        { "name": "queue_backlog_growth", "abnormal": false, "detail": "normal" },
        { "name": "oldest_eligible_age", "abnormal": false, "detail": "normal" },
        { "name": "worker_status", "abnormal": false, "detail": "RUNNING" }
      ]
    },
    "operational_intelligence": { "status": "HEALTHY", "degraded_by": null, "evidence": {"abnormal": 0, "total": 4}, "signals": [] },
    "watchtower": { "status": "HEALTHY", "degraded_by": null, "evidence": {"abnormal": 0, "total": 2}, "signals": [] },
    "infrastructure": { "status": "HEALTHY", "degraded_by": null, "evidence": {"abnormal": 0, "total": 3}, "signals": [] },
    "price_tracking": { "status": "HEALTHY", "degraded_by": null, "evidence": {"abnormal": 0, "total": 3}, "signals": [] }
  },

  // NEW:
  "incidents": [
    {
      "id": "live_ingestion:1786266000",     // capability + first_detected_at, deterministic, no UUID needed
      "capability": "live_ingestion",
      "severity": "CRITICAL",
      "title": "Live ingestion unavailable",
      "impact": [
        "No births for 95 minutes",
        "No migrations for 95 minutes"
      ],
      "contributing_signals": [
        "PumpPortal retrying",
        "Funding worker heartbeat stale",
        "Queue growth",
        "Listener log stale"
      ],
      "first_detected_at": 1786266000,
      "current_duration_secs": 4000,
      "recovered_at": null
    }
  ]
}
```

Every existing consumer of `/api/health/full` (the dashboard template's
existing fetch calls, any other script) continues to work unmodified —
`capabilities` and `incidents` are additive.

---

## 11. Persistence

**Amendment 3 (design review): persistence is not assumed. It is gated
on an explicit answer to one question, decided before implementation:
does Mission Control need historical incident analytics (MTTR, SLA
reporting, trend-over-time), or only live/current operational status?**
The original draft added a table by default because incident timelines
(§9) *feel* like they need state — but §9's actual requirements
(`first_detected_at`, `current_duration_secs`, `recovered_at`) do not, by
themselves, require durable storage if the only requirement is showing
"how long has this been going on" while it's still going on.

**Decision required before implementation** (not resolved by this
document — this is the explicit gate Amendment 3 requires):

- **If Mission Control requires only live operational status** (answer
  the question "what's happening right now and how long has it been
  happening" for currently-active incidents, with no requirement to
  query "how many Live Ingestion incidents did we have last month" or
  compute MTTR/SLA metrics after the fact): **prefer the stateless
  option below.** No new table.
- **If Mission Control requires historical incident analytics** (MTTR
  reporting, SLA tracking, trend analysis across past incidents, an
  incident history view): **persistence is justified**, and the table
  design below (unchanged from the original draft, still a reasonable
  shape if this path is chosen) applies.

**Stateless option** (default recommendation pending the decision above,
since nothing in the charter's stated success criteria explicitly
requires historical analytics — the charter's worked example and
acceptance criteria are entirely about live/current incident display):
derive `first_detected_at` from data that already exists without a new
table. For capabilities whose triggering signal is a single monotonic
timestamp (Live Ingestion's births/migrations: `first_detected_at` for a
"rate collapsed" incident can be reconstructed as "the first rolling
window, walking backward from now, whose observed rate was still below
threshold" — computable per-poll directly from `token_analysis` timestamps,
no separate state needed), this is fully computable statelessly on every
poll. For composite multi-signal capabilities where no single timestamp
captures "when did this incident start," a lighter-weight alternative to
a full table is an in-memory (per-process) tracking dict, accepting that
`first_detected_at` resets on a Gunicorn worker restart — a real but
bounded limitation, explicitly smaller in scope than adding durable
persistence, and consistent with "prefer stateless" if analytics aren't
required.

**Persistent option** (only if the decision above selects it): one new
small table, following this codebase's existing convention for
worker/health state (`wt_worker_heartbeat`-style, single ops DB, no
migration framework dependency):

```sql
CREATE TABLE IF NOT EXISTS mc_capability_incidents (
    incident_id TEXT PRIMARY KEY,       -- "{capability}:{first_detected_at}"
    capability TEXT NOT NULL,
    severity TEXT NOT NULL,             -- severity AT first detection
    first_detected_at INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL,      -- updated every poll while still active
    recovered_at INTEGER,               -- NULL while active
    root_contributing_signals_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_mc_incidents_capability ON mc_capability_incidents(capability, recovered_at);
```

Written from the same process that already computes `/api/health/full`
(the Gunicorn/API process) — no new worker/process needed. Read-modify-write
per poll: if a capability is CRITICAL/WARNING and no open incident row
exists for it, insert one; if one exists, update `last_seen_at`; if the
capability has returned to HEALTHY and an open row exists, set
`recovered_at`. This is a small, bounded-frequency write (once per
dashboard poll interval, not per request), consistent with this
codebase's established low-write-amplification discipline (directly
informed by the X78.12/X78.16 lessons about write-lane cost).

**This document does not make the persistence decision.** It is listed
in §16 (Open Questions Resolved / Remaining) as the one item still
requiring an explicit answer before implementation begins.

---

## 12. UI Specification (wireframe-level, implementation-time detail deferred)

Replace today's practice of rendering each of the 7 subsystem blocks as
independent status cards (which is presumably how `PumpPortal RETRYING`,
`No births in 95 minutes`, `No migrations in 95 minutes`, and
`Funding worker heartbeat stale` currently appear as 4 separate warning
cards) with:

1. **Top banner**: single platform-level severity (§8), matching the
   existing `top` field's role but now capability-derived.
2. **Incident cards** (only rendered when `incidents` is non-empty):
   one card per open incident, per §10's shape —
   ```
   🔴 CRITICAL — Live ingestion unavailable
   Impact:
     • No births for 95 minutes
     • No migrations for 95 minutes
   Contributing signals:
     • PumpPortal retrying
     • Funding worker heartbeat stale
     • Queue growth
     • Listener log stale
   First detected: 09:15:00Z (66 min ago)
   ```
   This directly matches the charter's worked example.
3. **Capability grid** (below incident cards): one compact tile per
   capability (§3's hierarchy), showing its current status, its
   Amendment-4 evidence count (`N/M signals`), and, if `degraded_by` is
   set, a small "↳ degraded by {upstream capability}" annotation — this
   is where the hierarchy/propagation becomes visible without needing a
   separate diagram. Per review's worked example:
   ```
   Live Ingestion — CRITICAL — Evidence 4/4 signals
     ✓ Birth rate collapse
     ✓ Migration rate collapse
     ✓ PumpPortal retrying
     ✓ Listener unhealthy
   ```
   A tile's evidence list is expandable to show each signal's `detail`
   string (§10); collapsed by default it shows only the `N/M` count so
   the grid stays scannable at a glance, matching the "immediately
   obvious" acceptance criterion.
4. **Subsystem detail** (collapsed by default, expandable): today's
   existing 7-block detail view, unchanged, for operators who want the
   raw diagnostic fields — nothing here is removed, only demoted from
   "primary view" to "detail view," directly satisfying "the operator's
   first question should be answered immediately... only then should the
   dashboard explain why."

No implementation (HTML/CSS/JS) is written in this document, per the
"design doc first, review, freeze" approach.

---

## 13. Operator Playbook (grouped incidents)

**When you see a `live_ingestion` CRITICAL incident:**

1. Read the `impact` list first — this tells you what's actually broken
   (births, migrations, or both).
2. Read `contributing_signals` — these are diagnostic hints, not
   separate problems. A PumpPortal RETRYING signal alongside a births
   silence strongly suggests the WebSocket connection is the root cause;
   check `logs/supervisor/listener.log` for the actual reconnect/error
   sequence.
3. Do **not** separately investigate a `creator_funding` WARNING that
   shows `degraded_by: live_ingestion` at the same time — it is very
   likely a downstream consequence (no new creators arriving to fund),
   not an independent funding-worker problem. Confirm via
   `creator_funding`'s own `signals` block: if its heartbeat is current
   and its own queue isn't growing abnormally, this is purely a
   propagated annotation, not a second incident.
4. If `creator_funding` (or any downstream capability) shows its OWN
   independent CRITICAL alongside the upstream incident, treat that as a
   **separate, second problem** requiring its own investigation — the
   grouping model deliberately does not suppress a genuinely independent
   downstream failure (per §7.5/X78.13A's lesson: don't assume
   correlation is causation without checking).
5. Check `first_detected_at`/`current_duration_secs` before escalating —
   a 2-minute-old incident may self-resolve (transient reconnect); a
   90-minute-old one, per this charter's own framing, should not be
   waved off as "still just a warning."

**When an incident's `recovered_at` becomes non-null**: no action needed
beyond confirming the recovery is genuine (spot-check the underlying
timestamps once) — the incident record remains visible for a bounded
window so a flapping condition is still noticeable as a pattern, not
silently forgotten the instant it clears.

---

## 14. Explicitly Out of Scope for This Design (confirmed against the charter)

- No change to how `ingestion`, `price_worker`, `cascade_infrastructure`,
  `cascade_activity`, `database`, `api`, or `intelligence` compute their
  own fields — every existing measurement, query, and threshold in those
  7 blocks is reused as-is.
- No change to any worker process, listener, or ingestion code.
- No change to `creator_funding_worker.py`, `pumpfun_curve_listener.py`,
  or any X78-series file.
- Per Amendment 2: no absolute rate constant (`EXPECTED_BIRTHS_PER_MIN`,
  `EXPECTED_MIGRATIONS_PER_MIN`) and no silence-duration constant beyond
  the explicitly-named fallback safety net (§5/§6) may be frozen into
  this design as a permanent production value. The historical-baseline
  derivation mechanism (rolling window, exclusion-of-incident-time logic,
  recompute cadence, minimum-sample threshold for "insufficient history")
  is implementation-time work, out of scope for this document to specify
  beyond the contract stated in §5.

---

## 15. Design Review Resolutions

Resolved by design review (verdict: APPROVED WITH AMENDMENTS):

1. **Capability set**: Price Tracking remains an independent capability,
   not folded into Live Ingestion. `PEAK-ONLY` continues to map to a
   non-alarming state within Price Tracking's own vocabulary, unchanged
   from the original proposal in §3.
2. **Birth/migration thresholds**: resolved as Amendment 2 — no fixed
   threshold values (silence duration or absolute rate) become permanent
   defaults. Thresholds are derived from production data during
   implementation (historical rolling baseline + configurable multiplier
   ratios, per §5/§6). The only literal duration constants that survive
   into the frozen design are the two fallback safety-net values
   (`BIRTH_SILENCE_FALLBACK_SEC`, `MIGRATION_SILENCE_FALLBACK_SEC`,
   both defaulted to the charter's own 90-minute figure), which are
   explicitly a fallback path, not the primary detection mechanism.
3. **Persistence**: resolved as Amendment 3 — not decided by this
   document. §11 now states the explicit gate (live-status-only vs.
   historical-analytics-required) and both the stateless and persistent
   implementations, but the actual choice is deferred to an explicit,
   separate decision before implementation begins. This decision is the
   one remaining item blocking the start of implementation (everything
   else in this document is frozen).

**Remaining before implementation can begin**: the §11 persistence
decision (live-only vs. analytics-required) must be made explicitly. No
other open question remains — the capability hierarchy, incident
grouping, severity vocabulary, evidence-count presentation, and API
contract are all approved as specified above.
