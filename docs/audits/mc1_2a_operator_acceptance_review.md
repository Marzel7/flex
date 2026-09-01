# MC1.2A — Operator Acceptance Review

UX and operational validation only. No code changes, no threshold
tuning, no redesign performed as part of this review — reading the
already-implemented, already-tested rendering path end to end as an
on-call operator would encounter it.

---

## 1. Primary Question — Is the failing capability immediately obvious?

**YES.**

The banner (`mcUpdateBanner`) is driven exclusively by the same
`fullHealth.incidents` array the incident cards below it read — there is
no separate classifier that could disagree with what's shown underneath
(this was MC1.1B's fix, still holding). It leads with the single most
upstream failing capability by hierarchy order, e.g. `Critical — Birth
rate collapsed`, with any other concurrent incidents summarized in one
sub-line (`Creator Funding WARNING · Infrastructure WARNING`). An
operator sees the answer before scrolling past the banner.

---

## 2. Births — Healthy / Degraded / How bad, without expanding anything?

**YES.**

`mcCapabilityMetricsHtml`'s `live_ingestion` branch always renders
Observed/Expected/vs-baseline rows directly on the card, unexpanded
(MC1.1A/MC1.2's promotion of operational metrics to the top of the
card). The overall capability status badge (`HEALTHY`/`WARNING`/
`CRITICAL`) is also visible without expanding — collapsed cards still
show `mcCollapsedSummary`, and if Live Ingestion is the primary
(highest-severity) capability it renders expanded by default. "How bad"
is answered numerically (`92% below baseline`), not just a color.

---

## 3. Migrations — Dormancy vs. real outage, without expanding?

**YES**, and this is MC1.2A's core deliverable working as designed.

`migrationRows()` renders a dedicated badge (🟢 Dormant / 🟡 Warning /
🟠 Concern / 🔴 Critical) plus the plain-language elapsed-time sentence
("12 minutes since last migration — within expected burst profile") —
distinct in both icon/color AND wording from Births' badge, on the same
card, unexpanded. An operator does not need to know the 20/30/40-minute
band thresholds to read this correctly; "Dormant... within expected
burst profile" reads as *not a problem* on sight, which is exactly the
charter's success criterion for this milestone.

---

## 4. Capability Hierarchy — Does the ordering feel natural?

**Yes, matches the charter's expected order exactly**: Live Ingestion →
Creator Funding → Operational Intelligence → Infrastructure → Price
Tracking (`MC_CAPABILITY_LABELS` object order, reused as
`MC_HIERARCHY_ORDER` for every sort in the dashboard). WATCHTOWER is
correctly excluded from top-level navigation and nested as a one-line
subline inside Operational Intelligence's card — this reads as "an
operation running on top of the platform," not a sixth platform
capability, which is the right mental model. Nothing feels misplaced.

---

## 5. Information Density — unnecessary text?

Mostly lean, with two areas worth flagging (not fixing, per scope):

- **Repeated information**: the banner's sub-line and the incident
  card's title can restate the same capability+severity pair the cap
  strip directly above it already shows (e.g. banner says "Critical —
  Birth rate collapsed," the strip right below shows a red Live
  Ingestion row, the incident card below that repeats "CRITICAL / Live
  ingestion unavailable"). This is **intentional redundancy across
  zoom levels** (glance → strip → detail), not noise — each surface
  answers the primary question at a different level of commitment
  (banner = must-see, strip = scan, card = investigate). Acceptable,
  not a defect.
- **Diagnostic noise**: `mc-capability-tile-signals` (the ✓/✗ list)
  shows *every* signal including healthy ones when a card is expanded —
  e.g. a CRITICAL Live Ingestion card still lists `✓ PumpPortal
  retrying: CONNECTED`. This is correct evidence-completeness (MC1.0's
  evidence model), but on an already-primary, already-expanded card, the
  healthy checkmarks add scroll length without adding decision value —
  an operator investigating a CRITICAL card wants the ✗ lines first.
- **Labels that can disappear**: `Evidence 1/6 signals` on a collapsed
  card is engineering-shaped phrasing (fraction-of-signals) sitting next
  to `mcCollapsedSummary`'s plain-English one-liner directly above it —
  slightly redundant once the plain summary is already shown.

None of these rise to "the operator can't find the answer" — they are
polish-tier, addressed under Priority-Ranked Improvements below.

---

## 6. Incident Cards — "What happened" before "Why"?

**YES.** Card order is: title (`CRITICAL — Live ingestion unavailable`)
→ duration → Started → Trend → **Impact** (what happened, plain labels
like "Birth rate collapsed") → **Contributing signals** (why, with raw
detail strings like `observed 1.47/min vs expected 19.17/min baseline`)
→ jump link. This is the correct order — a plain-language "what" leads,
technical "why" detail follows and is skippable.

**Unnecessary engineering detail**: the "Contributing signals" section's
detail strings are the raw signal `detail` text computed by the backend
engine (e.g. `observed 1.47/min vs expected 19.17/min baseline (primary
signal)` — the literal `(primary signal)` engine-internal annotation
leaks into operator-facing copy here). This is the single most
"engineering, not operator" string on the whole dashboard. Minor, but
real — flagged below.

---

## 7. Capability Cards — Are operational metrics prominent, or do diagnostics dominate?

**Prominent, not dominated.** Card layout order is: operational metrics
(Births/Migrations rows, or Worker Status/Queue Depth/Heartbeat for
other capabilities) → trend badge/sparkline → status/evidence row →
diagnostics list — this ordering was locked in MC1.1A and held
consistently through MC1.3 and now MC1.2A. Birth rate, migration state,
queue depth, and heartbeat age are all headline rows above the fold on
every card type, matching the charter's own examples. Diagnostics
(the ✓/✗ signal list) only appear below the status/evidence row and only
fully unfold on an expanded card — they support, they don't lead.

---

## 8. Colors — Correct severity representation?

**Yes, no mis-tiered colors found.** Consistent 3-tier ramp used
everywhere status-driven color appears (banner, incident cards,
capability tiles, cap strip, status badges): green `#4ade80` (HEALTHY) →
yellow `#facc15` (WARNING) → red `#f87171`/`#ef4444` (CRITICAL), with
grey `#94a3b8` for UNKNOWN. Migration's new 4-tier badge (dormant=green,
warning=yellow, concern=orange `#fb923c`, critical=red) inserts orange
*between* yellow and red specifically to visually carry the extra
"Concern" band the charter asked for — this is additive, not a
re-tiering of the existing 3-color severity scale, so it doesn't
conflict with or dilute the meaning of yellow/red elsewhere on the page.
No yellow that should be green, no orange that should be yellow, no red
that should be orange — the ramp is internally consistent.

---

## 9. Cognitive Load — Seconds to answer "What is broken?"

**Estimated 2-3 seconds** for an experienced operator: banner text alone
(`Critical — Birth rate collapsed`) answers capability + severity in one
read, with zero scrolling or clicking required — this is the single
highest-commitment surface on the page and it's also the first thing
rendered. Getting to "how severe / trending which way / what to
investigate first" (the charter's full 3-question success criterion,
not just "what's broken") takes slightly longer — maybe 5-8 seconds —
since trend requires reading the incident card's Trend badge or the
capability card's sparkline, and "what to investigate first" requires
reading the Impact list. Still comfortably inside a single glance-and-
scan, not a hunt.

