# ARCHITECTURE_STATE.md

## UI Migration Objectives (Current State)

### Authoritative Source

-   `networks_release` is the single authoritative source for all
    network UI reads.
-   `network_membership` is canonical membership truth (used only for
    member lists).
-   UI must not compute network sizes, tags, stability, or versions.

------------------------------------------------------------------------

### UI Responsibilities

UI may only:

-   Read from `networks_release`
-   Map `network_type` → display badges
-   Map `stability_state` → lifecycle badges
-   Display `build_version`
-   Display `last_built_at`

UI must NOT:

-   Derive `network_type`
-   Derive `stability_state`
-   Increment versions
-   Join legacy tables for summary logic

------------------------------------------------------------------------

### Fields Exposed to UI

From `networks_release`:

-   network_name
-   network_size
-   network_risk_level
-   network_type
-   has_cex_funder
-   has_infra_funder
-   cex_funder_count
-   infra_funder_count
-   stability_state
-   build_version
-   last_built_at

------------------------------------------------------------------------

### Badge Mapping (Presentation Only)

Network Type:

-   cex_connected
-   infra_connected
-   cex_and_infra_connected
-   organic

Stability:

-   new
-   stable
-   growing
-   shrinking

------------------------------------------------------------------------

### Migration Safety

-   If `networks_release` exists → use new path
-   If not → fallback to legacy tables
-   Legacy tables remain for export/debug only

------------------------------------------------------------------------

### Build Contract

After any modification to `network_membership`, the system must call:

``` python
build_networks_release(db_path)
```

UI assumes both tables are always precomputed:
- `networks_release` - Network structure (size, type, stability, version)
- `network_evidence` - Aggregated coordination evidence (edges, confidence, risk)

------------------------------------------------------------------------

### network_evidence Table (Evidence Rollup)

Added in Phase F of `build_networks_release()`:

-   **Purpose**: Precomputed evidence aggregation per network
-   **Source**: `coordinated_creator_edges` joined with `network_membership`
-   **Fields**:
    -   `total_edges` - Count of coordinated creator pairs
    -   `average_confidence` - Mean confidence score (0-100)
    -   `evidence_risk_score` - Composite risk (0-100, precomputed)
    -   `evidence_version` - Incremented on data change (idempotent)
    -   `last_changed_at` - Timestamp of actual change (not update)

-   **Safety**:
    -   Separate table (no impact on networks_release)
    -   Foreign key to networks_release (referential integrity)
    -   Optional aggregation (try-except fallback)
    -   Idempotent (multiple builds produce same result)
    -   Atomic (all-or-nothing with networks_release)

-   **UI Usage**:
    -   Read evidence_risk_score for network risk ranking
    -   Display total_edges, average_confidence
    -   Show evidence_version for change tracking
    -   Left join to networks_release (all networks readable)
