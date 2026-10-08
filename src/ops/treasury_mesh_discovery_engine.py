"""Bounded, resumable, review-only Watchtower treasury-mesh discovery.

The engine owns an *isolated* SQLite evidence store.  It deliberately has no
network client, scheduler, runtime registration, or canonical database write
path: callers inject a bounded RPC adapter during DEV qualification or a
separately approved future integration.  Only compact page boundaries,
statuses, and decoded native-SOL facts are retained.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
import sqlite3
import time
from typing import Any, Iterable, Mapping, Protocol

from src.ops.treasury_rotation_discovery import (
    FUNDING_ACCOUNT_MATCH,
    INSUFFICIENT_EVIDENCE,
    KNOWN_SUBPROVIDER_MATCH,
    NEW_TREASURY_CANDIDATE,
    PARTIAL_LINEAGE,
    CONFIRMED_TREASURY_MATCH,
    extract_compact_native_facts,
    signature_page_boundary,
    signature_window_coverage,
    transaction_request_config,
    transaction_version_status,
)

DISCOVERY_SCHEMA_VERSION = "dev019-mesh-v1"
MAX_SIGNATURES_PER_PAGE = 20
MAX_COMPACT_RECORD_BYTES = 16_384
MAX_STORE_BYTES = 10_000_000
MATERIAL_SCREENING_LAMPORTS = 10_000_000_000
DIRECT = "DIRECT"
INBOUND = "INBOUND"
OUTBOUND = "OUTBOUND"
WAITING_PAGE = "WAITING_PAGE"
PAGE_INCOMPLETE = "PAGE_INCOMPLETE"
PAGE_COMPLETE = "PAGE_COMPLETE"
PROVIDER_LIMIT_STOP = "PROVIDER_LIMIT_STOP"
NEEDS_AUTHORITATIVE_CREATOR = "NEEDS_AUTHORITATIVE_CREATOR"

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS wt_mesh_jobs (
 job_id INTEGER PRIMARY KEY,
 seed_kind TEXT NOT NULL CHECK(seed_kind IN ('TREASURY','LAUNCH')),
 seed_value TEXT NOT NULL,
 address TEXT NOT NULL,
 direction TEXT NOT NULL CHECK(direction IN ('INBOUND','OUTBOUND')),
 hop INTEGER NOT NULL CHECK(hop >= 0),
 state TEXT NOT NULL,
 next_cursor TEXT,
 next_page INTEGER NOT NULL DEFAULT 1,
 parent_job_id INTEGER,
 created_at INTEGER NOT NULL,
 updated_at INTEGER NOT NULL,
 UNIQUE(seed_kind, seed_value, address, direction, hop)
);
CREATE TABLE IF NOT EXISTS wt_mesh_pages (
 job_id INTEGER NOT NULL REFERENCES wt_mesh_jobs(job_id),
 page_number INTEGER NOT NULL,
 before_cursor TEXT,
 boundary_json TEXT NOT NULL,
 signatures_json TEXT NOT NULL,
 coverage_state TEXT NOT NULL,
 PRIMARY KEY(job_id, page_number)
);
CREATE TABLE IF NOT EXISTS wt_mesh_signature_decodes (
 signature TEXT PRIMARY KEY,
 transaction_version TEXT,
 status TEXT NOT NULL,
 error_code INTEGER,
 decoded_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS wt_mesh_page_signatures (
 job_id INTEGER NOT NULL,
 page_number INTEGER NOT NULL,
 signature TEXT NOT NULL REFERENCES wt_mesh_signature_decodes(signature),
 PRIMARY KEY(job_id, page_number, signature),
 FOREIGN KEY(job_id, page_number) REFERENCES wt_mesh_pages(job_id, page_number)
);
CREATE TABLE IF NOT EXISTS wt_mesh_funding_facts (
 signature TEXT NOT NULL,
 kind TEXT NOT NULL,
 sender TEXT NOT NULL,
 receiver TEXT NOT NULL,
 lamports INTEGER,
 slot INTEGER,
 transaction_index INTEGER,
 instruction_index INTEGER,
 inner_instruction_index INTEGER,
 source_balance_delta INTEGER,
 receiver_balance_delta INTEGER,
 balance_delta_verified INTEGER NOT NULL,
 route_semantics TEXT NOT NULL,
 provenance TEXT NOT NULL,
 PRIMARY KEY(signature, kind, instruction_index, inner_instruction_index)
);
CREATE TABLE IF NOT EXISTS wt_mesh_launch_seeds (
 launch_mint TEXT PRIMARY KEY,
 creator TEXT,
 creator_signature TEXT,
 slot INTEGER,
 transaction_index INTEGER,
 instruction_index INTEGER,
 canonical_creator_launch INTEGER NOT NULL,
 state TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS wt_mesh_activity (
 address TEXT PRIMARY KEY,
 last_transaction_at INTEGER,
 last_meaningful_funding_at INTEGER,
 coverage_complete INTEGER NOT NULL DEFAULT 0,
 updated_at INTEGER NOT NULL
);
"""


