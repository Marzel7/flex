"""Provider-free, review-only treasury rotation discovery primitives.

This module deliberately separates causal route assessment from canonical
treasury and operation membership.  It has no provider dependency and never
writes ``wt_confirmed_treasuries`` or membership tables.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from typing import Iterable, Mapping

KNOWN_OPERATION_TREASURY = "KNOWN_OPERATION_TREASURY"
NEW_TREASURY_CANDIDATE = "NEW_TREASURY_CANDIDATE"
CONTRADICTORY_TREASURY = "CONTRADICTORY_TREASURY"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
CANDIDATE_SEMANTIC_VERSION = "treasury-rotation-v1"

# Acquisition code may use this provider-free contract to prevent a decoded
# prefix of a signature page from being reported as a negative route result.
COVERAGE_COMPLETE = "COMPLETE_SIGNATURE_WINDOW"
COVERAGE_INCOMPLETE = "INCOMPLETE_SIGNATURE_WINDOW"
PARTIAL_LINEAGE = "PARTIAL_LINEAGE"
CONFIRMED_TREASURY_MATCH = "CONFIRMED_TREASURY_MATCH"
KNOWN_SUBPROVIDER_MATCH = "KNOWN_SUBPROVIDER_MATCH"
FUNDING_ACCOUNT_MATCH = "FUNDING_ACCOUNT_MATCH"

SCHEMA = """
CREATE TABLE IF NOT EXISTS wt_treasury_rotation_candidates (
 operation_family TEXT NOT NULL, treasury TEXT NOT NULL,
 semantic_version TEXT NOT NULL, first_seen_at INTEGER NOT NULL,
 last_seen_at INTEGER NOT NULL, known_at_event_time INTEGER NOT NULL,
 known_now INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING_REVIEW',
 PRIMARY KEY(operation_family, treasury, semantic_version)
);
CREATE TABLE IF NOT EXISTS wt_treasury_rotation_candidate_evidence (
 operation_family TEXT NOT NULL, treasury TEXT NOT NULL,
 semantic_version TEXT NOT NULL, launch_mint TEXT NOT NULL,
 route_evidence_digest TEXT NOT NULL, fingerprint_version TEXT NOT NULL,
 event_slot INTEGER, event_time INTEGER, terminal_hop TEXT NOT NULL,
 classification_reason TEXT NOT NULL, discovered_at INTEGER NOT NULL,
 PRIMARY KEY(operation_family, treasury, semantic_version, launch_mint, route_evidence_digest)
);
CREATE TABLE IF NOT EXISTS wt_treasury_rotation_contradictions (
 operation_family TEXT NOT NULL, treasury TEXT NOT NULL,
 semantic_version TEXT NOT NULL, launch_mint TEXT NOT NULL,
 route_evidence_digest TEXT NOT NULL, reason TEXT NOT NULL, observed_at INTEGER NOT NULL,
 PRIMARY KEY(operation_family, treasury, semantic_version, launch_mint, route_evidence_digest, reason)
);
CREATE TABLE IF NOT EXISTS wt_treasury_rotation_causal_order_evidence (
 operation_family TEXT NOT NULL, launch_mint TEXT NOT NULL,
 semantic_version TEXT NOT NULL, parent_signature TEXT NOT NULL,
 parent_slot INTEGER, parent_transaction_index INTEGER,
 parent_instruction_index INTEGER, child_signature TEXT NOT NULL,
 child_slot INTEGER, child_transaction_index INTEGER,
 child_instruction_index INTEGER, evidence_source TEXT NOT NULL,
 parser_version TEXT NOT NULL, qualification_state TEXT NOT NULL,
 recorded_at INTEGER NOT NULL,
 PRIMARY KEY(operation_family, launch_mint, semantic_version,
             parent_signature, child_signature, evidence_source, parser_version)
);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def _ordering_value(edge: Mapping, field: str) -> int | None:
    value = edge.get(field)
    # Legacy edge rows encode unknown indexes as -1.  Those are not ordering
    # facts and must never be treated as an earlier event.
    if value is None or int(value) < 0:
        return None
    return int(value)


