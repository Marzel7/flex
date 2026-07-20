"""X29.3 — Funding Boundary Intelligence (renamed from X29.2's Capital Origin).

The core reframe: the bounded 2-hop walk (src/core/walkback_worker.py) does
not attempt to prove wallet genesis, so the primary honest question is not
"what is this launch's capital origin?" but "what known funding boundary does
the observed lineage reach?" Origin is a rare, stronger SUBTYPE of that
observation -- promoted only when a future history-exhaustion signal proves
the boundary is also the actual first funder. This module never manufactures
that promotion: PROVEN requires history_exhausted=1, which the current
bounded walk can never produce (see is_boundary_proven_valid()).

Separates two independent questions per launch (per X29.1.4's investigation):

  Operational Attribution — "which known operation/infrastructure is
    directly relevant to this launch?" (wt_attribution_outcomes.outcome_type,
    UNCHANGED by this module).
  Funding Boundary — "what funding boundary has actually been observed, and
    how complete was the search that found it?" (this module,
    wt_funding_boundary, purely additive).

Zero new RPC calls anywhere in this module: every derivation reads only
already-persisted wt_walkback_queue/wt_attribution_outcomes/token_analysis
rows.
"""
from __future__ import annotations

import datetime
import hashlib
import sqlite3
import time
from typing import Any, Optional

# ─────────────────────────── vocabulary ───────────────────────────

STATUS_PROVEN = "PROVEN"
STATUS_BOUNDED_OBSERVATION = "BOUNDED_OBSERVATION"
STATUS_STATIC_MATCH = "STATIC_MATCH"
STATUS_UNRESOLVED = "UNRESOLVED"

STATUS_ORDER = (STATUS_PROVEN, STATUS_BOUNDED_OBSERVATION, STATUS_STATIC_MATCH, STATUS_UNRESOLVED)

TYPE_CEX = "CEX"
TYPE_BRIDGE = "BRIDGE"
TYPE_RELAY = "RELAY"
TYPE_KNOWN_OPERATOR = "KNOWN_OPERATOR"
TYPE_EXTERNAL_WALLET = "EXTERNAL_WALLET"
TYPE_UNKNOWN = "UNKNOWN"

TYPE_ORDER = (TYPE_CEX, TYPE_BRIDGE, TYPE_RELAY, TYPE_KNOWN_OPERATOR, TYPE_EXTERNAL_WALLET, TYPE_UNKNOWN)

REASON_STATIC_REGISTRY_MATCH_ONLY = "STATIC_REGISTRY_MATCH_ONLY"
REASON_NON_CAUSAL_FUNDING_EVENT = "NON_CAUSAL_FUNDING_EVENT"
REASON_NO_QUALIFYING_FUNDER = "NO_QUALIFYING_FUNDER"
REASON_MISSING_WALKBACK_EVIDENCE = "MISSING_WALKBACK_EVIDENCE"
REASON_INVALID_TIMESTAMP = "INVALID_TIMESTAMP"
REASON_BOUNDED_WALK_COMPLETE = "BOUNDED_WALK_COMPLETE"
REASON_IGNORED_SPAM_SENDER = "IGNORED_SPAM_SENDER"

_OUTCOME_TYPE_TO_BOUNDARY_TYPE = {
    "KNOWN_CEX_REACHED": TYPE_CEX,
    "KNOWN_BRIDGE_REACHED": TYPE_BRIDGE,
    "KNOWN_RELAY_REACHED": TYPE_RELAY,
}

AGE_BUCKET_ORDER = ("<=1d", "1-7d", "8-30d", "31-100d", ">100d", "unknown")

