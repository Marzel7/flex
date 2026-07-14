"""
Operations OS — Analyst Inbox.

The inbox is the platform's attention engine.  Every operation emits
InboxItems when it observes something that deserves analyst review.
Mission Control surfaces only the count.  The analyst opens the inbox
when they choose to act.

Design rules:
- Items are DEDUPLICATED per (operation_id, entity_id, subject_type).
  Updating an existing item replaces it rather than appending.
- Operations never create UI.  They emit InboxItems.
- Priority ordering: CRITICAL > HIGH > MEDIUM > LOW > INFO.
- status flow: NEW → ACKNOWLEDGED → IN_PROGRESS → RESOLVED | EXPIRED.
- The store is append-light: only meaningful state changes create or
  update rows.  Never spam.
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Optional, List

# ── Priority ──────────────────────────────────────────────────────────────────

CRITICAL = "CRITICAL"
HIGH     = "HIGH"
MEDIUM   = "MEDIUM"
LOW      = "LOW"
INFO     = "INFO"

PRIORITIES_ORDERED = (CRITICAL, HIGH, MEDIUM, LOW, INFO)
PRIORITIES_VALID   = frozenset(PRIORITIES_ORDERED)

PRIORITY_RANK: dict[str, int] = {p: i for i, p in enumerate(PRIORITIES_ORDERED)}

# ── Status ────────────────────────────────────────────────────────────────────

NEW          = "NEW"
ACKNOWLEDGED = "ACKNOWLEDGED"
IN_PROGRESS  = "IN_PROGRESS"
RESOLVED     = "RESOLVED"
EXPIRED      = "EXPIRED"

STATUSES_VALID = frozenset((NEW, ACKNOWLEDGED, IN_PROGRESS, RESOLVED, EXPIRED))

# ── Subject types ─────────────────────────────────────────────────────────────

SUBJECT_LIFECYCLE       = "LIFECYCLE"
SUBJECT_BEHAVIOUR       = "BEHAVIOUR"
SUBJECT_CONFIDENCE      = "CONFIDENCE"
SUBJECT_OPERATION       = "OPERATION"
SUBJECT_RELATIONSHIP    = "RELATIONSHIP"
SUBJECT_ASSESSMENT      = "ASSESSMENT"
SUBJECT_FORECAST        = "FORECAST"

SUBJECT_TYPES_VALID = frozenset((
    SUBJECT_LIFECYCLE, SUBJECT_BEHAVIOUR, SUBJECT_CONFIDENCE,
    SUBJECT_OPERATION, SUBJECT_RELATIONSHIP, SUBJECT_ASSESSMENT,
    SUBJECT_FORECAST,
))


# ── InboxItem ─────────────────────────────────────────────────────────────────

@dataclass
class InboxItem:
    """
    One attention item visible to the analyst.

    item_id          Stable UUID.  Used for deduplication key.
    operation_id     Emitting operation.
    entity_id        Primary entity (wallet address) or None for op-level items.
    subject_type     Category (LIFECYCLE / BEHAVIOUR / CONFIDENCE / OPERATION / RELATIONSHIP).
    priority         CRITICAL / HIGH / MEDIUM / LOW / INFO.
    confidence       0.0–1.0 or None.
    headline         One short sentence.  The analyst reads this first.
    summary          Two-to-four sentences of context.
    reason           Why the platform flagged this item.
    recommended_action  What the analyst should do.
    created_at       Unix timestamp.
    updated_at       Unix timestamp — last content change.
    status           NEW / ACKNOWLEDGED / IN_PROGRESS / RESOLVED / EXPIRED.
    dedup_key        Stable key for upsert — (operation_id, entity_id or '', subject_type, variant).
                     Callers must set this; the store uses it instead of item_id for upsert.
    meta             Adapter-specific extra fields.
    """
    operation_id:       str
    subject_type:       str
    priority:           str
    headline:           str
    summary:            str
    reason:             str
    recommended_action: str
    dedup_key:          str
    entity_id:          Optional[str]      = None
    confidence:         Optional[float]    = None
    created_at:         int                = field(default_factory=lambda: int(time.time()))
    updated_at:         int                = field(default_factory=lambda: int(time.time()))
    status:             str                = NEW
    item_id:            str                = field(default_factory=lambda: str(uuid.uuid4()))
    meta:               dict               = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.priority not in PRIORITIES_VALID:
            raise ValueError(f"Invalid priority: {self.priority!r}")
        if self.status not in STATUSES_VALID:
            raise ValueError(f"Invalid status: {self.status!r}")
        if self.subject_type not in SUBJECT_TYPES_VALID:
            raise ValueError(f"Invalid subject_type: {self.subject_type!r}")

    def to_dict(self) -> dict:
        return {
            "item_id":            self.item_id,
            "operation_id":       self.operation_id,
            "entity_id":          self.entity_id,
            "subject_type":       self.subject_type,
            "priority":           self.priority,
            "priority_rank":      PRIORITY_RANK.get(self.priority, 99),
            "confidence":         self.confidence,
            "headline":           self.headline,
            "summary":            self.summary,
            "reason":             self.reason,
            "recommended_action": self.recommended_action,
            "created_at":         self.created_at,
            "updated_at":         self.updated_at,
            "status":             self.status,
            "dedup_key":          self.dedup_key,
            "meta":               self.meta,
        }


# ── Schema ────────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS analyst_inbox (
    item_id            TEXT PRIMARY KEY,
    dedup_key          TEXT UNIQUE NOT NULL,
    operation_id       TEXT NOT NULL,
    entity_id          TEXT,
    subject_type       TEXT NOT NULL,
    priority           TEXT NOT NULL,
    confidence         REAL,
    headline           TEXT NOT NULL,
    summary            TEXT NOT NULL,
    reason             TEXT NOT NULL,
    recommended_action TEXT NOT NULL,
    created_at         INTEGER NOT NULL,
    updated_at         INTEGER NOT NULL,
    status             TEXT NOT NULL DEFAULT 'NEW',
    meta               TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_inbox_status     ON analyst_inbox(status);
CREATE INDEX IF NOT EXISTS idx_inbox_operation  ON analyst_inbox(operation_id);
CREATE INDEX IF NOT EXISTS idx_inbox_priority   ON analyst_inbox(priority);
CREATE INDEX IF NOT EXISTS idx_inbox_created    ON analyst_inbox(created_at DESC);
"""