def _event_key(edge: Mapping) -> tuple | None:
    slot = edge.get("slot")
    if slot is None:
        return None
    return int(slot), _ordering_value(edge, "transaction_index"), _ordering_value(edge, "instruction_index")


def signature_window_coverage(*, page_signatures: Iterable[str], decoded_signatures: Iterable[str]) -> dict:
    """Describe exactly whether every signature in a bounded page was decoded.

    A bounded page is a coverage window, not a representative sample.  Its
    result must therefore be marked incomplete when a request cap, missing
    transaction, or decode failure leaves even one signature unexamined.
    """
    page = tuple(dict.fromkeys(str(signature) for signature in page_signatures))
    decoded = {str(signature) for signature in decoded_signatures}
    missing = [signature for signature in page if signature not in decoded]
    return {
        "status": COVERAGE_COMPLETE if not missing else COVERAGE_INCOMPLETE,
        "page_signature_count": len(page),
        "decoded_signature_count": len(page) - len(missing),
        "missing_signature_count": len(missing),
        "missing_signatures": missing,
    }


def _account_keys(transaction: Mapping) -> list[str]:
    message = transaction.get("transaction", {}).get("message", {})
    return [str(item.get("pubkey")) if isinstance(item, Mapping) else str(item)
            for item in message.get("accountKeys", [])]


def _balance_delta(keys: list[str], pre: list, post: list, wallet: str) -> int | None:
    if wallet not in keys:
        return None
    index = keys.index(wallet)
    if index >= len(pre) or index >= len(post):
        return None
    return int(post[index]) - int(pre[index])


def extract_compact_native_facts(transaction: Mapping, *, signature: str) -> list[dict]:
    """Extract compact native-SOL facts without retaining the transaction.

    System transfers can be outer or inner instructions.  SPL-token account
    closes are retained separately as WSOL-close context: their lamport
    balance movement is real, but it is not promoted to a direct wallet
    funding edge.  Transaction-level net deltas alone never create a fact.
    """
    meta = transaction.get("meta") or {}
    keys = _account_keys(transaction)
    pre, post = list(meta.get("preBalances") or []), list(meta.get("postBalances") or [])
    outer = list(transaction.get("transaction", {}).get("message", {}).get("instructions", []) or [])
    indexed: list[tuple[int, int | None, Mapping]] = [(i, None, item) for i, item in enumerate(outer)]
    for inner_group in meta.get("innerInstructions", []) or []:
        outer_index = int(inner_group.get("index", -1))
        for inner_index, item in enumerate(inner_group.get("instructions", []) or []):
            indexed.append((outer_index, inner_index, item))
    facts: list[dict] = []
    for outer_index, inner_index, item in indexed:
        parsed = item.get("parsed") if isinstance(item, Mapping) else None
        if not isinstance(parsed, Mapping):
            continue
        kind, info = parsed.get("type"), parsed.get("info") or {}
        program = item.get("program")
        if program == "system" and kind == "transfer" and info.get("source") and info.get("destination") and info.get("lamports") is not None:
            sender, receiver, lamports = str(info["source"]), str(info["destination"]), int(info["lamports"])
            source_delta, receiver_delta = _balance_delta(keys, pre, post, sender), _balance_delta(keys, pre, post, receiver)
            facts.append({
                "kind": "SYSTEM_TRANSFER", "route_semantics": "DIRECT", "sender": sender, "receiver": receiver,
                "lamports": lamports, "signature": signature, "slot": transaction.get("slot"),
                "transaction_index": transaction.get("transactionIndex"), "instruction_index": outer_index,
                "inner_instruction_index": inner_index, "source_balance_delta": source_delta,
                "receiver_balance_delta": receiver_delta,
                "balance_delta_verified": source_delta is not None and receiver_delta is not None and source_delta <= -lamports and receiver_delta >= lamports,
            })
        elif program in {"spl-token", "spl-token-2022"} and kind == "closeAccount" and info.get("account") and info.get("destination"):
            account, destination = str(info["account"]), str(info["destination"])
            source_delta, receiver_delta = _balance_delta(keys, pre, post, account), _balance_delta(keys, pre, post, destination)
            lamports = -source_delta if source_delta is not None and source_delta < 0 else None
            facts.append({
                "kind": "WRAPPED_SOL_ACCOUNT_CLOSE_CONTEXT", "route_semantics": "ACCOUNT_CLOSE", "sender": account,
                "receiver": destination, "lamports": lamports, "signature": signature, "slot": transaction.get("slot"),
                "transaction_index": transaction.get("transactionIndex"), "instruction_index": outer_index,
                "inner_instruction_index": inner_index, "source_balance_delta": source_delta,
                "receiver_balance_delta": receiver_delta,
                "balance_delta_verified": lamports is not None and receiver_delta is not None and receiver_delta >= lamports,
            })
    return facts


