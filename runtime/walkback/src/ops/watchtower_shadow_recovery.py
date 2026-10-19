"""Bounded, one-time WATCHTOWER shadow corpus recovery."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

import aiohttp

from src.acquisition.transaction import (
    AcquisitionMetadata,
    AcquisitionResponse,
    SharedTransactionAcquisition,
    acquisition_scope,
)
from src.evidence.service import EvidencePlatform
from src.evidence.mirror import amend_acquisition_observation
from src.evidence.artifacts import ArtifactStore
from src.evidence.database import EvidenceDatabase
from src.evidence.normalization import NormalizationEngine
from src.ops.watchtower_shadow_corpus import (
    PopulationLimits,
    WatchtowerShadowCorpusMaterializer,
    _digest,
    _read_only,
    _signature_valid,
)


@dataclass(frozen=True)
class RecoveryLimits:
    known_transactions: int = 134
    discovery_subjects: int = 31
    pages_per_subject: int = 3
    rpc_calls: int = 258
    credits: int = 2_580


class RecoveryBudgetExceeded(RuntimeError):
    pass


class RecoveryBudget:
    CREDIT_COSTS = {"getTransaction": 10, "getSignaturesForAddress": 10}

    def __init__(self, limits: RecoveryLimits) -> None:
        self.limits = limits
        self.calls = 0
        self.credits = 0
        self.by_method: dict[str, int] = {}
        self.by_provider: dict[str, int] = {}

    def observe(self, metadata: AcquisitionMetadata) -> None:
        cost = self.CREDIT_COSTS.get(metadata.method, 0)
        if self.calls + 1 > self.limits.rpc_calls or self.credits + cost > self.limits.credits:
            raise RecoveryBudgetExceeded("approved EP3.0F RPC budget exhausted")
        self.calls += 1
        self.credits += cost
        self.by_method[metadata.method] = self.by_method.get(metadata.method, 0) + 1
        self.by_provider[metadata.provider] = self.by_provider.get(metadata.provider, 0) + 1

    def report(self) -> dict[str, Any]:
        return {
            "rpc_calls": self.calls, "credits": self.credits,
            "by_method": dict(sorted(self.by_method.items())),
            "by_provider": dict(sorted(self.by_provider.items())),
            "limits": self.limits.__dict__,
        }


class WatchtowerShadowRecovery:
    def __init__(self, *, operations_db: Path, main_db: Path, transaction_cache_db: Path,
                 output_root: Path, limits: RecoveryLimits = RecoveryLimits()) -> None:
        self.operations_db = Path(operations_db)
        self.main_db = Path(main_db)
        self.transaction_cache_db = Path(transaction_cache_db)
        self.output_root = Path(output_root)
        self.limits = limits
        self.materializer = WatchtowerShadowCorpusMaterializer(
            operations_db=self.operations_db,
            transaction_cache_db=self.transaction_cache_db,
            output_root=self.output_root,
            limits=PopulationLimits(),
        )
        self.budget = RecoveryBudget(limits)
        self.metrics: list[dict[str, Any]] = []
        self.results: dict[str, dict[str, Any]] = {}
        self.creation_overrides: dict[str, str] = {}

    @staticmethod
    def _metrics_sink(**fields: Any) -> None:
        # Deliberately no production metrics database writes.
        return None

    def _rpc_urls(self) -> list[str]:
        key = (os.getenv("HELIUS_MONITORING_API_KEY") or os.getenv("HELIUS_API_KEY") or "").strip()
        urls = []
        if key:
            urls.append(f"https://mainnet.helius-rpc.com/?api-key={key}")
        urls.append("https://api.mainnet-beta.solana.com")
        return urls

    def _existing_transactions(self) -> set[str]:
        connection = _read_only(self.output_root / "evidence.db")
        try:
            result = set()
            for row in connection.execute(
                "SELECT payload_json FROM normalized_evidence_records WHERE fact_family='TransactionFact'"
            ):
                payload = json.loads(row[0])
                if isinstance(payload.get("signature"), str):
                    result.add(payload["signature"])
            return result
        finally:
            connection.close()

    def _local_creation_signatures(self) -> dict[str, str]:
        connection = sqlite3.connect(f"file:{self.operations_db.resolve()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute(f"ATTACH DATABASE 'file:{self.main_db.resolve()}?mode=ro' AS production")
            rows = connection.execute(
                "SELECT l.mint,COALESCE(t.create_tx_signature,b.launch_sig) signature "
                "FROM wt_watchtower_launches l "
                "LEFT JOIN production.token_analysis t ON t.mint=l.mint "
                "LEFT JOIN wt_creator_birth_launch b ON b.token_mint=l.mint "
                "WHERE (l.create_signature IS NULL OR length(l.create_signature) NOT BETWEEN 80 AND 90) "
                "AND COALESCE(t.create_tx_signature,b.launch_sig) IS NOT NULL ORDER BY l.mint"
            ).fetchall()
            return {row["mint"]: row["signature"] for row in rows if _signature_valid(row["signature"])}
        finally:
            connection.close()

    @staticmethod
    def _required_signatures(population: Mapping[str, list[dict[str, Any]]],
                             overrides: Mapping[str, str]) -> dict[str, dict[str, Any]]:
        required: dict[str, dict[str, Any]] = {}
        for launch in population["launches"]:
            signature = launch.get("create_signature")
            if not _signature_valid(signature):
                signature = overrides.get(launch["mint"])
            for value, purpose in ((signature, "launch_creation"),
                                   (launch.get("wrap_close_signature"), "wrap_close")):
                if _signature_valid(value):
                    required.setdefault(value, {"purpose": purpose,
                                                "creator": launch.get("creator_wallet"),
                                                "launch": launch["mint"]})
        frozen_mints = {row["mint"] for row in population["launches"]}
        for edge in population["provisioning_edges"]:
            signature = edge.get("funding_tx_signature")
            if edge.get("source_mint") in frozen_mints and _signature_valid(signature):
                required.setdefault(signature, {"purpose": "provisioning_edge",
                                                "creator": edge.get("to_wallet"),
                                                "launch": edge.get("source_mint")})
        return required

    @staticmethod
    def _response_url(response: AcquisitionResponse, urls: list[str]) -> str:
        provider = response.metadata.provider
        if provider == "helius_rpc":
            return next((url for url in urls if "helius" in url), urls[0])
        if provider == "solana_public_rpc":
            return next((url for url in urls if "solana.com" in url), urls[-1])
        return urls[0]

    async def _request(self, client: SharedTransactionAcquisition, platform: EvidencePlatform,
                       urls: list[str], payload: dict[str, Any], *, purpose: str,
                       creator: Optional[str], launch: Optional[str], page: Optional[int] = None,
                       cursor: Optional[str] = None) -> Optional[AcquisitionResponse]:
        with acquisition_scope(purpose=purpose, creator=creator, launch=launch):
            response = await client.json_rpc_response_legacy(
                payload, rpc_urls=urls, max_retries=5, timeout_seconds=30,
                metrics_sink=self._metrics_sink, cache_action="miss", credits_saved=0,
                page_number=page, cursor=cursor,
            )
        if response is not None:
            if not platform.mirror.publish_nowait(
                response, http_method="POST", url=self._response_url(response, urls),
                request_payload=payload,
            ):
                raise RuntimeError("Evidence mirror rejected bounded recovery response")
        return response

    async def _fetch_transaction(self, client: SharedTransactionAcquisition,
                                 platform: EvidencePlatform, urls: list[str], signature: str,
                                 context: Mapping[str, Any]) -> str:
        payload = {"jsonrpc": "2.0", "id": 1, "method": "getTransaction",
                   "params": [signature, {"encoding": "jsonParsed", "commitment": "finalized",
                                           "maxSupportedTransactionVersion": 0}]}
        response = await self._request(
            client, platform, urls, payload, purpose=str(context["purpose"]),
            creator=context.get("creator"), launch=context.get("launch"),
        )
        if response is None:
            return "PROVIDER_UNAVAILABLE"
        data = response.data if isinstance(response.data, Mapping) else {}
        return "RECOVERED" if data.get("result") is not None else "HISTORICAL_UNAVAILABLE"

    async def _discover_creation(self, client: SharedTransactionAcquisition,
                                 platform: EvidencePlatform, urls: list[str],
                                 launch: Mapping[str, Any]) -> Optional[str]:
        subject = str(launch["creator_wallet"])
        cursor = None
        candidates: list[dict[str, Any]] = []
        for page in range(1, self.limits.pages_per_subject + 1):
            options: dict[str, Any] = {"limit": 1000, "commitment": "finalized"}
            if cursor:
                options["before"] = cursor
            payload = {"jsonrpc": "2.0", "id": 1, "method": "getSignaturesForAddress",
                       "params": [subject, options]}
            response = await self._request(
                client, platform, urls, payload, purpose="launch_signature_discovery",
                creator=subject, launch=launch["mint"], page=page, cursor=cursor,
            )
            if response is None:
                self.results[launch["mint"]] = {"status": "PROVIDER_UNAVAILABLE"}
                return None
            data = response.data if isinstance(response.data, Mapping) else {}
            rows = data.get("result")
            if not isinstance(rows, list):
                self.results[launch["mint"]] = {"status": "PROVIDER_UNAVAILABLE"}
                return None
            candidates.extend(row for row in rows if isinstance(row, Mapping)
                              and _signature_valid(row.get("signature")))
            target_time = launch.get("create_time")
            if any(row.get("blockTime") == target_time for row in candidates) or len(rows) < 1000:
                break
            cursor = rows[-1].get("signature") if rows else None
            if not cursor:
                break
        if not candidates:
            self.results[launch["mint"]] = {"status": "PERMANENTLY_UNAVAILABLE"}
            return None
        target = int(launch.get("create_time") or 0)
        eligible = [row for row in candidates if isinstance(row.get("blockTime"), int)]
        chosen = min(eligible or candidates,
                     key=lambda row: (abs(int(row.get("blockTime") or 0) - target),
                                      str(row["signature"])))
        return str(chosen["signature"])

    @staticmethod
    def _counts(database_path: Path) -> dict[str, int]:
        connection = _read_only(database_path)
        try:
            return {
                "artifacts": connection.execute("SELECT COUNT(*) FROM artifact_references").fetchone()[0],
                "evidence": connection.execute("SELECT COUNT(*) FROM normalized_evidence_records").fetchone()[0],
                "primitives": connection.execute("SELECT COUNT(*) FROM primitive_observations").fetchone()[0],
            }
        finally:
            connection.close()

    def _amend_durable_queue(self) -> dict[str, Any]:
        queue_root = self.output_root / "intake"
        amended = 0
        artifacts: set[str] = set()
        observations: set[str] = set()
        for state in ("pending", "processing", "retry", "dead_letter"):
            for path in sorted((queue_root / state).glob("*.json")):
                payload = json.loads(path.read_text(encoding="utf-8"))
                envelope = payload.get("envelope") or {}
                if envelope.get("payload_type") != "acquisition/response":
                    continue
                artifact_digest = str((envelope.get("artifact") or {}).get("digest") or "")
                artifacts.add(artifact_digest)
                if envelope.get("evidence_digest") == artifact_digest:
                    payload["envelope"] = amend_acquisition_observation(envelope)
                    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
                    try:
                        encoded = (json.dumps(
                            payload, sort_keys=True, separators=(",", ":"),
                            allow_nan=False,
                        ) + "\n").encode("utf-8")
                        with temporary.open("xb") as handle:
                            handle.write(encoded)
                            handle.flush()
                            os.fsync(handle.fileno())
                        os.replace(temporary, path)
                        directory = os.open(path.parent, os.O_RDONLY)
                        try:
                            os.fsync(directory)
                        finally:
                            os.close(directory)
                    finally:
                        temporary.unlink(missing_ok=True)
                    amended += 1
                    envelope = payload["envelope"]
                observations.add(str(envelope.get("evidence_digest") or ""))
        return {
            "messages_amended": amended,
            "observation_digests": len(observations - {""}),
            "artifact_digests": len(artifacts - {""}),
            "shared_artifact_references": len(observations - {""}) - len(artifacts - {""}),
        }

    def resume_without_rpc(self) -> dict[str, Any]:
        """Resume the durable EP3.0F queue after EP3.0G; performs no acquisition."""
        started = time.monotonic()
        before = self._counts(self.output_root / "evidence.db")
        amendment = self._amend_durable_queue()
        config = self.materializer._config()
        platform = EvidencePlatform(config)
        platform.writer.primitive_engine = None
        platform.writer.start()
        try:
            writer = self.materializer._drain_writer(platform)
            primitive = platform.primitive_engine.run_once()
            health = platform.health()
        finally:
            platform.writer.stop()
            platform.mirror.stop()
        after = self._counts(self.output_root / "evidence.db")
        digests = self.materializer._digests(self.output_root / "evidence.db")
        replay = self.materializer._validate_replay(digests)
        replay["component_parity"] = {
            key: replay["expected"].get(key) == replay["actual"].get(key)
            for key in sorted(set(replay["expected"]) | set(replay["actual"]))
        }
        observation_replay = self._validate_amended_observations()
        connection = _read_only(self.output_root / "evidence.db")
        try:
            artifact_count = connection.execute(
                "SELECT COUNT(*) FROM immutable_artifacts"
            ).fetchone()[0]
            foreign_key_errors = len(connection.execute("PRAGMA foreign_key_check").fetchall())
        finally:
            connection.close()
        report = {
            "milestone": "EP3.0G",
            "authority": "SHADOW_NON_AUTHORITATIVE",
            "rpc_calls": 0,
            "amendment": amendment,
            "before": before,
            "after": after,
            "growth": {key: after[key] - before[key] for key in before},
            "immutable_artifacts": artifact_count,
            "foreign_key_errors": foreign_key_errors,
            "writer": writer,
            "primitive": primitive,
            "replay": replay,
            "observation_replay": observation_replay,
            "health": health,
            "duration_seconds": round(time.monotonic() - started, 3),
            "production_writes": 0,
            "detectors_executed": 0,
            "runtime_evaluations": 0,
            "governance_actions": 0,
        }
        self.materializer._write_json(self.output_root / "ep3_0g_amendment.json", report)
        return report

    def _validate_amended_observations(self) -> dict[str, Any]:
        """Replay only EP3.0G observations to isolate the amended contract."""
        source_db = self.output_root / "evidence.db"
        source = _read_only(source_db)
        try:
            expected_rows = source.execute(
                "SELECT DISTINCT n.evidence_id,n.payload_digest,n.raw_artifact_digest "
                "FROM normalized_evidence_records n "
                "JOIN normalized_evidence_provenance np USING(evidence_id) "
                "JOIN evidence_provenance p ON p.provider_request_id=np.provider_request_id "
                "JOIN evidence_envelopes e USING(envelope_id) "
                "WHERE e.source_version='ep3.0g-observation-v1' ORDER BY n.evidence_id"
            ).fetchall()
            expected = [list(row) for row in expected_rows]
        finally:
            source.close()
        with tempfile.TemporaryDirectory(prefix="ep3_0g_observation_replay_",
                                         dir=self.output_root) as temporary:
            database = EvidenceDatabase(Path(temporary) / "evidence.db", clock=lambda: 0)
            database.open_writer()
            try:
                normalizer = NormalizationEngine(
                    database, ArtifactStore(self.output_root / "artifacts", enabled=True,
                                            clock=lambda: 0),
                )
                envelopes = self.materializer._reconstruct_envelopes(
                    source_db, source_version="ep3.0g-observation-v1"
                )
                for envelope in envelopes:
                    database.append_batch([{
                        "message_id": f"ep3g-replay-{envelope['envelope_id']}",
                        "envelope": envelope,
                    }])
                    normalizer.normalize_envelope(envelope)
                actual_rows = database.connection.execute(
                    "SELECT evidence_id,payload_digest,raw_artifact_digest "
                    "FROM normalized_evidence_records ORDER BY evidence_id"
                ).fetchall()
                actual = [list(row) for row in actual_rows]
            finally:
                database.close()
        expected_set = {tuple(row) for row in expected}
        actual_set = {tuple(row) for row in actual}
        missing = sorted(expected_set - actual_set)
        unexpected = sorted(actual_set - expected_set)
        return {
            "identical": expected_set == actual_set,
            "observations": len(envelopes),
            "expected_facts": len(expected_set),
            "actual_facts": len(actual_set),
            "missing_facts": len(missing),
            "unexpected_facts": len(unexpected),
            "missing_examples": [list(row) for row in missing[:20]],
            "unexpected_examples": [list(row) for row in unexpected[:20]],
            "additional_rpc": 0,
        }

    async def run(self) -> dict[str, Any]:
        started = time.monotonic()
        population = self.materializer._population()
        population_digest = _digest(population)
        before = self._counts(self.output_root / "evidence.db")
        existing = self._existing_transactions()
        self.creation_overrides.update(self._local_creation_signatures())
        unresolved = [row for row in population["launches"]
                      if not _signature_valid(row.get("create_signature"))
                      and row["mint"] not in self.creation_overrides]
        if len(unresolved) > self.limits.discovery_subjects:
            raise RecoveryBudgetExceeded("signature-discovery population exceeds approved bound")

        config = self.materializer._config()
        platform = EvidencePlatform(config)
        platform.writer.primitive_engine = None
        urls = self._rpc_urls()
        platform.writer.start()
        try:
            async with aiohttp.ClientSession() as session:
                client = SharedTransactionAcquisition(
                    session, semaphore=asyncio.Semaphore(8), telemetry_sink=self.budget.observe,
                )
                required = self._required_signatures(population, self.creation_overrides)
                missing_known = [(signature, context) for signature, context in sorted(required.items())
                                 if signature not in existing]
                if len(missing_known) > self.limits.known_transactions:
                    raise RecoveryBudgetExceeded("known transaction population exceeds approved bound")
                for signature, context in missing_known:
                    self.results[signature] = {
                        "status": await self._fetch_transaction(
                            client, platform, urls, signature, context
                        ), "purpose": context["purpose"], "launch": context.get("launch"),
                    }
                for launch in unresolved:
                    signature = await self._discover_creation(client, platform, urls, launch)
                    if signature is None:
                        continue
                    self.creation_overrides[launch["mint"]] = signature
                    status = await self._fetch_transaction(
                        client, platform, urls, signature,
                        {"purpose": "launch_creation", "creator": launch["creator_wallet"],
                         "launch": launch["mint"]},
                    )
                    self.results[launch["mint"]] = {"status": status,
                                                    "discovered_signature": signature}
            if not platform.mirror.drain(timeout=300):
                raise RuntimeError("Evidence mirror did not drain")
            writer = self.materializer._drain_writer(platform)
            primitive = platform.primitive_engine.run_once()
            health = platform.health()
        finally:
            platform.writer.stop()
            platform.mirror.stop()

        after = self._counts(self.output_root / "evidence.db")
        digests = self.materializer._digests(self.output_root / "evidence.db")
        replay = self.materializer._validate_replay(digests)
        if not replay["identical"]:
            raise RuntimeError("bounded recovery replay digest mismatch")

        adjusted = {key: [dict(item) for item in values] for key, values in population.items()}
        for launch in adjusted["launches"]:
            override = self.creation_overrides.get(launch["mint"])
            if override and not _signature_valid(launch.get("create_signature")):
                launch["create_signature"] = override
        current = self._existing_transactions()
        required = self._required_signatures(adjusted, self.creation_overrides)
        unavailable = {signature: self.results.get(signature, {}).get("status", "HISTORICAL_UNAVAILABLE")
                       for signature in required if signature not in current}
        coverage = self.materializer._coverage(
            adjusted, {signature: {} for signature in current}, unavailable, True,
        )
        report = {
            "milestone": "EP3.0F", "authority": "SHADOW_NON_AUTHORITATIVE",
            "population": {key: len(value) for key, value in population.items()},
            "population_digest": population_digest, "budget": self.budget.report(),
            "duration_seconds": round(time.monotonic() - started, 3),
            "creation_signatures_recovered_from_metadata": len(self._local_creation_signatures()),
            "creation_signatures_available_after_recovery": len(self.creation_overrides),
            "results": self.results, "before": before, "after": after,
            "growth": {key: after[key] - before[key] for key in before},
            "coverage": coverage["summary"], "replay": replay,
            "writer": writer, "primitive": primitive, "health": health,
            "detectors_executed": 0, "runtime_evaluations": 0,
            "governance_actions": 0, "production_writes": 0,
        }
        self.materializer._write_json(self.output_root / "ep3_0f_recovery.json", report)
        self.materializer._write_json(self.output_root / "ep3_0f_coverage.json", coverage)
        self.materializer._write_json(self.output_root / "ep3_0f_replay.json", replay)
        return report
