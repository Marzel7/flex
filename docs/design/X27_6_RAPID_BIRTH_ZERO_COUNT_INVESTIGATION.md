# X27.6 — Rapid Birth → Launch Zero-Count Investigation

**Investigation only. No thresholds, detection logic, or production data
were changed.**

Frozen window used for every query in this investigation:
```
WINDOW_START = 1784182282   (2026-07-16 03:11:22 UTC)
FROZEN_NOW   = 1784268682   (2026-07-17 03:11:22 UTC)
```

## Acceptance criterion answer

**Conclusion C — Zero is not trustworthy.** Lifecycle evidence for the
frozen window is **stale** (the sole evidence source,
`wt_watchtower_launches`, has received zero new rows since 2026-07-14 —
over 2.5 days before the frozen window even begins). No code or join
defect was found in the classification logic itself (Conclusion D is
ruled out for the paths checked); no priority-consumption effect is
occurring (Conclusion B is ruled out — there are no raw matches to
consume). The "0" is not evidence of an absence of rapid launches; it is
evidence of an absent evidence source.

## Phase 1 — Frozen-window population

```
window_start: 1784182282
window_end:   1784268682
A. total migrated launches: 584
```

## Phase 2 — Eligibility funnel

| Step | Count |
|---|---|
| A. Total migrated launches | 584 |
| B. Launches with a matching `wt_watchtower_launches` row | **0** |
| C. Rows with genuine `create_time` | 0 |
| D. Rows with genuine birth time | N/A — no raw birth-time column is stored in `wt_watchtower_launches`; birth is only implicit in the precomputed `birth_to_launch_seconds` value written at detection time |
| E. Rows with `birth_to_launch_seconds` populated | 0 |
| F. Rows with `birth_to_launch_seconds <= 5` | 0 |
| G. Raw `RAPID_BIRTH_LAUNCH` matches | 0 |
| H. Raw matches claimed by Known Operation | 0 |
| I. Raw matches claimed by Known Infrastructure | 0 |
| J. Raw matches claimed by Repeat Creator | 0 |
| K. Exclusive `RAPID_BIRTH_LAUNCH` assignments | 0 |

**G = H + I + J + K → 0 = 0 + 0 + 0 + 0.** Trivially conserved, because
every term is zero — the funnel is internally consistent, but consistency
alone does not make the zero meaningful (see Phase 3).

Since **G = 0**, per the brief's own decision rule this investigation
continued into the evidence pipeline rather than stopping at "UI is
correct but needs metadata."

## Phase 3 — Source freshness

```
wt_watchtower_launches total rows (all-time): 43
latest recorded_at:  1784048671  -> 2026-07-14 17:04:31 UTC
latest create_time:  1784048633  -> 2026-07-14 17:03:53 UTC
rows inserted in last 1h  (relative to FROZEN_NOW): 0
rows inserted in last 6h  (relative to FROZEN_NOW): 0
rows inserted in last 24h (relative to FROZEN_NOW): 0
rows recorded WITHIN the frozen window:             0
```

```
lifecycle coverage = B / A = 0 / 584 = 0.0%
```

The most recent row in the platform's ONLY source of trustworthy
birth→launch timing is **more than 2.5 days older** than the start of the
frozen window. Per the brief's own instruction: *"A zero raw match is not
meaningful when lifecycle coverage is zero or stale."* Coverage here is
both zero and stale simultaneously.

## Phase 4 — Join integrity

Checked explicitly for every listed failure mode:

| Reason | Finding |
|---|---|
| No `wt_watchtower_launches` row | **584 of 584 (100%)** — this is the entire explanation |
| Mint mismatch (case/whitespace) | None found — exact-string overlap and normalized-string overlap both measured 0; mint formats match the expected Pump.fun mint shape on both sides |
| Duplicate mint rows | None — `wt_watchtower_launches` has 43 distinct mints, zero duplicates |
| Creator-based join instead of mint-based | Not applicable — the join is correctly mint-based; confirmed by direct set-intersection on `mint` |
| Timestamp outside window | Not applicable — the disjoint is a population disjoint (zero overlapping mints at all), not a timestamp-filtering artifact |
| Null `create_time`/birth/`birth_to_launch_seconds` | Not the cause here — the 43 existing rows mostly have these fields populated; the issue is that none of the 43 rows' mints appear in today's 584-mint frozen population at all |
| Row excluded by provenance/trust gate | No such gate exists in `rapid_birth_launch_lookup()` beyond requiring `create_time IS NOT NULL AND birth_to_launch_seconds IS NOT NULL` — not the limiting factor here |
| Migration recorded after lifecycle lookup | Ruled out — the lifecycle table simply has no rows from this period at all, regardless of ordering |

**Conclusion: 100% of the missing-evidence reason is "no
`wt_watchtower_launches` row," with no secondary join defect
contributing.**

## Phase 5 — Independent recomputation

`wt_watchtower_launches` has no raw birth-timestamp column; `birth_to_launch_seconds`
is precomputed at write time from `create_time - wrap_close_time`
(`src/core/ws_cascade.py:3699-3717`). An independent cross-check is only
possible for creators that also have a `wt_wrap_close_candidates` row
(different table, separately populated):

