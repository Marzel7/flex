"""
Operations OS — Lifecycle Adapters.

Each adapter translates one operation's raw internal state into a
platform-standard LifecycleSnapshot.

Rules:
- Adapters are pure read-only DB queries.  Never write.
- Adapters must not import from the operation's detection code.
- No adapter may reference WATCHTOWER-specific terms in the snapshot text
  it returns — only generic lifecycle vocabulary.
- If the DB is unavailable, return a DEGRADED snapshot (never raise).
- Confidence is optional; return None when not reliably computable.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Optional

from src.ops.lifecycle import (
    IDLE, OBSERVING, ARMED, ACTIVE, COMPLETED,
    LifecycleSnapshot,
)

# ── DB path helpers (imported lazily to keep this module side-effect-free) ───

def _ops_db_path() -> str:
    from src.core.db import OPS_DB_PATH
    return str(OPS_DB_PATH)

def _hot_db_path() -> str:
    from src.core.db import DB_PATH
    return str(DB_PATH)

def _ro(path: str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3)

def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
    )

def _count(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    try:
        row = conn.execute(sql, params).fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    except Exception:
        return 0


# ── WATCHTOWER adapter ───────────────────────────────────────────────────────
#
# Internal state → Platform lifecycle mapping:
#
#   wt_active_subprov_sessions (state=ACTIVE)     → OBSERVING
#     (watching subprov sessions, waiting for creator wrap-close)
#   wt_candidate_websocket_watches (state=WATCHING)→ OBSERVING
#     (creator candidates being watched for token create)
#   wt_ops_v2_armed (state=ARMED)                 → ARMED
#   wt_watchtower_launches (created in last 24h)  → ACTIVE (recent)
#   wt_watchtower_launches migrated (last 24h)    → COMPLETED (recent)

def watchtower_lifecycle() -> LifecycleSnapshot:
    now = int(time.time())
    try:
        ops_path = _ops_db_path()
        conn = _ro(ops_path)
        with conn:
            # OBSERVING: active subprov sessions (waiting for creator)
            observing_sessions = _count(
                conn,
                "SELECT COUNT(*) FROM wt_active_subprov_sessions WHERE state='ACTIVE'"
            ) if _table_exists(conn, "wt_active_subprov_sessions") else 0

            # OBSERVING: creator candidates being watched for CREATE
            watching_candidates = _count(
                conn,
                "SELECT COUNT(*) FROM wt_candidate_websocket_watches "
                "WHERE state='WATCHING' AND expires_at > ?",
                (now,)
            ) if _table_exists(conn, "wt_candidate_websocket_watches") else 0

            observing = observing_sessions + watching_candidates

            # ARMED: creators waiting for pump.fun CREATE
            armed = _count(
                conn,
                "SELECT COUNT(*) FROM wt_ops_v2_armed WHERE state='ARMED'"
            ) if _table_exists(conn, "wt_ops_v2_armed") else 0

            # ACTIVE: launches detected in last 30 minutes (pre-migration window)
            active = _count(
                conn,
                "SELECT COUNT(*) FROM wt_watchtower_launches "
                "WHERE create_time > ? AND migrated_at IS NULL",
                (now - 1800,)
            ) if _table_exists(conn, "wt_watchtower_launches") else 0

            # COMPLETED: migrated today
            completed_today = _count(
                conn,
                "SELECT COUNT(*) FROM wt_watchtower_launches "
                "WHERE migrated_at > ?",
                (now - 86400,)
            ) if _table_exists(conn, "wt_watchtower_launches") else 0

            # Last transition: most recent armed_at or create_time
            last_arm_ts: Optional[int] = None
            if _table_exists(conn, "wt_ops_v2_armed"):
                row = conn.execute(
                    "SELECT MAX(armed_at) FROM wt_ops_v2_armed WHERE state='ARMED'"
                ).fetchone()
                if row and row[0]:
                    last_arm_ts = int(row[0])

            last_launch_ts: Optional[int] = None
            if _table_exists(conn, "wt_watchtower_launches"):
                row = conn.execute(
                    "SELECT MAX(create_time) FROM wt_watchtower_launches"
                ).fetchone()
                if row and row[0]:
                    last_launch_ts = int(row[0])

            last_transition = max(
                filter(None, [last_arm_ts, last_launch_ts]), default=None
            )

        # Determine platform state
        if armed > 0:
            state = ARMED
            reason = f"{armed} creator(s) armed — actionable event imminent"
            next_state = ACTIVE
        elif active > 0:
            state = ACTIVE
            reason = f"{active} launch(es) in progress (pre-migration)"
            next_state = COMPLETED
        elif observing > 0:
            state = OBSERVING
            reason = (
                f"{observing_sessions} session(s) observed"
                + (f", {watching_candidates} candidate(s) watched" if watching_candidates else "")
            )
            next_state = ARMED
        else:
            state = IDLE
            reason = "No active sessions or candidates"
            next_state = OBSERVING

        # Confidence: ratio of armed to (armed+observing) gives a rough signal
        confidence: Optional[float] = None
        if armed > 0 and observing >= 0:
            confidence = round(armed / max(armed + observing, 1), 2)

        return LifecycleSnapshot(
            operation_id="watchtower",
            display_name="WATCHTOWER",
            lifecycle_state=state,
            state_reason=reason,
            counts={
                OBSERVING:  observing,
                ARMED:      armed,
                ACTIVE:     active,
                COMPLETED:  completed_today,
            },
            confidence=confidence,
            last_transition_at=last_transition,
            next_expected_state=next_state,
            generated_at=now,
            meta={
                "sessions": observing_sessions,
                "candidates_watched": watching_candidates,
            },
        )

    except Exception as exc:
        return LifecycleSnapshot(
            operation_id="watchtower",
            display_name="WATCHTOWER",
            lifecycle_state=IDLE,
            state_reason=f"Adapter error: {exc}",
            counts={},
            confidence=None,
            last_transition_at=None,
            next_expected_state=OBSERVING,
            generated_at=now,
        )


# ── Launcher Observatory adapter ─────────────────────────────────────────────
#
# Internal state → Platform lifecycle mapping:
#
#   wt_farm_launches (last 30d, unknown funder)    → OBSERVING
#   wt_farm_launches (last 30d, known funder)      → ARMED
#   wt_farm_launches (today)                       → ACTIVE
#   wt_farm_launches migrated today                → COMPLETED

def launcher_observatory_lifecycle() -> LifecycleSnapshot:
    now = int(time.time())
    try:
        ops_path = _ops_db_path()
        conn = _ro(ops_path)
        with conn:
            if not _table_exists(conn, "wt_farm_launches"):
                raise RuntimeError("wt_farm_launches not found")

            # OBSERVING: launches from unknown funders in last 30 days
            observing = _count(
                conn,
                """
                SELECT COUNT(*) FROM wt_farm_launches fl
                WHERE fl.create_time > ?
                  AND NOT EXISTS (
                    SELECT 1 FROM wt_confirmed_treasuries t WHERE fl.funder = t.treasury
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM wt_discovered_subprovs sp WHERE fl.funder = sp.subprov
                  )
                """,
                (now - 86400 * 30,)
            )

            # ARMED: launches from known WATCHTOWER operators — already attributed,
            # predictable pattern → platform can anticipate next wave
            armed = _count(
                conn,
                """
                SELECT COUNT(DISTINCT funder) FROM wt_farm_launches fl
                WHERE fl.create_time > ?
                  AND (
                    EXISTS (SELECT 1 FROM wt_confirmed_treasuries t WHERE fl.funder = t.treasury)
                    OR
                    EXISTS (SELECT 1 FROM wt_discovered_subprovs sp WHERE fl.funder = sp.subprov)
                  )
                """,
                (now - 86400 * 7,)
            )

            # ACTIVE: launches in the last 24h from any funder
            active = _count(
                conn,
                "SELECT COUNT(*) FROM wt_farm_launches WHERE create_time > ?",
                (now - 86400,)
            )

            # COMPLETED: migrated launches today
            completed_today = _count(
                conn,
                "SELECT COUNT(*) FROM wt_farm_launches WHERE migrated_at > ?",
                (now - 86400,)
            ) if "migrated_at" in {
                r[1] for r in conn.execute("PRAGMA table_info(wt_farm_launches)").fetchall()
            } else 0

            last_transition: Optional[int] = conn.execute(
                "SELECT MAX(create_time) FROM wt_farm_launches WHERE create_time > ?",
                (now - 86400,)
            ).fetchone()[0]
            if last_transition:
                last_transition = int(last_transition)

        if active > 0:
            state = ACTIVE
            reason = f"{active} launch(es) detected in last 24h"
            next_state = COMPLETED
        elif armed > 0:
            state = ARMED
            reason = f"{armed} attributed operator(s) with recent activity — next wave predictable"
            next_state = ACTIVE
        elif observing > 0:
            state = OBSERVING
            reason = f"{observing} unattributed launch(es) under investigation (last 30d)"
            next_state = ARMED
        else:
            state = IDLE
            reason = "No launch activity detected"
            next_state = OBSERVING

        return LifecycleSnapshot(
            operation_id="launcher-observatory",
            display_name="Launcher Observatory",
            lifecycle_state=state,
            state_reason=reason,
            counts={
                OBSERVING:  observing,
                ARMED:      armed,
                ACTIVE:     active,
                COMPLETED:  completed_today,
            },
            confidence=None,
            last_transition_at=last_transition,
            next_expected_state=next_state,
            generated_at=now,
        )

    except Exception as exc:
        return LifecycleSnapshot(
            operation_id="launcher-observatory",
            display_name="Launcher Observatory",
            lifecycle_state=IDLE,
            state_reason=f"Adapter error: {exc}",
            counts={},
            confidence=None,
            last_transition_at=None,
            next_expected_state=OBSERVING,
            generated_at=now,
        )


# ── Buy Swarm Observatory adapter ────────────────────────────────────────────
#
# Internal state → Platform lifecycle mapping:
#
#   wt_swarm_buys (any observations)                → OBSERVING
#   qualified swarms (>=3 participants, known subprov) → ARMED
#   qualified swarms seen in last 1h                 → ACTIVE
#   qualified swarms from yesterday                  → COMPLETED

_BSO_MIN_PARTICIPANTS = 3
_BSO_MAX_WINDOW       = 7200

def buy_swarm_observatory_lifecycle() -> LifecycleSnapshot:
    now = int(time.time())
    try:
        ops_path = _ops_db_path()
        conn = _ro(ops_path)
        with conn:
            if not _table_exists(conn, "wt_swarm_buys"):
                raise RuntimeError("wt_swarm_buys not found")

            # OBSERVING: any raw observations in last 24h
            observing = _count(
                conn,
                "SELECT COUNT(DISTINCT mint) FROM wt_swarm_buys WHERE observed_at > ?",
                (now - 86400,)
            )

            # ARMED: qualified swarms (known subprov, enough participants)
            armed = _count(
                conn,
                f"""
                SELECT COUNT(DISTINCT mint) FROM wt_swarm_buys
                WHERE subprov_wallet IS NOT NULL
                  AND observed_at > ?
                GROUP BY mint
                HAVING COUNT(DISTINCT swarm_wallet) >= {_BSO_MIN_PARTICIPANTS}
                     AND MAX(observed_at) - MIN(observed_at) <= {_BSO_MAX_WINDOW}
                """,
                (now - 86400 * 7,)
            )

            # ACTIVE: qualified swarms seen in the last hour
            active = _count(
                conn,
                f"""
                SELECT COUNT(*) FROM (
                  SELECT mint FROM wt_swarm_buys
                  WHERE subprov_wallet IS NOT NULL
                    AND observed_at > ?
                  GROUP BY mint
                  HAVING COUNT(DISTINCT swarm_wallet) >= {_BSO_MIN_PARTICIPANTS}
                       AND MAX(observed_at) - MIN(observed_at) <= {_BSO_MAX_WINDOW}
                )
                """,
                (now - 3600,)
            )

            # COMPLETED: qualified swarms that ended (last observed > 2h ago, last 24h)
            completed_today = _count(
                conn,
                f"""
                SELECT COUNT(*) FROM (
                  SELECT mint FROM wt_swarm_buys
                  WHERE subprov_wallet IS NOT NULL
                    AND observed_at > ?
                  GROUP BY mint
                  HAVING COUNT(DISTINCT swarm_wallet) >= {_BSO_MIN_PARTICIPANTS}
                       AND MAX(observed_at) < ?
                       AND MAX(observed_at) - MIN(observed_at) <= {_BSO_MAX_WINDOW}
                )
                """,
                (now - 86400, now - 7200)
            )

            last_transition: Optional[int] = conn.execute(
                "SELECT MAX(observed_at) FROM wt_swarm_buys WHERE observed_at > ?",
                (now - 86400,)
            ).fetchone()[0]
            if last_transition:
                last_transition = int(last_transition)

        if active > 0:
            state = ACTIVE
            reason = f"{active} coordinated buy campaign(s) in progress (last 1h)"
            next_state = COMPLETED
        elif armed > 0:
            state = ARMED
            reason = f"{armed} qualified campaign(s) — coordinated activity confirmed"
            next_state = ACTIVE
        elif observing > 0:
            state = OBSERVING
            reason = f"{observing} token(s) under observation (last 24h)"
            next_state = ARMED
        else:
            state = IDLE
            reason = "No buy activity observed"
            next_state = OBSERVING

        return LifecycleSnapshot(
            operation_id="buy-swarm-observatory",
            display_name="Buy Swarm Observatory",
            lifecycle_state=state,
            state_reason=reason,
            counts={
                OBSERVING:  observing,
                ARMED:      armed,
                ACTIVE:     active,
                COMPLETED:  completed_today,
            },
            confidence=None,
            last_transition_at=last_transition,
            next_expected_state=next_state,
            generated_at=now,
        )

    except Exception as exc:
        return LifecycleSnapshot(
            operation_id="buy-swarm-observatory",
            display_name="Buy Swarm Observatory",
            lifecycle_state=IDLE,
            state_reason=f"Adapter error: {exc}",
            counts={},
            confidence=None,
            last_transition_at=None,
            next_expected_state=OBSERVING,
            generated_at=now,
        )


# ── Registry ─────────────────────────────────────────────────────────────────

_ADAPTERS: dict[str, object] = {
    "watchtower":             watchtower_lifecycle,
    "launcher-observatory":   launcher_observatory_lifecycle,
    "buy-swarm-observatory":  buy_swarm_observatory_lifecycle,
}


def get_lifecycle(operation_id: str) -> Optional[LifecycleSnapshot]:
    """Return a LifecycleSnapshot for the given operation, or None if unknown."""
    adapter = _ADAPTERS.get(operation_id)
    if adapter is None:
        return None
    return adapter()  # type: ignore[operator]


def get_all_lifecycles() -> list[LifecycleSnapshot]:
    """Return snapshots for all registered adapters."""
    return [fn() for fn in _ADAPTERS.values()]  # type: ignore[misc]
