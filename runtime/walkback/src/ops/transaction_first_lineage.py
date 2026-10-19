"""X78.13 transaction-first historical lineage substrate.

This module deliberately has no dependency on canonical identities, treasury
labels, review decisions, or inherited session roots.  It turns launch facts and
parsed transactions into an additive, auditable graph.  Canonical comparison is
implemented as a separate post-freeze overlay.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence


SYSTEM_PROGRAM = "11111111111111111111111111111111"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
WSOL_MINT = "So11111111111111111111111111111111111111112"
SCHEMA_VERSION = 1


SCHEMA = """
CREATE TABLE IF NOT EXISTS tf_runs (
    run_id TEXT PRIMARY KEY,
    started_at INTEGER NOT NULL,
    completed_at INTEGER,
    graph_frozen_at INTEGER,
    status TEXT NOT NULL,
    launch_count INTEGER NOT NULL DEFAULT 0,
    transaction_count INTEGER NOT NULL DEFAULT 0,
    edge_count INTEGER NOT NULL DEFAULT 0,
    path_count INTEGER NOT NULL DEFAULT 0,
    population_count INTEGER NOT NULL DEFAULT 0,
    rpc_calls INTEGER NOT NULL DEFAULT 0,
    cache_hits INTEGER NOT NULL DEFAULT 0,
    db_write_ms INTEGER NOT NULL DEFAULT 0,
    peak_batch_size INTEGER NOT NULL DEFAULT 0,
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS tf_launches (
    mint TEXT PRIMARY KEY,
    creator TEXT,
    creation_signature TEXT,
    creation_time INTEGER,
    source_platform TEXT,
    launch_status TEXT NOT NULL,
    verification_reason TEXT,
    has_persisted_walkback INTEGER NOT NULL DEFAULT 0,
    acquisition_state TEXT NOT NULL DEFAULT 'PERSISTED_EVIDENCE_PENDING',
    verified_at INTEGER
);

CREATE TABLE IF NOT EXISTS tf_transaction_cache (
    signature TEXT PRIMARY KEY,
    block_time INTEGER,
    transaction_json TEXT,
    fetched_at INTEGER NOT NULL,
    source TEXT NOT NULL,
    rpc_verified INTEGER NOT NULL DEFAULT 0,
    parse_status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tf_edges (
    edge_id TEXT PRIMARY KEY,
    sender TEXT NOT NULL,
    recipient TEXT NOT NULL,
    signature TEXT NOT NULL,
    block_time INTEGER NOT NULL,
    amount TEXT,
    asset TEXT NOT NULL,
    relationship_type TEXT NOT NULL,
    mechanism TEXT NOT NULL,
    source_program TEXT,
    hop_depth INTEGER,
    launch_context TEXT,
    creator_context TEXT,
    evidence_source TEXT NOT NULL,
    observed_or_inherited TEXT NOT NULL CHECK(observed_or_inherited='OBSERVED'),
    rpc_verified INTEGER NOT NULL,
    incoming_amount TEXT,
    outgoing_amount TEXT,
    time_gap_seconds INTEGER,
    amount_difference TEXT,
    UNIQUE(signature, sender, recipient, relationship_type, asset, launch_context)
);

CREATE INDEX IF NOT EXISTS ix_tf_edges_launch ON tf_edges(launch_context, block_time);
CREATE INDEX IF NOT EXISTS ix_tf_edges_recipient ON tf_edges(recipient, block_time);
CREATE INDEX IF NOT EXISTS ix_tf_edges_sender ON tf_edges(sender, block_time);

CREATE TABLE IF NOT EXISTS tf_context_observations (
    context_id TEXT PRIMARY KEY,
    signature TEXT NOT NULL,
    block_time INTEGER,
    context_type TEXT NOT NULL,
    wallets_json TEXT NOT NULL,
    launch_context TEXT,
    evidence_source TEXT NOT NULL,
    rpc_verified INTEGER NOT NULL,
    UNIQUE(signature, context_type, launch_context)
);

CREATE TABLE IF NOT EXISTS tf_paths (
    mint TEXT PRIMARY KEY,
    creator TEXT,
    root TEXT,
    subprovider TEXT,
    edge_count INTEGER NOT NULL,
    max_depth INTEGER NOT NULL,
    path_status TEXT NOT NULL,
    edge_ids_json TEXT NOT NULL,
    termination_reason TEXT NOT NULL,
    chronology_valid INTEGER NOT NULL,
    reconstructed_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS tf_populations (
    root TEXT PRIMARY KEY,
    population_type TEXT NOT NULL,
    launch_count INTEGER NOT NULL,
    creator_count INTEGER NOT NULL,
    direct_child_count INTEGER NOT NULL,
    max_depth INTEGER NOT NULL,
    mechanisms_json TEXT NOT NULL,
    first_seen INTEGER,
    last_seen INTEGER,
    topology_json TEXT NOT NULL,
    infrastructure_status TEXT NOT NULL DEFAULT 'UNCLASSIFIED',
    graph_frozen_run_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tf_population_members (
    root TEXT NOT NULL,
    mint TEXT NOT NULL,
    creator TEXT,
    subprovider TEXT,
    PRIMARY KEY(root, mint)
);

CREATE TABLE IF NOT EXISTS tf_acquisition_queue (
    mint TEXT PRIMARY KEY,
    creator TEXT,
    creation_signature TEXT,
    priority_key TEXT NOT NULL,
    state TEXT NOT NULL,
    required_evidence TEXT NOT NULL,
    estimated_rpc_calls INTEGER NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_attempt_at INTEGER,
    failure_reason TEXT
);

CREATE TABLE IF NOT EXISTS tf_canonical_overlay (
    object_type TEXT NOT NULL,
    object_id TEXT NOT NULL,
    mint TEXT,
    canonical_root TEXT,
    reconstructed_root TEXT,
    comparison_class TEXT NOT NULL,
    compared_at INTEGER NOT NULL,
    PRIMARY KEY(object_type, object_id, mint)
);

CREATE TABLE IF NOT EXISTS tf_session_comparison (
    session_id INTEGER PRIMARY KEY,
    stored_root TEXT,
    stored_child TEXT,
    direct_sender TEXT,
    signature TEXT,
    comparison_class TEXT NOT NULL,
    compared_at INTEGER NOT NULL
);
"""


@dataclass(frozen=True)
class LaunchFact:
    mint: str
    creator: str | None
    creation_signature: str | None
    creation_time: int | None
    source_platform: str | None = None


def connect_substrate(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    ensure_schema(conn)
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def stable_id(*parts: object) -> str:
    return hashlib.sha256("\x1f".join(str(p or "") for p in parts).encode()).hexdigest()


def priority_key(mint: str) -> str:
    """Stable, identity-independent acquisition order."""
    return hashlib.sha256(mint.encode()).hexdigest()


def account_keys(tx: Mapping) -> list[str]:
    raw = (((tx.get("transaction") or {}).get("message") or {}).get("accountKeys") or [])
    return [x.get("pubkey") if isinstance(x, Mapping) else x for x in raw]


def transaction_signers(tx: Mapping) -> list[str]:
    raw = (((tx.get("transaction") or {}).get("message") or {}).get("accountKeys") or [])
    if raw and isinstance(raw[0], Mapping):
        return [x.get("pubkey") for x in raw if x.get("signer")]
    header = (((tx.get("transaction") or {}).get("message") or {}).get("header") or {})
    return list(raw[: int(header.get("numRequiredSignatures") or 0)])


def parsed_instructions(tx: Mapping) -> Iterable[Mapping]:
    message = ((tx.get("transaction") or {}).get("message") or {})
    for instruction in message.get("instructions") or []:
        if isinstance(instruction.get("parsed"), Mapping):
            yield instruction
    for group in (tx.get("meta") or {}).get("innerInstructions") or []:
        for instruction in group.get("instructions") or []:
            if isinstance(instruction.get("parsed"), Mapping):
                yield instruction


def verify_launch(fact: LaunchFact, tx: Mapping | None) -> tuple[str, str]:
    if not fact.mint or not fact.creator or not fact.creation_signature:
        return "PARTIAL_LAUNCH", "missing launch fact"
    if not tx:
        return "PARTIAL_LAUNCH", "creation transaction unavailable"
    block_time = tx.get("blockTime")
    if not isinstance(block_time, int) or block_time <= 0:
        return "UNVERIFIABLE_LAUNCH", "invalid creation timestamp"
    if fact.creator not in transaction_signers(tx):
        return "UNVERIFIABLE_LAUNCH", "recorded creator did not sign creation transaction"
    if fact.mint not in account_keys(tx):
        return "UNVERIFIABLE_LAUNCH", "mint absent from creation transaction"
    return "VERIFIED_LAUNCH", "creator signer and mint account transaction-verified"


def extract_directional_edges(tx: Mapping | None) -> list[dict]:
    """Extract only explicit directional value movement.

    Balance deltas, account-key presence, self-closes and co-occurrence are never
    consulted to establish an edge.
    """
    if not tx or not isinstance(tx.get("blockTime"), int) or tx["blockTime"] <= 0:
        return []
    keys = account_keys(tx)
    meta = tx.get("meta") or {}
    wsol_accounts: dict[str, str] = {}
    for balance in (meta.get("preTokenBalances") or []) + (meta.get("postTokenBalances") or []):
        if balance.get("mint") != WSOL_MINT:
            continue
        index = balance.get("accountIndex")
        if isinstance(index, int) and 0 <= index < len(keys) and balance.get("owner"):
            wsol_accounts[keys[index]] = balance["owner"]

    edges: dict[tuple, dict] = {}
    for instruction in parsed_instructions(tx):
        parsed = instruction.get("parsed") or {}
        info = parsed.get("info") or {}
        kind = parsed.get("type")
        program = instruction.get("program") or instruction.get("programId") or ""
        row = None
        if kind in ("transfer", "transferWithSeed") and program in ("system", SYSTEM_PROGRAM):
            sender = info.get("source")
            recipient = info.get("destination") or info.get("newAccount")
            amount = info.get("lamports")
            if sender and recipient and sender != recipient and isinstance(amount, int) and amount > 0:
                row = dict(sender=sender, recipient=recipient, amount=str(amount), asset="SOL",
                           relationship_type="DIRECT_SOL_TRANSFER",
                           mechanism="PLAIN_TRANSFER", source_program=SYSTEM_PROGRAM)
        elif kind in ("createAccount", "createAccountWithSeed") and program in ("system", SYSTEM_PROGRAM):
            sender = info.get("source") or info.get("fromPubkey") or info.get("base")
            recipient = info.get("newAccount") or info.get("newAccountPubkey")
            amount = info.get("lamports")
            if sender and recipient and sender != recipient and isinstance(amount, int) and amount > 0:
                row = dict(sender=sender, recipient=recipient, amount=str(amount), asset="SOL",
                           relationship_type="ACCOUNT_CREATION_FUNDING",
                           mechanism="ACCOUNT_CREATION", source_program=SYSTEM_PROGRAM)
        elif kind == "withdrawNonce" and program in ("system", SYSTEM_PROGRAM):
            sender = info.get("nonceAccount")
            recipient = info.get("destination")
            amount = info.get("lamports")
            if sender and recipient and sender != recipient and isinstance(amount, int) and amount > 0:
                row = dict(sender=sender, recipient=recipient, amount=str(amount), asset="SOL",
                           relationship_type="SEEDED_ACCOUNT_CLOSE",
                           mechanism="SEEDED_ACCOUNT_CLOSE", source_program=SYSTEM_PROGRAM)
        elif kind in ("transfer", "transferChecked") and program in ("spl-token", TOKEN_PROGRAM):
            sender = info.get("source")
            recipient = info.get("destination")
            amount = info.get("amount") or (info.get("tokenAmount") or {}).get("amount")
            mint = info.get("mint") or "SPL_TOKEN"
            if sender and recipient and sender != recipient and amount not in (None, "0", 0):
                row = dict(sender=sender, recipient=recipient, amount=str(amount), asset=mint,
                           relationship_type="SPL_TRANSFER",
                           mechanism="SPL_TRANSFER", source_program=TOKEN_PROGRAM)
        elif kind == "closeAccount" and program in ("spl-token", TOKEN_PROGRAM):
            account = info.get("account")
            owner = info.get("owner") or info.get("authority")
            recipient = info.get("destination")
            # Only a controlled WSOL close to another wallet establishes direction.
            if (account in wsol_accounts and owner == wsol_accounts[account]
                    and owner and recipient and owner != recipient):
                row = dict(sender=owner, recipient=recipient, amount=None, asset="SOL",
                           relationship_type="WSOL_WRAP_CLOSE",
                           mechanism="WSOL_WRAP_CLOSE", source_program=TOKEN_PROGRAM)
        elif kind in ("transfer", "transferChecked"):
            sender = info.get("source")
            recipient = info.get("destination")
            amount = info.get("amount") or (info.get("tokenAmount") or {}).get("amount")
            if sender and recipient and sender != recipient and amount not in (None, "0", 0):
                row = dict(sender=sender, recipient=recipient, amount=str(amount),
                           asset=info.get("mint") or "UNKNOWN_ASSET",
                           relationship_type="OTHER_EXPLICIT_TRANSFER",
                           mechanism="OTHER_EXPLICIT_TRANSFER", source_program=str(program))
        if row:
            key = (row["sender"], row["recipient"], row["relationship_type"], row["asset"])
            edges[key] = row
    return list(edges.values())


def extract_context(tx: Mapping | None) -> list[dict]:
    if not tx:
        return []
    signers = sorted(x for x in transaction_signers(tx) if x)
    observations = []
    if len(signers) > 1:
        observations.append({"context_type": "CO_SIGNED_ACTIVITY", "wallets": signers})
    keys = sorted(set(x for x in account_keys(tx) if x))
    if len(keys) > 1:
        observations.append({"context_type": "TRANSACTION_CO_OCCURRENCE", "wallets": keys})
    return observations


def longest_chronological_path(edges: Sequence[Mapping], creator: str, launch_time: int,
                               max_depth: int = 12) -> list[Mapping]:
    incoming: dict[str, list[Mapping]] = defaultdict(list)
    for edge in edges:
        if edge["block_time"] < launch_time:
            incoming[edge["recipient"]].append(edge)

    def walk(node: str, cutoff: int, seen: frozenset[str], depth: int) -> list[Mapping]:
        if node in seen or depth >= max_depth:
            return []
        choices = []
        for edge in incoming.get(node, []):
            if edge["block_time"] >= cutoff:
                continue
            if edge["sender"] in seen or edge["sender"] == node:
                continue
            prefix = walk(edge["sender"], edge["block_time"], seen | {node}, depth + 1)
            choices.append(prefix + [edge])
        return max(choices, key=lambda p: (len(p), tuple(e["edge_id"] for e in p)), default=[])

    return walk(creator, launch_time, frozenset(), 0)


def classify_path(path: Sequence[Mapping], launch_verified: bool) -> tuple[str, str]:
    if not launch_verified:
        return "EVIDENCE_UNAVAILABLE", "launch identity not transaction-verified"
    if not path:
        return "EVIDENCE_UNAVAILABLE", "no explicit creator funding edge"
    if len(path) == 1:
        return "PARTIALLY_REDISCOVERED", "one explicit creator-funding edge"
    return "COMPLETE", "oldest explicit source reached"


def build_populations(conn: sqlite3.Connection, run_id: str) -> int:
    rows = conn.execute("""
        SELECT p.mint,p.creator,p.root,p.subprovider,p.edge_count,p.max_depth,
               e.mechanism,e.block_time,e.sender,e.recipient
        FROM tf_paths p
        JOIN json_each(p.edge_ids_json) selected_edge ON 1=1
        JOIN tf_edges e ON e.edge_id=selected_edge.value
        WHERE p.path_status='COMPLETE' AND p.root IS NOT NULL
        ORDER BY p.root,p.mint,e.block_time,e.edge_id
    """).fetchall()
    grouped = defaultdict(lambda: {"mints": set(), "creators": set(), "children": set(),
                                  "depth": 0, "mechanisms": Counter(), "times": [],
                                  "topology": Counter(), "members": {}})
    for mint, creator, root, subprovider, edge_count, depth, mechanism, bt, sender, recipient in rows:
        group = grouped[root]
        group["mints"].add(mint)
        group["creators"].add(creator)
        if sender == root:
            group["children"].add(recipient)
        group["depth"] = max(group["depth"], depth)
        group["mechanisms"][mechanism] += 1
        group["times"].append(bt)
        group["topology"][f"{sender}->{recipient}"] += 1
        group["members"][mint] = (creator, subprovider)

    conn.execute("DELETE FROM tf_population_members")
    conn.execute("DELETE FROM tf_populations")
    for root in sorted(grouped):
        group = grouped[root]
        count = len(group["mints"])
        if count == 1:
            population_type = "SINGLE_LAUNCH_ROOT"
        elif len(group["children"]) == 1:
            population_type = "REPEATED_ROOT"
        else:
            population_type = "REPEATED_TOPOLOGY"
        conn.execute("""
            INSERT INTO tf_populations
            (root,population_type,launch_count,creator_count,direct_child_count,max_depth,
             mechanisms_json,first_seen,last_seen,topology_json,graph_frozen_run_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (root, population_type, count, len(group["creators"]), len(group["children"]),
              group["depth"], json.dumps(dict(group["mechanisms"]), sort_keys=True),
              min(group["times"]), max(group["times"]),
              json.dumps(dict(group["topology"]), sort_keys=True), run_id))
        conn.executemany(
            "INSERT INTO tf_population_members(root,mint,creator,subprovider) VALUES (?,?,?,?)",
            [(root, mint, *group["members"][mint]) for mint in sorted(group["mints"])])
    conn.commit()
    return len(grouped)


def graph_digest(conn: sqlite3.Connection) -> str:
    digest = hashlib.sha256()
    for table, columns in (
        ("tf_launches", "mint,creator,creation_signature,creation_time,launch_status"),
        ("tf_edges", "edge_id,sender,recipient,signature,block_time,relationship_type,launch_context"),
        ("tf_paths", "mint,root,subprovider,edge_count,path_status,edge_ids_json"),
        ("tf_populations", "root,population_type,launch_count,creator_count,direct_child_count,max_depth"),
    ):
        digest.update(table.encode())
        for row in conn.execute(f"SELECT {columns} FROM {table} ORDER BY 1"):
            digest.update(json.dumps(tuple(row), separators=(",", ":"), default=str).encode())
    return digest.hexdigest()


def initialise_run(conn: sqlite3.Connection, run_id: str) -> None:
    conn.execute("""
        INSERT INTO tf_runs(run_id,started_at,status,schema_version)
        VALUES (?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET started_at=excluded.started_at,
        completed_at=NULL,graph_frozen_at=NULL,status=excluded.status
    """, (run_id, int(time.time()), "COLLECTING", SCHEMA_VERSION))
    conn.commit()


def finish_run(conn: sqlite3.Connection, run_id: str, metrics: Mapping[str, int]) -> None:
    now = int(time.time())
    conn.execute("""
        UPDATE tf_runs SET completed_at=?,graph_frozen_at=?,status='FROZEN',
          launch_count=?,transaction_count=?,edge_count=?,path_count=?,population_count=?,
          rpc_calls=?,cache_hits=?,db_write_ms=?,peak_batch_size=? WHERE run_id=?
    """, (now, now, metrics.get("launch_count", 0), metrics.get("transaction_count", 0),
          metrics.get("edge_count", 0), metrics.get("path_count", 0),
          metrics.get("population_count", 0), metrics.get("rpc_calls", 0),
          metrics.get("cache_hits", 0), metrics.get("db_write_ms", 0),
          metrics.get("peak_batch_size", 0), run_id))
    conn.commit()
