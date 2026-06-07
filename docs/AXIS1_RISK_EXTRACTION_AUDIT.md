# Axis 1 — Pure Risk Extraction: Audit & Implementation

**Status:** audit + parallel derivation built. No production classification changed. No migration.
**Date:** 2026-06-07
**Companion modules:** `src/analysis/creator_state_axis.py` (Axis 2), `src/analysis/attribution_axis.py` (Axis 3).

The classification separation has three axes. Axes 2 and 3 are built. This completes
Axis 1 — **pure severity** — extracted from the two legacy `risk_level` producers as a
read-only parallel derivation.

---

## Task 1 — Current Risk Logic Report

`risk_level` is produced in **two independent places** writing **two different columns**.
This is the first structural finding: there is no single risk function.

### Path A — Creator risk (`creator_risk_scores.risk_level`)

Producer: `src/core/risk_scoring_builder.py`

```
_final_score()  (line 848)            _risk_level(final_score)  (line 859)
  0.35 * operator_score                 >= 80  -> CRITICAL
  0.25 * outcome_score                  >= 60  -> HIGH
  0.25 * g_score                        >= 40  -> MEDIUM
  0.15 * liquidation_score              >= 20  -> WATCH      <-- contamination
  (critical_floor -> max(.,80))          < 20  -> LOW
```

This path is **almost pure** already: severity is a clean monotonic function of
`final_score`. The *only* contaminant is the `WATCH` band at 20–39, which is a
creator-state concept (see Task 2) wearing a severity label.

Live distribution (`creator_risk_scores`, 55,485 rows):

| risk_level | n |
|---|---|
| LOW | 53,074 |
| WATCH | 972 |
| CRITICAL | 940 |
| MEDIUM | 396 |
| HIGH | 103 |

### Path B — Token prediction risk (`token_prediction_scores.risk_level`)

Producer: `src/core/token_prediction_builder.py`

```
prediction_score  (TokenScore.prediction_score, line 150)
  creator_w * creator_score   (0.55 if no network else 0.40)
  network_w * network_score   (0.00 if no network else 0.15)
  0.25 * funding_score
  0.15 * outcome_history_score
  0.05 * liquidation_score
```

then `_risk_level_for_score()` (line 1245):

```
prediction_status == NETWORK_RISK_TOKEN          -> "HIGH"        (status override)
prediction_status != COMPLETE                     -> None          (no score yet)
label == FRESH_UNLINKED_EVENT
   OR (creator_was_fresh and not network_name)    -> "WATCH"       <-- contamination
else -> _risk_level(prediction_score, label,                       (module fn, line 56)
                    creator_has_liq, network_has_liq)
```

and the module-level `_risk_level()` (line 56) adds **label-driven** and
**liquidation-history** overrides on top of the score:

```
creator_has_liq                 -> >=80 CRITICAL else HIGH         (floor by history)
network_has_liq                 -> >=80 CRITICAL / >=60 HIGH / else MEDIUM
label in (LIKELY_DUMP, SELF_FUNDED_TOKEN, LIQUIDATION_RISK,
          NETWORK_RISK_TOKEN)   -> >=80 CRITICAL / >=60 HIGH / else MEDIUM
label == FRESH_UNLINKED_EVENT   -> >=60 HIGH / >=40 MEDIUM / else WATCH   <-- contamination
label in (CRITICAL_RISK, SERIAL_OPERATOR_TOKEN) -> CRITICAL
score >= 80 / 60 / 40 / 20 / else -> CRITICAL / HIGH / MEDIUM / WATCH / LOW   <-- WATCH band
```

Live distribution (`token_prediction_scores`, 224,380 rows):

| risk_level | n |
|---|---|
| *(empty — not COMPLETE)* | 167,659 |
| HIGH | 24,652 |
| MEDIUM | 18,614 |
| LOW | 7,073 |
| WATCH | 6,320 |
| CRITICAL | 62 |