DDL = """
CREATE TABLE IF NOT EXISTS wt_funding_boundary (
    id                            INTEGER PRIMARY KEY AUTOINCREMENT,
    launch_mint                   TEXT NOT NULL,
    subject_wallet                TEXT NOT NULL,
    boundary_status               TEXT NOT NULL CHECK(boundary_status IN ('PROVEN','BOUNDED_OBSERVATION','STATIC_MATCH','UNRESOLVED')),
    boundary_type                 TEXT NOT NULL CHECK(boundary_type IN ('CEX','BRIDGE','RELAY','KNOWN_OPERATOR','EXTERNAL_WALLET','UNKNOWN')),
    boundary_wallet               TEXT,
    boundary_entity                TEXT,
    boundary_signature            TEXT,
    boundary_block_time           INTEGER,
    boundary_age_at_launch_seconds INTEGER,
    boundary_hop_depth            INTEGER,
    boundary_transfer_lamports    INTEGER,
    boundary_transfer_sol         REAL,
    transactions_inspected        INTEGER,
    rpc_calls_used                INTEGER,
    oldest_inspected_signature    TEXT,
    oldest_inspected_block_time   INTEGER,
    history_exhausted             INTEGER NOT NULL DEFAULT 0 CHECK(history_exhausted IN (0,1)),
    pagination_limit_reached      INTEGER NOT NULL DEFAULT 0 CHECK(pagination_limit_reached IN (0,1)),
    resolution_reason             TEXT,
    provenance                    TEXT,
    created_at                    INTEGER NOT NULL,
    updated_at                    INTEGER NOT NULL,
    UNIQUE(launch_mint, subject_wallet)
);
CREATE INDEX IF NOT EXISTS ix_funding_boundary_mint ON wt_funding_boundary(launch_mint);
CREATE INDEX IF NOT EXISTS ix_funding_boundary_wallet ON wt_funding_boundary(boundary_wallet);
CREATE INDEX IF NOT EXISTS ix_funding_boundary_entity ON wt_funding_boundary(boundary_entity);
CREATE INDEX IF NOT EXISTS ix_funding_boundary_status ON wt_funding_boundary(boundary_status);
CREATE INDEX IF NOT EXISTS ix_funding_boundary_type ON wt_funding_boundary(boundary_type);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)
    conn.commit()


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone())


def _normalize_timestamp(value: Any) -> Optional[float]:
    """Handles the mixed unix-epoch/ISO-8601 formats already observed in
    token_analysis.created_at (X29.1.4 finding) -- never fabricates a value,
    returns None for anything unparseable."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        pass
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _upsert_key(launch_mint: str, subject_wallet: str) -> str:
    """Deterministic id purely for logging/dedup checks; the actual
    idempotency guarantee comes from the UNIQUE(launch_mint, subject_wallet)
    constraint + INSERT ... ON CONFLICT DO UPDATE, not this hash."""
    return hashlib.sha256(f"{launch_mint}|{subject_wallet}".encode()).hexdigest()[:24]


