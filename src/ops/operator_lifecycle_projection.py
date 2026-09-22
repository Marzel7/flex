"""Canonical, downstream lifecycle state for qualified operations.

This module is deliberately not imported by birth, migration, matching, or
Watchtower decision code. ``post_commit_enrol`` is a small *consumer* invoked
only after a caller has committed a canonical membership mutation.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS operator_lifecycle_projection (
 operator_id TEXT NOT NULL REFERENCES operators(operator_id), mint TEXT NOT NULL,
 lifecycle_model_version TEXT NOT NULL, lifecycle_status TEXT NOT NULL,
 qualification_status TEXT NOT NULL, next_phase TEXT, next_enrichment_due_at INTEGER,
 facts_json TEXT NOT NULL, provenance_json TEXT NOT NULL, source_corpus_digest TEXT,
 created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, qualified_at INTEGER,
 PRIMARY KEY(operator_id,mint,lifecycle_model_version));
CREATE INDEX IF NOT EXISTS ix_operator_lifecycle_due ON operator_lifecycle_projection(next_enrichment_due_at);
CREATE TABLE IF NOT EXISTS operation_monitor_facts (
 operation_id TEXT NOT NULL,mint TEXT NOT NULL,cohort_class TEXT NOT NULL,assignment_timestamp INTEGER,assignment_provenance TEXT,
 entry_method TEXT NOT NULL,entry_timestamp INTEGER,entry_mc_usd REAL,entry_status TEXT,entry_exactness TEXT,
 latest_mc_usd REAL,latest_mc_timestamp INTEGER,current_multiple REAL,running_peak_mc_usd REAL,running_peak_timestamp INTEGER,running_peak_multiple REAL,drawdown_percent REAL,
 reached_2x INTEGER,reached_5x INTEGER,reached_10x INTEGER,first_2x_timestamp INTEGER,first_5x_timestamp INTEGER,first_10x_timestamp INTEGER,
 drawdown_25_timestamp INTEGER,drawdown_50_timestamp INTEGER,drawdown_75_timestamp INTEGER,drawdown_85_timestamp INTEGER,monitor_state TEXT NOT NULL,
 monitor_started_at INTEGER,last_observation_at INTEGER,next_observation_at INTEGER,monitor_completed_at INTEGER,provider_call_count INTEGER NOT NULL DEFAULT 0,candles_retained INTEGER NOT NULL DEFAULT 0,candle_resolution TEXT,evidence_status TEXT,provenance_digest TEXT NOT NULL,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL,PRIMARY KEY(operation_id,mint));
CREATE INDEX IF NOT EXISTS ix_operation_monitor_due ON operation_monitor_facts(operation_id,monitor_state,next_observation_at);
CREATE INDEX IF NOT EXISTS ix_operation_monitor_updated ON operation_monitor_facts(operation_id,updated_at);
CREATE TABLE IF NOT EXISTS operation_monitor_observations (
 operation_id TEXT NOT NULL,mint TEXT NOT NULL,observation_timestamp INTEGER NOT NULL,mc_usd REAL NOT NULL,resolution TEXT NOT NULL,
 source TEXT NOT NULL,request_identity TEXT NOT NULL,provenance_digest TEXT NOT NULL,created_at INTEGER NOT NULL,
 PRIMARY KEY(operation_id,mint,observation_timestamp,resolution));
CREATE INDEX IF NOT EXISTS ix_operation_monitor_observation_path ON operation_monitor_observations(operation_id,mint,observation_timestamp);
CREATE TABLE IF NOT EXISTS operation_monitor_mode_transitions (
 mode TEXT NOT NULL,effective_at INTEGER NOT NULL,previous_mode TEXT,config_source TEXT NOT NULL,contract_version TEXT NOT NULL,provenance TEXT NOT NULL,created_at INTEGER NOT NULL,
 PRIMARY KEY(mode,effective_at));
CREATE TABLE IF NOT EXISTS operation_playbooks (
 operator_id TEXT NOT NULL REFERENCES operators(operator_id), playbook_version INTEGER NOT NULL,
 lifecycle_model_version TEXT NOT NULL, source_corpus_digest TEXT NOT NULL, sample_size INTEGER NOT NULL,
 qualification_status TEXT NOT NULL, strategy_status TEXT NOT NULL, playbook_json TEXT NOT NULL,
 created_at INTEGER NOT NULL, qualified_at INTEGER, superseded_at INTEGER,
 PRIMARY KEY(operator_id,playbook_version));
"""
ACTUAL_QUALIFICATION = ("CONFIRMED", "ELIGIBLE")


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    fact_columns={r[1] for r in conn.execute('PRAGMA table_info(operation_monitor_facts)')}
    for name, typ in (
        ('retained_monitor_peak_mc_usd', 'REAL'), ('retained_monitor_peak_evidence', 'TEXT'),
        ('final_proven_ath_mc', 'REAL'), ('final_ath_multiple', 'REAL'),
        ('final_ath_evidence', 'TEXT'), ('final_ath_resolution', 'TEXT'),
        ('final_ath_bucket_start', 'INTEGER'), ('final_ath_bucket_end', 'INTEGER'),
        ('ath_finalized_at', 'INTEGER'), ('ath_finalization_request_id', 'TEXT'),
        ('ath_finalization_provenance_digest', 'TEXT'),
    ):
        if name not in fact_columns:
            conn.execute(f'ALTER TABLE operation_monitor_facts ADD COLUMN {name} {typ}')
    columns={r[1] for r in conn.execute('PRAGMA table_info(operation_monitor_observations)')}
    for name in ('open_mc_usd','high_mc_usd','low_mc_usd','close_mc_usd'):
        if name not in columns: conn.execute(f'ALTER TABLE operation_monitor_observations ADD COLUMN {name} REAL')