def ensure_inbox_schema(conn: sqlite3.Connection) -> None:
    for stmt in _DDL.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    conn.commit()


# ── InboxStore ────────────────────────────────────────────────────────────────

class InboxStore:
    """
    Read/write access to the analyst_inbox table.

    Uses the ops DB (wt_ops_v2.db or equivalent) so it never touches
    the hot token DB.
    """

    def __init__(self, db_path: str) -> None:
        self._path = db_path
        with self._connect() as conn:
            ensure_inbox_schema(conn)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    # ── Write ─────────────────────────────────────────────────────────────────

    def upsert(self, item: InboxItem) -> None:
        """
        Insert or update by dedup_key.  If the item already exists:
        - Update headline, summary, reason, recommended_action, confidence,
          priority, updated_at.
        - Do NOT reset status (analyst may have already acknowledged it).
        - Do NOT update created_at.
        """
        import json
        now = int(time.time())
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT item_id, status FROM analyst_inbox WHERE dedup_key = ?",
                (item.dedup_key,)
            ).fetchone()

            if existing:
                conn.execute("""
                    UPDATE analyst_inbox SET
                        headline           = ?,
                        summary            = ?,
                        reason             = ?,
                        recommended_action = ?,
                        confidence         = ?,
                        priority           = ?,
                        updated_at         = ?,
                        meta               = ?
                    WHERE dedup_key = ?
                """, (
                    item.headline, item.summary, item.reason,
                    item.recommended_action, item.confidence,
                    item.priority, now,
                    json.dumps(item.meta),
                    item.dedup_key,
                ))
            else:
                conn.execute("""
                    INSERT INTO analyst_inbox
                        (item_id, dedup_key, operation_id, entity_id, subject_type,
                         priority, confidence, headline, summary, reason,
                         recommended_action, created_at, updated_at, status, meta)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    item.item_id, item.dedup_key,
                    item.operation_id, item.entity_id, item.subject_type,
                    item.priority, item.confidence,
                    item.headline, item.summary, item.reason,
                    item.recommended_action,
                    now, now, item.status,
                    json.dumps(item.meta),
                ))
            conn.commit()

    def set_status(self, item_id: str, status: str) -> bool:
        if status not in STATUSES_VALID:
            return False
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE analyst_inbox SET status=?, updated_at=? WHERE item_id=?",
                (status, int(time.time()), item_id)
            )
            conn.commit()
            return cur.rowcount > 0

    def expire_resolved(self, older_than_seconds: int = 86400 * 7) -> int:
        """Mark RESOLVED items older than threshold as EXPIRED."""
        cutoff = int(time.time()) - older_than_seconds
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE analyst_inbox SET status='EXPIRED', updated_at=? "
                "WHERE status='RESOLVED' AND updated_at < ?",
                (int(time.time()), cutoff)
            )
            conn.commit()
            return cur.rowcount

    # ── Read ──────────────────────────────────────────────────────────────────

    def _row_to_dict(self, row: sqlite3.Row) -> dict:
        import json
        d = dict(row)
        try:
            d["meta"] = json.loads(d.get("meta") or "{}")
        except Exception:
            d["meta"] = {}
        d["priority_rank"] = PRIORITY_RANK.get(d.get("priority", "INFO"), 99)
        return d

    def fetch_active(
        self,
        operation_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[dict]:
        """
        Return NEW + ACKNOWLEDGED + IN_PROGRESS items, sorted by priority then recency.
        """
        with self._connect() as conn:
            if operation_id:
                rows = conn.execute("""
                    SELECT * FROM analyst_inbox
                    WHERE status IN ('NEW','ACKNOWLEDGED','IN_PROGRESS')
                      AND operation_id = ?
                    ORDER BY
                        CASE priority
                            WHEN 'CRITICAL' THEN 0
                            WHEN 'HIGH'     THEN 1
                            WHEN 'MEDIUM'   THEN 2
                            WHEN 'LOW'      THEN 3
                            ELSE                 4
                        END,
                        updated_at DESC
                    LIMIT ?
                """, (operation_id, limit)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT * FROM analyst_inbox
                    WHERE status IN ('NEW','ACKNOWLEDGED','IN_PROGRESS')
                    ORDER BY
                        CASE priority
                            WHEN 'CRITICAL' THEN 0
                            WHEN 'HIGH'     THEN 1
                            WHEN 'MEDIUM'   THEN 2
                            WHEN 'LOW'      THEN 3
                            ELSE                 4
                        END,
                        updated_at DESC
                    LIMIT ?
                """, (limit,)).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def fetch_summary(self) -> dict:
        """Counts per status for Mission Control attention strip."""
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT status, COUNT(*) AS n FROM analyst_inbox
                WHERE status != 'EXPIRED'
                GROUP BY status
            """).fetchall()
        counts = {r["status"]: r["n"] for r in rows}

        # Highest-priority active item
        active = self.fetch_active(limit=1)
        top = active[0] if active else None

        return {
            "new":         counts.get(NEW, 0),
            "acknowledged": counts.get(ACKNOWLEDGED, 0),
            "in_progress": counts.get(IN_PROGRESS, 0),
            "resolved_today": self._resolved_today(),
            "total_active": counts.get(NEW, 0) + counts.get(ACKNOWLEDGED, 0) + counts.get(IN_PROGRESS, 0),
            "top_item": top,
        }

    def _resolved_today(self) -> int:
        cutoff = int(time.time()) - 86400
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM analyst_inbox "
                "WHERE status='RESOLVED' AND updated_at > ?",
                (cutoff,)
            ).fetchone()
        return row["n"] if row else 0

    def fetch_by_id(self, item_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM analyst_inbox WHERE item_id = ?", (item_id,)
            ).fetchone()
        return self._row_to_dict(row) if row else None