class DiscoveryError(RuntimeError):
    pass


class BudgetExceeded(DiscoveryError):
    pass


class ProviderLimited(DiscoveryError):
    pass


class DiscoveryClient(Protocol):
    def get_signatures(self, address: str, direction: str, before: str | None, limit: int) -> Iterable[Mapping[str, Any]]: ...
    def get_transaction(self, signature: str, config: Mapping[str, Any]) -> Mapping[str, Any] | None: ...


@dataclass
class DiscoveryBudget:
    max_rpc_calls: int
    max_seconds: int
    max_hops: int
    max_concurrency: int = 1
    rpc_calls: int = 0
    started_at: float | None = None

    def start(self) -> None:
        if self.max_concurrency != 1:
            raise DiscoveryError("CONCURRENCY_MUST_BE_ONE")
        self.started_at = time.monotonic()

    def charge(self) -> None:
        if self.started_at is None:
            self.start()
        if time.monotonic() - self.started_at > self.max_seconds:
            raise BudgetExceeded("TIME_BUDGET_EXCEEDED")
        if self.rpc_calls >= self.max_rpc_calls:
            raise BudgetExceeded("RPC_BUDGET_EXCEEDED")
        self.rpc_calls += 1


def _now() -> int:
    return int(time.time())


def _compact(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode()) > MAX_COMPACT_RECORD_BYTES:
        raise DiscoveryError("COMPACT_RECORD_LIMIT_EXCEEDED")
    return encoded


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def _assert_store_bound(conn: sqlite3.Connection) -> None:
    row = conn.execute("PRAGMA database_list").fetchone()
    path = row[2] if row else ""
    if path and path != ":memory:" and os.path.exists(path) and os.path.getsize(path) > MAX_STORE_BYTES:
        raise DiscoveryError("DISCOVERY_STORE_SIZE_LIMIT_EXCEEDED")


def open_isolated_store(path: str) -> sqlite3.Connection:
    resolved = os.path.realpath(path)
    if not resolved.startswith("/private/tmp/"):
        raise DiscoveryError("DISCOVERY_STORE_MUST_BE_TEMPORARY")
    conn = sqlite3.connect(resolved)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    _assert_store_bound(conn)
    return conn


def seed_confirmed_treasuries(conn: sqlite3.Connection, treasuries: Iterable[str], *, now: int | None = None) -> list[int]:
    """Create bounded inbound and outbound jobs per distinct confirmed seed."""
    ensure_schema(conn)
    timestamp = _now() if now is None else int(now)
    ids: list[int] = []
    for treasury in sorted(set(str(item) for item in treasuries if item)):
        for direction in (INBOUND, OUTBOUND):
            conn.execute(
                "INSERT OR IGNORE INTO wt_mesh_jobs(seed_kind,seed_value,address,direction,hop,state,next_cursor,next_page,parent_job_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                ("TREASURY", treasury, treasury, direction, 0, WAITING_PAGE, None, 1, None, timestamp, timestamp),
            )
            row = conn.execute("SELECT job_id FROM wt_mesh_jobs WHERE seed_kind='TREASURY' AND seed_value=? AND address=? AND direction=? AND hop=0", (treasury, treasury, direction)).fetchone()
            ids.append(int(row[0]))
    _assert_store_bound(conn)
    return ids