The 167,659 blank rows are predictions that never reached `COMPLETE`. In Axis 1 these
become an explicit **`UNKNOWN`** value rather than an empty string.

### Source fields, weights, thresholds — summary

| Field | Path A weight | Path B weight | Legitimate risk input? |
|---|---|---|---|
| operator_score / creator_score | 0.35 | 0.40–0.55 | yes |
| outcome_score / outcome_history_score | 0.25 | 0.15 | yes |
| g_score | 0.25 | — | yes |
| funding_score | — | 0.25 | yes |
| network_score | — | 0.00–0.15 | yes |
| liquidation_score | 0.15 | 0.05 | yes |
| **WATCH band (score 20–39)** | injected | injected | **NO — creator state** |
| **creator_was_fresh / FRESH_UNLINKED_EVENT** | — | forces WATCH | **NO — creator state** |
| creator_has_liq / network_has_liq floor | — | floors severity | borderline (see Task 2) |

---

## Task 2 — Keep / Remove / Move

| Logic | Verdict | Rationale |
|---|---|---|
| `final_score` / `prediction_score` numeric weighting | **KEEP** | This *is* severity. The weighted blend of operator/outcome/funding/liquidation/network is exactly "how dangerous is this token". |
| Score → band thresholds (80/60/40) | **KEEP** | Pure severity cutpoints. |
| `creator_has_liq` / `network_has_liq` severity floor | **KEEP** | Liquidation *history* is an outcome signal, not a state/identity signal — it answers "how dangerous", so it stays. (It is also already folded into the scores; the floor just guarantees it can't be diluted away.) |
| Label overrides (LIKELY_DUMP, SELF_FUNDED_TOKEN, LIQUIDATION_RISK, CRITICAL_RISK) | **KEEP (as risk_basis annotations)** | These describe *behaviour/severity* and legitimately raise risk. Axis 1 keeps their effect but records them in `risk_basis` instead of mutating a label field. |
| **`WATCH` band (score 20–39 → WATCH)** | **MOVE → Axis 2** | "Do we know enough yet?" is creator-state, not severity. A 20–39 score is genuinely **LOW** severity; the uncertainty belongs in `creator_state ∈ {FRESH,…}`. |
| **`creator_was_fresh` → WATCH** | **MOVE → Axis 2** | Freshness is the definition of Axis 2 `FRESH`. Risk should be derived from the score the fresh creator actually has, not overwritten to WATCH. |
| **`FRESH_UNLINKED_EVENT` label forcing WATCH** | **MOVE → Axis 2 (+ Axis 3)** | "Fresh" → state=FRESH; "unlinked" → attribution=NONE. Neither is severity. |
| `SERIAL_OPERATOR_TOKEN` → CRITICAL | **SPLIT** | The *serial* fact is Axis 2 (SERIAL); the *risk* it implies stays in Axis 1 via the creator/operator score that already encodes the serial-dumper history. Axis 1 must not read "is serial" directly — only the score. |
| `NETWORK_RISK_TOKEN` status → HIGH | **KEEP** | Network co-offending is an outcome/severity signal. |
| WATCHTOWER / lineage / operations / shared-funder promotion | **NONE PRESENT in risk path** | Confirmed: none of these touch `_risk_level`. Good — Axis 1 is already clean of attribution. |

**Net contamination to remove from Axis 1: exactly one concept — `WATCH` in all its
forms** (score-band WATCH, `creator_was_fresh` WATCH, `FRESH_UNLINKED_EVENT` WATCH).
Everything else in the risk path is legitimately severity.

---

## Task 3 — Proposed Pure Axis 1 Derivation

Output schema:

```python
{
  "risk":       "UNKNOWN|LOW|MEDIUM|HIGH|CRITICAL",
  "risk_score": float,        # the underlying 0-100 severity score (or None)
  "risk_basis": {             # why — pure-severity inputs only
     "source": "creator|prediction",
     "score": float,
     "floors": [ ... ],       # e.g. "creator_liq_history", "network_liq_history"
     "escalators": [ ... ],   # e.g. "LIKELY_DUMP", "NETWORK_RISK_TOKEN"
     "band": "..."
  }
}
```

Rules (severity only — see module docstring for the authoritative list):

1. No score / not COMPLETE → `UNKNOWN` (replaces the legacy empty string and the
   illegitimate WATCH-for-fresh).
2. Band by score: `>=80 CRITICAL`, `>=60 HIGH`, `>=40 MEDIUM`, `<40 LOW`.
   **There is no WATCH band** — 20–39 is LOW severity.
3. Liquidation-history floors and behaviour-label escalators may *raise* the band
   (never lower it), and are recorded in `risk_basis`.
4. Axis 1 reads **only** numeric severity inputs + outcome-behaviour labels. It never
   reads freshness, token count, creator state, attribution, lineage, or operations.

---

## Validation Matrix

See `/api/watchtower/risk-axis` (live cross-tabs). Headline findings recorded in
`AXIS1_VALIDATION_MATRIX.md` after the endpoint run.

---

## Retirement — can legacy `risk_level` be retired?

**Recommendation: YES, in phases. The legacy `risk_level` column can eventually be
retired, but only after the WATCH consumers are migrated to Axis 2.** It is not safe to
drop today because code still reads WATCH as if it were a risk level.

### Why retirement is now viable

Validation (see `AXIS1_VALIDATION_MATRIX.md`) shows Axis 1 reproduces the *current*
legacy `_risk_level()` on **every COMPLETE row**. The only differences are intentional:
WATCH leaves the severity scale, and the legacy empty string becomes UNKNOWN. There is
no severity information in legacy `risk_level` that Axis 1 does not carry — confirmed by
running the current legacy function directly against the apparent-diff rows.

### Blockers that must be cleared first (live WATCH consumers)

Legacy WATCH is still *read* in production. These must move to Axis 2 (`creator_state`)
or Axis 1 (`risk`) before the column is dropped:

| Consumer | File:line | Reads | Migrate to |
|---|---|---|---|
| Auto-sim-buy gate | `token_prediction_builder.py:305` | `risk_level in {"LOW","WATCH"}` | `risk == LOW` OR `creator_state == FRESH` |
| Watch-outbound scan scheduler | `token_prediction_builder.py:1211` | `== "WATCH"` | `creator_state == FRESH` (Axis 2) |
| Predictions UI label | `templates/predictions.html` | renders WATCH as "WATCHLIST" | render `creator_state` directly (already display-only) |
| Predictions UI sort | `templates/predictions.html:383` | `RISK_ORDER` includes 'WATCH' | drop WATCH from risk sort; add a state column |

### Phased plan (no migration performed here)

1. **Parallel (done):** Axis 1/2/3 derivations + validation endpoints. Read-only. ✅
2. **Dual-write:** when scoring runs, populate new `risk` / `creator_state` /
   `attribution` columns alongside legacy `risk_level`. Verify equality on COMPLETE rows
   and WATCH→FRESH mapping over a full rescore cycle.
3. **Cut consumers over:** repoint the three consumers above to the new columns.
4. **Stop writing legacy `risk_level`;** keep it readable for one release as a fallback.
5. **Drop the column** once no reader references it.

### One-time data note

5,703 token rows are stored as HIGH but the current logic scores them MEDIUM (older
scoring version). A full rescore corrects this independently of the axis work — worth
running before dual-write so the equality check in phase 2 is clean.

---

## Files delivered

| Deliverable | Path |
|---|---|
| Risk audit report (this doc) | `docs/AXIS1_RISK_EXTRACTION_AUDIT.md` |
| Validation matrix | `docs/AXIS1_VALIDATION_MATRIX.md` |
| Axis 1 derivation (read-only) | `src/analysis/risk_axis.py` |
| Validation endpoint | `/api/watchtower/risk-axis` (`src/core/main.py`) |

No production classification changed. No migration performed. No existing logic replaced.