def derive_funding_boundary(
    *,
    mint: str,
    outcome_type: Optional[str],
    boundary: Optional[dict],
    subject_wallet: Optional[str],
    origin_wallet: Optional[str],
    origin_signature: Optional[str],
    origin_block_time_raw: Any,
    origin_amount_sol: Optional[float],
    rpc_used: Optional[int],
    launch_block_time_raw: Any,
    hop_depth: Optional[int] = None,
    known_spam_wallets: Optional[frozenset[str]] = None,
) -> dict[str, Any]:
    """Pure function: converts already-persisted walkback/boundary evidence
    into one funding-boundary record. No DB access, no RPC -- a plain
    classification over the inputs, so it's trivially unit-testable and
    reusable by both the backfill pass and any future live-write path.

    Inputs map directly onto existing columns X29.1.4 already traced:
      boundary          -- evidence_json['boundary'] (address/entity_type/name/source/type)
      origin_wallet/origin_signature/origin_block_time_raw/origin_amount_sol
                        -- wt_walkback_queue.funder_wallet/funder_sig/funder_block_time/funder_amount_sol
      rpc_used          -- wt_walkback_queue.rpc_used (proxy for "some RPC evidence exists")
      launch_block_time_raw -- token_analysis.created_at (mixed epoch/ISO format)
      known_spam_wallets -- X29.4: caller-supplied set of confirmed
                        wt_known_spam_wallets addresses (kept as a passed-in
                        set, not a DB lookup, so this function stays pure).
                        If origin_wallet is a member, it is NEVER treated as
                        funding boundary evidence -- Infrastructure Spam is
                        environmental noise, not evidence (X29.4 brief).
    """
    now = int(time.time())

    if known_spam_wallets and origin_wallet and origin_wallet in known_spam_wallets:
        return {
            "launch_mint": mint,
            "subject_wallet": subject_wallet,
            "boundary_type": _OUTCOME_TYPE_TO_BOUNDARY_TYPE.get(outcome_type or "", TYPE_UNKNOWN),
            "boundary_status": STATUS_UNRESOLVED,
            "boundary_wallet": None,
            "boundary_entity": None,
            "boundary_signature": None,
            "boundary_block_time": None,
            "boundary_age_at_launch_seconds": None,
            "boundary_hop_depth": hop_depth,
            "boundary_transfer_lamports": None,
            "boundary_transfer_sol": None,
            "transactions_inspected": None,
            "rpc_calls_used": rpc_used,
            "oldest_inspected_signature": None,
            "oldest_inspected_block_time": None,
            "history_exhausted": 0,
            "pagination_limit_reached": 0,
            "resolution_reason": REASON_IGNORED_SPAM_SENDER,
            "provenance": f"ignored_spam_sender={origin_wallet}",
        }

    boundary_type = _OUTCOME_TYPE_TO_BOUNDARY_TYPE.get(outcome_type or "", TYPE_UNKNOWN)
    entity_name = None
    if boundary:
        entity_name = boundary.get("name")
        # Prefer the boundary's own address as the boundary wallet if the
        # walkback-derived origin_wallet is absent (the static-match case).
        origin_wallet = origin_wallet or boundary.get("address")

    launch_ts = _normalize_timestamp(launch_block_time_raw)
    origin_ts = _normalize_timestamp(origin_block_time_raw)

    record: dict[str, Any] = {
        "launch_mint": mint,
        "subject_wallet": subject_wallet,
        "boundary_type": boundary_type,
        "boundary_wallet": origin_wallet,
        "boundary_entity": entity_name,
        "boundary_signature": origin_signature,
        "boundary_block_time": int(origin_ts) if origin_ts is not None else None,
        "boundary_age_at_launch_seconds": None,
        "boundary_hop_depth": hop_depth,
        "boundary_transfer_lamports": int(round(origin_amount_sol * 1_000_000_000)) if origin_amount_sol is not None else None,
        "boundary_transfer_sol": origin_amount_sol,
        "transactions_inspected": None,  # X29.1.4: rpc_used counts RPC calls, not tx inspected — no honest value to store
        "rpc_calls_used": rpc_used,
        "oldest_inspected_signature": None,       # not tracked anywhere upstream — left null, never fabricated
        "oldest_inspected_block_time": None,      # not tracked anywhere upstream — left null, never fabricated
        "history_exhausted": 0,
        "pagination_limit_reached": 0,
        "resolution_reason": None,
        "provenance": None,
    }

    # ── Temporal validation (X29.2's rule: boundary_block_time <= launch_block_time) ──
    if origin_ts is not None and launch_ts is not None and origin_ts > launch_ts:
        record["boundary_status"] = STATUS_UNRESOLVED
        record["resolution_reason"] = REASON_NON_CAUSAL_FUNDING_EVENT
        record["boundary_age_at_launch_seconds"] = int(launch_ts - origin_ts)  # negative, preserved as diagnostic
        record["provenance"] = (
            f"rejected_non_causal_source={origin_wallet};rejected_signature={origin_signature}"
        )
        # Do not present the rejected source as a valid boundary: null it out of the
        # primary fields, keep it only in provenance/diagnostic metadata.
        record["boundary_wallet"] = None
        record["boundary_entity"] = None
        record["boundary_signature"] = None
        record["boundary_block_time"] = None
        return record

    if origin_ts is not None and launch_ts is not None:
        record["boundary_age_at_launch_seconds"] = int(launch_ts - origin_ts)

    # ── Status classification ──
    has_rpc_evidence = bool(origin_signature)
    if has_rpc_evidence:
        record["boundary_status"] = STATUS_BOUNDED_OBSERVATION
        record["resolution_reason"] = REASON_BOUNDED_WALK_COMPLETE
        record["pagination_limit_reached"] = 1
        record["history_exhausted"] = 0  # FULL_WALKBACK never proves exhaustion — X29.1.4
    elif boundary:
        record["boundary_status"] = STATUS_STATIC_MATCH
        record["resolution_reason"] = REASON_STATIC_REGISTRY_MATCH_ONLY
        record["boundary_signature"] = None
    else:
        record["boundary_status"] = STATUS_UNRESOLVED
        record["resolution_reason"] = (
            REASON_MISSING_WALKBACK_EVIDENCE if origin_wallet else REASON_NO_QUALIFYING_FUNDER
        )

    return record


def is_boundary_proven_valid(record: dict[str, Any]) -> bool:
    """PROVEN gate: history_exhausted must be explicitly true. This function
    exists so callers never hand-roll the check — a bounded walk (the only
    evidence source this codebase has today) can never satisfy it, by
    construction (derive_funding_boundary never sets history_exhausted=1)."""
    return bool(record.get("history_exhausted"))


