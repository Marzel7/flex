# FLEX v2 Operating Model — Attention-First Information Architecture

**Author:** UX architecture review · **Date:** 2026-06-07 · **Status:** design only, no code changed.

> **This document is not a page redesign — it defines how operators interact with FLEX.**
> It is the operating model: the mission-state vocabulary, the attention-first layering,
> and the confidence grammar that the whole UI should speak. It is preserved as its own
> design artifact, separate from detection-architecture and classification-architecture
> work, because it answers a different class of question — *why* Command Center exists,
> *why* Mission Status sits above Attention, *why* Operators was split, *why* confidence
> tiers exist — that would be lost if buried in a code commit.

> Opinionated thesis: FLEX does not have a data problem or a feature problem. It has a
> **lead problem** — every page leads with the wrong thing. A command center is judged by
> what it shows in the first screen *before you scroll*. Today that first screen is almost
> always a table. The fix is not new data; it is **re-ordering what already exists** so the
> top of every surface answers "what do I do now?" and the tables fall to the bottom.

A grounding fact that shapes everything below: the sidebar is **already layered** into
four sections (Live Market / Intelligence / Review & Investigation / Diagnostics), and a
`/api/watchtower/command-center` endpoint **already aggregates** hubs, reservoir, an
ignition feed, a review queue, and the pipeline. So this is largely a **promotion and
re-cut**, not a greenfield build. That makes it cheap and low-risk.

---

## 0. Real page inventory (from the live sidebar, not the idealized list)

| Route | Label | Today's lead element |
|---|---|---|
| `/` | Live Launches | live table |
| `/pumpfun` | PumpFun Feed | live feed |
| `/token-intelligence` | Tokens | table |
| `/creator-analysis` | Creators | table |
| `/ecosystem` | Ecosystems | dataset |
| `/funder-intelligence` | Funding | dataset |
| `/predictions` | Predictions | table |
| `/trading-sim` | Portfolio | metrics |
| `/approval-queue` | Token Review | queue table |
| `/network-approval` | Ecosystem Review | queue table |
| `/spike-analysis` | Flash Spikes | table |
| `/network-diagram` | Investigations | graph |
| `/watchtower` | WATCHTOWER (landing) | mixed |
| `/watchtower/dashboard` | ↳ Dashboard | metrics |
| `/watchtower/intelligence` | ↳ Operations | table |
| `/watchtower/operators` | ↳ Operators | command-center on top (recent), tables below |
| `/watchtower/interceptor` | ↳ Interceptor | status |
| `/transfer-graph` | Graph Diagnostics | graph |
| `/webhook-monitor` | Webhook Monitor | status |
| `/funding-queue` | Funding Queue | queue |
| `/snapshots` | Snapshots | table |
| `/vaults` | Vaults | table |
| `/usage` | Usage | metrics |
| `/system-health` | System Health | status |
| `/settings` | Settings | form |

Note the operators page **already** leads with a command-center block (commit `571b8a7`)
— the pattern this whole redesign generalizes already exists in one place and works.

---

## 1. New information architecture — five layers

The prompt proposed four layers (Attention / Operations / Review / Intelligence). I am
adding a fifth — **DIAGNOSTICS** — because feed/infra health is operationally load-bearing
(a dead webhook silently breaks attention) but does not belong *in* the attention stream
as a peer of "new operator discovered." It is a distinct concern: the health of the lens
itself. Five layers:

| Layer | Question it answers | Decay |
|---|---|---|
| **0 MISSION STATUS** | Is WATCHTOWER doing anything *right now*? | live (always current) |
| **1 ATTENTION** | What changed / needs me / is new? | minutes–hours (ephemeral) |
| **2 OPERATIONS** | What is active right now? | live (always current) |
| **3 REVIEW** | What is queued for my judgment? | until actioned |
| **4 INTELLIGENCE** | Let me investigate deeply | timeless (forensic) |
| **5 DIAGNOSTICS** | Is the system itself healthy? | live |

The key insight: **layers 1–3 are operational (time-sensitive, action-oriented), layers
4–5 are reference (durable).** The current sidebar mixes them. The redesign sorts them.

