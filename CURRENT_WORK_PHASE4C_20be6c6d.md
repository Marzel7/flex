# CURRENT_WORK.md --- Phase 4C (Monitoring + Drift Detection)

## Objective

Make the system proactive by tracking network score changes over time
and surfacing "drift" signals.

Deliver: 1) Persisted score history per build 2) Detection rules for
significant changes (spikes, new high-risk, type flips) 3) A monitoring
view/query for the UI (read-only)

No live scoring in UI. Scoring remains build-time only.

------------------------------------------------------------------------

## New Tables (SQLite)

### 1) network_score_history

Store one row per network per build.

Fields: - network_name (TEXT) - build_version (INTEGER) -- from
networks_release - score (INTEGER) - score_version (INTEGER) -
components_json (TEXT) -- from network_scores.score_components_json -
computed_at (TIMESTAMP)

Primary key: - (network_name, build_version)

Indexes: - idx_nsh_computed_at - idx_nsh_score - idx_nsh_build_version

### 2) network_alerts

Store derived alerts for monitoring.

Fields: - alert_id (INTEGER PRIMARY KEY AUTOINCREMENT) - network_name
(TEXT) - build_version (INTEGER) - alert_type (TEXT) -- SCORE_SPIKE,
NEW_HIGH_RISK, TYPE_FLIP, LIFECYCLE_FLIP - severity (TEXT) --
low/medium/high - message (TEXT) - details_json (TEXT) - created_at
(TIMESTAMP)

Indexes: - idx_alerts_created_at - idx_alerts_type - idx_alerts_severity

------------------------------------------------------------------------

## Build Pipeline Integration

Add Phase H after scoring (Phase G) in build_networks_release.py:

Steps: 1) Insert current scores into network_score_history for this
build_version (idempotent) 2) Compare current vs previous build_version
and generate alerts 3) Insert alerts into network_alerts (idempotent for
same build_version)

All inside existing transaction pattern.

------------------------------------------------------------------------

## Alert Rules (v1)

Let: - prev_score = score from previous build_version (if exists) -
curr_score = current score - delta = curr_score - prev_score

### A) SCORE_SPIKE

Trigger if delta \>= +20 Severity: - high if delta \>= 35 - medium if
20--34 Message includes prev_score, curr_score, delta.

### B) NEW_HIGH_RISK

Trigger if prev_score is NULL and curr_score \>= 70 Severity: high

### C) TYPE_FLIP

Trigger if networks_release.network_type changed since previous build.
Severity: - high if flips to cex_and_infra_connected - medium if flips
to infra_connected or cex_connected - low otherwise Details include
old_type, new_type.

### D) LIFECYCLE_FLIP

Trigger if stability_state changed AND curr_score \>= 50 Severity: -
medium if new state is growing - low otherwise Details include
old_state, new_state.

Avoid duplicate alerts for same (network_name, build_version,
alert_type).

------------------------------------------------------------------------

## Monitoring Queries (for UI)

### 1) Latest alerts

SELECT network_name, alert_type, severity, message, created_at FROM
network_alerts ORDER BY created_at DESC LIMIT 100;

### 2) Top risky networks (current)

SELECT ns.network_name, ns.score FROM network_scores ns ORDER BY
ns.score DESC LIMIT 50;

### 3) Biggest score movers (last build)

SELECT h.network_name, (h.score - p.score) AS delta, p.score AS
prev_score, h.score AS curr_score FROM network_score_history h JOIN
network_score_history p ON p.network_name = h.network_name AND
p.build_version = h.build_version - 1 WHERE h.build_version = (SELECT
MAX(build_version) FROM network_score_history) ORDER BY delta DESC LIMIT
50;

------------------------------------------------------------------------

## Minimal UI Integration (Optional this phase)

Add a new page or section (if trivial): - /network-monitoring (HTML) OR
an API endpoint /api/network-alerts

But if UI changes are large, deliver DB + queries only and defer UI.

------------------------------------------------------------------------

## Constraints

-   No changes to scoring model logic (Phase 4)
-   No live computation in UI endpoints
-   Keep schema changes minimal and additive
-   Must be idempotent (re-running build does not duplicate
    history/alerts)

------------------------------------------------------------------------

## Definition of Done

-   network_score_history populated on build
-   network_alerts populated with correct rules
-   Re-running build does not duplicate alerts/history
-   Monitoring queries return expected results
-   Ready for UI surfacing

------------------------------------------------------------------------

End of Instructions.
