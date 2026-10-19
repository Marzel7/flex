"""Concrete, bounded bridge from the listener's existing infrastructure.

This module deliberately owns no provider URL, credential, signer, or submitter.
It receives the already-running listener and uses its endpoint selection and
quota-governed JSON-RPC method.
"""
from __future__ import annotations

import asyncio
import os
import hashlib
import json
import time
from pathlib import Path
from typing import Any, Mapping

import websockets

from src.ops.pumpswap_program_websocket_task import PumpSwapProgramWebsocketTask
from src.ops.pumpswap_raw_materializer import MAX_PROVIDER_FETCHES
from src.ops.pumpswap_materializer_worker import PumpSwapMaterializerWorker
from src.ops.pumpswap_transaction_resolver import ConcretePumpSwapTransactionResolver
from src.ops.pumpswap_capture_session import CaptureSession
from src.ops.pumpswap_semantic_workers import Workers as PumpSwapSemanticWorkers


class ListenerPumpSwapRuntime:
    """Adapter for one passive research task; all mutable evidence is separate."""

    def __init__(self, listener: Any, *, raw_store: str, materialization_store: str):
        self.listener = listener
        self.raw_store = raw_store
        self.materialization_store = materialization_store
        self.provider_fetches = 0
        self._fetched: set[str] = set()

    def endpoint(self) -> str:
        # The listener's first endpoint is its configured provider endpoint.
        return self.listener._websocket_endpoints()[0][0]

    async def connect(self, endpoint: str) -> Any:
        # Match the listener's existing websocket library and conservative keepalive.
        return await websockets.connect(endpoint, ping_interval=20, ping_timeout=20,
                                        close_timeout=2, max_size=10 * 1024 * 1024)

    async def resolve_transaction(self, signature: str) -> Mapping[str, Any]:
        """Cache-first, single-request exact transaction resolver.

        The returned envelope makes provenance explicit.  A provider result is
        retained by the materializer before instruction parsing.
        """
        cached = getattr(self.listener, "tx_cache", {}).get(signature)
        if cached:
            return {"status": "CACHE_HIT", "transaction": cached[0]}
        if signature in self._fetched or self.provider_fetches >= MAX_PROVIDER_FETCHES:
            return {"status": "UNAVAILABLE", "transaction": None}
        self._fetched.add(signature)
        self.provider_fetches += 1
        response = await self.listener.call_discovery_rpc("getTransaction", [signature, {
            "encoding": "jsonParsed", "commitment": "confirmed",
            "maxSupportedTransactionVersion": 0,
        }], timeout=15)
        tx = response.get("result") if isinstance(response, Mapping) else None
        return {
            "status": "PROVIDER_FETCH" if tx else "UNAVAILABLE",
            "transaction": tx,
            "provider": "listener_discovery_rpc",
            "method": "getTransaction",
            "response": response,
            "response_sha256": hashlib.sha256(json.dumps(response, sort_keys=True,
                separators=(",", ":"), default=str).encode()).hexdigest() if response is not None else None,
            "acquired_at": int(time.time()),
        }

    def task(self, env: Mapping[str, str] | None = None) -> PumpSwapProgramWebsocketTask:
        values = env or {}
        self.session=CaptureSession(self.raw_store + '.sessions.sqlite', values.get('PUMPSWAP_RAW_CAPTURE_SESSION_ID','pumpswap-session-v1'), values.get('PUMPSWAP_RAW_CAPTURE_SESSION_ADMISSION_LIMIT',20))
        return PumpSwapProgramWebsocketTask(endpoint=self.endpoint(), store_path=self.raw_store,
                                            connector=self.connect, env=env, on_commit=self.materializer.signal,session=self.session)

    def _provider_from_worker_thread(self, signature: str) -> Mapping[str, Any] | None:
        future = asyncio.run_coroutine_threadsafe(self.listener.call_discovery_rpc("getTransaction", [signature, {
            "encoding": "jsonParsed", "commitment": "confirmed", "maxSupportedTransactionVersion": 0}], timeout=15), self.loop)
        return future.result(timeout=20)

    def bind_materializer(self, loop: asyncio.AbstractEventLoop, env: Mapping[str, str] | None = None) -> PumpSwapMaterializerWorker:
        self.loop=loop
        resolver=ConcretePumpSwapTransactionResolver(
            evidence_store=self.materialization_store + '.transactions.sqlite',
            transaction_first_db='database/transaction_first_lineage.db',
            rpc_cache_db='database/flex_complete_database.db', provider_fetch=self._provider_from_worker_thread)
        self.semantic_workers=PumpSwapSemanticWorkers(self.materialization_store,self.materialization_store + '.semantic.sqlite',env)
        self.materializer=PumpSwapMaterializerWorker(raw_store=self.raw_store, materialization_store=self.materialization_store,
                                                      resolver=resolver.resolve_exact_transaction, env=env,on_commit=self.semantic_workers.signal)
        return self.materializer


def default_evidence_paths() -> tuple[str, str]:
    configured = os.environ.get('PUMPSWAP_RAW_SESSION_STORE')
    if configured:
        raw = Path(configured)
        return str(raw), str(raw.with_name(raw.stem + '_materializations.sqlite'))
    root = Path("database") / "research_evidence"
    return str(root / "pumpswap_raw_events.sqlite"), str(root / "pumpswap_materializations.sqlite")


def submission_capability() -> str:
    return "NONE"
