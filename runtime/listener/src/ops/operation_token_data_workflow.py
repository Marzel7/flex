"""Durable, post-commit-only token-data workflow orchestration records."""
from __future__ import annotations
import json, sqlite3, time, uuid
from pathlib import Path

WORKFLOW_VERSION = "OPERATION_TOKEN_DATA_WORKFLOW_V1"
WORKER_VERSION = "OPERATION_TOKEN_DATA_WORKER_V1"
PLAYBOOK_VERSION = "OPERATION_TOKEN_DATA_PLAYBOOK_V1"
SCHEMA = """CREATE TABLE IF NOT EXISTS operation_token_data_workflows (
 workflow_id TEXT PRIMARY KEY, operation_id TEXT NOT NULL, playbook_version TEXT NOT NULL,
 state TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
 source_event TEXT NOT NULL, source_commit_reference TEXT, eligible_cohort_state TEXT NOT NULL,
 sample_state TEXT NOT NULL, anchor_state TEXT NOT NULL, acquisition_state TEXT NOT NULL,
 offline_finalization_state TEXT NOT NULL, completion_state TEXT NOT NULL, last_error TEXT,
 artifacts_json TEXT NOT NULL, current_run_id TEXT, lease_token TEXT, lease_expires_at INTEGER,
 started_at INTEGER, completed_at INTEGER, UNIQUE(operation_id, playbook_version));"""

def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    columns={r[1] for r in conn.execute("PRAGMA table_info(operation_token_data_workflows)")}
    for name, typ in (("current_run_id","TEXT"),("lease_token","TEXT"),("lease_expires_at","INTEGER"),("started_at","INTEGER"),("completed_at","INTEGER")):
        if name not in columns: conn.execute(f"ALTER TABLE operation_token_data_workflows ADD COLUMN {name} {typ}")

