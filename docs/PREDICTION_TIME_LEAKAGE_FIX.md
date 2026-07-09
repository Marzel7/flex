# Prediction-Time Leakage Fix

## A. Leakage audit

The leakage path was introduced after prediction creation:
- `PredictionDecisionContextAnalyzer` joined current `creator_profitability`, `network_profitability`, historical-performance tables, and same-token `trade_simulations`.
- `/api/predictions/live` exposed those live rollups as decision evidence.
- `templates/predictions.html` rendered them as creator/network evidence chips and action reasons.

The original `token_prediction_scores` row was closer to point-in-time truth because `reason_codes` and `explanation_json` are written when the prediction is created.

## B. Snapshot schema

`prediction_decision_context` now stores immutable fields:
- `prediction_id`, `predicted_at`
- `creator_quality_at_prediction`
- `creator_history_count_at_prediction`
- `ecosystem_quality_at_prediction`
- `network_size_at_prediction`
- `coordinator_exposure_at_prediction`
- `liquidity_health_at_prediction`
- `risk_state_at_prediction`
- `funding_context_at_prediction`
- `prediction_features_json`
- `evidence_summary_json`
- `confidence_at_prediction`
- immutable decision/action fields

## C/D. Pipeline changes

- `TokenPredictionBuilder._write_scores()` now calls `insert_prediction_snapshots()` immediately after score creation.
- Snapshots use `INSERT OR IGNORE`; later rescoring cannot rewrite an old snapshot.
- `PredictionDecisionContextAnalyzer` now only backfills missing legacy snapshots from already-frozen `token_prediction_scores` fields. It no longer joins current profitability/history tables.
- Legacy reconstructed rows are labeled `snapshot_source = legacy_reconstructed_from_prediction_score`.

## E. UI redesign

`/predictions` now separates:
- **Decision-Time Evidence** — frozen snapshot only
- **Post-Prediction Feedback** — current creator quality, current historical score, simulation result, and resolved token outcome

Fresh creators display:
- `NO_PRIOR_CREATOR_HISTORY`
- “no prior creator history; prediction based on live token/funding signals only”

## F. Example

For `8Kpqq...`:
- Decision-time snapshot now says `NO_PRIOR_CREATOR_HISTORY`, count `0`.
- Its later successful simulated ROI appears only in post-prediction feedback.

## G. Validation

Added `tests/test_prediction_decision_context.py`:
- fresh creator has no prior history
- creator with frozen prior launches retains prior history
- no simulation-quality evidence can appear in decision-time snapshot

## H. Remaining edge cases

- Old predictions can only be reconstructed from fields originally persisted in `token_prediction_scores`; unavailable historical point-in-time fields are intentionally `UNKNOWN` rather than invented.
- Full network-size-at-prediction and liquidity-health-at-prediction require capturing those fields at prediction creation going forward.
- Existing legacy snapshots are replayable from stored prediction rows, but they are marked as reconstructed rather than native creation-time snapshots.