def record_configured_monitor_mode(
    conn: sqlite3.Connection, mode: str, *, config_source: str,
    provenance: dict[str, Any], effective_at: int | None = None,
) -> bool:
    """Append a real Operations mode transition once, never on a same-mode restart.

    This is intentionally called from the normal Operations startup path, not from
    a read route.  ``effective_at`` is only supplied for a reconstructed legacy
    boundary; normal transitions use the observed startup epoch.
    """
    ensure_schema(conn)
    mode = mode.upper()
    previous = conn.execute(
        "SELECT mode FROM operation_monitor_mode_transitions ORDER BY effective_at DESC, created_at DESC LIMIT 1"
    ).fetchone()
    if previous and str(previous[0]).upper() == mode:
        return False
    now = int(time.time())
    conn.execute(
        "INSERT INTO operation_monitor_mode_transitions(mode,effective_at,previous_mode,config_source,contract_version,provenance,created_at) VALUES(?,?,?,?,?,?,?)",
        (mode, int(effective_at or now), str(previous[0]) if previous else "OFF",
         config_source, "operation-monitor-mode-transition.v1",
         json.dumps(provenance, sort_keys=True, separators=(",", ":")), now),
    )
    return True


def seed_legacy_monitor_activation_boundary(
    conn: sqlite3.Connection, *, earliest_possible: int, latest_confirmed: int,
    sources: list[str], confidence: str = 'BOUNDED',
) -> bool:
    """One-time migration record for the activation that predates this table."""
    ensure_schema(conn)
    legacy = conn.execute(
        "SELECT 1 FROM operation_monitor_mode_transitions WHERE provenance LIKE '%LEGACY_ACTIVATION_BOUNDARY_RECONSTRUCTED%'"
    ).fetchone()
    if legacy:
        return False
    # The initial startup observation can only exist because this table arrived
    # after activation.  Replace that synthetic observation, never add a second
    # MONITOR transition for the same historical event.
    conn.execute("DELETE FROM operation_monitor_mode_transitions WHERE mode='MONITOR' AND provenance LIKE '%OBSERVED_STARTUP%'")
    now = int(time.time())
    provenance = {
        'source': 'LEGACY_ACTIVATION_BOUNDARY_RECONSTRUCTED',
        'boundary_type': confidence,
        'earliest_possible_activation': int(earliest_possible),
        'latest_confirmed_activation': int(latest_confirmed),
        'sources': sources,
        'confidence': 'HIGH_FOR_BOUND_ONLY',
        'reconstructed_at': now,
    }
    conn.execute(
        "INSERT INTO operation_monitor_mode_transitions(mode,effective_at,previous_mode,config_source,contract_version,provenance,created_at) VALUES(?,?,?,?,?,?,?)",
        ('MONITOR', int(latest_confirmed), 'OFF', 'LEGACY_RECONSTRUCTION',
         'operation-monitor-mode-transition.v1', json.dumps(provenance, sort_keys=True, separators=(',', ':')), now),
    )
    return True


