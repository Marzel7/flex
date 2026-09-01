from __future__ import annotations

import json
import sqlite3
from collections import namedtuple
from pathlib import Path

import pytest

from src.evidence.contracts.ra1_retained_acquisition_architecture import GIB_BYTES, RetentionBudget, RetentionResourcePolicy
from src.evidence.contracts.ra2_retained_acquisition_implementation import (
    REPLAYABLE,
    analyze_rows,
    build_hot_row,
    estimate_growth,
    verify_replay_observation,
)
from scripts import run_ra2_retained_acquisition_local_replay_preflight as ra2_script


def _payload(idx: int, acq: str, corr: str, mint: str, digest: str, status: int = 200) -> dict:
    return {
        "observation_id": f"obs-{idx}",
        "metadata": {"timestamp": f"2026-08-18T00:00:{idx:02d}Z", "launch": mint, "purpose": "probe", "provider": "provider-a"},
        "http_method": "GET",
        "url": "https://example.test/",
        "request_payload": {"n": idx},
        "response_status": status,
        "response_data": {"value": idx},
        "response_text": "ok",
        "response_headers": {"x": "1"},
        "raw_body_base64": "eA==",
        "artifact_representation": "bytes",
        "artifact_digest": digest,
        "artifact_size_bytes": 1024,
        "artifact_compressed_bytes": 128,
        "content_type": "application/json",
        "acquisition_id": acq,
        "correlation_id": corr,
        "launch_mint": mint,
    }


def _make_db(path: Path, rows: int = 8) -> Path:
    db = path / "retained.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE retained_acquisition_observations (observation_id TEXT PRIMARY KEY,schema_version INTEGER,launch_mint TEXT,acquisition_id TEXT,correlation_id TEXT,payload_json TEXT,retained_at INTEGER)")
    for idx in range(rows):
        acq = f"acq-{idx % 3}"
        corr = f"corr-{idx % 2}"
        mint = f"mint-{idx % 2}"
        digest = "shared-digest" if idx % 2 else f"uniq-{idx}"
        payload = _payload(idx, acq=acq, corr=corr, mint=mint, digest=digest, status=200 if idx != 7 else 500)
        conn.execute(
            "INSERT INTO retained_acquisition_observations VALUES (?,?,?,?,?,?,?)",
            (payload["observation_id"], 1, mint, acq, corr, json.dumps(payload, sort_keys=True), idx + 1_000_000),
        )
    conn.commit()
    conn.close()
    return db


def test_build_hot_row_and_replay_observability():
    payload = _payload(1, acq="acq-1", corr="c-1", mint="m-1", digest="d-1")
    hot = build_hot_row(payload)
    state, digest, reasons = verify_replay_observation(payload)
    assert hot["acquisition_id"] == "acq-1"
    assert hot["artifact_digest"] == "d-1"
    assert state == REPLAYABLE
    assert digest is not None
    assert reasons == []


def test_analyze_rows_preserves_acquisition_identity_and_budget_caps():
    rows = []
    for i in range(6):
        payload = _payload(i, acq=f"acq-{i % 2}", corr=f"corr-{i % 3}", mint=f"mint-{i % 2}", digest="shared" if i % 3 == 0 else f"d-{i}")
        rows.append((i + 1, json.dumps(payload, sort_keys=True)))

    budget = RetentionBudget(
        daily_payload_bytes=120,
        per_hour_payload_bytes=60,
        max_payload_bytes_per_correlation=128,
        max_payloads_per_correlation=2,
        max_payload_bytes_per_mint=256,
        max_payloads_per_mint=16,
        max_payload_bytes_per_observation=64,
    )
    policy = RetentionResourcePolicy(normal_min_free_bytes=1, degraded_min_free_bytes=1, critical_min_free_bytes=1, hard_floor_bytes=1)

    checks, summary, metrics = analyze_rows(rows, budget=budget, policy=policy, free_bytes=10 * 1024 * 1024)
    assert summary["sample_size"] == 6
    assert checks[0].preserved_acquisition_identity
    assert len({check.acquisition_id for check in checks}) == 2
    assert metrics["not_replayable_events"] >= 0
    assert metrics["stats"]["artifacts_per_mint"]["max"] >= 1


def test_estimate_growth_projection_is_bounded_and_labelled():
    sample = {
        "sample_size": 4,
        "mean_full_bytes": 4096.0,
        "mean_hot_bytes": 1024.0,
        "hot_to_full_ratio": 0.25,
        "observations_per_correlation": {},
        "observations_per_mint": {},
        "acquisitions_per_correlation": {},
        "artifacts_per_correlation": {},
        "artifacts_per_mint": {},
    }
    estimate = estimate_growth(sample, observed_observations=1_800_000, observed_db_bytes=61_916_803_072, daily_budget_bytes=1 * GIB_BYTES)
    assert estimate["bounded_growth_estimate_label"] == "BOUNDED_DUPLICATION_ESTIMATE"
    assert estimate["projected_ra2_daily_gb"] <= 1.0 + 1e-6
    assert estimate["label"] == "bounded_sample_only"


def test_ra2_build_preflight_smoke_and_disk_gating(tmp_path, monkeypatch):
    db = _make_db(tmp_path, rows=10)

    experiment, preflight = ra2_script.build_preflight(db_path=db, sample_ceiling=10)
    assert experiment["milestone"] == "RA2"
    assert preflight["label"] == "BOUNDED_DUPLICATION_ESTIMATE"
    assert preflight["implementation_verdict"] == "READY_BOUNDED_RETENTION_IMPLEMENTATION"

    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(ra2_script.shutil, "disk_usage", lambda path: usage(0, 0, 10_000_000_000))
    with pytest.raises(RuntimeError):
        ra2_script.build_preflight(db_path=db, sample_ceiling=5000, retry_free_bytes=20_000_000_000, hard_floor_bytes=5_000_000_000)