**Layer 0 is not a bigger version of Layer 1 — it is a different object.** Attention is a
*list of deltas* ("what changed?"). Mission Status is a *single state machine* ("is the
mission running?"). The first question an operator asks almost every day is not "what
changed" — it is "is WATCHTOWER doing anything?". A status banner answers that in one
glance and, crucially, **sets the emotional register of the entire page**: DORMANT should
feel quiet, ACTIVE should feel urgent. Everything below it is read in that light. See §3A.

### Page-by-page categorization

| Page | Layer | Standalone or drilldown? |
|---|---|---|
| Command Center *(new)* | 1 ATTENTION | **standalone — new top item** |
| Live Launches `/` | 2 OPERATIONS | standalone |
| PumpFun Feed `/pumpfun` | 2 OPERATIONS | **drilldown** of Live Launches (raw feed behind the curated one) |
| WATCHTOWER Interceptor | 2 OPERATIONS | standalone |
| WATCHTOWER Operations | 2 OPERATIONS | standalone (active campaigns/hubs) |
| Token Review `/approval-queue` | 3 REVIEW | standalone |
| Ecosystem Review `/network-approval` | 3 REVIEW | standalone |
| Funding Queue | 3 REVIEW | standalone |
| Predictions | 3 REVIEW *(was Intelligence)* | standalone — it is a **work queue**, not a dataset |
| WATCHTOWER Operators | 4 INTELLIGENCE | **registry → drilldown**; its "recent" block graduates to Command Center |
| Creators `/creator-analysis` | 4 INTELLIGENCE | standalone |
| Funding `/funder-intelligence` | 4 INTELLIGENCE | standalone |
| Ecosystems `/ecosystem` | 4 INTELLIGENCE | standalone |
| Tokens `/token-intelligence` | 4 INTELLIGENCE | standalone |
| Investigations `/network-diagram` | 4 INTELLIGENCE | standalone |
| WATCHTOWER Attribution *(API today)* | 4 INTELLIGENCE | **drilldown** of a creator |
| Flash Spikes `/spike-analysis` | 4 INTELLIGENCE | drilldown (signal, not a destination) |
| Portfolio `/trading-sim` | 4 INTELLIGENCE | standalone |
| Snapshots / Vaults | 4 INTELLIGENCE | drilldowns (reference data) |
| WATCHTOWER Dashboard | **merge → Command Center** | the metrics become CC tiles |
| Graph Diagnostics `/transfer-graph` | 5 DIAGNOSTICS | standalone |
| Webhook Monitor | 5 DIAGNOSTICS | standalone |
| System Health | 5 DIAGNOSTICS | standalone |
| Usage | 5 DIAGNOSTICS | standalone |
| Settings | — | footer |

**Opinionated calls:**
- **Predictions moves from Intelligence to Review.** It is a list of tokens awaiting an
  accept/ignore decision — that is the definition of a work queue.
- **WATCHTOWER Dashboard is absorbed into the Command Center**, not kept as a parallel
  "dashboard of dashboards." Two top-level summary pages is the overload, restated.
- **Operators becomes a registry drilldown.** Its time-sensitive "recent discoveries"
  content graduates up to the Command Center (see §4); what remains is the searchable
  registry, which is reference, not attention.
- **PumpFun Feed and Flash Spikes become drilldowns**, not top-level items. They are raw
  signal sources you reach *from* a curated view, not places you start your day.

---

## 2. New sidebar hierarchy

Ordered by layer. The single most important change: **Command Center is the new home
and the only thing above the fold for a returning operator.**

```
◆ COMMAND CENTER                      ← Layer 1, new default landing

  LIVE OPERATIONS                     ← Layer 2
    Live Launches
    WATCHTOWER Operations
    WATCHTOWER Interceptor

  WORK QUEUES                         ← Layer 3   (badge = open count)
    Predictions            ⓷
    Token Review           ⓷
    Ecosystem Review       ⓷
    Funding Queue          ⓷

  INTELLIGENCE                        ← Layer 4  (deep dives, collapsed by default)
    Creators
    Funding
    Ecosystems
    Tokens
    Investigations
    WATCHTOWER Operators (Registry)
    Portfolio

  DIAGNOSTICS                         ← Layer 5  (collapsed by default)
    System Health
    Webhook Monitor
    Graph Diagnostics
    Usage

  Settings
```

Drilldowns (PumpFun Feed, Flash Spikes, Attribution, Snapshots, Vaults, WATCHTOWER
Dashboard-as-tiles) **leave the top level** and are reached contextually. Nothing is
deleted — every route still resolves; it is just no longer a starting point.

Two behavioural rules:
- **Work Queues carry live count badges.** An empty queue collapses; a queue with items
  is the second thing the eye finds after the Command Center.
- **Intelligence and Diagnostics are collapsed by default.** They are where you go on
  purpose, not where you land.

---

## 3. COMMAND CENTER — wireframe

The Command Center is the entire redesign in one page. It must answer five questions
**before a single table**, in this vertical order (most ephemeral at top):

```
┌────────────────────────────────────────────────────────────────────────────┐
│  COMMAND CENTER                              live ● · last sync 12s ago      │
├════════════════════════════════════════════════════════════════════════════┤
│  ⬡ WATCHTOWER STATUS          ● FORMING            (state-coloured banner)   │  ← 0. MISSION STATUS
│    Active hubs 2 · Armed 1 · Pending creators 4   ·  Expected launch 15–90m  │
├════════════════════════════════════════════════════════════════════════════┤
│  [ Last hour ▾ ]   3 new operators · 11 attributions · 2 hubs · 1 conversion │  ← A. PULSE BAR
├────────────────────────────────────────────────────────────────────────────┤
│  ⚠ NEEDS ATTENTION  (4)                                                       │  ← B. ATTENTION
│  ● New WATCHTOWER operator  5E1Rvu…   confirmed 4/4 tests      [investigate] │
│  ● New provisioning hub     HS9NA3…   800 SOL · dual-signaller [investigate] │
│  ● Reservoir conversion     2 dormant → launch                [investigate] │
│  ⛔ Webhook feed stalled     helius_watch  no events 18m       [diagnostics] │
├────────────────────────────────────────────────────────────────────────────┤
│  ◆ LIVE OPERATIONS                                                            │  ← C. OPERATIONS
│  ┌──────────────┬──────────────┬──────────────┬──────────────┐              │
│  │ Active hubs  │ Armed hubs   │ Pending      │ Interceptor  │              │
│  │      6       │      2       │ creators 14  │   ● armed    │              │
│  └──────────────┴──────────────┴──────────────┴──────────────┘              │
│  Active campaigns: 3   ·   New launches (1h): 27   ·   Reservoir: 41 dormant │
├────────────────────────────────────────────────────────────────────────────┤
│  ▤ WORK QUEUES                                                                │  ← D. QUEUES
│  Predictions 18 · Token Review 5 · Ecosystem Review 2 · Funding 7   [open ▸] │
├────────────────────────────────────────────────────────────────────────────┤
│  ↺ WHAT CHANGED TODAY   (timeline, newest first)                             │  ← E. CHANGE FEED
│  11:49  attribution axis shipped …  09:12  hub HS9NA3 first seen …           │
└────────────────────────────────────────────────────────────────────────────┘
        ▼ tables (operator registry, full launch list) live BELOW the fold ▼
```

### Section specs

**0. WATCHTOWER Status (Mission Status banner)** — *the one-glance answer to "is the
mission running?", sitting above everything.*
- Purpose: collapse the entire operational posture into a **single state** with a colour
  and a one-line supporting readout. It is the first thing rendered and it tints the page.
- State machine (derived, not stored — **five lifecycle states**). These five are not
  invented for the UI; they are the operation lifecycle the WATCHTOWER investigations
  already describe (provisioning → seeding → launch → extraction → reservoir refill).
  Making them explicit gives the UI and the detection layer **one shared vocabulary**.

  | State | Triggers (from `command-center` payload + axis data) | Banner colour | Supporting readout |
  |---|---|---|---|
  | **DORMANT** | 0 active hubs · 0 armed · 0 pending creators · no launch in window | grey/quiet | `Last launch 41h ago · Reservoir conversions (24h) 0 · Confidence: Watching` |
  | **FORMING** | provisioning/hub activity detected · ≥1 active/armed hub · pending creators exist · no launch yet | amber | `Active hubs 2 · Armed 1 · Pending creators 4 · Expected launch window 15–90m` |
  | **ACTIVE** | creators seeded · launches occurring in window · interceptor armed | red/urgent | `Creators seeded 6 · Launches 4 · Interceptor ARMED` |
  | **EXTRACTING** | profit-relay routing active · sweep activity elevated · no *new* creators | orange | `Profit relay active · Sweeps elevated · No new seeds — value leaving` |
  | **RECYCLING** | reservoir growing · relay-funded dormant cohort increasing · no launches yet | blue/quiet-watch | `Reservoir +N dormant · Relay-funded cohort growing · Preparing next wave` |

  The natural cycle is **DORMANT → FORMING → ACTIVE → EXTRACTING → RECYCLING → (FORMING
  again or DORMANT)**. It is not strictly linear — an operation can skip states or hold —
  so the banner reports the *currently dominant* signal, not a forced step counter.
  Transitions **animate** on change; a DORMANT→FORMING or RECYCLING→FORMING flip (the
  next wave starting) is itself an attention event and should pulse.

  Two of these states are the operationally important early-warnings the old three-state
  model missed: **EXTRACTING** tells the operator value is leaving *now* (act on the
  relay), and **RECYCLING** is the leading indicator that a new wave is being provisioned
  — the single most valuable thing to see *before* FORMING, because it is the earliest
  point of intervention.
- Layout: full-width banner, state-coloured background, large state word, one supporting
  line. No tables, no lists — a single readable sentence of posture.
- Data: `command-center` → `active_hubs`, `armed_hubs`, `pending_creators`, `launches`,
  interceptor `status`, `reservoir` (dormant/converted counts → RECYCLING), `base_status`;
  relay/sweep telemetry (`/api/watchtower/relay-telemetry`, `sweep-events` → EXTRACTING);
  "expected launch window" from the provisioning-hub ~150s-pre-CREATE lead model already
  in the engine.
- Priority: **P0 — the single most important element on the page.**
- Refresh: **15 s** (this is the heartbeat; it must feel live).
- Empty/zero state: DORMANT is the resting state and must read as calm and intentional —
  "nothing forming" is information, not a blank.

**A. Pulse Bar** — *the one-line answer to "what changed?"*
- Purpose: a single scannable delta line with a time-window toggle (Last hour / Today).
- Layout: sticky top strip; counts are links that scroll to the matching section.
- Data: counts derived from `command-center` payload (`events`, `hubs`, `conversions`,
  `review`) filtered by `ts`/`born_at`/`checked_at` within the window.
- Priority: **P0** — it is the headline.
- Refresh: **30 s**.

**B. Needs Attention** — *the only section that can be empty and that is a good day.*
- Purpose: items that require a decision now, ranked. New operators, new hubs, reservoir
  conversions, missing creator resolutions, new shared-funder clusters, **and feed/infra
  anomalies** (these earn a place here because a dead feed invalidates everything below).
- Layout: severity-coded rows (⛔ infra > ● discovery), each with a one-tap
  `[investigate]`/`[diagnostics]` deep-link. Hard cap ~8 rows; overflow → "view all".
- Data: `events` (new attributions/operators), `hubs` (new), `reservoir.conversions`,
  plus a health probe (webhook last-event age, listener heartbeat).
- Priority: **P0**.
- Refresh: **30 s** (infra anomalies **15 s**).

**C. Live Operations** — *what is active.*
- Purpose: current operational state at a glance — not history.
- Layout: 4 stat tiles (active hubs / armed hubs / pending creators / interceptor) + a
  one-line secondary (campaigns, new launches, reservoir).
- Data: `command-center` → `active_hubs`, `armed_hubs`, `pending_creators`,
  interceptor `status`, `reservoir`, `launches`.
- Priority: **P1**.
- Refresh: **60 s**.

**D. Work Queues** — *what is mine to clear.*
- Purpose: open-count summary across all four queues; one click to each.
- Layout: single inline strip with counts; zero-state collapses.
- Data: `review` counts + the three approval/funding queue tables (`COUNT(*) WHERE open`).
- Priority: **P1**.
- Refresh: **60 s**.

**E. What Changed Today** — *the audit trail / "what is new" in full.*
- Purpose: reverse-chronological event timeline for the day; the durable record behind
  the Pulse Bar's count.
- Layout: compact timeline, newest first, lazy-loaded; this is where tables may begin.
- Data: `events` feed (already exists), unbounded by window.
- Priority: **P2**.
- Refresh: **120 s** (or on-demand).

**Design rule for the page:** sections A–D must fit in one viewport on a 1440×900 screen.
If they do not, cut content, not the order. Tables appear only after E.

---

## 4. WATCHTOWER Operators — redesign

**Current behaviour:** a registry. Even with the recent command-center block on top, the
page's center of gravity is the operator/cluster tables — it is a place you *search*, not
a place that *tells you something new*.

**Diagnosis:** the page conflates two jobs — "show me what's new in operator-land" (an
attention job) and "let me look up a known operator" (a reference job). Splitting them is
the fix.

**Redesign — two-zone page, attention on top:**

```
┌──────────────────────────────────────────────────────────────────────────┐
│  WATCHTOWER · OPERATORS                                                     │
│  ════════════════════════ NEW & ACTIVE ═════════════════════════════════   │
│  🆕 Newly discovered operators (24h)                                        │
│     5E1Rvu…  confirmed 4/4 · 6 launches · first seen 09:12   [open dossier] │
│  🆕 Newly attributed creators (24h)        11 new  →  [review attributions] │
│  🆕 New hub discoveries (24h)                                               │
│     HS9NA3…  800 SOL · dual-signaller · 1 launch             [open hub]     │
│  ♻ Reservoir conversions (24h)             2 dormant → launch [investigate] │
│  ⚡ Recent campaign formations (24h)        3 active          [open ops]     │
│  ──────────────────────────────────────────────────────────────────────    │
│  ════════════════════════ REGISTRY ═════════════════════════════════════    │
│  [ search… ]  [ grade ▾ ] [ confidence ▾ ] [ state ▾ ]                      │
│  Operator         Hubs  Launches  Confidence  Last active                   │
│  5E1Rvu…            2       6      CONFIRMED   2h ago        [dossier]       │
│  … full sortable operator table …                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

- **Zone 1 — New & Active** leads. Five blocks, each capped at the top few items with a
  "view all" deep-link: newly discovered operators, newly attributed creators, new hub
  discoveries, reservoir conversions, recent campaign formations. Each item is a verb
  (open dossier / review / investigate), not just a row.
- **Zone 2 — Registry** is the existing table, demoted below the fold, with its full
  search/filter/sort intact. **Nothing is removed.**
- Cross-link: the three new-axis fields now have a home here — each operator/creator row
  shows **Risk · State · Attribution** as three chips (the orthogonal model from
  `c2f677a`), replacing the old single overloaded label.

Data sources: `command-center` (`events`, `hubs`, `reservoir`, campaigns) for Zone 1; the
existing operators/clusters queries for Zone 2; the three axis derivations for the chips.
Refresh: Zone 1 at **30 s**, Zone 2 on interaction.

---

## 4b. Confidence tiering — separate Confirmed from Suspected from Interesting

A recurring theme across the investigations is the distinction between **Confirmed vs
Suspected vs Interesting**, and the UI does not yet draw it strongly. Every aggregate
count today mixes epistemic levels — "7 confirmed hub launches" sits in the same list as
"4 unclassified hubs". That is the *count-level* version of the exact overload the
three-axis model fixed at the *record level*: one undifferentiated bucket hiding three
different kinds of claim.

**This is not a fourth thing to invent — it is already derived.** The attribution axis
(`c2f677a`) emits `confidence ∈ {CONFIRMED, STRONG, WEAK}`. Tier the UI on it directly:

| Tier | Maps to | Visual | Example aggregates |
|---|---|---|---|
| **CONFIRMED** | attribution confidence CONFIRMED | solid, full-colour | 7 provisioning-hub launches · 14 direct launches |
| **PROBABLE** | STRONG | medium, muted | 71 reservoir wallets · 12 shared-funder clusters |
| **INVESTIGATIVE** | WEAK / unclassified | outline, low-emphasis | 4 unclassified hubs · 3 new funding corridors |

Wherever counts are summarized — Command Center, Operators "New & Active", Operations —
**group them under these three headers, strongest first, never intermixed.** A confirmed
fact and a hypothesis must never share a row. Visual weight encodes confidence:
CONFIRMED is loud, INVESTIGATIVE is quiet. This stops the operator from acting on a guess
as if it were proven — the single most important failure mode for an intelligence system.

```
CONFIRMED        ███  7 Provisioning-Hub Launches   ███ 14 Direct Launches
PROBABLE         ▓▓   71 Reservoir Wallets          ▓▓  12 Shared-Funder Clusters
INVESTIGATIVE    ░    4 Unclassified Hubs           ░   3 New Funding Corridors
```

---

## 5. Attention-first design principles

Seven rules to hold every page to:

1. **Lead with the verb, not the noun.** The top of a page is a list of *actions*
   (investigate, review, resolve), not a list of *records*.
2. **Tables live below the fold.** A table is a destination for a decision already made,
   never the first thing rendered.
3. **Empty is a valid — and good — state.** An empty Attention section means "nothing
   needs you," and must render as reassurance, not blankness.
4. **Every attention item is one tap from its investigation.** No item appears without a
   deep-link to the page that resolves it.
5. **Rank by decision urgency, not recency or severity alone.** Infra anomalies outrank
   discoveries (a dead feed invalidates discoveries); within a class, newest first.
6. **One summary page, not many.** Exactly one Command Center. Per-domain dashboards are
   the overload; fold their tiles into the Command Center or delete them.
7. **Time-box everything operational.** Every operational surface has a window selector
   (1h / today / 7d). "What changed" is meaningless without a clock.

8. **Lead with posture, then deltas.** The first object on the operational home is a
   single mission state (DORMANT/FORMING/ACTIVE), not a list. "Is it running?" precedes
   "what changed?". The state tints everything below it.
9. **Never mix confidence levels in one view.** Confirmed, Probable, and Investigative
   get separate headers and separate visual weight. A hypothesis must never be rendered
   with the authority of a fact.

And the meta-principle: **the three-axis model is the visual grammar.** Wherever a
creator or token appears, show Risk · State · Attribution as three chips, and tier every
aggregate count by attribution confidence (Confirmed/Probable/Investigative). One
overloaded label was the data-model version of the same overload this IA redesign fixes
in the UI — and the same orthogonality (separating *severity* from *certainty* from
*identity*) is what makes both the records and the counts honest.

---

## 6. Migration plan (low-risk, reversible, no data loss)

Sequenced to the agreed priority — these five phases remove an estimated **70–80% of the
overload** while keeping all data available. Each ships independently; nothing is removed
(routes keep resolving throughout).

**Phase 0 — Command Center (1 PR). [highest leverage]**
New `/command-center` route backed by the existing `command-center` endpoint. Lift the
block already living on `/watchtower/operators`; add the Pulse Bar + Needs Attention +
Live Operations + Work Queues shell. No other page touched. *Reversible: delete one route.*

**Phase 1 — WATCHTOWER Status banner (1 PR).**
Add the Layer-0 Mission Status banner (§3.0) to the top of the Command Center: the
DORMANT/FORMING/ACTIVE state machine derived from the same payload. Small, self-contained,
and it is the change that most changes how the page *feels*. *Reversible: remove one block.*

**Phase 2 — Operators redesign (1 PR).**
Apply §4: "New & Active" zone on top, "Registry" demoted below the fold, Risk·State·
Attribution chips on rows, confidence tiering (§4b) on the New & Active aggregates.

**Phase 3 — Move Predictions into Review (1 PR).**
Re-home Predictions under a Review group alongside Token/Ecosystem/Funding review. Pure
nav + grouping change; the page itself is untouched. *Reversible: revert one template.*

**Phase 4 — Re-cut sidebar + collapse Intelligence by default (1 PR).**
Reorder into the layer hierarchy (§2); collapse Intelligence and Diagnostics by default;
add live count badges to Work Queues; demote PumpFun/Flash Spikes/Snapshots/Vaults to
drilldowns (still routable). Make Command Center the landing page; keep Live Launches one
click away. *Reversible: revert one template.*

**Phase 5+ — Everything else, later.**
Attention-first sweep of remaining Intelligence pages (one small PR each); confidence
tiering rolled out to Operations; absorb WATCHTOWER Dashboard metrics into CC tiles and
redirect the old route. None of this is on the critical path — the first five phases carry
the bulk of the win.

**Guardrails:**
- No route is ever removed in the same PR that demotes it — demotion (off the sidebar)
  and removal (route deleted) are different phases, and removal is essentially never done.
- Each phase is one revertable PR.
- Ship Phase 0+1 first and live with them for a few days before Phase 2 changes the
  landing page — the landing-page change is the only one users feel immediately.

---

## Appendix — what I deliberately did NOT do

- **Did not invent pages.** Every page above maps to a real route in the live sidebar.
  The prompt's idealized list (e.g. "Reservoir Tracking", "Creator Intelligence") was
  reconciled to actual routes (reservoir lives inside the command-center payload and
  WATCHTOWER; "Creator Intelligence" = `/creator-analysis`).
- **Did not remove functionality.** Every change is a re-order or a demotion-to-drilldown.
- **Did not write code.** This is an IA review; implementation is the migration plan above.
