# Mission Status State Machine — Audit & Revised Model

**Date:** 2026-06-07 · **Trigger:** operator-reported false FORMING.
**Verdict: confirmed bug.** The state machine escalates on an analytical backlog, not
operational reality. This breaks the one thing Mission Status exists to provide — trust.

---

## 1. The bug, confirmed in code

Observed: `FORMING / "launch window forming"` with **0 active hubs, 0 armed, 0 launches,
0 conversions** — only `pending_creators = 214` and `reservoir_dormant = 71`.

Root cause, `command_center.html` `deriveMission()`:

```js
// FORMING: hubs active/armed, creators pending, no launch yet
if (active > 0 || armed > 0 || pending > 0) {   // ← pending alone forces FORMING
    return ['FORMING', …, 'launch window forming'];
}
```

`pending` is `status.pending_creators`. The backend defines it (main.py) as:

> Pending = WATCHTOWER-attributed creators that don't yet belong to any confirmed
> operation (i.e. **unreviewed lineage/infra hits**).

So `pending_creators` is a **standing review backlog of historical attributions** — it
does not change when an operation forms, and 214 of them can sit there for weeks with
nothing operational happening. Driving FORMING off it is a category error:
**attribution backlog read as operational state.**

### The core distinction the old model missed

| Signal | Kind | Should drive Mission Status? |
|---|---|---|
| active hubs, armed hubs, creator seeds, launches, interceptor-armed | **operational** (happening now) | **YES** |
| profit-relay active, sweeps elevated | **operational** (monetising now) | **YES** |
| reservoir conversions (a dormant wallet *just* launched) | **operational** (an event) | **YES** |
| reservoir dormant *count* (71 wallets sitting idle) | **analytical** (standing pool) | only as RECYCLING evidence, never as escalation |
| pending_creators (214 unreviewed attributions) | **analytical** (review backlog) | **NO — never** |
| attribution review queue depth | **analytical** (work backlog) | **NO — never** |

Mission Status answers *"is the operation running?"*. Only **operational** signals —
things with a clock on them — may answer it. Analytical backlogs belong in Work Queues
and metric cards, where their job is "here is accumulated work", not "something is
happening now".

---

## 2. Audit findings (the six questions)

**1. Current logic** — a fall-through `if/else` ladder with no time-bounding on the
operational counts and no confidence. `active/armed/pending` are treated as
interchangeable FORMING triggers; `pending` (analytical) sits in the same `OR` as
`active`/`armed` (operational). EXTRACTING/RECYCLING are below FORMING, so the `pending`
short-circuit also **masks** them.

**2. Transition triggers** — there are effectively none: the model recomputes a state
from current counts each tick with no entry/exit hysteresis. A single stale count flips
the banner. No dwell, no decay.

**3. Operational vs analytical weighting** — *none exists*. That is the whole defect.
Operational and analytical signals carry equal weight in the same boolean.

**4. Should `pending_creators` influence Mission Status?** — **No. Remove it entirely.**
It is a review backlog. It belongs in the Attribution Review work queue, badged by count.

**5. Should `reservoir_dormant` influence Mission Status?** — **Only as RECYCLING
evidence, and only when it is *growing*, never as an escalation from DORMANT.** A static
pool of 71 dormant wallets is posture, not activity. A pool that *grew in the last 24h*
(new relay-funded staging) is the RECYCLING signal. The count alone → metric card.

**6. Should attribution review queues influence Mission Status?** — **No.** Same reason
as (4): backlog, not activity.

---

## 3. Revised state model

Principle: **a state may only be entered by an operational signal with a timestamp.**
Counts without a clock (dormant pool size, review backlog) can *describe* a state but can
never *cause* one. Default is DORMANT — the system must earn any higher state with
time-bounded evidence.

Signal definitions (all from the existing `command-center` payload + relay/sweep
telemetry):

- `active_hubs`, `armed_hubs` — live hub op-status (already time-derived in backend).
- `seeds24`, `launches24`, `launches30m` — from `events` (CREATE_DETECTED, seeds), windowed.
- `armed24` — HUB_ARMED events in 24h.
- `newHubs24` — NEW_PROVISIONING_HUB events in 24h.
- `conv24` — RESERVOIR_CONVERTED events in 24h (an operational *event*).
- `reservoirGrowth24` — dormant-pool delta over 24h (RECYCLING evidence).
- `relaysActive`, `sweepBurst` — relay-telemetry + sweep-events (≥0.01 SOL floor).
- **Never used for state:** `pending_creators`, `reservoir_dormant` (static count),
  review-queue depth.

