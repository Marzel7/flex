# CURRENT_WORK.md — Patch: Phase H.1 History Snapshot via build_tick

## Goal

Fix sparse history that prevents Phase 7A–7E stability/trend features from activating.

**Do not change Phase C.** Keep `networks_release.build_version` as the *structural version* (only increments when a network changes).

Introduce a new *global time axis* for builds: `build_tick`.

### Why

Current H.1 inserts into `network_score_history` using `nr.build_version`. For unchanged networks, `nr.build_version` stays constant (often 1), so `INSERT OR IGNORE` skips inserts. This causes:

- `network_score_history` to contain only a few rows for changed networks
- Stability stays `1.00`
- Trend stays `FLAT`
- Momentum/acceleration alerts never activate for most networks

This patch ensures **every build run writes one snapshot per network**.

---

## Deliverables

1. Migration to add `build_tick` (+ optional `network_version`) and uniqueness on `(network_name, build_tick)`.
2. Replace Phase H.1 history insert with a snapshot based on `build_tick`.
3. Wire the new H.1 snapshot to run after Phase J (trend/risk bands) and before Phase L (metadata).
4. Minimal verification queries + stats output.
5. All tests must pass.

---

## Part 1 — Migration (required once)

Run via your migration system (idempotent checks in code are acceptable for SQLite).

### Add columns

```sql
ALTER TABLE network_score_history ADD COLUMN build_tick INTEGER;
ALTER TABLE network_score_history ADD COLUMN network_version INTEGER;
```

### Enforce idempotency per tick

```sql
CREATE UNIQUE INDEX IF NOT EXISTS uq_score_history_network_tick
ON network_score_history(network_name, build_tick);
```

### Optional perf indexes (recommended)

```sql
CREATE INDEX IF NOT EXISTS idx_score_history_tick
ON network_score_history(build_tick);

CREATE INDEX IF NOT EXISTS idx_score_history_network_tick_desc
ON network_score_history(network_name, build_tick DESC);
```

---

## Part 2 — Phase H.1 Patch (drop-in)

Replace the current H.1 implementation that uses `nr.build_version` as the history key with the following `build_tick` snapshot approach.

### Helpers

```python
def _ensure_column(db, table: str, col: str, col_type: str):
    cur = db.execute(f"PRAGMA table_info({table})")
    cols = {r[1] for r in cur.fetchall()}  # r[1] = name
    if col not in cols:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
```

### Phase H.1: Snapshot all networks per build

```python
def phase_h1_snapshot_score_history(db, build_profile: bool = False):
    """
    Phase H.1 — Snapshot score history for ALL networks each run using build_tick.

    Preserves Phase C structural version:
      - network_version = networks_release.build_version

    build_tick is the global time axis:
      - increments by 1 every successful build run
      - enables stability/trend calculations for all networks
    """

    # 1) Ensure schema (idempotent)
    _ensure_column(db, "network_score_history", "build_tick", "INTEGER")
    _ensure_column(db, "network_score_history", "network_version", "INTEGER")

    db.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_score_history_network_tick
        ON network_score_history(network_name, build_tick)
    """)

    # 2) Compute global build_tick (time axis)
    row = db.execute(
        "SELECT COALESCE(MAX(build_tick), 0) + 1 FROM network_score_history"
    ).fetchone()
    build_tick = int(row[0])

    # 3) Snapshot ALL networks for this build_tick
    #    Use INSERT OR REPLACE so rerunning the same tick (if it happens) is safe.
    inserted = db.execute("""
        INSERT OR REPLACE INTO network_score_history (
            network_name,
            build_tick,
            network_version,
            build_version,
            score,
            smoothed_score,
            stability_coeff,
            stability_trend,
            trend_direction,
            risk_band,
            score_version,
            score_components_json,
            computed_at
        )
        SELECT
            nr.network_name,
            ? AS build_tick,
            nr.build_version AS network_version,
            nr.build_version AS build_version,
            ns.score,
            ns.smoothed_score,
            ns.stability_coeff,
            ns.stability_trend,
            ns.trend_direction,
            ns.risk_band,
            ns.score_version,
            ns.score_components_json,
            COALESCE(ns.computed_at, CURRENT_TIMESTAMP)
        FROM networks_release nr
        LEFT JOIN network_scores ns
          ON ns.network_name = nr.network_name
    """, (build_tick,)).rowcount

    return {
        "build_tick": build_tick,
        "history_rows_written": inserted,
    }
```

> **Important:** If your actual `network_score_history` schema differs (column names), adapt the INSERT list to match. The principle is: **key = (network_name, build_tick)**.

---

## Part 3 — Wire into build_networks_release()

Call this **after Phase J** (trend/risk bands computed) and **before Phase L** (metadata).

Example:

```python
# ... Phase J complete ...
h1 = phase_h1_snapshot_score_history(db, build_profile=BUILD_PROFILE)
stats["build_tick"] = h1["build_tick"]
stats["history_rows_written"] = h1["history_rows_written"]
```

---

## Part 4 — Validation

After one successful build:

```sql
SELECT build_tick, COUNT(*) AS rows
FROM network_score_history
GROUP BY build_tick
ORDER BY build_tick DESC
LIMIT 5;
```

Expect ~`networks_release` count rows per tick (e.g., ~103).

After 3 builds, stability/trend should activate:
- stability_coeff not always 1.00
- trend_direction not always FLAT

---

## Definition of Done

- Every build run creates a new `build_tick`
- `network_score_history` gains ~N rows per build tick (N = number of networks)
- Stability/trend features become meaningful after 3 builds
- Phase C structural versioning remains unchanged
- All tests pass

---

End of Patch Instructions.
