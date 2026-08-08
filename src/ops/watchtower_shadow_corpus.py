"""EP3.0C no-RPC materialization of a frozen comparison population."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from src.acquisition.transaction import AcquisitionMetadata, AcquisitionResponse

from src.evidence.artifacts import ArtifactStore
from src.evidence.config import EvidenceConfig
from src.evidence.contracts import canonical_json_bytes
from src.evidence.database import EvidenceDatabase
from src.evidence.normalization import NormalizationEngine
from src.evidence.primitives.engine import PrimitiveEngine
from src.evidence.service import EvidencePlatform


WATCHTOWER_OPERATOR_ID = "04265d9f-6eb2-568c-a49e-9253091a4dbb"


@dataclass(frozen=True)
class PopulationLimits:
    treasuries: int = 62
    launches: int = 176
    provisioning_edges: int = 5_937
    entities: int = 69


def _digest(value: Any) -> str:
    encoded = (json.dumps(value, sort_keys=True, separators=(",", ":"),
                          allow_nan=False) + "\n").encode()
    return hashlib.sha256(encoded).hexdigest()


def _plain_rows(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def _read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _signature_valid(value: Any) -> bool:
    if not isinstance(value, str) or not 80 <= len(value) <= 90:
        return False
    alphabet = set("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")
    return set(value) <= alphabet


class WatchtowerShadowCorpusMaterializer:
    """Copies existing cached responses into an isolated, non-authoritative corpus."""

    def __init__(self, *, operations_db: Path, transaction_cache_db: Path,
                 output_root: Path, limits: PopulationLimits = PopulationLimits(),
                 clock: int = 1_786_118_400) -> None:
        self.operations_db = Path(operations_db)
        self.transaction_cache_db = Path(transaction_cache_db)
        self.output_root = Path(output_root)
        self.limits = limits
        self.clock = int(clock)
        sources = {self.operations_db.resolve(), self.transaction_cache_db.resolve()}
        targets = {
            (self.output_root / "evidence.db").resolve(),
            (self.output_root / "intake").resolve(),
            (self.output_root / "artifacts").resolve(),
            (self.output_root / "mirror_spool").resolve(),
        }
        if sources & targets:
            raise ValueError("shadow corpus targets must not alias source databases")

    def _config(self, root: Path | None = None) -> EvidenceConfig:
        base = root or self.output_root
        return EvidenceConfig(
            platform_enabled=True, writer_enabled=True, queue_enabled=True,
            artifact_store_enabled=True, health_enabled=True, mirror_enabled=True,
            normalization_enabled=True, primitive_engine_enabled=True,
            database_path=base / "evidence.db", queue_path=base / "intake",
            artifact_path=self.output_root / "artifacts",
            mirror_spool_path=base / "mirror_spool", queue_max_messages=20_000,
            queue_max_bytes=2 * 1024 * 1024 * 1024, writer_batch_size=500,
            mirror_buffer_size=2_000,
        )

    def _population(self) -> dict[str, list[dict[str, Any]]]:
        connection = _read_only(self.operations_db)
        try:
            connection.execute("BEGIN")
            population = {
                "treasuries": _plain_rows(connection.execute(
                    "SELECT * FROM wt_confirmed_treasuries ORDER BY treasury LIMIT ?",
                    (self.limits.treasuries,),
                )),
                "launches": _plain_rows(connection.execute(
                    "SELECT * FROM wt_watchtower_launches ORDER BY mint LIMIT ?",
                    (self.limits.launches,),
                )),
                "provisioning_edges": _plain_rows(connection.execute(
                    "SELECT * FROM wt_provisioning_edges "
                    "ORDER BY COALESCE(first_observed_by_flex,9223372036854775807),edge_id LIMIT ?",
                    (self.limits.provisioning_edges,),
                )),
                "entities": _plain_rows(connection.execute(
                    "SELECT * FROM operator_entities WHERE operator_id=? "
                    "ORDER BY entity_type,entity_address LIMIT ?",
                    (WATCHTOWER_OPERATOR_ID, self.limits.entities),
                )),
            }
            expected = asdict(self.limits)
            actual = {key: len(value) for key, value in population.items()}
            if actual != expected:
                raise ValueError(f"comparison population mismatch: expected={expected} actual={actual}")
            return population
        finally:
            connection.close()

    @staticmethod
    def _contexts(population: Mapping[str, list[dict[str, Any]]]) -> dict[str, dict[str, Any]]:
        contexts: dict[str, dict[str, Any]] = {}
        for launch in population["launches"]:
            for field, purpose in (("create_signature", "launch_creation"),
                                   ("wrap_close_signature", "wrap_close")):
                signature = launch.get(field)
                if isinstance(signature, str):
                    contexts.setdefault(signature, {"purpose": purpose,
                                                     "creator": launch.get("creator_wallet"),
                                                     "launch": launch.get("mint")})
        for edge in population["provisioning_edges"]:
            signature = edge.get("funding_tx_signature")
            if isinstance(signature, str):
                contexts.setdefault(signature, {"purpose": "provisioning_edge",
                                                 "creator": edge.get("to_wallet"),
                                                 "launch": edge.get("source_mint")})
        return contexts

    def _cached_transactions(self, signatures: Iterable[str]) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
        valid = sorted({item for item in signatures if _signature_valid(item)})
        connection = _read_only(self.transaction_cache_db)
        found: dict[str, dict[str, Any]] = {}
        try:
            for offset in range(0, len(valid), 500):
                batch = valid[offset:offset + 500]
                query = "SELECT * FROM tf_transaction_cache WHERE signature IN (%s)" % ",".join("?" * len(batch))
                for row in connection.execute(query, batch):
                    body = row["transaction_json"]
                    if body is None:
                        continue
                    try:
                        json.loads(body)
                    except (TypeError, json.JSONDecodeError):
                        continue
                    found[row["signature"]] = dict(row)
        finally:
            connection.close()
        unavailable: dict[str, str] = {}
        connection = _read_only(self.transaction_cache_db)
        try:
            cached_rows = {row["signature"]: row for offset in range(0, len(valid), 500)
                           for row in connection.execute(
                               "SELECT signature,transaction_json FROM tf_transaction_cache WHERE signature IN (%s)" %
                               ",".join("?" * len(valid[offset:offset + 500])),
                               valid[offset:offset + 500],
                           )}
        finally:
            connection.close()
        for signature in sorted(set(valid) - set(found)):
            unavailable[signature] = (
                "CACHED_ARTIFACT_BODY_UNAVAILABLE" if signature in cached_rows
                else "RAW_ARTIFACT_NOT_IN_TRANSACTION_CACHE"
            )
        return found, unavailable

    def _publish_cache(self, platform: EvidencePlatform,
                       cache: Mapping[str, dict[str, Any]],
                       contexts: Mapping[str, dict[str, Any]]) -> list[dict[str, Any]]:
        envelopes: list[dict[str, Any]] = []
        original_enqueue = platform.mirror.intake.enqueue

        def capture(envelope: dict[str, Any], *, message_id: str | None = None) -> str:
            envelopes.append(envelope)
            return original_enqueue(envelope, message_id=message_id)

        platform.mirror.intake.enqueue = capture  # type: ignore[method-assign]
        for signature in sorted(cache):
            row = cache[signature]
            context = contexts[signature]
            transaction = json.loads(row["transaction_json"])
            timestamp = int(row.get("fetched_at") or row.get("block_time") or self.clock)
            metadata = AcquisitionMetadata(
                acquisition_id=f"shadow-cache-{signature}",
                correlation_id=f"watchtower-shadow-{signature}",
                purpose=context["purpose"], creator=context.get("creator"),
                launch=context.get("launch"), request_type="json_rpc_cache_replay",
                provider=str(row.get("source") or "transaction_cache"),
                method="getTransaction", page_number=1, cursor=None,
                timestamp=float(timestamp), cache_state="hit", retry_count=0,
            )
            response = AcquisitionResponse(
                status=200, data={"jsonrpc": "2.0", "id": 1, "result": transaction},
                text=None, headers={"Content-Type": "application/json"},
                metadata=metadata, latency_ms=0.0, raw_body=None,
                artifact_representation="CANONICALIZED_RESPONSE_REPRESENTATION",
            )
            if not platform.mirror.publish_nowait(
                response, http_method="POST", url="cache://transaction-first-lineage",
                request_payload={"jsonrpc": "2.0", "id": 1, "method": "getTransaction",
                                 "params": [signature]},
            ):
                raise RuntimeError(f"mirror rejected cached transaction {signature}")
        if not platform.mirror.drain(timeout=300):
            raise RuntimeError("Evidence mirror did not drain")
        return sorted(envelopes, key=lambda item: item["envelope_id"])

    @staticmethod
    def _drain_writer(platform: EvidencePlatform) -> dict[str, int]:
        totals = {"claimed": 0, "inserted": 0, "duplicates": 0, "failed": 0}
        while True:
            result = platform.writer.run_once()
            for key in totals:
                totals[key] += int(result[key])
            if result["claimed"] == 0:
                return totals

    @staticmethod
    def _semantic_digest(connection: sqlite3.Connection, table: str,
                         columns: tuple[str, ...], order: str) -> str:
        rows = [list(row) for row in connection.execute(
            f"SELECT {','.join(columns)} FROM {table} ORDER BY {order}"
        )]
        return _digest(rows)

    def _digests(self, database_path: Path) -> dict[str, str]:
        connection = _read_only(database_path)
        try:
            return {
                "evidence": self._semantic_digest(
                    connection, "normalized_evidence_records",
                    ("evidence_id", "payload_digest", "raw_artifact_digest", "parser_id", "parser_version"),
                    "evidence_id",
                ),
                "primitives": self._semantic_digest(
                    connection, "primitive_observations",
                    ("primitive_id", "primitive_type", "primitive_version", "output_digest", "quality_state"),
                    "primitive_id",
                ),
                "primitive_inputs": self._semantic_digest(
                    connection, "primitive_evidence_inputs", ("primitive_id", "evidence_id"),
                    "primitive_id,evidence_id",
                ),
            }
        finally:
            connection.close()

    def _reconstruct_envelopes(self, database_path: Path) -> list[dict[str, Any]]:
        connection = _read_only(database_path)
        try:
            rows = connection.execute(
                "SELECT e.*,p.provider_request_id,p.rpc_verification_state,p.acquisition_method,"
                "p.source_metadata_json,a.size_bytes,a.compressed_bytes,a.content_type,a.compression,"
                "n.artifact_representation FROM evidence_envelopes e "
                "JOIN evidence_provenance p USING(envelope_id) "
                "JOIN artifact_references a USING(envelope_id) "
                "LEFT JOIN normalization_status n ON n.envelope_id=e.envelope_id "
                "ORDER BY e.envelope_id"
            ).fetchall()
            result = []
            for row in rows:
                source_metadata = json.loads(row["source_metadata_json"])
                result.append({
                    "envelope_id": row["envelope_id"], "observed_at": row["observed_at"],
                    "acquired_at": row["acquired_at"], "source": row["source"],
                    "source_version": row["source_version"], "provider": row["provider"],
                    "evidence_digest": row["evidence_digest"], "replay_version": row["replay_version"],
                    "parser_version": row["parser_version"], "payload_type": row["payload_type"],
                    "artifact": {"digest": row["artifact_digest"], "size_bytes": row["size_bytes"],
                                 "compressed_bytes": row["compressed_bytes"], "content_type": row["content_type"],
                                 "compression": row["compression"],
                                 "representation": row["artifact_representation"]},
                    "provenance": {"provider_request_id": row["provider_request_id"],
                                   "rpc_verification_state": row["rpc_verification_state"],
                                   "acquisition_method": row["acquisition_method"],
                                   "source_metadata": source_metadata},
                    "acquisition": {"method": row["acquisition_method"], "cache_state": "hit",
                                    "artifact_representation": row["artifact_representation"],
                                    "transaction_signatures": []},
                })
            return result
        finally:
            connection.close()

    def _validate_replay(self, expected: Mapping[str, str]) -> dict[str, Any]:
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="ep3_0c_replay_", dir=self.output_root) as temporary:
            root = Path(temporary)
            database = EvidenceDatabase(root / "evidence.db", clock=lambda: self.clock)
            database.open_writer()
            try:
                normalizer = NormalizationEngine(
                    database, ArtifactStore(self.output_root / "artifacts", enabled=True,
                                            clock=lambda: self.clock)
                )
                envelopes = self._reconstruct_envelopes(self.output_root / "evidence.db")
                for envelope in envelopes:
                    database.append_batch([{"message_id": f"replay-{envelope['envelope_id']}",
                                            "envelope": envelope}])
                    result = normalizer.normalize_envelope(envelope)
                PrimitiveEngine(database, clock=lambda: self.clock).run_once()
            finally:
                database.close()
            actual = self._digests(root / "evidence.db")
        return {"identical": dict(expected) == actual, "expected": dict(expected),
                "actual": actual, "additional_rpc": 0,
                "latency_ms": round((time.monotonic() - started) * 1000, 3)}

    @staticmethod
    def _write_json(path: Path, value: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                   allow_nan=False) + "\n", encoding="utf-8")

    def _coverage(self, population: Mapping[str, list[dict[str, Any]]],
                  cache: Mapping[str, dict[str, Any]], unavailable_cache: Mapping[str, str],
                  replay_complete: bool) -> dict[str, Any]:
        connection = _read_only(self.output_root / "evidence.db")
        try:
            transaction_ids: dict[str, str] = {}
            launch_facts: set[str] = set()
            participants: set[str] = set()
            for row in connection.execute(
                "SELECT evidence_id,fact_family,payload_json FROM normalized_evidence_records"
            ):
                payload = json.loads(row["payload_json"])
                if row["fact_family"] == "TransactionFact":
                    transaction_ids[str(payload.get("signature"))] = row["evidence_id"]
                elif row["fact_family"] == "LaunchFact" and isinstance(payload.get("mint"), str):
                    launch_facts.add(payload["mint"])
                elif row["fact_family"] == "AccountParticipationFact" and isinstance(payload.get("public_key"), str):
                    participants.add(payload["public_key"])
            primitive_evidence = {row[0] for row in connection.execute(
                "SELECT DISTINCT evidence_id FROM primitive_evidence_inputs"
            )}
            quality = {row[0]: row[1] for row in connection.execute(
                "SELECT quality_state,COUNT(*) FROM primitive_observations GROUP BY quality_state"
            )}
            types = {row[0]: row[1] for row in connection.execute(
                "SELECT primitive_type,COUNT(*) FROM primitive_observations GROUP BY primitive_type"
            )}
            normalization = {row[0]: row[1] for row in connection.execute(
                "SELECT state,COUNT(*) FROM normalization_status GROUP BY state"
            )}
            parser_versions = [list(row) for row in connection.execute(
                "SELECT DISTINCT parser_id,parser_version,fact_schema_version FROM normalization_status ORDER BY 1,2,3"
            )]
        finally:
            connection.close()

        edges_by_mint: dict[str, list[dict[str, Any]]] = {}
        for edge in population["provisioning_edges"]:
            if edge.get("source_mint"):
                edges_by_mint.setdefault(edge["source_mint"], []).append(edge)

        def signature_state(signature: Any) -> dict[str, Any]:
            if signature is None:
                return {"signature": None, "available": False, "reason": "SOURCE_SIGNATURE_UNAVAILABLE"}
            if not _signature_valid(signature):
                return {"signature": signature, "available": False, "reason": "INVALID_RECORDED_SIGNATURE"}
            if signature in unavailable_cache:
                return {"signature": signature, "available": False,
                        "reason": unavailable_cache[signature]}
            evidence_id = transaction_ids.get(signature)
            if evidence_id is None:
                return {"signature": signature, "available": False, "reason": "TRANSACTION_FACT_NOT_NORMALIZED"}
            primitive = evidence_id in primitive_evidence
            return {"signature": signature, "available": True, "evidence_id": evidence_id,
                    "primitive_available": primitive,
                    "reason": None if primitive else "NO_APPROVED_PRIMITIVE_GENERATED"}

        launches = []
        for launch in population["launches"]:
            signatures = [signature_state(launch.get("create_signature"))]
            if launch.get("wrap_close_signature") is not None:
                signatures.append(signature_state(launch.get("wrap_close_signature")))
            signatures.extend(signature_state(item.get("funding_tx_signature"))
                              for item in edges_by_mint.get(launch["mint"], ()))
            evidence_complete = bool(signatures) and all(item["available"] for item in signatures)
            primitive_complete = evidence_complete and all(item.get("primitive_available") for item in signatures)
            reasons = sorted({item["reason"] for item in signatures if item.get("reason")})
            if launch["mint"] not in launch_facts:
                reasons.append("LAUNCH_FACT_NOT_DERIVED")
                primitive_complete = False
            launches.append({
                "mint": launch["mint"], "creator": launch.get("creator_wallet"),
                "evidence_complete": evidence_complete, "primitive_complete": primitive_complete,
                "replay_complete": replay_complete, "ready_for_runtime": primitive_complete and replay_complete,
                "reasons": sorted(set(reasons)), "signatures": signatures,
            })

        def participant_rows(rows: Iterable[dict[str, Any]], key: str) -> list[dict[str, Any]]:
            return [{key: row[key], "evidence_complete": row[key] in participants,
                     "reason": None if row[key] in participants else "NO_TRANSACTION_OBSERVATION_IN_CORPUS"}
                    for row in rows]

        return {
            "population": {key: len(value) for key, value in population.items()},
            "launches": launches,
            "treasuries": participant_rows(population["treasuries"], "treasury"),
            "entities": participant_rows(population["entities"], "entity_address"),
            "provisioning_edges": [{"edge_id": row["edge_id"],
                                     "funding": signature_state(row.get("funding_tx_signature"))}
                                    for row in population["provisioning_edges"]],
            "summary": {
                "raw_artifacts_available": len(cache), "raw_artifacts_missing": len(unavailable_cache),
                "evidence_records": len(transaction_ids),
                "runtime_ready_launches": sum(item["ready_for_runtime"] for item in launches),
                "evidence_complete_launches": sum(item["evidence_complete"] for item in launches),
                "primitive_complete_launches": sum(item["primitive_complete"] for item in launches),
                "normalization_states": normalization, "primitive_quality_states": quality,
                "primitive_types": types, "parser_versions": parser_versions,
                "primitive_version": PrimitiveEngine.VERSION,
            },
        }

    def materialize(self) -> dict[str, Any]:
        self.output_root.mkdir(parents=True, exist_ok=True)
        population = self._population()
        contexts = self._contexts(population)
        cache, missing = self._cached_transactions(contexts)
        config = self._config()
        config.validate_isolation((self.operations_db, self.transaction_cache_db))
        platform = EvidencePlatform(config)
        # Corpus generation runs the Primitive Engine exactly once after intake.
        # The existing writer hook is deliberately disconnected to avoid an
        # O(batch * corpus) full replay while retaining the enabled shadow health state.
        platform.writer.primitive_engine = None
        started = time.monotonic()
        try:
            self._publish_cache(platform, cache, contexts)
            platform.writer.start()
            writer = self._drain_writer(platform)
            primitive = platform.primitive_engine.run_once()
            health = platform.health()
        finally:
            platform.writer.stop()
            platform.mirror.stop()
        digests = self._digests(config.database_path)
        replay = self._validate_replay(digests)
        if not replay["identical"]:
            raise RuntimeError("shadow corpus replay digest mismatch")
        coverage = self._coverage(population, cache, missing, True)
        manifest = {
            "milestone": "EP3.0C", "authority": "SHADOW_NON_AUTHORITATIVE",
            "population_digest": _digest(population), "semantic_digests": digests,
            "population": {key: len(value) for key, value in population.items()},
            "source_databases": [str(self.operations_db), str(self.transaction_cache_db)],
            "additional_rpc": 0, "detectors_executed": 0, "runtime_evaluations": 0,
            "governance_actions": 0, "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
        }
        self._write_json(self.output_root / "population.json", population)
        self._write_json(self.output_root / "compatibility_manifest.json", manifest)
        self._write_json(self.output_root / "coverage.json", coverage)
        self._write_json(self.output_root / "replay.json", replay)
        self._write_json(self.output_root / "health.json", health)
        return {"manifest": manifest, "coverage": coverage["summary"], "replay": replay,
                "writer": writer, "primitive": primitive, "health": health}
