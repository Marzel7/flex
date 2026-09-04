from __future__ import annotations

import sqlite3

import pytest

import src.core.creator_funding_worker as worker
import src.extractors.realtime_creator_funding_extractor as extractor_module
from src.utils.infra_mapping import build_excluded_set


@pytest.mark.asyncio
async def test_authoritative_funder_check_precedes_optional_profile_bridge(tmp_path, monkeypatch):
    path = str(tmp_path / "funding.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE creator_funders (creator_address TEXT, funder_address TEXT)")
    conn.execute("INSERT INTO creator_funders VALUES ('creator','funder')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(extractor_module, "DB_PATH", path)

    async def must_not_extract():
        raise AssertionError("known creator reached expensive extractor")

    monkeypatch.setattr(extractor_module, "get_extractor", must_not_extract)
    result = await extractor_module.extract_funding_for_new_token(
        "creator", "2026-08-10T00:00:00Z", "sig", "mint"
    )
    assert result == {
        "skipped": True,
        "reason": "creator_funders_count",
        "cached_funders": 1,
    }


@pytest.mark.asyncio
async def test_worker_exposes_fast_completion_without_changing_terminal_mark(tmp_path, monkeypatch):
    async def skip(*_args, **_kwargs):
        return {"skipped": True, "reason": "creator_funders_count", "cached_funders": 2}

    monkeypatch.setattr(extractor_module, "extract_funding_for_new_token", skip)
    monkeypatch.setattr(worker, "_funder_count", lambda _creator: 2)
    marks = []
    monkeypatch.setattr(worker, "_mark_complete", lambda creator, mint, attempts, now: marks.append((creator, mint, attempts)))
    row = {
        "creator_address": "creator",
        "mint": "mint",
        "migration_timestamp": "2026-08-10T00:00:00Z",
        "create_tx_signature": "sig",
        "attempts": 0,
        "job_priority": 1,
        "priority_reason": "test",
    }
    assert await worker._process_job(row) == "complete_fast"
    assert marks == [("creator", "mint", 0)]
    assert worker._outcome_deltas("complete_fast") == (1, 0, 0)


def test_candidate_bounded_exclusion_preserves_dynamic_protocol_semantics(tmp_path):
    path = str(tmp_path / "infra.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE infra_wallets (address TEXT PRIMARY KEY)")
    conn.execute("CREATE TABLE cex_wallets (cex_address TEXT, is_active INTEGER)")
    conn.execute("CREATE TABLE infra_funders_observed (funder_address TEXT)")
    conn.execute("""
        CREATE TABLE token_analysis (
          bonding_curve_pda TEXT, pool_address TEXT, pumpswap_pool_address TEXT)
    """)
    conn.execute("INSERT INTO token_analysis VALUES ('curve','pool','pumpswap')")
    conn.execute("INSERT INTO infra_wallets VALUES ('curve')")
    conn.commit()
    full = build_excluded_set(conn)
    bounded = build_excluded_set(conn, candidate_addresses=["curve", "ordinary"])
    materialized = build_excluded_set(
        conn, candidate_addresses=["curve", "ordinary"], include_token_analysis=False
    )
    conn.close()
    assert "curve" in full and "curve" in bounded
    assert "curve" in materialized
    assert "ordinary" not in bounded