def classify_mesh_role(*, wallet: str, confirmed_treasuries: Iterable[str],
                       known_subproviders: Iterable[str], funding_accounts: Iterable[str]) -> str:
    """Classify known mesh roles without promoting any unknown wallet."""
    if wallet in set(confirmed_treasuries):
        return CONFIRMED_TREASURY_MATCH
    if wallet in set(known_subproviders):
        return KNOWN_SUBPROVIDER_MATCH
    if wallet in set(funding_accounts):
        return FUNDING_ACCOUNT_MATCH
    return PARTIAL_LINEAGE


def qualify_mesh_route(*, treasury: Mapping, transfers: Iterable[Mapping],
                       creator_launch: Mapping | None) -> dict:
    """Qualify one Treasury -> Subprovider -> Funding -> Creator -> Launch path.

    Each hop has to be a direct, balance-verified native transfer.  A creator
    and mint sharing a transaction is contextual linkage only unless the
    caller supplies an explicit creator-launch event coordinate.  This helper
    has no provider or persistence dependency.
    """
    chain = [dict(treasury)]
    current = str(treasury.get("wallet") or "")
    for transfer in transfers:
        edge = dict(transfer)
        if edge.get("sender") != current or not edge.get("receiver"):
            return {"route_complete": False, "classification": INSUFFICIENT_EVIDENCE,
                    "reason": "DISCONNECTED_OR_DIRECTIONLESS_TRANSFER", "accepted_edges": chain}
        if not edge.get("balance_delta_verified"):
            return {"route_complete": False, "classification": INSUFFICIENT_EVIDENCE,
                    "reason": "UNVERIFIED_ECONOMIC_MOVEMENT", "accepted_edges": chain}
        if not edge.get("signature") or _event_key(edge) is None:
            return {"route_complete": False, "classification": INSUFFICIENT_EVIDENCE,
                    "reason": "TRANSFER_ORDER_COORDINATES_UNAVAILABLE", "accepted_edges": chain}
        ordered, reason = causal_order(edge, chain[-1])
        if not ordered:
            return {"route_complete": False, "classification": INSUFFICIENT_EVIDENCE,
                    "reason": reason, "accepted_edges": chain}
        chain.append(edge)
        current = str(edge["receiver"])
    if creator_launch is None or creator_launch.get("creator") != current:
        return {"route_complete": False, "classification": PARTIAL_LINEAGE,
                "reason": "CREATOR_LAUNCH_LINK_UNAVAILABLE", "accepted_edges": chain}
    launch = dict(creator_launch)
    if launch.get("status") != "VERIFIED_CREATOR_LAUNCH" or not launch.get("signature") or _event_key(launch) is None:
        return {"route_complete": False, "classification": PARTIAL_LINEAGE,
                "reason": "CREATOR_LAUNCH_CONTEXTUAL_ONLY", "accepted_edges": chain}
    ordered, reason = causal_order(launch, chain[-1])
    if not ordered:
        return {"route_complete": False, "classification": INSUFFICIENT_EVIDENCE,
                "reason": reason, "accepted_edges": chain}
    return {"route_complete": True, "classification": CONFIRMED_TREASURY_MATCH,
            "reason": "QUALIFIED_TREASURY_TO_LAUNCH_ROUTE", "accepted_edges": chain + [launch]}


