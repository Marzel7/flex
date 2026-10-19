"""Isolated EP1.3 normalization workflow; never performs acquisition or RPC."""

from __future__ import annotations

import time
import sqlite3
from typing import Any, Mapping

from .artifacts import ArtifactStore
from .database import EvidenceDatabase
from .metrics import EvidenceMetrics
from .normalizers import AcquisitionNormalizer


class NormalizationEngine:
    def __init__(self, database: EvidenceDatabase, artifacts: ArtifactStore,
                 *, normalizer: AcquisitionNormalizer | None = None,
                 metrics: EvidenceMetrics | None = None) -> None:
        self.database = database
        self.artifacts = artifacts
        self.normalizer = normalizer or AcquisitionNormalizer()
        self.metrics = metrics or EvidenceMetrics()

    def normalize_envelope(self, envelope: Mapping[str, Any]) -> dict[str, Any]:
        parser = self.normalizer
        envelope_id = str(envelope["envelope_id"])
        representation = str(
            (envelope.get("artifact") or {}).get("representation")
            or (envelope.get("acquisition") or {}).get("artifact_representation")
            or "CANONICALIZED_RESPONSE_REPRESENTATION"
        )
        existing = self.database.get_normalization_status(
            envelope_id, parser.parser_id, parser.parser_version,
            parser.fact_schema_version,
        )
        if existing is not None and existing["state"] == "COMPLETE":
            self.metrics.increment("normalization_replay")
        self.database.set_normalization_status(
            envelope_id=envelope_id, parser_id=parser.parser_id,
            parser_version=parser.parser_version,
            fact_schema_version=parser.fact_schema_version,
            state="RUNNING", representation=representation,
            increment_attempt=True,
        )
        started = time.monotonic()
        try:
            raw = self.artifacts.get(str(envelope["artifact"]["digest"]))
            records = parser.normalize(envelope, raw)
            result = self.database.append_normalized_records(
                envelope_id=envelope_id, parser_id=parser.parser_id,
                parser_version=parser.parser_version,
                fact_schema_version=parser.fact_schema_version,
                representation=representation, records=records,
            )
            self.metrics.increment("normalization_complete")
            self.metrics.increment("normalization_facts", len(records))
            self.metrics.observe("normalization_latency_ms", (time.monotonic() - started) * 1000)
            if representation != "EXACT_PROVIDER_ARTIFACT":
                self.metrics.increment("normalization_legacy_artifacts")
            return {"state": "COMPLETE", "facts": len(records), **result}
        except NotImplementedError as exc:
            self.database.set_normalization_status(
                envelope_id=envelope_id, parser_id=parser.parser_id,
                parser_version=parser.parser_version,
                fact_schema_version=parser.fact_schema_version,
                state="UNSUPPORTED", representation=representation, error=str(exc),
            )
            self.metrics.increment("normalization_unsupported")
            return {"state": "UNSUPPORTED", "facts": 0, "error": str(exc)}
        except Exception as exc:
            self.database.set_normalization_status(
                envelope_id=envelope_id, parser_id=parser.parser_id,
                parser_version=parser.parser_version,
                fact_schema_version=parser.fact_schema_version,
                state="FAILED", representation=representation, error=str(exc),
            )
            self.metrics.increment("normalization_failures")
            self.metrics.increment("normalization_malformed")
            return {"state": "FAILED", "facts": 0, "error": str(exc)}

    def health(self) -> dict[str, Any]:
        owned = self.database.connection
        connection = owned
        if connection is None:
            if not self.database.path.exists():
                return {"status": "NOT_INITIALIZED"}
            try:
                connection = sqlite3.connect(
                    f"file:{self.database.path}?mode=ro", uri=True, timeout=2
                )
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA query_only=ON")
            except sqlite3.Error as exc:
                return {"status": "DEGRADED", "error": str(exc)}
        rows = connection.execute(
            "SELECT state,COUNT(*) AS count FROM normalization_status GROUP BY state"
        ).fetchall()
        states = {row["state"]: row["count"] for row in rows}
        versions = connection.execute(
            "SELECT DISTINCT parser_id,parser_version,fact_schema_version "
            "FROM normalization_status ORDER BY parser_id,parser_version,fact_schema_version"
        ).fetchall()
        legacy, total = connection.execute(
            "SELECT SUM(CASE WHEN artifact_representation!='EXACT_PROVIDER_ARTIFACT' THEN 1 ELSE 0 END),COUNT(*) "
            "FROM normalization_status"
        ).fetchone()
        awaiting = connection.execute(
            "SELECT COUNT(*) FROM evidence_envelopes e WHERE NOT EXISTS ("
            "SELECT 1 FROM normalization_status n WHERE n.envelope_id=e.envelope_id "
            "AND n.parser_id=? AND n.parser_version=? AND n.fact_schema_version=? "
            "AND n.state IN ('COMPLETE','UNSUPPORTED'))",
            (self.normalizer.parser_id, self.normalizer.parser_version,
             self.normalizer.fact_schema_version),
        ).fetchone()[0]
        result = {
            "status": "DEGRADED" if states.get("FAILED") else "HEALTHY",
            "states": states,
            "artifacts_awaiting_normalization": awaiting,
            "versions": [dict(row) for row in versions],
            "legacy_artifact_ratio": (legacy or 0) / total if total else 0.0,
            "metrics": self.metrics.snapshot(),
        }
        if owned is None:
            connection.close()
        return result