### DORMANT  (default / resting)
- **Entry:** no operational signal present — `active_hubs=0 ∧ armed_hubs=0 ∧
  launches24=0 ∧ seeds24=0 ∧ newHubs24=0 ∧ ¬relaysActive ∧ ¬sweepBurst ∧
  reservoirGrowth24≤0`. (A non-zero *static* reservoir does NOT lift out of DORMANT.)
- **Exit:** any FORMING/ACTIVE/EXTRACTING/RECYCLING entry condition becomes true.
- **Priority:** 0 (lowest). **Confidence:** inverse of strongest near-miss signal
  (typically 95–100% when truly idle). **Cadence:** 15 s.
- **Caption:** "No active hubs · no launches · no extraction — watching." A standing
  reservoir is mentioned as posture, not as activity.

### FORMING  (a wave is being provisioned)
- **Entry (any):** `newHubs24>0 ∨ armed_hubs>0 ∨ armed24>0 ∨ seeds24>0` and
  `launches30m=0`. i.e. **fresh hub/arming/seeding activity, no launch yet.**
  Pending-creator backlog is explicitly NOT an entry condition.
- **Exit:** `launches30m>0` → ACTIVE; all forming signals age out (>24h) → DORMANT.
- **Priority:** 2. **Confidence:** weighted sum of present signals (see §4).
  **Cadence:** 15 s.
- **Caption:** lists the *actual* forming signals; if only one weak signal, say so.

### ACTIVE  (launching now)
- **Entry (any):** `launches30m>0 ∨ (armed_hubs>0 ∧ seeds24>0) ∨ active_hubs>0`.
- **Exit:** no launch in 30m AND no active/armed hub → fall to EXTRACTING (if extracting)
  else FORMING (if still seeding) else DORMANT.
- **Priority:** 4 (highest — live launches outrank everything). **Confidence:** high
  when launches present. **Cadence:** 10 s (fastest — this is the live moment).
- **Caption:** active hubs, armed, seeds, launches-in-30m.

### EXTRACTING  (monetising)
- **Entry:** `(relaysActive ∨ sweepBurst) ∧ active_hubs=0 ∧ armed_hubs=0 ∧ launches30m=0`.
  Value leaving, no new creators forming.
- **Exit:** relays/sweeps quiet → RECYCLING (if reservoir growing) else DORMANT; new
  hub/seed activity → FORMING.
- **Priority:** 3. **Confidence:** from relay count + sweep volume. **Cadence:** 15 s.
- **Caption:** relays active, SOL routed (1h), sweep count.

### RECYCLING  (staging the next wave)
- **Entry:** `(conv24>0 ∨ reservoirGrowth24>0) ∧` no FORMING/ACTIVE/EXTRACTING signal.
  i.e. the reservoir is *growing* or wallets are *converting*, but nothing is forming yet.
- **Exit:** forming signal appears → FORMING; growth stops and pool goes static → DORMANT.
- **Priority:** 1 (just above DORMANT — it is the earliest pre-warning). **Confidence:**
  from growth magnitude. **Cadence:** 30 s (slow-moving).
- **Caption:** reservoir growth (24h), conversions (24h). A *static* pool alone does NOT
  enter RECYCLING — only growth or conversion does.

### Evaluation order (highest priority wins)
`ACTIVE → EXTRACTING → FORMING → RECYCLING → DORMANT`.
Note FORMING no longer masks EXTRACTING/RECYCLING, and nothing escalates on backlog.

---

## 4. Confidence calculation

Each state computes a 0–100% confidence = normalised weighted sum of the operational
signals that triggered it. This is what the "Why?" panel shows.

```
weights (operational evidence strength):
  launches30m      40    active_hubs      25
  armed_hubs       20    seeds24          15
  newHubs24        15    relaysActive     20
  sweepBurst       15    conv24           12
  reservoirGrowth  10
confidence = min(100, Σ weight(signal present, scaled by magnitude))
DORMANT confidence = 100 − (max single near-miss weight)   // high when truly idle
```