def causal_order(child: Mapping, parent: Mapping) -> tuple[bool, str]:
    """Prove only the ordering facts which the retained evidence supports.

    Slots order different blocks.  Events in one slot require a transaction
    ordinal; events in one transaction require an instruction ordinal.  We
    intentionally do not use block time, SQLite row order, or an age limit as
    a substitute for any missing coordinate.
    """
    child_key = _event_key(child)
    parent_key = _event_key(parent)
    if not child_key or not parent_key:
        return False, "SLOT_ORDER_UNAVAILABLE"
    parent_slot, parent_tx, parent_ix = parent_key
    child_slot, child_tx, child_ix = child_key
    if parent_slot > child_slot:
        return False, "PARENT_AFTER_CHILD_SLOT"
    if parent_slot < child_slot:
        return True, "EARLIER_SLOT"
    if parent_tx is None or child_tx is None:
        return False, "SAME_SLOT_TRANSACTION_ORDER_UNAVAILABLE"
    if parent_tx > child_tx:
        return False, "PARENT_AFTER_CHILD_TRANSACTION"
    if parent_tx < child_tx:
        return True, "EARLIER_TRANSACTION_SAME_SLOT"
    if parent_ix is None or child_ix is None:
        return False, "SAME_TRANSACTION_INSTRUCTION_ORDER_UNAVAILABLE"
    if parent_ix >= child_ix:
        return False, "PARENT_NOT_BEFORE_CHILD_INSTRUCTION"
    return True, "EARLIER_INSTRUCTION_SAME_TRANSACTION"


def select_causal_parent(child: Mapping, parents: Iterable[Mapping]) -> tuple[Mapping | None, str, int]:
    """Select a semantically linked predecessor, never by database row order.

    An earlier slot is causal. Same-slot selection requires transaction
    ordering and same-transaction selection requires instruction ordering. A
    parent must explicitly name the child wallet it funds. Ties at the same
    semantic/event rank fail closed.
    """
    child_key = _event_key(child)
    if not child_key:
        return None, "CHILD_ORDER_UNAVAILABLE", 0
    viable = []
    rejected = 0
    for parent in parents:
        key = _event_key(parent)
        if not key or parent.get("funded_wallet") != child.get("wallet") or parent.get("route_semantics") != "DIRECT":
            rejected += 1; continue
        ordered, _ = causal_order(child, parent)
        if not ordered:
            rejected += 1; continue
        viable.append(parent)
    if not viable:
        return None, "NO_CAUSAL_PARENT", rejected
    # strongest retained semantic rank, then nearest preceding event; ties are ambiguous.
    ranked = sorted(
        viable,
        key=lambda p: (
            int(p.get("semantic_rank", 0)), _event_key(p)[0],
            _event_key(p)[1] if _event_key(p)[1] is not None else -1,
            _event_key(p)[2] if _event_key(p)[2] is not None else -1,
        ),
        reverse=True,
    )
    top = ranked[0]
    top_rank = (int(top.get("semantic_rank", 0)), _event_key(top))
    if sum((int(p.get("semantic_rank", 0)), _event_key(p)) == top_rank for p in ranked) > 1:
        return None, "AMBIGUOUS_CAUSAL_PARENT", rejected
    return top, "SEMANTIC_CAUSAL_PREDECESSOR", rejected