def persist_monitor_mode_transition(
    db_path: str | Path, mode: str, *, config_source: str, provenance: dict[str, Any],
    legacy_boundary: tuple[int, int] | None = None,
) -> bool:
    """Persist a Monitor transition exclusively through the shared writer.

    Read-only inspection determines idempotence; all mutations are one short,
    atomic ``commit_write_and_wait`` batch.  The bounded interval is retained in
    provenance, so ``effective_at`` remains an ordering point rather than a
    fabricated claim of the precise historical activation second.
    """
    from src.core.db_write_queue import WriteItem
    from src.core.db_writer import commit_write_and_wait
    mode = mode.upper()
    now = int(time.time())
    with sqlite3.connect(str(db_path), timeout=5) as read_conn:
        latest = read_conn.execute(
            "SELECT mode FROM operation_monitor_mode_transitions ORDER BY effective_at DESC, created_at DESC LIMIT 1"
        ).fetchone()
        legacy_exists = read_conn.execute(
            "SELECT 1 FROM operation_monitor_mode_transitions WHERE provenance LIKE '%LEGACY_ACTIVATION_BOUNDARY_RECONSTRUCTED%'"
        ).fetchone()
    if legacy_boundary is None and latest and str(latest[0]).upper() == mode:
        return False
    if legacy_boundary is not None and legacy_exists:
        return False
    previous = str(latest[0]) if latest else 'OFF'
    statements: list[tuple[str, tuple[Any, ...]]] = []
    if legacy_boundary is not None:
        earliest, confirmed = legacy_boundary
        provenance = {**provenance, 'source': 'LEGACY_ACTIVATION_BOUNDARY_RECONSTRUCTED',
                      'boundary_type': 'BOUNDED', 'earliest_possible_activation': int(earliest),
                      'latest_confirmed_activation': int(confirmed), 'reconstructed_at': now}
        # Supersede only the one synthetic same-mode startup observation created
        # before legacy reconstruction, never a genuine later transition.
        statements.append(("DELETE FROM operation_monitor_mode_transitions WHERE mode='MONITOR' AND provenance LIKE '%OBSERVED_STARTUP%'", ()))
        previous, effective_at, config_source = 'OFF', int(confirmed), 'LEGACY_RECONSTRUCTION'
    else:
        effective_at = now
    statements.append((
        "INSERT INTO operation_monitor_mode_transitions(mode,effective_at,previous_mode,config_source,contract_version,provenance,created_at) VALUES(?,?,?,?,?,?,?)",
        (mode, effective_at, previous, config_source, 'operation-monitor-mode-transition.v1',
         json.dumps(provenance, sort_keys=True, separators=(',', ':')), now),
    ))
    receipt = commit_write_and_wait(str(db_path), WriteItem(
        'enrichment', 'operation-monitor-mode-transition', statements,
        f"operation-monitor-transition:{mode}:{legacy_boundary or effective_at}",
    ))
    return bool(receipt.committed)