A state entered on **one weak signal** (e.g. a single new hub, nothing else) yields a low
confidence (e.g. 30–40%), and the banner shows that — so the operator sees "FORMING,
confidence 35%, on a single new hub" rather than a falsely emphatic FORMING. **Low
confidence is itself the anti-false-escalation guard.**

---

## 5. "Why am I in this state?" panel

A panel directly beneath the banner, always present, listing the exact signals that
triggered the current state with their values, plus the confidence. Makes the machine
auditable — the operator never has to guess.

```
┌──────────────────────────────────────────────┐
│ ACTIVE                          confidence 92% │
│ Triggered by:                                  │
│   • 2 active hubs                               │
│   • 1 armed hub                                 │
│   • 4 seeded creators (24h)                     │
│   • 2 launches (last 30m)                       │
│ Not counted: 214 pending attributions (backlog,│
│   not operational) · 71 dormant reservoir       │
│   (static pool)                                 │
└──────────────────────────────────────────────┘
```

Two design rules for the panel:
1. **Show what triggered it** — every contributing signal with its value.
2. **Show what was deliberately ignored** when it might surprise the operator — i.e.
   explicitly state that pending attributions and a static reservoir did NOT drive the
   state. This directly answers "why am I NOT in a higher state despite 214 pending?".

For the reported live data, the corrected panel reads:

```
DORMANT                              confidence 96%
Triggered by:
  • 0 active hubs · 0 armed · 0 launches · 0 extraction
Not counted (analytical, not operational):
  • 214 pending attributions → Attribution Review queue
  • 71 dormant reservoir (static — not growing)
Watching.
```

That is the honest state. The operator immediately understands the 214 are a review
backlog, not a forming operation.

---

## 6. Does the Reservoir deserve elevation into Mission Status?

**Partially — and the distinction is the whole point.**

- **Reservoir *count* (71 dormant):** No. It is a static pool. It stays a metric card.
  Elevating a standing count is the same false-escalation class as the pending bug.
- **Reservoir *growth / conversion* (Δ over 24h, or a wallet launching):** Yes — this is
  the RECYCLING signal and the *earliest* pre-warning of a future wave, which is exactly
  the kind of thing Mission Status should surface. But it enters RECYCLING (priority 1,
  quiet), never escalates to FORMING/ACTIVE.

So: the reservoir earns a place in Mission Status **only in its derivative (rate of
change), never in its level.** Rate is operational; level is analytical.

---

## 7. Confidence hierarchy in the Command Center

Map the Attribution Axis confidence to a display hierarchy, applied wherever counts are
shown (already specified in the IA redesign §4b; restated here for the state machine):

| Attribution Axis | Command Center tier | Visual |
|---|---|---|
| CONFIRMED | **CONFIRMED** | solid, full-colour |
| STRONG | **PROBABLE** | medium, muted |
| WEAK | **INVESTIGATE** | outline, low-emphasis |

Where it appears:
- **Mission Status confidence %** — the state-machine confidence above is operational
  certainty (did this really happen?), distinct from attribution certainty. Keep them
  separate and label them so: banner shows *operational* confidence; attribution tiers
  apply to *creators/operators* in the discovery and review surfaces.
- **Needs Attention / discovery counts** — always tier-grouped (e.g. "11 attributions:
  8 confirmed · 3 probable") so a hypothesis never reads with a fact's authority.
- **The "Why?" panel** — when a state is driven by attributed activity, the panel tags
  each contributing item with its tier, so the operator sees not just *what* triggered the
  state but *how sure* we are of each piece.

---

## 8. Summary of changes to implement

1. **Remove `pending_creators` from `deriveMission` entirely.** It never touches state.
2. **Remove static `reservoir_dormant` as an escalation trigger;** use 24h growth/
   conversion for RECYCLING instead.
3. **Time-bound every operational signal** (24h for forming evidence, 30m for "live"
   launch/active).
4. **Add per-state confidence** (§4) and surface it on the banner.
5. **Add the "Why am I in this state?" panel** (§5), including the "not counted" line.
6. **Re-order evaluation** ACTIVE → EXTRACTING → FORMING → RECYCLING → DORMANT so nothing
   is masked.
7. Default to DORMANT; require earned, time-stamped evidence for anything higher.