def qualify_selected_edge_chain(*, child: Mapping, selected_edges: Iterable[Mapping]) -> dict:
    """Validate a retained selected-edge chain without reselecting it.

    Legacy selected-edge rows may retain block time but not the slot and
    instruction ordering required to establish that a transfer caused the next
    child event.  That absence is deliberately ``INSUFFICIENT_EVIDENCE``;
    block-time age is not converted into an arbitrary staleness threshold.
    """
    current = dict(child)
    accepted: list[dict] = []
    rejected = 0
    for edge in selected_edges:
        parent = {
            "funded_wallet": edge.get("wallet"),
            "slot": edge.get("slot"),
            "transaction_index": edge.get("transaction_index"),
            "instruction_index": edge.get("instruction_index"),
            "route_semantics": edge.get("route_semantics", "DIRECT"),
            "semantic_rank": edge.get("semantic_rank", 1),
            "signature": edge.get("signature"),
            "block_time": edge.get("block_time"),
            "candidate_parent": edge.get("candidate_parent"),
        }
        selected, reason, rejected_count = select_causal_parent(current, [parent])
        rejected += rejected_count
        if selected is None:
            return {
                "route_complete": False,
                "reason": reason,
                "accepted_edges": accepted,
                "rejected_competing_edge_count": rejected,
                "terminal_root": None,
            }
        accepted.append({
            "child_wallet": current.get("wallet"), "child_slot": current.get("slot"),
            "child_signature": current.get("signature"), "parent_wallet": selected.get("candidate_parent"),
            "parent_slot": selected.get("slot"), "parent_signature": selected.get("signature"),
            "parent_block_time": selected.get("block_time"), "selection_reason": reason,
        })
        current = {
            "wallet": selected.get("candidate_parent"), "slot": selected.get("slot"),
            "transaction_index": selected.get("transaction_index"),
            "instruction_index": selected.get("instruction_index"), "signature": selected.get("signature"),
        }
    return {
        "route_complete": bool(accepted),
        "reason": "QUALIFIED_CAUSAL_SELECTED_CHAIN" if accepted else "NO_SELECTED_EDGE",
        "accepted_edges": accepted,
        "rejected_competing_edge_count": rejected,
        "terminal_root": current.get("wallet"),
    }


def record_causal_order_evidence(conn: sqlite3.Connection, *, operation_family: str,
                                 launch_mint: str, parent: Mapping, child: Mapping,
                                 evidence_source: str, parser_version: str,
                                 qualification_state: str,
                                 now: int | None = None) -> None:
    """Append compact ordering coordinates; never retain raw transactions.

    This table is intentionally separate from legacy selected-edge facts.  It
    can document a future discovery decision without rewriting any historical
    queue, membership, or canonical treasury record.
    """
    ensure_schema(conn)
    timestamp = int(time.time()) if now is None else int(now)
    conn.execute(
        "INSERT OR IGNORE INTO wt_treasury_rotation_causal_order_evidence "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            operation_family, launch_mint, CANDIDATE_SEMANTIC_VERSION,
            str(parent.get("signature") or ""), parent.get("slot"),
            _ordering_value(parent, "transaction_index"), _ordering_value(parent, "instruction_index"),
            str(child.get("signature") or ""), child.get("slot"),
            _ordering_value(child, "transaction_index"), _ordering_value(child, "instruction_index"),
            evidence_source, parser_version, qualification_state, timestamp,
        ),
    )


def classify_route(*, operation_family: str, full_fingerprint: bool, terminal_root: str | None,
                   route_complete: bool, known_at_event_time: bool, known_now: bool,
                   contradictory_reason: str | None = None) -> str:
    if contradictory_reason:
        return CONTRADICTORY_TREASURY
    if not full_fingerprint or not terminal_root or not route_complete:
        return INSUFFICIENT_EVIDENCE
    if known_at_event_time:
        return KNOWN_OPERATION_TREASURY
    return NEW_TREASURY_CANDIDATE


def candidate_identity(operation_family: str, treasury: str, semantic_version: str = CANDIDATE_SEMANTIC_VERSION) -> str:
    return hashlib.sha256(f"{operation_family}\x1f{treasury}\x1f{semantic_version}".encode()).hexdigest()


