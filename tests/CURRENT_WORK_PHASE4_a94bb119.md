# CURRENT_WORK.md — Phase 4 (Network Scoring + Monitoring Foundation)

## Objective

Introduce a first version of **network scoring** built from already-precomputed fields.
This phase MUST NOT introduce any live computation inside UI endpoints.

Deliver:
1) A deterministic `network_score` (0–100) per network
2) A clear breakdown of contributing factors
3) A monitoring view for “recently changed” / “high risk” networks

---

## Inputs (Already Available)

Use only existing/precomputed tables/fields:

From `networks_release`:
- network_name
- network_size
- network_type (organic / cex_connected / infra_connected / cex_and_infra_connected)
- stability_state (new / stable / growing / shrinking)
- build_version
- last_built_at
- network_risk_level (if present)

From `network_evidence` (if present):
- total_edges
- high_confidence_edges
- medium_confidence_edges
- low_confidence_edges
- average_confidence
- evidence_risk_score (if present)
- evidence_version
- last_changed_at (if present)

---

## Scoring Model v1 (Simple, Transparent)

Create a score 0–100 with additive points (cap at 100).

### A) Connectivity Risk (0–40)
- organic: +0
- cex_connected: +10
- infra_connected: +15
- cex_and_infra_connected: +25

### B) Lifecycle Risk (0–25)
- stable: +0
- new: +10
- growing: +20
- shrinking: +5

### C) Evidence Risk (0–35)
If `network_evidence` exists:
- Normalize by total_edges (avoid division by zero)
- Example:
  - + (high_confidence_edges / max(total_edges,1)) * 35

If evidence table not available:
- Evidence component = 0

Final:
score = min(100, A + B + C)

Also compute `score_version` to track scoring rule updates (start at 1).

---

## Schema Changes

### Preferred: Separate Table (Minimal Impact)
Create `network_scores`:

- network_name (PK)
- score (INTEGER)
- score_version (INTEGER default 1)
- score_components_json (TEXT JSON)
- computed_at (TIMESTAMP)

Indexes:
- idx_network_scores_score
- idx_network_scores_computed_at

Alternative:
Add to `networks_release`:
- network_score
- score_version
- score_components_json
- score_updated_at

Choose the minimal change compatible with current codebase.

---

## Build Integration

Scoring MUST run inside the existing build pipeline after:
- networks_release built
- network_evidence built (if used)

Implement in `build_networks_release.py` as a final step (e.g., Phase G):
- Recompute scores idempotently
- Update/replace rows deterministically
- Wrap in same transaction pattern

---

## UI Integration (Read Only)

Update network list views to optionally include:
- score
- score badge (e.g., 0–29 low, 30–69 medium, 70+ high)
- “Recently changed networks” using build_version or last_changed_at

Do NOT compute score in UI.

---

## Monitoring Views / Queries

Add query helpers for:

1) High risk networks:
SELECT ns.network_name, ns.score
FROM network_scores ns
ORDER BY ns.score DESC
LIMIT 50;

2) Recently changed (by build_version or last_built_at):
SELECT nr.network_name, nr.build_version, nr.last_built_at
FROM networks_release nr
ORDER BY nr.last_built_at DESC
LIMIT 50;

---

## Definition of Done

- A reproducible score exists for each network
- Score computed in build pipeline, not UI
- Score components stored for explainability
- UI can display score without extra computation
- Minimal schema impact, easy to evolve

---

End of Instructions.