---

## 10. Overall Scores (1-5)

| Dimension | Score | Note |
|---|---|---|
| Information hierarchy | 5 | Banner → strip → incidents → cards is a clean, consistent zoom progression; nothing competes for primary attention |
| Visual hierarchy | 5 | Color, size, and position all agree with severity; primary incident/card visually dominant, everything else correctly recedes |
| Operational usefulness | 5 | Answers all 4 of the charter's success-criterion questions (what/how severe/trending/investigate-first) without expansion |
| Engineering usefulness | 4 | Full evidence/signal detail, trend windows, and raw contributing-signal strings remain one click away — nothing was sacrificed for operator-friendliness |
| Operator usefulness | 5 | This milestone's actual target: MC1.2A's core deliverable (migration dormancy vs. outage, at a glance, without knowing the threshold numbers) works exactly as intended |

---

## Suggested UI Improvements (Priority-Ranked)

Review only — none implemented here, per scope.

1. **(Low-medium)** Strip the engine-internal `(primary signal)` /
   `(fallback threshold ...)` parenthetical annotations out of the
   "Contributing signals" detail strings before they reach the incident
   card — these are debugging breadcrumbs for whoever built the rate
   engine, not operator vocabulary.
2. **(Low)** On an already-expanded/primary capability card, consider
   sorting `mc-capability-tile-signals` abnormal-first (✗ lines before
   ✓ lines) so the evidence list matches the card's own visual priority
   instead of its declaration order.
3. **(Low)** `Evidence N/M signals` on a collapsed card is slightly
   redundant next to the plain-English collapsed summary directly above
   it — could drop on collapsed cards specifically (keep it on expanded
   cards, where it's the useful denominator for the ✓/✗ list right
   below it).

None of these affect the five-second answer the charter's success
criterion is actually gating on — they're refinements to the
already-successful design, not corrections to a failing one.

---

## Recommendation

**Push.** The three items above are real but minor, apply equally well
as fast follow-ups without blocking this milestone, and none of them
touch the actual calibration work MC1.2A was chartered to deliver
(which is confirmed working: migration dormancy reads as "not a
problem" at a glance, exactly as intended).

---

## Final Verdict

**A — Ready to Push**

Operator-focused reasoning: the banner alone answers "what capability
failed" in under 3 seconds with zero interaction; severity, trend, and
"what to investigate first" are all available within a single glance-
and-scan (5-8 seconds) without expanding any diagnostics — meeting the
charter's explicit five-second success criterion for the primary
question and comfortably meeting the fuller 4-question criterion.
Migration dormancy is now visually and textually indistinguishable from
"not a problem" during the normal 0-20 minute band, and visually
escalates in clear steps (green → yellow → orange → red) exactly
tracking the calibrated bands — this was the entire point of MC1.2A and
it reads correctly to an operator who has never seen the threshold
numbers. The three flagged improvements are polish on an
already-successful design, not corrections to a failing one, and do not
warrant delaying the push.