def seed_known_intermediary(conn: sqlite3.Connection, *, treasury_seed: str, address: str,
                            direction: str = OUTBOUND, hop: int = 1,
                            now: int | None = None) -> int:
    """Seed a review-context intermediary without confirming its identity."""
    if not treasury_seed or not address or direction not in {INBOUND, OUTBOUND} or hop < 1:
        raise DiscoveryError("INVALID_INTERMEDIARY_SEED")
    timestamp = _now() if now is None else int(now)
    conn.execute(
        "INSERT OR IGNORE INTO wt_mesh_jobs(seed_kind,seed_value,address,direction,hop,state,next_cursor,next_page,parent_job_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        ("TREASURY", treasury_seed, address, direction, hop, WAITING_PAGE, None, 1, None, timestamp, timestamp),
    )
    row = conn.execute("SELECT job_id FROM wt_mesh_jobs WHERE seed_kind='TREASURY' AND seed_value=? AND address=? AND direction=? AND hop=?", (treasury_seed, address, direction, hop)).fetchone()
    _assert_store_bound(conn)
    return int(row[0])


def seed_unresolved_launch(conn: sqlite3.Connection, *, launch_mint: str, creator_event: Mapping[str, Any] | None,
                           now: int | None = None) -> int | None:
    """Seed launch-backward discovery only from explicit canonical creator evidence."""
    ensure_schema(conn)
    timestamp = _now() if now is None else int(now)
    event = dict(creator_event or {})
    canonical = bool(event.get("canonical_creator_launch"))
    creator = str(event.get("creator") or "") or None
    state = WAITING_PAGE if canonical and creator else NEEDS_AUTHORITATIVE_CREATOR
    conn.execute(
        "INSERT INTO wt_mesh_launch_seeds VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(launch_mint) DO UPDATE SET creator=excluded.creator,creator_signature=excluded.creator_signature,slot=excluded.slot,transaction_index=excluded.transaction_index,instruction_index=excluded.instruction_index,canonical_creator_launch=excluded.canonical_creator_launch,state=excluded.state",
        (launch_mint, creator, event.get("signature"), event.get("slot"), event.get("transaction_index"), event.get("instruction_index"), int(canonical), state),
    )
    if state != WAITING_PAGE:
        return None
    conn.execute(
        "INSERT OR IGNORE INTO wt_mesh_jobs(seed_kind,seed_value,address,direction,hop,state,next_cursor,next_page,parent_job_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        ("LAUNCH", launch_mint, creator, INBOUND, 0, WAITING_PAGE, None, 1, None, timestamp, timestamp),
    )
    row = conn.execute("SELECT job_id FROM wt_mesh_jobs WHERE seed_kind='LAUNCH' AND seed_value=? AND address=? AND direction=? AND hop=0", (launch_mint, creator, INBOUND)).fetchone()
    _assert_store_bound(conn)
    return int(row[0])