def upsert_funding_boundary(conn: sqlite3.Connection, record: dict[str, Any]) -> None:
    """Idempotent write: UNIQUE(launch_mint, subject_wallet) + ON CONFLICT
    DO UPDATE, matching the wt_provisioning_edges upsert convention. Running
    the same input twice never creates a duplicate row and always leaves the
    row in the same final state (verified by test)."""
    now = int(time.time())
    conn.execute(
        """INSERT INTO wt_funding_boundary (
            launch_mint, subject_wallet, boundary_status, boundary_type, boundary_wallet,
            boundary_entity, boundary_signature, boundary_block_time, boundary_age_at_launch_seconds,
            boundary_hop_depth, boundary_transfer_lamports, boundary_transfer_sol,
            transactions_inspected, rpc_calls_used, oldest_inspected_signature,
            oldest_inspected_block_time, history_exhausted, pagination_limit_reached,
            resolution_reason, provenance, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(launch_mint, subject_wallet) DO UPDATE SET
            boundary_status=excluded.boundary_status,
            boundary_type=excluded.boundary_type,
            boundary_wallet=excluded.boundary_wallet,
            boundary_entity=excluded.boundary_entity,
            boundary_signature=excluded.boundary_signature,
            boundary_block_time=excluded.boundary_block_time,
            boundary_age_at_launch_seconds=excluded.boundary_age_at_launch_seconds,
            boundary_hop_depth=excluded.boundary_hop_depth,
            boundary_transfer_lamports=excluded.boundary_transfer_lamports,
            boundary_transfer_sol=excluded.boundary_transfer_sol,
            transactions_inspected=excluded.transactions_inspected,
            rpc_calls_used=excluded.rpc_calls_used,
            oldest_inspected_signature=excluded.oldest_inspected_signature,
            oldest_inspected_block_time=excluded.oldest_inspected_block_time,
            history_exhausted=excluded.history_exhausted,
            pagination_limit_reached=excluded.pagination_limit_reached,
            resolution_reason=excluded.resolution_reason,
            provenance=excluded.provenance,
            updated_at=excluded.updated_at
        """,
        (
            record["launch_mint"], record["subject_wallet"], record["boundary_status"],
            record["boundary_type"], record.get("boundary_wallet"), record.get("boundary_entity"),
            record.get("boundary_signature"), record.get("boundary_block_time"),
            record.get("boundary_age_at_launch_seconds"), record.get("boundary_hop_depth"),
            record.get("boundary_transfer_lamports"), record.get("boundary_transfer_sol"),
            record.get("transactions_inspected"), record.get("rpc_calls_used"),
            record.get("oldest_inspected_signature"), record.get("oldest_inspected_block_time"),
            int(bool(record.get("history_exhausted"))), int(bool(record.get("pagination_limit_reached"))),
            record.get("resolution_reason"), record.get("provenance"), now, now,
        ),
    )


def get_funding_boundary(conn: sqlite3.Connection, mint: str) -> Optional[dict[str, Any]]:
    """Read-only lookup, zero RPC — the API/UI consumption path."""
    if not _table_exists(conn, "wt_funding_boundary"):
        return None
    row = conn.execute(
        "SELECT * FROM wt_funding_boundary WHERE launch_mint=? ORDER BY id DESC LIMIT 1", (mint,)
    ).fetchone()
    if not row:
        return None
    cols = [d[0] for d in conn.execute("SELECT * FROM wt_funding_boundary LIMIT 0").description]
    return dict(zip(cols, row))


def serialize_funding_boundary(record: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """API shape: boundary_* fields plus a computed origin_proven flag —
    never stored separately, so it can never disagree with boundary_status
    (origin_proven is always exactly `boundary_status == PROVEN`)."""
    if not record:
        return None
    status = record.get("boundary_status")
    return {
        "status": status,
        "type": record.get("boundary_type"),
        "wallet": record.get("boundary_wallet"),
        "entity": record.get("boundary_entity"),
        "signature": record.get("boundary_signature"),
        "block_time": record.get("boundary_block_time"),
        "age_at_launch_seconds": record.get("boundary_age_at_launch_seconds"),
        "hop_depth": record.get("boundary_hop_depth"),
        "transfer_lamports": record.get("boundary_transfer_lamports"),
        "history_exhausted": bool(record.get("history_exhausted")),
        "pagination_limit_reached": bool(record.get("pagination_limit_reached")),
        "resolution_reason": record.get("resolution_reason"),
        "origin_proven": status == STATUS_PROVEN,
    }


def age_bucket_for(age_seconds: Optional[int]) -> str:
    if age_seconds is None:
        return "unknown"
    days = age_seconds / 86400.0
    if days < 0:
        return "unknown"  # non-causal rows never reach this path (already UNRESOLVED before bucketing)
    if days <= 1:
        return "<=1d"
    if days <= 7:
        return "1-7d"
    if days <= 30:
        return "8-30d"
    if days <= 100:
        return "31-100d"
    return ">100d"