def record_candidate(conn: sqlite3.Connection, *, operation_family: str, treasury: str,
                     launch_mint: str, fingerprint_version: str, route: Mapping,
                     known_at_event_time: bool, known_now: bool, now: int | None = None) -> str:
    ensure_schema(conn); now = int(time.time()) if now is None else int(now)
    digest = hashlib.sha256(json.dumps(route, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    conn.execute("INSERT INTO wt_treasury_rotation_candidates VALUES(?,?,?,?,?,?,?, 'PENDING_REVIEW') ON CONFLICT(operation_family,treasury,semantic_version) DO UPDATE SET last_seen_at=excluded.last_seen_at,known_now=excluded.known_now", (operation_family,treasury,CANDIDATE_SEMANTIC_VERSION,now,now,int(known_at_event_time),int(known_now)))
    conn.execute("INSERT OR IGNORE INTO wt_treasury_rotation_candidate_evidence VALUES(?,?,?,?,?,?,?,?,?,?,?)", (operation_family,treasury,CANDIDATE_SEMANTIC_VERSION,launch_mint,digest,fingerprint_version,route.get("event_slot"),route.get("event_time"),route.get("terminal_hop",treasury),"QUALIFIED_CAUSAL_ROUTE",now))
    return candidate_identity(operation_family, treasury)


def record_contradiction(conn: sqlite3.Connection, *, operation_family: str,
                         treasury: str, launch_mint: str, route: Mapping,
                         reason: str, now: int | None = None) -> None:
    """Append a later contradiction without deleting candidate evidence."""
    ensure_schema(conn)
    timestamp = int(time.time()) if now is None else int(now)
    digest = hashlib.sha256(json.dumps(route, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    conn.execute(
        "INSERT OR IGNORE INTO wt_treasury_rotation_contradictions VALUES(?,?,?,?,?,?,?)",
        (operation_family, treasury, CANDIDATE_SEMANTIC_VERSION, launch_mint, digest, reason, timestamp),
    )


def evaluate_discovery(*, operation_family: str, fingerprint_version: str,
                       full_fingerprint: bool, terminal_root: str | None,
                       route_complete: bool, known_at_event_time: bool,
                       known_now: bool, launch_mint: str, route: Mapping,
                       contradictory_reason: str | None = None,
                       candidate_conn: sqlite3.Connection | None = None,
                       now: int | None = None) -> dict:
    """Classify one fully retained route without changing canonical state.

    Candidate persistence is intentionally optional and limited to the
    append-only review tables.  This function never imports or touches the
    treasury authority or operation-membership projection.
    """
    classification = classify_route(
        operation_family=operation_family, full_fingerprint=full_fingerprint,
        terminal_root=terminal_root, route_complete=route_complete,
        known_at_event_time=known_at_event_time, known_now=known_now,
        contradictory_reason=contradictory_reason,
    )
    result = {
        "classification": classification,
        "operation_family": operation_family,
        "terminal_root": terminal_root,
        "known_at_event_time": bool(known_at_event_time),
        "known_now": bool(known_now),
        "candidate_identity": None,
    }
    if candidate_conn is not None and terminal_root:
        if classification == NEW_TREASURY_CANDIDATE:
            result["candidate_identity"] = record_candidate(
                candidate_conn, operation_family=operation_family,
                treasury=terminal_root, launch_mint=launch_mint,
                fingerprint_version=fingerprint_version, route=route,
                known_at_event_time=known_at_event_time, known_now=known_now,
                now=now,
            )
        elif classification == CONTRADICTORY_TREASURY:
            record_contradiction(
                candidate_conn, operation_family=operation_family,
                treasury=terminal_root, launch_mint=launch_mint, route=route,
                reason=contradictory_reason or "AFFIRMATIVE_CONTRADICTION", now=now,
            )
    return result