def enrol(conn: sqlite3.Connection, operator_id: str, mint: str, model: str, *,
          status: str = "PENDING", phase: str | None = "OPENING", due: int | None = None,
          provenance: dict[str, Any] | None = None) -> None:
    """Ensure one lifecycle state after membership is already canonical."""
    if not conn.execute("SELECT 1 FROM operators WHERE operator_id=?", (operator_id,)).fetchone():
        raise ValueError("UNKNOWN_OPERATOR")
    if not conn.execute("SELECT 1 FROM operator_launch_membership WHERE operator_id=? AND mint=?", (operator_id, mint)).fetchone():
        raise ValueError("NOT_CANONICAL_MEMBER")
    now = int(time.time())
    conn.execute("INSERT OR IGNORE INTO operator_lifecycle_projection VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (operator_id, mint, model, status, "PENDING", phase, due, "{}", json.dumps(provenance or {}, sort_keys=True), None, now, now, None))


def actual_operation_model(conn: sqlite3.Connection, operator_id: str) -> str | None:
    """Return an explicitly qualified model for an Actual operation only."""
    try:
        contract = conn.execute("SELECT 1 FROM operation_qualification_contracts WHERE operator_id=? AND qualification_category=? AND automation_eligibility=? ORDER BY created_at DESC LIMIT 1", (operator_id, *ACTUAL_QUALIFICATION)).fetchone()
    except sqlite3.Error:
        return None
    if not contract:
        return None
    try:
        row = conn.execute("SELECT lifecycle_model_version FROM operation_playbooks WHERE operator_id=? AND qualification_status='HISTORICAL_EVIDENCE_QUALIFIED' AND superseded_at IS NULL ORDER BY playbook_version DESC LIMIT 1", (operator_id,)).fetchone()
    except sqlite3.Error:
        return None
    return str(row[0]) if row else None


def post_commit_enrol(db_path: str | Path, operator_id: str, mint: str, *, source: str,
                      now: int | None = None) -> dict[str, Any]:
    """Best-effort post-commit consumer that cannot undo an assignment."""
    try:
        conn = sqlite3.connect(str(db_path), timeout=2)
        try:
            model = actual_operation_model(conn, operator_id)
            if model is None:
                conn.rollback()
                return {"status": "NOT_ELIGIBLE_OR_MODEL_UNQUALIFIED", "operator_id": operator_id, "mint": mint}
            ensure_schema(conn)
            enrol(conn, operator_id, mint, model, due=now, provenance={"source": source, "post_commit": True})
            conn.commit()
            return {"status": "ENROLLED", "operator_id": operator_id, "mint": mint, "lifecycle_model_version": model}
        finally:
            conn.close()
    except Exception as exc:  # failure isolation at the post-commit seam
        return {"status": "FAILED", "operator_id": operator_id, "mint": mint, "error_type": type(exc).__name__}


def bootstrap_after_actual_commit(db_path: str | Path, operator_id: str, *,
                                  source: str = "ACTUAL_OPERATION_BOOTSTRAP") -> dict[str, Any]:
    """Idempotently enrol already committed canonical members, post-promotion."""
    try:
        conn = sqlite3.connect(str(db_path), timeout=2)
        try:
            rows = conn.execute("SELECT mint FROM operator_launch_membership WHERE operator_id=? ORDER BY mint", (operator_id,)).fetchall()
        finally:
            conn.close()
    except Exception as exc:
        return {"status": "FAILED", "operator_id": operator_id, "error_type": type(exc).__name__}
    outcomes = [post_commit_enrol(db_path, operator_id, str(row[0]), source=source) for row in rows]
    return {"status": "BOOTSTRAPPED", "operator_id": operator_id, "members": len(rows), "enrolled": sum(item["status"] == "ENROLLED" for item in outcomes), "outcomes": outcomes}


def materialize(conn: sqlite3.Connection, operator_id: str, model: str, rows: list[dict[str, Any]], provenance: dict[str, Any]) -> None:
    ensure_schema(conn)
    now = int(time.time())
    for row in rows:
        enrol(conn, operator_id, row["mint"], model, status="COMPLETE", phase=None, provenance=provenance)
        conn.execute("UPDATE operator_lifecycle_projection SET lifecycle_status=?,qualification_status=?,facts_json=?,provenance_json=?,source_corpus_digest=?,updated_at=?,qualified_at=? WHERE operator_id=? AND mint=? AND lifecycle_model_version=?", ("COMPLETE", row["evidence_status"], json.dumps(row, sort_keys=True), json.dumps(provenance, sort_keys=True), provenance["source_digest"], now, now, operator_id, row["mint"], model))


def create_playbook_candidate(conn: sqlite3.Connection, operator_id: str, model: str, digest: str, sample: int, payload: dict[str, Any]) -> int:
    """Append a candidate only; this never promotes a strategy."""
    ensure_schema(conn)
    encoded = json.dumps(payload, sort_keys=True)
    existing = conn.execute("SELECT playbook_version FROM operation_playbooks WHERE operator_id=? AND lifecycle_model_version=? AND source_corpus_digest=? AND playbook_json=?", (operator_id, model, digest, encoded)).fetchone()
    if existing:
        return int(existing[0])
    now = int(time.time())
    version = conn.execute("SELECT COALESCE(MAX(playbook_version),0)+1 FROM operation_playbooks WHERE operator_id=?", (operator_id,)).fetchone()[0]
    conn.execute("INSERT INTO operation_playbooks VALUES(?,?,?,?,?,?,?,?,?,NULL,NULL)", (operator_id, version, model, digest, sample, "HISTORICAL_EVIDENCE_QUALIFIED", "STRATEGY_ANALYSIS_PENDING", encoded, now))
    return int(version)


def read_playbook_projection(conn: sqlite3.Connection, operator_id: str) -> dict[str, Any] | None:
    """Small DB-only generic Operation Playbook read projection."""
    conn.row_factory = sqlite3.Row
    operator = conn.execute("SELECT operator_id,display_name,status,confidence,updated_at FROM operators WHERE operator_id=?", (operator_id,)).fetchone()
    if not operator:
        return None
    qualification = conn.execute("SELECT qualification_category,automation_eligibility,detector_version,created_at FROM operation_qualification_contracts WHERE operator_id=? ORDER BY created_at DESC LIMIT 1", (operator_id,)).fetchone()
    playbook = conn.execute("SELECT * FROM operation_playbooks WHERE operator_id=? AND superseded_at IS NULL ORDER BY playbook_version DESC LIMIT 1", (operator_id,)).fetchone()
    coverage = conn.execute("SELECT COUNT(*) AS total, SUM(qualification_status='QUALIFIED') AS qualified, SUM(qualification_status='INSUFFICIENT_EVIDENCE') AS insufficient, SUM(lifecycle_status='PENDING') AS pending FROM operator_lifecycle_projection WHERE operator_id=?", (operator_id,)).fetchone()
    members = conn.execute("SELECT COUNT(*) FROM operator_launch_membership WHERE operator_id=?", (operator_id,)).fetchone()[0]
    payload = json.loads(playbook["playbook_json"]) if playbook else {}
    playbook_data = ({key: playbook[key] for key in playbook.keys() if key != "playbook_json"} | {"content": payload}) if playbook else None
    return {"operator": dict(operator), "qualification": dict(qualification) if qualification else None, "member_count": members, "lifecycle": dict(coverage), "playbook": playbook_data, "limitations": ["Historical evidence is not an executable trading rule."] if playbook else ["No qualified lifecycle model is registered."]}


def list_playbook_projections(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """List existing versioned Playbooks; this is a read index, not a registry."""
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT o.operator_id,o.display_name,p.playbook_version,p.qualification_status,"
        "p.strategy_status,p.lifecycle_model_version,p.sample_size,p.created_at "
        "FROM operation_playbooks p JOIN operators o ON o.operator_id=p.operator_id "
        "WHERE p.superseded_at IS NULL ORDER BY o.display_name,p.playbook_version DESC"
    ).fetchall()
    return [dict(row) for row in rows]
