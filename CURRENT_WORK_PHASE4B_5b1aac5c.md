# CURRENT_WORK.md --- Phase 4B (UI Integration for Network Scoring)

## Objective

Integrate precomputed network scoring (Phase 4) into UI views.

This phase is DISPLAY-ONLY.

No scoring logic is allowed in UI. No schema changes. No modifications
to legacy routing.

------------------------------------------------------------------------

## Scope

Integrate score visibility into:

1)  /networks (network list dashboard)
2)  /creator-network/`<network_name>`{=html} (detail page)

------------------------------------------------------------------------

## Rules (Strict)

-   Scores must be retrieved using get_network_score(network_name)
-   No computation in route handlers
-   No computation in templates
-   No modification to Phase 2C routing
-   No changes to legacy fallback logic
-   Do not alter response schemas for API endpoints

This is purely additive UI display.

------------------------------------------------------------------------

## UI Requirements

### 1) Network List View (/networks)

Enhance each network row/card with:

-   Score value (0--100)
-   Score badge class:
    -   0--29 → low (green)
    -   30--69 → medium (yellow)
    -   70+ → high (red)

Minimal implementation:

-   Add score to template context via get_network_score()
-   Display small badge next to network name
-   No layout redesign required

Optional: - Allow sorting by score DESC (only if trivial) - Do NOT
change query logic significantly

------------------------------------------------------------------------

### 2) Network Detail Page (/creator-network/`<network_name>`{=html})

Add a "Risk Score" section:

Display:

Risk Score: XX / 100 (badge color) Breakdown: - Connectivity Risk: X /
40 - Lifecycle Risk: X / 25 - Evidence Risk: X / 35

Use components from score_components_json. Do not compute values
manually.

Section should render only if score exists.

------------------------------------------------------------------------

## Minimal Template Changes

Add CSS classes:

.badge-low → green .badge-medium → amber .badge-high → red

Use existing styling if available.

------------------------------------------------------------------------

## Performance Constraint

-   Only one additional query per network detail page
-   For list page, avoid N+1 queries if possible:
    -   Either batch prefetch scores
    -   OR accept minimal overhead if network count small

Prefer batched query: SELECT network_name, score FROM network_scores
WHERE network_name IN (...)

------------------------------------------------------------------------

## Definition of Done

-   Score visible on /networks
-   Score visible on /creator-network/`<name>`{=html}
-   No scoring logic in UI
-   No changes to legacy code
-   No change in existing behavior
-   Clean, minimal diff

------------------------------------------------------------------------

End of Instructions.