def ensure_workflow(db_path: str | Path, operation_id: str, *, source_event: str = "POST_COMMIT_OPERATION_PROMOTION") -> dict:
    """Idempotently create then provider-free-bootstrap a runnable workflow."""
    now = int(time.time()); conn = sqlite3.connect(str(db_path))
    try:
        ensure_schema(conn)
        row = conn.execute("SELECT * FROM operation_token_data_workflows WHERE operation_id=? AND playbook_version=?", (operation_id, PLAYBOOK_VERSION)).fetchone()
        if row:
            conn.commit(); return {"workflow_id": row[0], "created": False, "state": row[3]}
        count = conn.execute("SELECT COUNT(*) FROM operator_launch_membership WHERE operator_id=?", (operation_id,)).fetchone()[0]
        state = "READY_FOR_ACQUISITION" if count else "COMPLETE_NO_ELIGIBLE_TOKENS"
        ident = str(uuid.uuid4())
        conn.execute("INSERT INTO operation_token_data_workflows (workflow_id,operation_id,playbook_version,state,created_at,updated_at,source_event,source_commit_reference,eligible_cohort_state,sample_state,anchor_state,acquisition_state,offline_finalization_state,completion_state,last_error,artifacts_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (ident, operation_id, PLAYBOOK_VERSION, state, now, now, source_event, None, "ELIGIBLE_RETAINED" if count else "NO_ELIGIBLE_TOKENS", "SAMPLE_PENDING" if count else "NOT_APPLICABLE", "ANCHORS_PENDING" if count else "NOT_APPLICABLE", "NOT_STARTED", "NOT_STARTED", "PENDING" if count else "COMPLETE", None, json.dumps({"workflow_contract": WORKFLOW_VERSION}, sort_keys=True)))
        conn.commit(); return {"workflow_id": ident, "created": True, "state": state}
    except Exception:
        conn.rollback(); raise
    finally: conn.close()

def post_commit_ensure(db_path: str | Path, operation_id: str) -> dict:
    """Failure-isolating post-commit consumer; never calls a provider."""
    try: return ensure_workflow(db_path, operation_id)
    except Exception as exc: return {"created": False, "state": "BOOTSTRAP_FAILED", "error_type": type(exc).__name__}

def read_workflow(db_path: str | Path, operation_id: str) -> dict | None:
    conn=sqlite3.connect(str(db_path)); conn.row_factory=sqlite3.Row
    try:
        ensure_schema(conn); row=conn.execute("SELECT * FROM operation_token_data_workflows WHERE operation_id=? AND playbook_version=?",(operation_id,PLAYBOOK_VERSION)).fetchone(); return dict(row) if row else None
    finally: conn.close()


class OperationTokenDataWorker:
    """Single-item durable worker; runtime/finalizer are injected production seams."""
    def __init__(self, db_path: str | Path, runtime, finalizer, *, now=None):
        self.db_path, self.runtime, self.finalizer = str(db_path), runtime, finalizer
        self.now = now or (lambda: int(time.time()))

    def claim_one(self) -> dict | None:
        """Atomically persist ACQUIRING before the caller can dispatch work."""
        conn=sqlite3.connect(self.db_path, timeout=5); conn.row_factory=sqlite3.Row
        try:
            ensure_schema(conn); conn.execute("BEGIN IMMEDIATE")
            row=conn.execute("SELECT workflow_id FROM operation_token_data_workflows WHERE state='READY_FOR_ACQUISITION' ORDER BY created_at LIMIT 1").fetchone()
            if not row: conn.rollback(); return None
            now=self.now(); token=str(uuid.uuid4()); run_id=f"token-data-{uuid.uuid4().hex}"
            changed=conn.execute("UPDATE operation_token_data_workflows SET state='ACQUIRING',current_run_id=?,lease_token=?,lease_expires_at=?,started_at=?,updated_at=? WHERE workflow_id=? AND state='READY_FOR_ACQUISITION'",(run_id,token,now+300,now,now,row[0])).rowcount
            if changed != 1: conn.rollback(); return None
            record=dict(conn.execute("SELECT * FROM operation_token_data_workflows WHERE workflow_id=?",(row[0],)).fetchone() or {})
            conn.commit(); return record
        finally: conn.close()

    def _update(self, workflow_id: str, **changes) -> None:
        conn=sqlite3.connect(self.db_path)
        try:
            ensure_schema(conn); changes['updated_at']=self.now(); sql=','.join(f'{k}=?' for k in changes); conn.execute(f"UPDATE operation_token_data_workflows SET {sql} WHERE workflow_id=?",(*changes.values(),workflow_id)); conn.commit()
        finally: conn.close()

    def process_once(self) -> dict | None:
        work=self.claim_one()
        if not work: return self.resume_finalization()
        # No connection/transaction is open during runtime, retry sleeps, or parsing.
        try:
            self.runtime(work)
            self._update(work['workflow_id'],state='OFFLINE_FINALIZATION_PENDING',acquisition_state='TERMINAL_SAFE')
        except Exception as exc:
            self._update(work['workflow_id'],state='BLOCKED',last_error=type(exc).__name__)
            return {'workflow_id':work['workflow_id'],'state':'BLOCKED'}
        return self.resume_finalization(work['workflow_id'])

    def resume_finalization(self, workflow_id: str | None = None) -> dict | None:
        conn=sqlite3.connect(self.db_path); conn.row_factory=sqlite3.Row
        try:
            ensure_schema(conn)
            row=conn.execute("SELECT * FROM operation_token_data_workflows WHERE state='OFFLINE_FINALIZATION_PENDING'"+(" AND workflow_id=?" if workflow_id else " ORDER BY updated_at LIMIT 1"),((workflow_id,) if workflow_id else ())).fetchone()
            if not row: return None
            work=dict(row)
        finally: conn.close()
        try:
            outcome=self.finalizer(work) or {}
            state=outcome.get('state','COMPLETE_WITH_LEGITIMATE_GAPS')
            if state not in {'COMPLETE','COMPLETE_WITH_LEGITIMATE_GAPS','SAMPLE_EXPANSION_REQUIRED','BLOCKED','FAILED'}: state='BLOCKED'
            self._update(work['workflow_id'],state=state,completion_state=state,offline_finalization_state='COMPLETE',completed_at=self.now(),lease_token=None,lease_expires_at=None)
            return {'workflow_id':work['workflow_id'],'state':state}
        except Exception as exc:
            self._update(work['workflow_id'],state='BLOCKED',last_error=type(exc).__name__)
            return {'workflow_id':work['workflow_id'],'state':'BLOCKED'}