def _job(conn: sqlite3.Connection, job_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM wt_mesh_jobs WHERE job_id=?", (job_id,)).fetchone()
    if row is None:
        raise DiscoveryError("UNKNOWN_DISCOVERY_JOB")
    return row


def record_activity_transaction(conn: sqlite3.Connection, *, address: str, observed_at: int | None,
                                now: int | None = None) -> None:
    """Persist compact bounded signature activity, never a raw signature page."""
    if not address or not isinstance(observed_at, int) or observed_at < 0:
        return
    timestamp = _now() if now is None else int(now)
    conn.execute(
        "INSERT INTO wt_mesh_activity(address,last_transaction_at,last_meaningful_funding_at,coverage_complete,updated_at) VALUES(?,?,NULL,0,?) "
        "ON CONFLICT(address) DO UPDATE SET last_transaction_at=MAX(COALESCE(last_transaction_at,0),excluded.last_transaction_at),updated_at=excluded.updated_at",
        (address, observed_at, timestamp),
    )


def record_activity_meaningful_funding(conn: sqlite3.Connection, *, address: str, observed_at: int | None,
                                        now: int | None = None) -> None:
    if not address or not isinstance(observed_at, int) or observed_at < 0:
        return
    timestamp = _now() if now is None else int(now)
    conn.execute(
        "INSERT INTO wt_mesh_activity(address,last_transaction_at,last_meaningful_funding_at,coverage_complete,updated_at) VALUES(?,NULL,?,0,?) "
        "ON CONFLICT(address) DO UPDATE SET last_meaningful_funding_at=MAX(COALESCE(last_meaningful_funding_at,0),excluded.last_meaningful_funding_at),updated_at=excluded.updated_at",
        (address, observed_at, timestamp),
    )


def activity_snapshot(conn: sqlite3.Connection, *, address: str) -> dict:
    row = conn.execute("SELECT address,last_transaction_at,last_meaningful_funding_at,coverage_complete FROM wt_mesh_activity WHERE address=?", (address,)).fetchone()
    return dict(row) if row else {"address": address, "last_transaction_at": None, "last_meaningful_funding_at": None, "coverage_complete": 0}


def _mark_activity_coverage_complete(conn: sqlite3.Connection, *, address: str, now: int | None = None) -> None:
    timestamp = _now() if now is None else int(now)
    conn.execute(
        "INSERT INTO wt_mesh_activity(address,last_transaction_at,last_meaningful_funding_at,coverage_complete,updated_at) VALUES(?,NULL,NULL,1,?) "
        "ON CONFLICT(address) DO UPDATE SET coverage_complete=1,updated_at=excluded.updated_at",
        (address, timestamp),
    )


def record_signature_page(conn: sqlite3.Connection, *, job_id: int, signatures: Iterable[str], now: int | None = None) -> dict:
    """Durably record a bounded page before decoding any selected signature."""
    job = _job(conn, job_id)
    if job["state"] not in {WAITING_PAGE, PAGE_INCOMPLETE}:
        raise DiscoveryError("JOB_NOT_READY_FOR_PAGE")
    if job["state"] == PAGE_INCOMPLETE:
        existing = conn.execute("SELECT boundary_json FROM wt_mesh_pages WHERE job_id=? AND page_number=?", (job_id, job["next_page"])).fetchone()
        return json.loads(existing[0])
    deduped = list(dict.fromkeys(str(sig) for sig in signatures if sig))
    if len(deduped) > MAX_SIGNATURES_PER_PAGE:
        raise DiscoveryError("SIGNATURE_PAGE_LIMIT_EXCEEDED")
    boundary = signature_page_boundary(page_number=job["next_page"], before_cursor=job["next_cursor"], signatures=deduped)
    timestamp = _now() if now is None else int(now)
    conn.execute("INSERT INTO wt_mesh_pages VALUES(?,?,?,?,?,?)", (job_id, job["next_page"], job["next_cursor"], _compact(boundary), _compact(deduped), PAGE_INCOMPLETE))
    for signature in deduped:
        conn.execute("INSERT OR IGNORE INTO wt_mesh_signature_decodes(signature,transaction_version,status,error_code,decoded_at) VALUES(?,?,?,?,?)", (signature, None, "PENDING", None, timestamp))
        conn.execute("INSERT OR IGNORE INTO wt_mesh_page_signatures VALUES(?,?,?)", (job_id, job["next_page"], signature))
    conn.execute("UPDATE wt_mesh_jobs SET state=?,updated_at=? WHERE job_id=?", (PAGE_INCOMPLETE, timestamp, job_id))
    _assert_store_bound(conn)
    return boundary


def _persist_facts(conn: sqlite3.Connection, facts: Iterable[Mapping[str, Any]], *, provenance: str) -> None:
    for fact in facts:
        conn.execute(
            "INSERT OR IGNORE INTO wt_mesh_funding_facts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (fact.get("signature"), fact.get("kind"), fact.get("sender"), fact.get("receiver"), fact.get("lamports"), fact.get("slot"), fact.get("transaction_index"), fact.get("instruction_index"), fact.get("inner_instruction_index"), fact.get("source_balance_delta"), fact.get("receiver_balance_delta"), int(bool(fact.get("balance_delta_verified"))), fact.get("route_semantics"), provenance),
        )


def record_transaction_result(conn: sqlite3.Connection, *, job_id: int, signature: str, transaction: Mapping[str, Any] | None,
                              error_code: int | None = None, now: int | None = None) -> str:
    """Persist one compact decode outcome and facts; never retain raw transaction data."""
    job = _job(conn, job_id)
    page = conn.execute("SELECT page_number FROM wt_mesh_pages WHERE job_id=? AND page_number=?", (job_id, job["next_page"])).fetchone()
    if page is None or not conn.execute("SELECT 1 FROM wt_mesh_page_signatures WHERE job_id=? AND page_number=? AND signature=?", (job_id, job["next_page"], signature)).fetchone():
        raise DiscoveryError("SIGNATURE_NOT_IN_ACTIVE_PAGE")
    timestamp = _now() if now is None else int(now)
    if transaction is None:
        status = "RPC_ERROR" if error_code is not None else "TRANSACTION_UNAVAILABLE"
        conn.execute("UPDATE wt_mesh_signature_decodes SET status=?,error_code=?,decoded_at=? WHERE signature=?", (status, error_code, timestamp, signature))
        return status
    version_status = transaction_version_status(transaction)
    version = transaction.get("version", "legacy")
    if version_status != "SUPPORTED":
        conn.execute("UPDATE wt_mesh_signature_decodes SET transaction_version=?,status=?,error_code=NULL,decoded_at=? WHERE signature=?", (str(version), "UNSUPPORTED_TRANSACTION_VERSION", timestamp, signature))
        return "UNSUPPORTED_TRANSACTION_VERSION"
    facts = extract_compact_native_facts(transaction, signature=signature)
    _persist_facts(conn, facts, provenance=f"job:{job_id}:page:{job['next_page']}")
    conn.execute("UPDATE wt_mesh_signature_decodes SET transaction_version=?,status='DECODED',error_code=NULL,decoded_at=? WHERE signature=?", (str(version), timestamp, signature))
    _assert_store_bound(conn)
    return "DECODED"


def record_verified_activity_from_transaction(conn: sqlite3.Connection, *, job_id: int, signature: str,
                                              transaction: Mapping[str, Any] | None,
                                              now: int | None = None) -> None:
    """Record a timestamp only when a job has material direct funding evidence."""
    if transaction is None:
        return
    job = _job(conn, job_id)
    observed_at = transaction.get("blockTime")
    facts = extract_compact_native_facts(transaction, signature=signature)
    for fact in facts:
        if (fact.get("route_semantics") == DIRECT and fact.get("balance_delta_verified")
                and fact.get("lamports") is not None and int(fact["lamports"]) >= MATERIAL_SCREENING_LAMPORTS
                and job["address"] in {fact.get("sender"), fact.get("receiver")}):
            record_activity_meaningful_funding(conn, address=str(job["address"]), observed_at=observed_at, now=now)
            return


def page_coverage(conn: sqlite3.Connection, *, job_id: int) -> dict:
    job = _job(conn, job_id)
    row = conn.execute("SELECT signatures_json FROM wt_mesh_pages WHERE job_id=? AND page_number=?", (job_id, job["next_page"])).fetchone()
    if row is None:
        raise DiscoveryError("ACTIVE_PAGE_MISSING")
    signatures = json.loads(row[0])
    decoded = [item[0] for item in conn.execute("SELECT signature FROM wt_mesh_signature_decodes WHERE signature IN (SELECT signature FROM wt_mesh_page_signatures WHERE job_id=? AND page_number=?) AND status='DECODED'", (job_id, job["next_page"])).fetchall()]
    return signature_window_coverage(page_signatures=signatures, decoded_signatures=decoded)


def finalize_page(conn: sqlite3.Connection, *, job_id: int, now: int | None = None) -> dict:
    """Advance cursor only after every signature in this exact page decoded."""
    job = _job(conn, job_id)
    coverage = page_coverage(conn, job_id=job_id)
    timestamp = _now() if now is None else int(now)
    if coverage["status"] != "COMPLETE_SIGNATURE_WINDOW":
        conn.execute("UPDATE wt_mesh_pages SET coverage_state=? WHERE job_id=? AND page_number=?", ("INCOMPLETE_SIGNATURE_WINDOW", job_id, job["next_page"]))
        conn.execute("UPDATE wt_mesh_jobs SET state=?,updated_at=? WHERE job_id=?", (PAGE_INCOMPLETE, timestamp, job_id))
        return coverage
    boundary = json.loads(conn.execute("SELECT boundary_json FROM wt_mesh_pages WHERE job_id=? AND page_number=?", (job_id, job["next_page"])).fetchone()[0])
    conn.execute("UPDATE wt_mesh_pages SET coverage_state=? WHERE job_id=? AND page_number=?", ("COMPLETE_SIGNATURE_WINDOW", job_id, job["next_page"]))
    conn.execute("UPDATE wt_mesh_jobs SET state=?,next_cursor=?,next_page=next_page+1,updated_at=? WHERE job_id=?", (WAITING_PAGE, boundary["last_signature"], timestamp, job_id))
    _mark_activity_coverage_complete(conn, address=str(job["address"]), now=timestamp)
    return coverage


def _ancestor_addresses(conn: sqlite3.Connection, job: sqlite3.Row) -> set[str]:
    addresses = {str(job["address"])}
    parent_id = job["parent_job_id"]
    while parent_id is not None:
        parent = _job(conn, int(parent_id))
        addresses.add(str(parent["address"]))
        parent_id = parent["parent_job_id"]
    return addresses


def enqueue_adjacent_jobs(conn: sqlite3.Connection, *, job_id: int, max_hops: int,
                          material_screening_lamports: int = MATERIAL_SCREENING_LAMPORTS,
                          now: int | None = None) -> list[int]:
    """Queue unique next-hop scans from verified direct facts only.

    OUTBOUND pages continue from their verified receiver; INBOUND pages
    continue from their verified sender.  Reciprocal mesh edges remain
    distinct facts, while the ``UNIQUE`` job key prevents duplicate scans.
    """
    job = _job(conn, job_id)
    if job["hop"] >= max_hops:
        return []
    timestamp = _now() if now is None else int(now)
    if material_screening_lamports < 0:
        raise DiscoveryError("INVALID_MATERIAL_SCREENING_THRESHOLD")
    rows = conn.execute(
        "SELECT sender,receiver,lamports FROM wt_mesh_funding_facts WHERE provenance LIKE ? AND route_semantics=? AND balance_delta_verified=1",
        (f"job:{job_id}:page:%", DIRECT),
    ).fetchall()
    ancestors = _ancestor_addresses(conn, job)
    ids: list[int] = []
    for row in rows:
        address = row["receiver"] if job["direction"] == OUTBOUND else row["sender"]
        # A material threshold controls bounded *screening* only.  It is not
        # identity proof and dust remains persisted in the fact graph.
        if row["lamports"] is None or int(row["lamports"]) < material_screening_lamports:
            continue
        if address in ancestors:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO wt_mesh_jobs(seed_kind,seed_value,address,direction,hop,state,next_cursor,next_page,parent_job_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (job["seed_kind"], job["seed_value"], address, job["direction"], job["hop"] + 1, WAITING_PAGE, None, 1, job_id, timestamp, timestamp),
        )
        child = conn.execute("SELECT job_id FROM wt_mesh_jobs WHERE seed_kind=? AND seed_value=? AND address=? AND direction=? AND hop=?", (job["seed_kind"], job["seed_value"], address, job["direction"], job["hop"] + 1)).fetchone()
        ids.append(int(child[0]))
    _assert_store_bound(conn)
    return sorted(set(ids))


def resume_provider_limited_job(conn: sqlite3.Connection, *, job_id: int, now: int | None = None) -> None:
    """Make an explicit, durable resume decision; never retries automatically."""
    job = _job(conn, job_id)
    if job["state"] != PROVIDER_LIMIT_STOP:
        raise DiscoveryError("JOB_NOT_PROVIDER_LIMITED")
    conn.execute("UPDATE wt_mesh_jobs SET state=?,updated_at=? WHERE job_id=?", (PAGE_INCOMPLETE, _now() if now is None else int(now), job_id))


def run_one_page(conn: sqlite3.Connection, *, job_id: int, client: DiscoveryClient, budget: DiscoveryBudget,
                 now: int | None = None) -> dict:
    """Run exactly one bounded page through an injected client seam.

    Incomplete pages are resumed before a later cursor is fetched.  This
    method has no retry loop: provider limits and decode failures leave a
    durable resume point for a separately authorized caller.
    """
    job = _job(conn, job_id)
    if job["hop"] > budget.max_hops:
        raise BudgetExceeded("HOP_BUDGET_EXCEEDED")
    try:
        if job["state"] == WAITING_PAGE:
            budget.charge()
            records = list(client.get_signatures(job["address"], job["direction"], job["next_cursor"], MAX_SIGNATURES_PER_PAGE))
            signatures = [str(record.get("signature")) for record in records if record.get("signature")]
            boundary = record_signature_page(conn, job_id=job_id, signatures=signatures, now=now)
            for record in records:
                if record.get("err") is None:
                    record_activity_transaction(conn, address=str(job["address"]), observed_at=record.get("blockTime"), now=now)
        elif job["state"] == PAGE_INCOMPLETE:
            boundary = json.loads(conn.execute("SELECT boundary_json FROM wt_mesh_pages WHERE job_id=? AND page_number=?", (job_id, job["next_page"])).fetchone()[0])
        else:
            raise DiscoveryError("JOB_NOT_RUNNABLE")
        active = _job(conn, job_id)
        missing = page_coverage(conn, job_id=job_id)["missing_signatures"]
        for signature in missing:
            budget.charge()
            try:
                transaction = client.get_transaction(signature, transaction_request_config())
            except ProviderLimited:
                conn.execute("UPDATE wt_mesh_jobs SET state=?,updated_at=? WHERE job_id=?", (PROVIDER_LIMIT_STOP, _now() if now is None else int(now), job_id))
                return {"status": PROVIDER_LIMIT_STOP, "boundary": boundary, "coverage": page_coverage(conn, job_id=job_id)}
            status = record_transaction_result(conn, job_id=job_id, signature=signature, transaction=transaction, now=now)
            if status == "DECODED":
                record_verified_activity_from_transaction(conn, job_id=job_id, signature=signature, transaction=transaction, now=now)
        coverage = finalize_page(conn, job_id=job_id, now=now)
        return {"status": coverage["status"], "boundary": boundary, "coverage": coverage, "rpc_calls": budget.rpc_calls}
    except BudgetExceeded:
        conn.execute("UPDATE wt_mesh_jobs SET state=?,updated_at=? WHERE job_id=?", (PROVIDER_LIMIT_STOP, _now() if now is None else int(now), job_id))
        return {"status": PROVIDER_LIMIT_STOP, "rpc_calls": budget.rpc_calls}


def classify_wallet_role(*, wallet: str, confirmed_treasuries: Iterable[str], known_subproviders: Iterable[str], funding_accounts: Iterable[str]) -> str:
    if wallet in set(confirmed_treasuries):
        return CONFIRMED_TREASURY_MATCH
    if wallet in set(known_subproviders):
        return KNOWN_SUBPROVIDER_MATCH
    if wallet in set(funding_accounts):
        return FUNDING_ACCOUNT_MATCH
    return PARTIAL_LINEAGE


def review_only_candidate(*, wallet: str, role: str, route_complete: bool, full_fingerprint: bool) -> dict:
    """Return a review payload; never writes ``wt_treasury_review`` itself."""
    classification = NEW_TREASURY_CANDIDATE if route_complete and full_fingerprint and role == PARTIAL_LINEAGE else (role if route_complete else INSUFFICIENT_EVIDENCE)
    return {"wallet": wallet, "classification": classification, "disposition": "REQUIRES_WT_TREASURY_REVIEW" if classification == NEW_TREASURY_CANDIDATE else "NO_AUTOMATIC_PROMOTION", "writes_canonical_state": False}


def causal_graph(conn: sqlite3.Connection) -> list[dict]:
    """Read compact direct facts as a mesh; reciprocal edges are retained, not merged."""
    rows = conn.execute("SELECT sender,receiver,signature,slot,transaction_index,instruction_index,inner_instruction_index,lamports,balance_delta_verified,route_semantics FROM wt_mesh_funding_facts ORDER BY slot,transaction_index,instruction_index,inner_instruction_index").fetchall()
    return [dict(row) for row in rows]


def walkback_read_interface(conn: sqlite3.Connection, *, launch_mint: str) -> dict:
    """Read-only future seam; it neither calls nor modifies the live worker."""
    seed = conn.execute("SELECT * FROM wt_mesh_launch_seeds WHERE launch_mint=?", (launch_mint,)).fetchone()
    return {"read_only": True, "launch_mint": launch_mint, "launch_seed": dict(seed) if seed else None,
            "facts": causal_graph(conn), "requires_review": True, "canonical_writes": False}
