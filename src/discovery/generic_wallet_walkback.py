"""Semantics-neutral wrapper for the existing factual wallet-parent extractor.

This module deliberately delegates parent selection to
``walkback_worker._find_funder_via_rpc`` with ``ops=None``.  It adds no
Watchtower truth, labels, queue lifecycle, or persistence.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from typing import Callable, Optional

from src.core import walkback_worker


PARENT_FOUND = "PARENT_FOUND"
NO_QUALIFYING_PARENT = "NO_QUALIFYING_PARENT"
EVIDENCE_UNAVAILABLE = "EVIDENCE_UNAVAILABLE"
RPC_ERROR = "RPC_ERROR"
INCOMPLETE_HISTORY = "INCOMPLETE_HISTORY"
AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True)
class HistoricalContext:
    before_signature: Optional[str] = None
    prefer_oldest: bool = False
    depth: int = 1


@dataclass(frozen=True)
class ParentFinding:
    state: str
    child_wallet: str
    parent_wallet: Optional[str]
    signature: Optional[str]
    slot: Optional[int]
    block_time: Optional[int]
    amount_sol: Optional[float]
    mechanism: Optional[str]
    depth: int
    rpc_requests: int

    def canonical_sha256(self) -> str:
        return sha256(json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def find_funding_parent(wallet: str, historical_context: HistoricalContext = HistoricalContext()) -> ParentFinding:
    """Return factual parent evidence using the authoritative existing selector.

    Passing ``ops=None`` is intentional: it prevents Watchtower treasury,
    subprovider, spam-recording, queue, and attribution dependencies from
    participating in the result.
    """
    rpc_counter = [0]
    parent, signature, slot, block_time, amount_sol, mechanism = walkback_worker._find_funder_via_rpc(
        wallet,
        rpc_counter,
        None,
        before_signature=historical_context.before_signature,
        prefer_oldest=historical_context.prefer_oldest,
        source_mint=None,
        hop_depth=historical_context.depth,
    )
    return ParentFinding(
        state=PARENT_FOUND if parent else NO_QUALIFYING_PARENT,
        child_wallet=wallet,
        parent_wallet=parent,
        signature=signature,
        slot=slot,
        block_time=block_time,
        amount_sol=amount_sol,
        mechanism=mechanism,
        depth=historical_context.depth,
        rpc_requests=rpc_counter[0],
    )


def resolve_mint_seed(conn, mint: str) -> tuple[Optional[str], Optional[str]]:
    """Resolve a generic local creator seed without querying Watchtower tables.

    The precedence mirrors the non-Watchtower fallbacks of the existing worker:
    ``token_analysis.earliest_tx_creator``, then ``pf_ws_creator``, then
    ``migrated_tokens.creator``.  The returned second value is provenance.
    """
    row = conn.execute("SELECT earliest_tx_creator, pf_ws_creator FROM token_analysis WHERE mint=? LIMIT 1", (mint,)).fetchone()
    if row:
        creator = row[0] or row[1]
        if creator:
            return creator, "token_analysis"
    row = conn.execute("SELECT creator FROM migrated_tokens WHERE mint=? LIMIT 1", (mint,)).fetchone()
    return (row[0], "migrated_tokens") if row and row[0] else (None, None)


def walk_path(seed_wallet: str, parent_lookup: Callable[[str, HistoricalContext], ParentFinding], *, max_depth: int) -> tuple[ParentFinding, ...]:
    """Deterministic repeated application; the caller supplies retained evidence lookup.

    ``max_depth`` is an execution safety bound, not a claim about operation
    semantics.  Wallets are visited once; a repeated wallet yields AMBIGUOUS.
    """
    if max_depth < 1:
        raise ValueError("max_depth must be positive")
    wallet, before, visited, results = seed_wallet, None, set(), []
    for depth in range(1, max_depth + 1):
        if wallet in visited:
            results.append(ParentFinding(AMBIGUOUS, wallet, None, None, None, None, None, None, depth, 0))
            break
        visited.add(wallet)
        result = parent_lookup(wallet, HistoricalContext(before_signature=before, prefer_oldest=depth > 1, depth=depth))
        results.append(result)
        if result.state != PARENT_FOUND or not result.parent_wallet:
            break
        wallet, before = result.parent_wallet, result.signature
    return tuple(results)


# Future generic evidence namespace.  This DDL is a frozen contract only;
# this milestone never creates these tables against a production/source DB.
GENERIC_EVIDENCE_SCHEMA_SQL = """
CREATE TABLE generic_walkback_runs (run_id TEXT PRIMARY KEY, seed_manifest_sha256 TEXT NOT NULL, walker_sha256 TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, completed_at TEXT, canonical_sha256 TEXT NOT NULL);
CREATE TABLE generic_walkback_requests (request_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, wallet TEXT NOT NULL, method TEXT NOT NULL, params_canonical_json TEXT NOT NULL, params_sha256 TEXT NOT NULL, attempt_ordinal INTEGER NOT NULL, status TEXT NOT NULL, requested_at TEXT NOT NULL, completed_at TEXT, UNIQUE(run_id,wallet,method,params_sha256,attempt_ordinal));
CREATE TABLE generic_walkback_responses (request_id TEXT PRIMARY KEY, raw_response_sha256 TEXT, retained_response_canonical_json TEXT, pagination_state TEXT NOT NULL, history_complete INTEGER NOT NULL, provider_error TEXT, terminal_status TEXT NOT NULL);
CREATE TABLE generic_wallet_parent_edges (edge_sha256 TEXT PRIMARY KEY, run_id TEXT NOT NULL, child_wallet TEXT NOT NULL, parent_wallet TEXT, signature TEXT, slot INTEGER, block_time INTEGER, amount_sol REAL, mechanism TEXT, depth INTEGER NOT NULL, request_id TEXT, response_sha256 TEXT, walker_sha256 TEXT NOT NULL, state TEXT NOT NULL);
""".strip()


def canonical_contract_sha256(payload: dict) -> str:
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
