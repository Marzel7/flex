# FLEX Prediction Decision Layer

## Current implementation audit

- Route: `/predictions` in `src/core/main.py`
- Template: `templates/predictions.html`
- Main APIs:
  - `/api/predictions/live`
  - `/api/predictions/token/<mint>`
  - `/api/predictions/summary`
  - `/api/predictions/accuracy`
  - `/api/predictions/buy-sim`
- Primary tables:
  - `token_prediction_scores`
  - `token_prediction_events`
  - `token_prediction_outcomes`
  - `token_analysis`
- Existing prediction fields:
  - label, risk level, score, confidence, status, reason codes
  - creator/network/funding/outcome/liquidation sub-scores
  - explanation JSON
- Common labels/statuses observed:
  - labels: `LIKELY_DUMP`, `HIGH_RISK`, `WATCH`, fresh/unlinked variants, pending variants
  - statuses: `COMPLETE`, `NETWORK_RISK_TOKEN`, `PENDING_FUNDING`, `INSUFFICIENT_HISTORY`, `PENDING_RISK_SCORE`, `NO_FUNDING_FOUND`, `PENDING_CREATOR`

## New decision model

`prediction_decision_context` materializes the action layer on top of prediction output.

Suggested actions:
- `IGNORE`: high/critical risk, likely dump, liquidity-removal, or other blocking flags
- `WATCH`: incomplete context or mixed evidence
- `AUTO_ELIGIBLE`: low-risk call with adequate but not exceptional evidence
- `SIMULATE`: borderline/watch call with supportive context
- `ALLOCATE`: low-risk + high confidence + multiple independent quality supports

Inputs remain conceptually separate:
- Historical Performance = context
- Simulation PnL = feedback/outcome
- Prediction = current decision

## Materialized table

`prediction_decision_context`

Stores:
- suggested action + reason
- blocking risk flags
- positive evidence flags
- top explanation reasons
- creator/network/funder historical context
- creator/network simulation-quality context
- coordinator exposure
- liquidity removal flag
- simulation status/PnL fields
- historical-context label

Refreshes after historical-performance analyzers via `PredictionDecisionContextAnalyzer` in `scripts/run_graph_analyzers.py`.

## UI redesign

`/predictions` is now a compact queue with:
- queue modes: Action Required, Watchlist, Rejected / Ignore, Already Simulated, Pending Funding, All
- filters: suggested action, label, risk, confidence, creator quality, ecosystem quality, funding signal, liquidity risk, simulation status, time
- action-first table rows
- evidence chips instead of raw signal dumps
- drawer sections:
  - Decision
  - Why this prediction exists
  - Evidence Summary
  - Classification at Migration
  - Creator History
  - Outcome
  - Signals / event history

## Validation examples

- WATCH example → `WATCH`
- HIGH_RISK + `NETWORK_RISK_TOKEN` example → `IGNORE`
- LIKELY_DUMP example → `IGNORE`
- PENDING_FUNDING example → `WATCH`

## Remaining gaps

- `ALLOCATE` is rare/absent until historical-quality coverage improves enough to support a high-conviction positive path.
- Funder historical performance is still thin if the funder historical analyzer has not successfully materialized.
- Liquidity evidence currently uses available removal flags; richer live liquidity-health summaries can be folded in later.
- Drill-throughs exist, but canonical entity routes should replace the older destination pages in the next IA pass.
