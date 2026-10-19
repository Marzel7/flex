"""Concrete retained/cache/provider resolver for passive PumpSwap evidence."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from src.acquisition.tf_cache_lookup import TransactionFirstLineageCacheLookup
from src.core.rpc_cache import RPCCache

MAX_PROVIDER_FETCHES = 10
PARSER_VERSION = "pumpswap-exact-transaction-v1"


def _conn(path: str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_evidence_store(path: str) -> None:
    with _conn(path) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS pumpswap_exact_transactions (
            id TEXT PRIMARY KEY, signature TEXT UNIQUE NOT NULL, provenance TEXT NOT NULL,
            provider TEXT, method TEXT, request_json TEXT, response_json TEXT NOT NULL,
            response_sha256 TEXT NOT NULL, slot INTEGER, tx_version TEXT,
            parser_version TEXT NOT NULL, status TEXT NOT NULL, acquired_at INTEGER NOT NULL)""")


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _valid(tx: Any) -> bool:
    return isinstance(tx, Mapping) and isinstance(tx.get("slot"), int) and isinstance(
        ((tx.get("transaction") or {}).get("message") or {}).get("instructions"), list)


class ConcretePumpSwapTransactionResolver:
    """Strictly ordered resolver; provider work occurs without an open DB transaction."""

    def __init__(self, *, evidence_store: str, transaction_first_db: str | None = None,
                 rpc_cache_db: str | None = None,
                 provider_fetch: Callable[[str], Mapping[str, Any] | None] | None = None):
        self.evidence_store = evidence_store
        self.transaction_first_db = transaction_first_db
        self.rpc_cache_db = rpc_cache_db
        self.provider_fetch = provider_fetch
        self.provider_fetches = 0
        self._attempted: set[str] = set()
        self.metrics = {key: 0 for key in ("retained_lookup_attempts", "retained_hits",
            "cache_lookup_attempts", "cache_hits", "provider_fetches", "provider_failures",
            "unavailable", "artifacts_retained")}

    def _retained(self, signature: str) -> Mapping[str, Any] | None:
        self.metrics["retained_lookup_attempts"] += 1
        ensure_evidence_store(self.evidence_store)
        with _conn(self.evidence_store) as conn:
            row = conn.execute("SELECT * FROM pumpswap_exact_transactions WHERE signature=? AND status='SUCCESS'", (signature,)).fetchone()
        if not row:
            return None
        response = json.loads(row["response_json"])
        tx = response.get("result", response)
        if not _valid(tx):
            return None
        self.metrics["retained_hits"] += 1
        return {"status": "RETAINED_HIT", "transaction": tx, "transaction_evidence_id": row["id"],
                "response_sha256": row["response_sha256"], "slot": row["slot"], "tx_version": row["tx_version"]}

    def _cache(self, signature: str) -> Mapping[str, Any] | None:
        self.metrics["cache_lookup_attempts"] += 1
        tx = None
        source = None
        if self.transaction_first_db and Path(self.transaction_first_db).exists():
            try:
                hit = TransactionFirstLineageCacheLookup(Path(self.transaction_first_db)).lookup(signature)
            except (sqlite3.Error, json.JSONDecodeError):
                hit = None
            if hit and hit.transaction_json and hit.rpc_verified:
                tx, source = hit.transaction_json, "tf_transaction_cache"
        if tx is None and self.rpc_cache_db and Path(self.rpc_cache_db).exists():
            response = RPCCache(self.rpc_cache_db).get(RPCCache.make_key_get_transaction(signature))
            tx = response.get("result") if isinstance(response, Mapping) else None
            source = "rpc_response_cache" if tx else None
        if not _valid(tx):
            return None
        self.metrics["cache_hits"] += 1
        digest = hashlib.sha256(_canonical(tx).encode()).hexdigest()
        return {"status": "CACHE_HIT", "transaction": tx, "cache_source": source,
                "response_sha256": digest, "slot": tx.get("slot"), "tx_version": str(tx.get("version"))}

    def _retain_provider(self, signature: str, response: Mapping[str, Any]) -> Mapping[str, Any] | None:
        tx = response.get("result")
        if not _valid(tx):
            return None
        body = _canonical(response)
        digest = hashlib.sha256(body.encode()).hexdigest()
        ident = hashlib.sha256(f"{signature}\0{digest}\0{PARSER_VERSION}".encode()).hexdigest()
        ensure_evidence_store(self.evidence_store)
        with _conn(self.evidence_store) as conn:
            conn.execute("""INSERT OR IGNORE INTO pumpswap_exact_transactions
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (ident, signature, "PROVIDER_FETCH",
                "listener_discovery_rpc", "getTransaction", _canonical({"signature": signature}), body,
                digest, tx["slot"], str(tx.get("version")), PARSER_VERSION, "SUCCESS", int(time.time())))
        self.metrics["artifacts_retained"] += 1
        return {"status": "PROVIDER_FETCH", "transaction": tx, "transaction_evidence_id": ident,
                "response_sha256": digest, "slot": tx["slot"], "tx_version": str(tx.get("version")),
                "provider": "listener_discovery_rpc", "method": "getTransaction"}

    def resolve_exact_transaction(self, signature: str) -> Mapping[str, Any]:
        hit = self._retained(signature) or self._cache(signature)
        if hit:
            return hit
        if not self.provider_fetch or signature in self._attempted or self.provider_fetches >= MAX_PROVIDER_FETCHES:
            self.metrics["unavailable"] += 1
            return {"status": "UNAVAILABLE", "transaction": None}
        self._attempted.add(signature)
        self.provider_fetches += 1
        self.metrics["provider_fetches"] += 1
        # No connection is open here: provider acquisition is intentionally outside DB transactions.
        response = self.provider_fetch(signature)
        retained = self._retain_provider(signature, response) if isinstance(response, Mapping) else None
        if retained:
            return retained
        self.metrics["provider_failures"] += 1
        self.metrics["unavailable"] += 1
        return {"status": "UNAVAILABLE", "transaction": None}


def submission_capability() -> str:
    return "NONE"
