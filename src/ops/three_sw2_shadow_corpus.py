"""Bounded EP3.2A materialization for the frozen 3SW2 comparison population."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any

import aiohttp

from src.acquisition.transaction import SharedTransactionAcquisition
from src.evidence.service import EvidencePlatform
from src.evidence.artifacts import ArtifactStore
from src.evidence.database import EvidenceDatabase
from src.evidence.normalization import NormalizationEngine
from src.evidence.primitives.engine import PrimitiveEngine
from src.ops.watchtower_shadow_corpus import WatchtowerShadowCorpusMaterializer, _digest, _read_only
from src.ops.watchtower_shadow_recovery import RecoveryBudget, RecoveryLimits, WatchtowerShadowRecovery

CONTROLLER = "3SW2zquY2mVTbNuw1ZCGgtoehq2evfU36PFd6TTqSXdK"
MINTS = (
    "GkXUvai4Hk3XnhKbevAibRygvU5GASzHFNcjJcqpump", "Lt5a2XWZXgFiYrNaqwQzSVT3tPRarT3rQRiyMWPpump",
    "SPof24S7YxtfVBo9hN8utCf47c6j4fgVQfod2xmpump", "Y5bGdNx6BFDdRYCHuxZ6EyFuqxr4xS535KjiVE8pump",
    "YDxw5V4rMYzDFomPaxGbUSBUdiNgBJikGBPbBW2pump", "dDqcg6kAfrJ39D3uKDRaRaAugbZav5efevKPCnmpump",
    "hYDJmMxa3CrPmXzaDyatVoRQxZ3zJTPuvLNBQnWpump", "iUXa5BUbZ4EY3BReD4EFYU1eC75eVHzu8L9H1ZVpump",
    "kYkR6zZvgo7vpptX1F2eXgojzmRACox3be2upKupump", "qDKdQJT4WeLoAcdrTybWyP4N966XCaRBkjXm1Cxpump",
    "uxDBFdzJbmZkthhUQYFQ6unSfMAfdKxSuoKq8gCpump", "wAQpxAZRSspX3xG7RKDXZoPqKBoc67qnD7jtm5gpump",
    "wiW7UmNiE2Aud3GxmHtCN8UXfrFu1ombuyDyyYMpump",
)
LIMITS = RecoveryLimits(known_transactions=1, discovery_subjects=1,
                        pages_per_subject=3, rpc_calls=5, credits=50)


class ThreeSw2ShadowCorpus:
    def __init__(self, *, operations_db: Path, main_db: Path, cache_db: Path, output_root: Path) -> None:
        self.operations_db, self.main_db, self.cache_db = map(Path, (operations_db, main_db, cache_db))
        self.output_root = Path(output_root)
        self.helper = WatchtowerShadowCorpusMaterializer(
            operations_db=self.operations_db, transaction_cache_db=self.cache_db,
            output_root=self.output_root,
        )

    def population(self) -> dict[str, list[dict[str, Any]]]:
        main = _read_only(self.main_db); operations = _read_only(self.operations_db)
        try:
            placeholders = ",".join("?" * len(MINTS))
            launch_rows = {row["mint"]: dict(row) for row in main.execute(
                f"SELECT mint,earliest_tx_creator creator_wallet,create_tx_signature create_signature,"
                f"CAST(strftime('%s',created_at) AS INTEGER) create_time FROM token_analysis "
                f"WHERE mint IN ({placeholders})", MINTS)}
            edges = [dict(row) for row in operations.execute(
                f"SELECT * FROM wt_provisioning_edges WHERE source_mint IN ({placeholders}) "
                "AND edge_type='SUBPROV_TO_CREATOR' AND from_wallet=? ORDER BY source_mint,edge_id",
                (*MINTS, CONTROLLER))]
        finally:
            main.close(); operations.close()
        if set(launch_rows) != set(MINTS) or len(edges) != 13:
            raise ValueError("frozen 3SW2 population boundary mismatch")
        population = {"launches": [launch_rows[mint] for mint in MINTS],
                      "provisioning_edges": edges, "treasuries": [], "entities": []}
        if {row["source_mint"] for row in edges} != set(MINTS):
            raise ValueError("activation coverage is not exactly one per frozen launch")
        return population

    def plan(self) -> dict[str, Any]:
        population = self.population(); contexts = self.helper._contexts(population)
        cache, unavailable = self.helper._cached_transactions(contexts)
        missing_creation = [row for row in population["launches"] if not row.get("create_signature")]
        known_missing = sorted(set(unavailable) - {None})
        plan = {"milestone":"EP3.2A","population_digest":_digest(population),
                "frozen_launches":13,"activation_edges":13,"known_signatures":len(contexts),
                "cached_transactions":len(cache),"known_transaction_fetches":len(known_missing),
                "signature_discovery_subjects":len(missing_creation),"pages_per_subject":3,
                "discovered_transaction_fetches":len(missing_creation),
                "hard_rpc_ceiling":len(known_missing)+4*len(missing_creation),
                "hard_credit_ceiling":10*(len(known_missing)+4*len(missing_creation)),
                "approved_limits":LIMITS.__dict__,"known_missing_signatures":known_missing,
                "unresolved_mints":[row["mint"] for row in missing_creation]}
        if plan["hard_rpc_ceiling"] > LIMITS.rpc_calls:
            raise RuntimeError("derived plan exceeds frozen RPC limit")
        return plan

    async def run(self) -> dict[str, Any]:
        started=time.monotonic(); population=self.population(); plan=self.plan()
        self.output_root.mkdir(parents=True,exist_ok=True)
        self.helper._write_json(self.output_root/"population.json",population)
        self.helper._write_json(self.output_root/"acquisition_plan.json",plan)
        contexts=self.helper._contexts(population); cache,unavailable=self.helper._cached_transactions(contexts)
        config=self.helper._config(); config.validate_isolation((self.operations_db,self.main_db,self.cache_db))
        platform=EvidencePlatform(config); platform.writer.primitive_engine=None
        recovery=WatchtowerShadowRecovery(operations_db=self.operations_db,main_db=self.main_db,
            transaction_cache_db=self.cache_db,output_root=self.output_root,limits=LIMITS)
        recovery.materializer=self.helper; recovery.budget=RecoveryBudget(LIMITS)
        results={}; overrides={}
        platform.writer.start()
        try:
            self.helper._publish_cache(platform,cache,contexts)
            urls=recovery._rpc_urls()
            async with aiohttp.ClientSession() as session:
                client=SharedTransactionAcquisition(session,semaphore=asyncio.Semaphore(2),telemetry_sink=recovery.budget.observe)
                for signature in sorted(unavailable):
                    context=contexts[signature]
                    results[signature]={"status":await recovery._fetch_transaction(client,platform,urls,signature,context),"launch":context.get("launch"),"purpose":context["purpose"]}
                unresolved=[row for row in population["launches"] if not row.get("create_signature")]
                for launch in unresolved:
                    signature=await recovery._discover_creation(client,platform,urls,launch)
                    if signature:
                        overrides[launch["mint"]]=signature
                        results[launch["mint"]]={"status":await recovery._fetch_transaction(client,platform,urls,signature,{"purpose":"launch_creation","creator":launch["creator_wallet"],"launch":launch["mint"]}),"discovered_signature":signature}
            if not platform.mirror.drain(timeout=300): raise RuntimeError("mirror drain timeout")
            writer=self.helper._drain_writer(platform); primitive=platform.primitive_engine.run_once(); health=platform.health()
        finally:
            platform.writer.stop(); platform.mirror.stop()
        for row in population["launches"]:
            if row["mint"] in overrides: row["create_signature"]=overrides[row["mint"]]
        self.helper._write_json(self.output_root/"population.json",population)
        existing={}
        connection=_read_only(self.output_root/"evidence.db")
        try:
            for row in connection.execute("SELECT payload_json FROM normalized_evidence_records WHERE fact_family='TransactionFact'"):
                payload=json.loads(row[0]); existing[str(payload.get("signature"))]={}
        finally: connection.close()
        required=self.helper._contexts(population); missing={sig:"HISTORICAL_UNAVAILABLE" for sig in required if sig not in existing}
        digests=self.helper._digests(self.output_root/"evidence.db"); replay=self.helper._validate_replay(digests)
        coverage=self.helper._coverage(population,existing,missing,replay["identical"])
        report={"milestone":"EP3.2A","authority":"SHADOW_NON_AUTHORITATIVE","plan":plan,
                "budget":recovery.budget.report(),"population":{"launches":13,"activation_edges":13},
                "results":results,"coverage":coverage["summary"],"replay":replay,"writer":writer,
                "primitive":primitive,"health":health,"duration_seconds":round(time.monotonic()-started,3),
                "production_writes":0,"detectors_executed":0,"governance_actions":0}
        for name,value in (("coverage.json",coverage),("replay.json",replay),("recovery.json",report)):
            self.helper._write_json(self.output_root/name,value)
        return report

    async def materialize_creator_histories(self) -> dict[str, Any]:
        """Bounded supplemental freshness acquisition; no transaction fetches."""
        population=json.loads((self.output_root/"population.json").read_text())
        creators=sorted({row["creator_wallet"] for row in population["launches"]})
        limits=RecoveryLimits(known_transactions=0,discovery_subjects=len(creators),
                              pages_per_subject=1,rpc_calls=len(creators),credits=10*len(creators))
        plan={"purpose":"creator_freshness","subjects":len(creators),
              "rpc_ceiling":len(creators),"credit_ceiling":10*len(creators),
              "pages_per_subject":1,"frozen_launches":13}
        self.helper._write_json(self.output_root/"freshness_acquisition_plan.json",plan)
        config=self.helper._config(); platform=EvidencePlatform(config); platform.writer.primitive_engine=None
        recovery=WatchtowerShadowRecovery(operations_db=self.operations_db,main_db=self.main_db,
            transaction_cache_db=self.cache_db,output_root=self.output_root,limits=limits)
        recovery.materializer=self.helper; recovery.budget=RecoveryBudget(limits)
        platform.writer.start(); results={}
        try:
            urls=recovery._rpc_urls()
            async with aiohttp.ClientSession() as session:
                client=SharedTransactionAcquisition(session,semaphore=asyncio.Semaphore(3),telemetry_sink=recovery.budget.observe)
                launch_by_creator={row["creator_wallet"]:row["mint"] for row in population["launches"]}
                for creator in creators:
                    payload={"jsonrpc":"2.0","id":1,"method":"getSignaturesForAddress",
                             "params":[creator,{"limit":1000,"commitment":"finalized"}]}
                    response=await recovery._request(client,platform,urls,payload,purpose="creator_freshness_history",
                        creator=creator,launch=launch_by_creator[creator],page=1,cursor=None)
                    results[creator]="RECOVERED" if response is not None else "PROVIDER_UNAVAILABLE"
            if not platform.mirror.drain(timeout=300): raise RuntimeError("mirror drain timeout")
            writer=self.helper._drain_writer(platform); primitive=platform.primitive_engine.run_once(); health=platform.health()
        finally:
            platform.writer.stop(); platform.mirror.stop()
        digests=self.helper._digests(self.output_root/"evidence.db"); replay=self._validate_incremental_replay(digests)
        connection=_read_only(self.output_root/"evidence.db")
        try:
            freshness={row[0]:row[1] for row in connection.execute(
                "SELECT quality_state,COUNT(*) FROM primitive_observations WHERE primitive_type='WALLET_FRESH_AT_EVENT' GROUP BY quality_state")}
        finally: connection.close()
        report={"milestone":"EP3.2A-FRESHNESS","plan":plan,"budget":recovery.budget.report(),
                "results":results,"freshness_quality":freshness,"writer":writer,"primitive":primitive,
                "replay":replay,"health":health,"production_writes":0,"detectors_executed":0}
        self.helper._write_json(self.output_root/"freshness_recovery.json",report)
        return report

    def _validate_incremental_replay(self, expected: dict[str,str]) -> dict[str,Any]:
        """Replay the two append-only projection checkpoints deterministically."""
        envelopes=self.helper._reconstruct_envelopes(self.output_root/"evidence.db")
        transaction=[]; history=[]
        for envelope in envelopes:
            purpose=str((envelope.get("acquisition") or {}).get("purpose") or "")
            (history if purpose=="creator_freshness_history" else transaction).append(envelope)
        with tempfile.TemporaryDirectory(prefix="ep3_2a_replay_",dir=self.output_root) as temporary:
            root=Path(temporary); database=EvidenceDatabase(root/"evidence.db",clock=lambda:0); database.open_writer()
            try:
                normalizer=NormalizationEngine(database,ArtifactStore(self.output_root/"artifacts",enabled=True,clock=lambda:0))
                engine=PrimitiveEngine(database,clock=lambda:0)
                for group in (transaction,history):
                    for envelope in group:
                        database.append_batch([{"message_id":f"replay-{envelope['envelope_id']}","envelope":envelope}])
                        normalizer.normalize_envelope(envelope)
                    engine.run_once()
            finally: database.close()
            actual=self.helper._digests(root/"evidence.db")
        return {"identical":expected==actual,"expected":expected,"actual":actual,
                "transaction_envelopes":len(transaction),"history_envelopes":len(history),
                "projection_checkpoints":2,"additional_rpc":0}
