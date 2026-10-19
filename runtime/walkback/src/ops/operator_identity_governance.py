"""X73.0 durable lifecycle governance for canonical Operator Identities.

Identity status and operational activity are deliberately independent.  All
governance mutations append an immutable event; identities and historical
launch assignments are never deleted.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from typing import Any, Iterable

from src.core.database_write_service import database_write_service, execute_script


IDENTITY_STATUSES = frozenset({
    "CANDIDATE", "CONFIRMED", "REVIEW", "MERGED", "SPLIT", "RETIRED",
})
ACTIVITY_STATUSES = frozenset({"ACTIVE", "QUIET", "DORMANT", "REACTIVATED"})
ASSET_TYPES = frozenset({
    "TREASURY", "PROVISIONING_CONTROLLER", "SESSION_PATTERN", "CAMPAIGN",
    "CREATOR_FAMILY", "SETTLEMENT_BEHAVIOUR", "EVIDENCE",
})
ENTITY_ASSET_TYPES = {
    "TREASURY": "TREASURY", "PROVISIONING_CONTROLLER": "SUB_PROVISIONER",
    "CREATOR_FAMILY": "CREATOR",
}
EVENT_TYPES = frozenset({
    "CONFIRMED", "IDENTITY_EXPANDED", "CAMPAIGN_ADDED", "TREASURY_ADDED",
    "PROVISIONING_ROTATION", "ACTIVITY_QUIET", "DORMANT", "REACTIVATED",
    "MOVED_TO_REVIEW", "REVIEW_RESOLVED", "MERGED", "SPLIT", "RETIRED",
})


DDL = """
CREATE TABLE IF NOT EXISTS operator_identity_state (
    operator_id TEXT PRIMARY KEY REFERENCES operators(operator_id),
    identity_status TEXT NOT NULL,
    activity_status TEXT NOT NULL DEFAULT 'ACTIVE',
    source_population_id TEXT,
    source_population_revision TEXT,
    confirmation_evidence_package TEXT,
    disposition_at_confirmation TEXT,
    merged_into_operator_id TEXT,
    retired_at INTEGER,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_operator_identity_state_status
ON operator_identity_state(identity_status, activity_status);

CREATE TABLE IF NOT EXISTS operator_identity_events (
    event_id TEXT PRIMARY KEY,
    operator_id TEXT NOT NULL REFERENCES operators(operator_id),
    event_type TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    analyst TEXT NOT NULL,
    evidence_revision TEXT NOT NULL,
    reason TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_operator_identity_events_timeline
ON operator_identity_events(operator_id, timestamp, event_id);
CREATE TRIGGER IF NOT EXISTS operator_identity_events_immutable_update
BEFORE UPDATE ON operator_identity_events BEGIN
 SELECT RAISE(ABORT, 'operator identity history is immutable');
END;
CREATE TRIGGER IF NOT EXISTS operator_identity_events_immutable_delete
BEFORE DELETE ON operator_identity_events BEGIN
 SELECT RAISE(ABORT, 'operator identity history is immutable');
END;

CREATE TABLE IF NOT EXISTS operator_identity_assets (
    asset_id TEXT PRIMARY KEY,
    operator_id TEXT NOT NULL REFERENCES operators(operator_id),
    asset_type TEXT NOT NULL,
    asset_value TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    evidence_revision TEXT NOT NULL,
    added_at INTEGER NOT NULL,
    event_id TEXT NOT NULL REFERENCES operator_identity_events(event_id),
    UNIQUE(operator_id, asset_type, asset_value)
);
CREATE INDEX IF NOT EXISTS ix_operator_identity_assets_value
ON operator_identity_assets(asset_value, status);

CREATE TABLE IF NOT EXISTS operator_identity_merges (
    source_operator_id TEXT PRIMARY KEY REFERENCES operators(operator_id),
    destination_operator_id TEXT NOT NULL REFERENCES operators(operator_id),
    event_id TEXT NOT NULL REFERENCES operator_identity_events(event_id),
    merged_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS operator_identity_splits (
    parent_operator_id TEXT NOT NULL REFERENCES operators(operator_id),
    child_operator_id TEXT NOT NULL REFERENCES operators(operator_id),
    event_id TEXT NOT NULL REFERENCES operator_identity_events(event_id),
    split_at INTEGER NOT NULL,
    PRIMARY KEY(parent_operator_id, child_operator_id)
);

CREATE TABLE IF NOT EXISTS operator_launch_membership (
    mint TEXT PRIMARY KEY,
    operator_id TEXT NOT NULL REFERENCES operators(operator_id),
    source_population_id TEXT,
    assigned_at INTEGER NOT NULL,
    event_id TEXT REFERENCES operator_identity_events(event_id)
);
CREATE INDEX IF NOT EXISTS ix_operator_launch_membership_operator
ON operator_launch_membership(operator_id);

CREATE TABLE IF NOT EXISTS operator_launch_assignment_history (
    assignment_id TEXT PRIMARY KEY,
    mint TEXT NOT NULL,
    from_operator_id TEXT,
    to_operator_id TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    analyst TEXT NOT NULL,
    evidence_revision TEXT NOT NULL,
    reason TEXT NOT NULL,
    event_id TEXT NOT NULL REFERENCES operator_identity_events(event_id)
);
CREATE INDEX IF NOT EXISTS ix_operator_launch_history_mint
ON operator_launch_assignment_history(mint, timestamp);
CREATE TRIGGER IF NOT EXISTS operator_launch_history_immutable_update
BEFORE UPDATE ON operator_launch_assignment_history BEGIN
 SELECT RAISE(ABORT, 'operator launch assignment history is immutable');
END;
CREATE TRIGGER IF NOT EXISTS operator_launch_history_immutable_delete
BEFORE DELETE ON operator_launch_assignment_history BEGIN
 SELECT RAISE(ABORT, 'operator launch assignment history is immutable');
END;
"""


class GovernanceError(ValueError):
    def __init__(self, message: str, code: str = "INVALID_GOVERNANCE_ACTION", status: int = 400):
        super().__init__(message)
        self.code, self.status = code, status


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


# X76.1 -- Operator Identity Projection Integrity.
#
# ROOT CAUSE (see docs/audits/x76_1_projection_integrity_audit.md for the
# full architecture audit): operator_entities has 5 independent write
# sites; operator_identity_assets has exactly 2, both inside
# OperatorIdentityGovernanceService (expand()/merge()). The live production
# writer of operator_entities -- watchtower_alignment.reconcile_confirmed_
# treasury(), invoked from treasury_bank.promote_to_confirmed() on every
# real treasury confirmation -- writes operator_entities DIRECTLY and never
# calls into OperatorIdentityGovernanceService at all. This is not a broken
# dual-write; it is a missing single write at the one call site that
# actually produces live rows (69 of 70 current operator_entities rows
# trace to this path). operator_store.add_entity() and promotion_service.py's
# entity-insert loop are both confirmed dead/unreached in the live system
# (add_entity has zero callers; promotion_service's brand-new-operator path
# has never fired against current data) -- fixing them is unnecessary,
# but this projection helper is written generically enough that any of
# them COULD call it safely if that ever changes.
#
# CONTRACT: there is exactly one authoritative write (operator_entities,
# which encodes the confirmed role a wallet plays for an operator).
# operator_identity_assets is a DETERMINISTIC PROJECTION of that fact --
# never a second, independently-decided source of truth. This function is
# the single place that projection happens; every operator_entities writer
# that wants a corresponding asset row calls this, in the SAME transaction,
# on the SAME connection, so the two tables can never diverge from a
# partial write.  Idempotent (INSERT OR IGNORE against a deterministic
# uuid5 asset_id, mirroring expand()'s own idempotency contract) and safe
# to call redundantly -- a second call for the same (operator_id,
# asset_type, asset_value) is a no-op.
_ENTITY_TYPE_TO_ASSET_TYPE = {
    "TREASURY": "TREASURY",
    "SUB_PROVISIONER": "PROVISIONING_CONTROLLER",
    "CREATOR": "CREATOR_FAMILY",
}


def project_entity_to_asset(
    conn: sqlite3.Connection, operator_id: str, entity_type: str, entity_address: str,
    *, evidence_revision: str = "system:reconciliation",
) -> str | None:
    """Deterministically project one already-written operator_entities
    fact into operator_identity_assets, on the CALLER's own connection
    (must be inside the same transaction as the operator_entities write
    this projects). Returns the asset_id, or None if entity_type has no
    known asset-type mapping (not an error -- some entity_types, e.g.
    CLIENT, intentionally have no operator_identity_assets equivalent
    today; ASSET_TYPES/ENTITY_ASSET_TYPES above defines the only three
    reverse-mapped types, so this keeps the two lists symmetric without
    inventing a new asset_type for something expand() itself couldn't
    produce).

    Never raises GovernanceError and never enforces expand()'s
    CONFIRMED/REVIEW precondition -- this is a passive projection of a
    fact the caller has ALREADY decided (by writing operator_entities),
    not a new governance decision; the precondition belongs to human-
    initiated expand() calls, not to a deterministic mirror of existing
    state. If the operator has no operator_identity_state row yet, one is
    seeded (matching expand()'s own _ensure_state behaviour) so the
    projection is never silently dropped for a not-yet-lifecycle-tracked
    operator.
    """
    asset_type = _ENTITY_TYPE_TO_ASSET_TYPE.get(str(entity_type or "").upper())
    if not asset_type or not entity_address:
        return None
    now = int(time.time())
    operator_row = conn.execute("SELECT 1 FROM operators WHERE operator_id=?", (operator_id,)).fetchone()
    if not operator_row:
        return None
    existing_state = conn.execute(
        "SELECT 1 FROM operator_identity_state WHERE operator_id=?", (operator_id,)
    ).fetchone()
    if not existing_state:
        status_row = conn.execute("SELECT status FROM operators WHERE operator_id=?", (operator_id,)).fetchone()
        status = str(status_row["status"] if status_row and status_row["status"] else "CANDIDATE")
        if status not in IDENTITY_STATUSES:
            status = "CONFIRMED" if status == "PROVISIONAL" else "CANDIDATE"
        conn.execute(
            "INSERT OR IGNORE INTO operator_identity_state "
            "(operator_id,identity_status,activity_status,updated_at) VALUES(?,?,?,?)",
            (operator_id, status, "ACTIVE", now),
        )
    event_type = {"TREASURY": "TREASURY_ADDED", "PROVISIONING_CONTROLLER": "PROVISIONING_ROTATION",
                  "CREATOR_FAMILY": "IDENTITY_EXPANDED"}.get(asset_type, "IDENTITY_EXPANDED")
    asset_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"operator-asset:{operator_id}:{asset_type}:{entity_address}"))
    already_present = conn.execute(
        "SELECT 1 FROM operator_identity_assets WHERE asset_id=?", (asset_id,)
    ).fetchone()
    if already_present:
        return asset_id
    event_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO operator_identity_events "
        "(event_id,operator_id,event_type,timestamp,analyst,evidence_revision,reason,payload_json) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (event_id, operator_id, event_type, now, "system:reconciliation", evidence_revision,
         "Deterministic projection from an existing operator_entities row (X76.1).",
         _json({"asset_type": asset_type, "asset_value": entity_address, "source": "project_entity_to_asset"})),
    )
    conn.execute(
        "INSERT OR IGNORE INTO operator_identity_assets "
        "(asset_id,operator_id,asset_type,asset_value,status,evidence_revision,added_at,event_id) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (asset_id, operator_id, asset_type, entity_address, "ACTIVE", evidence_revision, now, event_id),
    )
    return asset_id


def _read_conn(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def read_identity_lifecycle(path: str, operator_id: str) -> dict[str, Any]:
    """Fault-tolerant current state plus immutable history projection."""
    try:
        conn = _read_conn(path)
        tables = _tables(conn)
        operator = conn.execute("SELECT * FROM operators WHERE operator_id=?", (operator_id,)).fetchone()
        if not operator:
            conn.close()
            return {}
        state = None
        if "operator_identity_state" in tables:
            state = conn.execute(
                "SELECT * FROM operator_identity_state WHERE operator_id=?", (operator_id,)
            ).fetchone()
        result = dict(state) if state else {
            "operator_id": operator_id,
            "identity_status": str(operator["status"] or "CANDIDATE"),
            "activity_status": "ACTIVE",
            "source_population_id": None,
            "source_population_revision": None,
            "confirmation_evidence_package": None,
            "disposition_at_confirmation": None,
            "merged_into_operator_id": None,
            "retired_at": None,
            "updated_at": operator["updated_at"],
        }
        result["timeline"] = []
        result["assets"] = []
        result["split_children"] = []
        if "operator_identity_events" in tables:
            result["timeline"] = [
                {**dict(row), "payload": json.loads(row["payload_json"] or "{}")}
                for row in conn.execute(
                    "SELECT * FROM operator_identity_events WHERE operator_id=? "
                    "ORDER BY timestamp, event_id", (operator_id,)
                )
            ]
        if "operator_identity_assets" in tables:
            result["assets"] = [dict(row) for row in conn.execute(
                "SELECT * FROM operator_identity_assets WHERE operator_id=? "
                "ORDER BY added_at, asset_id", (operator_id,)
            )]
        if "operator_identity_splits" in tables:
            result["split_children"] = [row[0] for row in conn.execute(
                "SELECT child_operator_id FROM operator_identity_splits "
                "WHERE parent_operator_id=? ORDER BY child_operator_id", (operator_id,)
            )]
        conn.close()
        return result
    except (OSError, sqlite3.Error, json.JSONDecodeError):
        return {}


def resolve_canonical_operator(path: str, entity_or_mint: str) -> dict[str, Any] | None:
    """Resolve expansions, launch assignments and merge redirects."""
    try:
        conn = _read_conn(path)
        tables = _tables(conn)
        operator_id = None
        if "operator_launch_membership" in tables:
            row = conn.execute(
                "SELECT operator_id FROM operator_launch_membership WHERE mint=?", (entity_or_mint,)
            ).fetchone()
            operator_id = row[0] if row else None
        if not operator_id and "operator_identity_assets" in tables:
            row = conn.execute(
                "SELECT operator_id FROM operator_identity_assets "
                "WHERE asset_value=? AND status='ACTIVE' ORDER BY added_at DESC LIMIT 1",
                (entity_or_mint,),
            ).fetchone()
            operator_id = row[0] if row else None
        if not operator_id:
            row = conn.execute(
                "SELECT operator_id FROM operator_entities WHERE entity_address=? "
                "ORDER BY added_at DESC LIMIT 1", (entity_or_mint,)
            ).fetchone()
            operator_id = row[0] if row else None
        if operator_id and "operator_identity_merges" in tables:
            seen = set()
            while operator_id not in seen:
                seen.add(operator_id)
                row = conn.execute(
                    "SELECT destination_operator_id FROM operator_identity_merges "
                    "WHERE source_operator_id=?", (operator_id,),
                ).fetchone()
                if not row:
                    break
                operator_id = row[0]
        row = conn.execute("SELECT * FROM operators WHERE operator_id=?", (operator_id,)).fetchone() if operator_id else None
        conn.close()
        return dict(row) if row else None
    except (OSError, sqlite3.Error):
        return None


class OperatorIdentityGovernanceService:
    def __init__(self, ops_db_path: str, *, write_service: Any = None) -> None:
        self.ops_db_path = ops_db_path
        self._write_service = write_service or database_write_service
        self._database = f"operations:{os.path.realpath(ops_db_path)}"
        self._write_service.register_database(self._database, ops_db_path)

    def ensure_schema(self) -> None:
        self._write_service.submit(
            self._database, "operator-identity-lifecycle-schema",
            lambda conn: execute_script(conn, DDL),
        )

    @staticmethod
    def _metadata(payload: dict[str, Any]) -> tuple[str, str, str]:
        analyst = str(payload.get("analyst") or "").strip()
        reason = str(payload.get("reason") or "").strip()
        revision = str(payload.get("evidence_revision") or "").strip()
        if not analyst or not reason or not revision:
            raise GovernanceError(
                "analyst, reason and evidence_revision are required",
                "GOVERNANCE_METADATA_REQUIRED",
            )
        return analyst, reason, revision

    @staticmethod
    def _operator(conn: sqlite3.Connection, operator_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM operators WHERE operator_id=?", (operator_id,)).fetchone()
        if not row:
            raise GovernanceError("Operator Identity not found", "OPERATOR_NOT_FOUND", 404)
        return row

    @staticmethod
    def _event(conn: sqlite3.Connection, operator_id: str, event_type: str,
               analyst: str, revision: str, reason: str, payload: dict[str, Any]) -> tuple[str, int]:
        if event_type not in EVENT_TYPES:
            raise GovernanceError(f"Unsupported lifecycle event: {event_type}")
        event_id, now = str(uuid.uuid4()), int(time.time())
        conn.execute(
            "INSERT INTO operator_identity_events "
            "(event_id,operator_id,event_type,timestamp,analyst,evidence_revision,reason,payload_json) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (event_id, operator_id, event_type, now, analyst, revision, reason, _json(payload)),
        )
        return event_id, now

    @staticmethod
    def _ensure_state(conn: sqlite3.Connection, operator_id: str, now: int | None = None) -> None:
        row = OperatorIdentityGovernanceService._operator(conn, operator_id)
        now = now or int(time.time())
        status = str(row["status"] or "CANDIDATE")
        if status not in IDENTITY_STATUSES:
            status = "CONFIRMED" if status == "PROVISIONAL" else "CANDIDATE"
        conn.execute(
            "INSERT OR IGNORE INTO operator_identity_state "
            "(operator_id,identity_status,activity_status,updated_at) VALUES(?,?,?,?)",
            (operator_id, status, "ACTIVE", now),
        )

    @staticmethod
    def _transition(
        conn: sqlite3.Connection, operator_id: str, event_type: str,
        analyst: str, revision: str, reason: str, payload: dict[str, Any], *,
        identity_state: dict[str, Any] | None = None,
        operators: dict[str, Any] | None = None,
        ensure_state: bool = True,
    ) -> tuple[str, int]:
        """The single choke point every identity-status change passes through:
        validate (via the caller's own precondition checks, made just before
        calling this), write the immutable lifecycle event, apply the
        `operator_identity_state` update, apply the mirrored `operators`
        update, and return (event_id, now) for the caller's response.

        `identity_state`/`operators` are column->value dicts for their
        respective UPDATE statements (identity_state's `updated_at` and
        operators' `updated_at` are always included even if the caller
        supplies no other columns for that table, so every transition
        touches both tables' timestamps exactly as every existing call site
        already did before this helper existed -- no behaviour change).

        Cache invalidation is NOT done here (it happens once, in
        OperatorIdentityGovernanceService._mutate(), which wraps every
        governance-service call). Callers outside this class (i.e.
        promotion_service._approve(), which runs its own transaction and does
        not go through _mutate()) must invalidate the cache themselves after
        the transaction commits -- see that call site's own comment.
        """
        if ensure_state:
            OperatorIdentityGovernanceService._ensure_state(conn, operator_id)
        event_id, now = OperatorIdentityGovernanceService._event(
            conn, operator_id, event_type, analyst, revision, reason, payload)

        identity_state = dict(identity_state or {})
        identity_state.setdefault("updated_at", now)
        id_cols = ",".join(f"{col}=?" for col in identity_state)
        conn.execute(
            f"UPDATE operator_identity_state SET {id_cols} WHERE operator_id=?",
            (*identity_state.values(), operator_id),
        )

        operators = dict(operators or {})
        operators.setdefault("updated_at", now)
        op_cols = ",".join(f"{col}=?" for col in operators)
        conn.execute(
            f"UPDATE operators SET {op_cols} WHERE operator_id=?",
            (*operators.values(), operator_id),
        )
        return event_id, now

    def _mutate(self, command: str, operation):
        def transaction(conn):
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            execute_script(conn, DDL)
            return operation(conn)
        result = self._write_service.submit(self._database, command, transaction)
        try:
            from src.ops.operation_attribution import clear_operation_attribution_cache
            clear_operation_attribution_cache(self.ops_db_path)
        except Exception:
            pass
        return result

    def bootstrap_confirmed(self) -> dict[str, int]:
        """Idempotently seed lifecycle state/history for existing confirmations."""
        def operation(conn):
            created = 0
            for row in conn.execute("SELECT * FROM operators WHERE status='CONFIRMED'"):
                operator_id = row["operator_id"]
                self._ensure_state(conn, operator_id, int(row["updated_at"] or time.time()))
                exists = conn.execute(
                    "SELECT 1 FROM operator_identity_events WHERE operator_id=? AND event_type='CONFIRMED'",
                    (operator_id,),
                ).fetchone()
                if exists:
                    continue
                review = conn.execute(
                    "SELECT * FROM operator_reviews WHERE operator_id=? AND decision='CONFIRMED' "
                    "ORDER BY timestamp LIMIT 1", (operator_id,),
                ).fetchone()
                analyst = str(review["reviewer"] or "legacy-governance") if review else "legacy-governance"
                reason = str(review["reason"] or "Existing canonical confirmation imported into identity lifecycle.") if review else "Existing canonical confirmation imported into identity lifecycle."
                revision = f"canonical-import:{operator_id}"
                event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"operator-confirmed:{operator_id}"))
                timestamp = int((review["timestamp"] if review else row["created_at"]) or time.time())
                conn.execute(
                    "INSERT INTO operator_identity_events VALUES(?,?,?,?,?,?,?,?)",
                    (event_id, operator_id, "CONFIRMED", timestamp, analyst, revision, reason, "{}"),
                )
                created += 1
            return {"states_seeded": conn.execute("SELECT COUNT(*) FROM operator_identity_state").fetchone()[0], "events_created": created}
        return self._mutate("operator-identity-bootstrap", operation)

    def expand(self, operator_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        analyst, reason, revision = self._metadata(payload)
        asset_type = str(payload.get("asset_type") or "").upper()
        asset_value = str(payload.get("asset_value") or "").strip()
        if asset_type not in ASSET_TYPES or not asset_value:
            raise GovernanceError("A supported asset_type and asset_value are required")
        def operation(conn):
            self._ensure_state(conn, operator_id)
            state = conn.execute("SELECT identity_status FROM operator_identity_state WHERE operator_id=?", (operator_id,)).fetchone()[0]
            if state not in {"CONFIRMED", "REVIEW"}:
                raise GovernanceError("Only confirmed or review identities can expand", "INVALID_IDENTITY_STATE", 409)
            event_type = {"TREASURY": "TREASURY_ADDED", "CAMPAIGN": "CAMPAIGN_ADDED", "PROVISIONING_CONTROLLER": "PROVISIONING_ROTATION"}.get(asset_type, "IDENTITY_EXPANDED")
            event_id, now = self._event(conn, operator_id, event_type, analyst, revision, reason, {"asset_type": asset_type, "asset_value": asset_value})
            asset_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"operator-asset:{operator_id}:{asset_type}:{asset_value}"))
            conn.execute(
                "INSERT OR IGNORE INTO operator_identity_assets "
                "(asset_id,operator_id,asset_type,asset_value,status,evidence_revision,added_at,event_id) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (asset_id, operator_id, asset_type, asset_value, "ACTIVE", revision, now, event_id),
            )
            entity_type = ENTITY_ASSET_TYPES.get(asset_type)
            if entity_type:
                conn.execute(
                    "INSERT INTO operator_entities "
                    "(operator_id,entity_address,entity_type,confidence,evidence_count,first_seen,last_seen,added_at) "
                    "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(operator_id,entity_address) DO UPDATE SET "
                    "last_seen=excluded.last_seen,evidence_count=operator_entities.evidence_count+1",
                    (operator_id, asset_value, entity_type, "HIGH", 1, now, now, now),
                )
            conn.execute("UPDATE operators SET updated_at=? WHERE operator_id=?", (now, operator_id))
            conn.execute("UPDATE operator_identity_state SET updated_at=? WHERE operator_id=?", (now, operator_id))
            return {"operator_id": operator_id, "event_id": event_id, "asset_id": asset_id, "event_type": event_type}
        return self._mutate("operator-identity-expand", operation)

    def set_activity(self, operator_id: str, activity_status: str, payload: dict[str, Any]) -> dict[str, Any]:
        analyst, reason, revision = self._metadata(payload)
        activity_status = str(activity_status or "").upper()
        if activity_status not in ACTIVITY_STATUSES:
            raise GovernanceError("Unsupported activity status")
        event_type = {"QUIET": "ACTIVITY_QUIET", "DORMANT": "DORMANT", "REACTIVATED": "REACTIVATED", "ACTIVE": "REACTIVATED"}[activity_status]
        def operation(conn):
            self._ensure_state(conn, operator_id)
            identity_status = conn.execute(
                "SELECT identity_status FROM operator_identity_state WHERE operator_id=?", (operator_id,)
            ).fetchone()[0]
            if identity_status in {"MERGED", "SPLIT", "RETIRED"}:
                raise GovernanceError(
                    "Activity cannot change for a closed identity",
                    "INVALID_IDENTITY_STATE", 409,
                )
            event_id, now = self._event(conn, operator_id, event_type, analyst, revision, reason, {"activity_status": activity_status})
            conn.execute(
                "UPDATE operator_identity_state SET activity_status=?,updated_at=? WHERE operator_id=?",
                (activity_status, now, operator_id),
            )
            return {"operator_id": operator_id, "event_id": event_id, "activity_status": activity_status}
        return self._mutate("operator-identity-activity", operation)

    def move_to_review(self, operator_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        analyst, reason, revision = self._metadata(payload)
        blocking = list(payload.get("blocking_evidence") or [])
        required = str(payload.get("required_investigation") or "").strip()
        if not blocking or not required:
            raise GovernanceError("blocking_evidence and required_investigation are required")
        def operation(conn):
            self._ensure_state(conn, operator_id)
            event_id, now = self._event(conn, operator_id, "MOVED_TO_REVIEW", analyst, revision, reason, {"blocking_evidence": blocking, "required_investigation": required})
            conn.execute("UPDATE operator_identity_state SET identity_status='REVIEW',updated_at=? WHERE operator_id=?", (now, operator_id))
            conn.execute("UPDATE operators SET status='REVIEW',review_state='IN_REVIEW',updated_at=? WHERE operator_id=?", (now, operator_id))
            return {"operator_id": operator_id, "event_id": event_id, "identity_status": "REVIEW"}
        return self._mutate("operator-identity-review", operation)

    def resolve_review(self, operator_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        analyst, reason, revision = self._metadata(payload)
        def operation(conn):
            self._ensure_state(conn, operator_id)
            state = conn.execute("SELECT identity_status FROM operator_identity_state WHERE operator_id=?", (operator_id,)).fetchone()[0]
            if state != "REVIEW":
                raise GovernanceError("Identity is not in review", "INVALID_IDENTITY_STATE", 409)
            event_id, now = self._event(conn, operator_id, "REVIEW_RESOLVED", analyst, revision, reason, {})
            conn.execute("UPDATE operator_identity_state SET identity_status='CONFIRMED',updated_at=? WHERE operator_id=?", (now, operator_id))
            conn.execute("UPDATE operators SET status='CONFIRMED',review_state='REVIEWED',updated_at=? WHERE operator_id=?", (now, operator_id))
            return {"operator_id": operator_id, "event_id": event_id, "identity_status": "CONFIRMED"}
        return self._mutate("operator-identity-resolve-review", operation)

    def retire(self, operator_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        analyst, reason, revision = self._metadata(payload)
        def operation(conn):
            self._ensure_state(conn, operator_id)
            event_id, now = self._event(conn, operator_id, "RETIRED", analyst, revision, reason, {})
            conn.execute("UPDATE operator_identity_state SET identity_status='RETIRED',activity_status='DORMANT',retired_at=?,updated_at=? WHERE operator_id=?", (now, now, operator_id))
            conn.execute("UPDATE operators SET status='RETIRED',updated_at=? WHERE operator_id=?", (now, operator_id))
            return {"operator_id": operator_id, "event_id": event_id, "identity_status": "RETIRED"}
        return self._mutate("operator-identity-retire", operation)

    def merge(self, destination_operator_id: str, source_operator_ids: Iterable[str], payload: dict[str, Any]) -> dict[str, Any]:
        analyst, reason, revision = self._metadata(payload)
        sources = sorted({str(value) for value in source_operator_ids if str(value) != destination_operator_id})
        if not sources:
            raise GovernanceError("At least one distinct source identity is required")
        def operation(conn):
            self._ensure_state(conn, destination_operator_id)
            for source in sources:
                self._ensure_state(conn, source)
            event_id, now = self._event(conn, destination_operator_id, "MERGED", analyst, revision, reason, {"source_operator_ids": sources})
            for source in sources:
                source_event_id, _ = self._event(
                    conn, source, "MERGED", analyst, revision, reason,
                    {"destination_operator_id": destination_operator_id},
                )
                conn.execute("INSERT INTO operator_identity_merges VALUES(?,?,?,?)", (source, destination_operator_id, source_event_id, now))
                conn.execute("UPDATE operator_identity_state SET identity_status='MERGED',merged_into_operator_id=?,updated_at=? WHERE operator_id=?", (destination_operator_id, now, source))
                conn.execute("UPDATE operators SET status='MERGED',updated_at=? WHERE operator_id=?", (now, source))
                conn.execute(
                    "INSERT OR IGNORE INTO operator_entities SELECT ?,entity_address,entity_type,confidence,evidence_count,first_seen,last_seen,added_at FROM operator_entities WHERE operator_id=?",
                    (destination_operator_id, source),
                )
                for asset in conn.execute(
                    "SELECT * FROM operator_identity_assets WHERE operator_id=?", (source,)
                ).fetchall():
                    asset_id = str(uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"operator-asset:{destination_operator_id}:{asset['asset_type']}:{asset['asset_value']}",
                    ))
                    conn.execute(
                        "INSERT OR IGNORE INTO operator_identity_assets "
                        "(asset_id,operator_id,asset_type,asset_value,status,evidence_revision,added_at,event_id) "
                        "VALUES(?,?,?,?,?,?,?,?)",
                        (asset_id, destination_operator_id, asset["asset_type"], asset["asset_value"],
                         asset["status"], revision, now, event_id),
                    )
                memberships = list(conn.execute("SELECT mint FROM operator_launch_membership WHERE operator_id=?", (source,)))
                for membership in memberships:
                    mint = membership[0]
                    conn.execute("UPDATE operator_launch_membership SET operator_id=?,assigned_at=?,event_id=? WHERE mint=?", (destination_operator_id, now, event_id, mint))
                    conn.execute("INSERT INTO operator_launch_assignment_history VALUES(?,?,?,?,?,?,?,?,?)", (str(uuid.uuid4()), mint, source, destination_operator_id, now, analyst, revision, reason, event_id))
            conn.execute("UPDATE operators SET updated_at=? WHERE operator_id=?", (now, destination_operator_id))
            return {"destination_operator_id": destination_operator_id, "source_operator_ids": sources, "event_id": event_id}
        return self._mutate("operator-identity-merge", operation)

    def split(self, parent_operator_id: str, children: list[dict[str, Any]], payload: dict[str, Any]) -> dict[str, Any]:
        analyst, reason, revision = self._metadata(payload)
        if len(children) < 2:
            raise GovernanceError("A split requires at least two child identities")
        def operation(conn):
            parent = self._operator(conn, parent_operator_id)
            self._ensure_state(conn, parent_operator_id)
            event_id, now = self._event(conn, parent_operator_id, "SPLIT", analyst, revision, reason, {"children": children})
            child_ids = []
            for index, child in enumerate(children):
                name = str(child.get("display_name") or "").strip()
                if not name:
                    raise GovernanceError("Every split child requires display_name")
                child_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"operator-split:{event_id}:{index}:{name}"))
                child_ids.append(child_id)
                conn.execute(
                    "INSERT INTO operators(operator_id,status,confidence,first_seen,last_seen,summary,review_state,display_name,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (child_id, "CONFIRMED", "CERTAIN", parent["first_seen"], parent["last_seen"], f"Split from {parent_operator_id}: {reason}", "REVIEWED", name, now, now),
                )
                conn.execute("INSERT INTO operator_identity_state(operator_id,identity_status,activity_status,source_population_id,updated_at) VALUES(?,?,?,?,?)", (child_id, "CONFIRMED", "ACTIVE", parent_operator_id, now))
                conn.execute("INSERT INTO operator_identity_splits VALUES(?,?,?,?)", (parent_operator_id, child_id, event_id, now))
                self._event(
                    conn, child_id, "CONFIRMED", analyst, revision,
                    f"Confirmed as a child identity split from {parent_operator_id}. {reason}",
                    {"parent_operator_id": parent_operator_id, "split_event_id": event_id},
                )
                for entity in child.get("entities") or []:
                    address = str(entity.get("entity_address") or "").strip()
                    if not address:
                        continue
                    conn.execute(
                        "INSERT OR IGNORE INTO operator_entities "
                        "(operator_id,entity_address,entity_type,confidence,evidence_count,first_seen,last_seen,added_at) "
                        "VALUES(?,?,?,?,?,?,?,?)",
                        (child_id, address, str(entity.get("entity_type") or "UNKNOWN").upper(),
                         "HIGH", 1, now, now, now),
                    )
                for mint in child.get("launches") or []:
                    previous = conn.execute("SELECT operator_id FROM operator_launch_membership WHERE mint=?", (mint,)).fetchone()
                    from_id = previous[0] if previous else parent_operator_id
                    conn.execute("INSERT INTO operator_launch_membership(mint,operator_id,source_population_id,assigned_at,event_id) VALUES(?,?,?,?,?) ON CONFLICT(mint) DO UPDATE SET operator_id=excluded.operator_id,assigned_at=excluded.assigned_at,event_id=excluded.event_id", (mint, child_id, parent_operator_id, now, event_id))
                    conn.execute("INSERT INTO operator_launch_assignment_history VALUES(?,?,?,?,?,?,?,?,?)", (str(uuid.uuid4()), mint, from_id, child_id, now, analyst, revision, reason, event_id))
            conn.execute("UPDATE operator_identity_state SET identity_status='SPLIT',updated_at=? WHERE operator_id=?", (now, parent_operator_id))
            conn.execute("UPDATE operators SET status='SPLIT',updated_at=? WHERE operator_id=?", (now, parent_operator_id))
            return {"parent_operator_id": parent_operator_id, "child_operator_ids": child_ids, "event_id": event_id}
        return self._mutate("operator-identity-split", operation)
