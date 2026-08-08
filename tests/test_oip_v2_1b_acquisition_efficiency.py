import asyncio
import json
from pathlib import Path

from src.acquisition.transaction import AcquisitionMetadata, AcquisitionResponse
from src.intelligence.acquisition_efficiency import (
    DurableAttemptLog, classify_response, summarize_attempt_log,
)


def _response(*, status=200, data=None, error=None):
    metadata = AcquisitionMetadata("a", "c", "pilot", None, "mint", "json_rpc",
        "helius_rpc", "getTransaction", None, None, 1.0, "none", 0)
    return AcquisitionResponse(status, data, None, {}, metadata, 12.5, error=error)


def test_failure_taxonomy_is_exhaustive_and_specific():
    cases = [
        (_response(data={"result": {"slot": 1}}), "RECOVERED"),
        (_response(data={"result": None}), "MISSING_TRANSACTION"),
        (_response(status=429), "RATE_LIMITED"),
        (_response(status=503), "PROVIDER_HTTP_UNAVAILABLE"),
        (_response(status=400), "MALFORMED_REQUEST"),
        (_response(data={"error": {"code": -32005}}), "RPC_ERROR_RETRYABLE"),
        (_response(data={"error": {"code": -1}}), "RPC_ERROR_TERMINAL"),
        (_response(data=[]), "MALFORMED_RESPONSE"),
        (_response(status=None, error=asyncio.TimeoutError()), "PROVIDER_TIMEOUT"),
        (_response(status=None, error=OSError()), "TRANSPORT_ERROR"),
    ]
    assert [classify_response(value)[0] for value, _ in cases] == [expected for _, expected in cases]


def test_attempt_log_is_durable_and_summarizable(tmp_path: Path):
    path = tmp_path / "attempts.jsonl"
    log = DurableAttemptLog(path)
    log.record(signature="s1", launch="m1", purpose="creation",
               response=_response(data={"result": {"slot": 1}}))
    log.record(signature="s2", launch="m2", purpose="migration",
               response=_response(status=429))
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert [row["sequence"] for row in rows] == [1, 2]
    summary = summarize_attempt_log(path)
    assert summary["attempts"] == 2
    assert summary["failure_classes"]["RECOVERED"] == 1
    assert summary["failure_classes"]["RATE_LIMITED"] == 1