```
total rows with create_time + birth_to_launch_seconds: 41
no recomputable independent source (funded_at missing): 38
exact matches (computed == stored): 3
small rounding diffs (<=2s): 0
negative computed durations: 0
large disagreements (>2s): 0
```

All 3 independently-recomputable rows matched exactly
(`computed - stored == 0`). No units defect, no sign inversion, no
rounding error was found in the rows that could be checked. This is not a
proof that all 41 stored values are correct — 38 of 41 have no second
source in this database to check against — but it rules out a systematic
unit/sign defect in the checked subset. **No corrections were written.**

## Phase 6 — Timing distribution (all 41 historical rows, all-time — none fall in the frozen window)

| Bucket | Count |
|---|---|
| ≤1s | 19 |
| >1–2s | 16 |
| >2–5s | 5 |
| >5–10s | 0 |
| >10–30s | 0 |
| >30s | 1 |

```
min: 0   median: 2   p90: 3   max: 98
```

No values sit narrowly above the 5-second threshold — the distribution is
heavily concentrated at ≤2s with a single 98s outlier. This rules out a
scenario where a unit-conversion or off-by-one defect is pushing
genuinely-rapid launches just past the cutoff.

## Phase 7 — Pipeline tracing

**No genuine Rapid-Birth candidate exists in today's frozen-window data to
trace** — stated explicitly per the brief's instruction, rather than
selecting an arbitrary launch. The 3 most recently migrated launches in
the frozen window were traced anyway, to show what the pipeline correctly
does in the absence of evidence:

| mint | creator | migration ts | `wt_watchtower_launches` row? | create_time | stored `birth_to_launch_seconds` | raw match? | final bucket |
|---|---|---|---|---|---|---|---|
| `DfkcGHVzy...oppump` | `FriSzj35a...G5MGmTm` | 1784268591 | **No** | — | — | N/A (no row) | `UNKNOWN_INFRASTRUCTURE` |
| `CGqQTNFVb...dqXppump` | `2LoiEvv4Y...juuvMHkGm` | 1784268539 | **No** | — | — | N/A (no row) | `LINEAGE_GAP` |
| `7C6fi7g32...KYBMa9YYKppump` | `4UKLdTBiz...UwmAdd4UX1sJKeUohSiP` | 1784268409 | **No** | — | — | N/A (no row) | `INSUFFICIENT_EVIDENCE` |

Each launch correctly falls to an attribution-derived bucket because
`RAPID_BIRTH_LAUNCH` was never even evaluated for it (absent evidence,
never inferred) — this is the pipeline behaving exactly as designed,
given the evidence gap identified in Phase 3.

## Phase 8 — UI semantics recommendation

The single exclusive bucket count must remain the only headline number —
no second dashboard, no change to priority order. Recommended
**metadata-only** enhancement (not implemented this sprint, since no
defect requires urgent remediation and the brief scopes this sprint as
investigation-first):

```
Rapid Birth → Launch: 0
  ├─ 0 raw matches (launches whose lifecycle timing qualifies)
  ├─ 0 claimed by a higher-priority bucket first
  └─ 0 of 584 launches even had lifecycle evidence available (0.0% coverage)
```

All three numbers are subordinate to the single exclusive count already
shown; none introduces a second overlapping total. This directly answers
the brief's Phase 8 question ("0 exclusive / N raw-claimed / M eligible")
using the existing bucket-metadata dict already returned by
`build_pipeline_health()` — `coverage_pct` already exists there (X27.4);
what's missing is exposing the raw-match count and the
higher-priority-claimed count alongside it. This is a presentation
enhancement, not a defect fix, and is proposed for a future,
separately-scoped sprint per the constraint against unscoped changes.

## Root cause

**The `wt_watchtower_launches` writer pipeline (`src/core/ws_cascade.py`)
has not produced a new row since 2026-07-14 17:04:31 UTC** — a
pre-existing condition, already flagged in a prior session's
`X27_4_ZERO_RAPID_BIRTH_LAUNCH_INVESTIGATION.md` (53+ hour gap measured
2026-07-16; now grown to 2.5+ days as of this frozen window). This
investigation independently reproduces and reconfirms that finding with a
fixed, reproducible window and a complete funnel, rather than a rolling
"now minus 24h" query. No new regression was introduced by X27.5's merge
of behavioural archetypes into the unified Investigation Queue — the
classification logic itself is correct; it has nothing to classify.

## Recommendation

1. **Immediate**: this is the same `ws_cascade` process-health issue
   already flagged for engineering triage in the prior session (skipped
   sweep cycles, stuck WS subscriptions). No new triage action is added by
   this investigation beyond reconfirming it with a frozen, reproducible
   measurement.
2. **Future, separately-scoped sprint**: expose raw-match /
   higher-priority-claimed / lifecycle-coverage counts in the existing
   bucket metadata (Phase 8), so a future zero is immediately
   distinguishable as "genuinely zero raw matches" vs. "stale evidence
   source" without needing to re-run this investigation each time.

## Confirmation

No thresholds, detection logic, priority order, attribution outcomes,
walkback logic, or launch detection were changed. No database mutation
occurred (verified via SHA-256 hash comparison in the accompanying test
suite). No second dashboard was added.
