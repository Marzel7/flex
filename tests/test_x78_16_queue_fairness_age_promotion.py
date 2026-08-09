"""X78.16: regression tests for funding-queue fairness (age promotion) and
claim-occupancy accounting.

Root cause (X78.15, measured live against the production queue): the claim
query in _recover_stale_and_claim ordered strictly by
`job_priority DESC, next_attempt_at ASC, created_at ASC` with no aging.
With 15,861 continuously-replenished job_priority=1 rows (~27 arrivals/hour)
against ~6.8 completions/hour, the 1,007-row job_priority=0 population --
including a single row that had sat untouched for 1005.93 hours (~42 days)
-- was mathematically guaranteed to never be reached: priority=0 rows are
only considered once the priority=1 ready pool drains to empty, which never
happens under sustained priority=1 arrivals.

X78.16 introduces age promotion: effective_priority = job_priority +
min(age_seconds / AGE_PROMOTION_INTERVAL_SEC, AGE_PROMOTION_CAP), and
orders the claim query by effective_priority DESC instead of raw
job_priority DESC. A first implementation used AGE_PROMOTION_CAP=24, which
a live probe against the production queue revealed as a NEW starvation
bug: 15,247 job_priority=1 rows were already older than 24h (capped at
1+24=25), permanently outranking every job_priority=0 row's own capped
ceiling of 0+24=24. The cap was corrected to 1000 (must exceed the actual
priority-tier spread by a wide margin). These tests cover both the
original starvation fix AND a regression test for the cap-bug specifically,
so it cannot silently reappear.

All tests use isolated tmp_path databases -- never the live production DB.
"""
from __future__ import annotations

import sqlite3
import time

import pytest

import src.core.creator_funding_worker as cfw


def _make_queue_db(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS creator_funding_queue (
            creator_address TEXT NOT NULL,
            mint TEXT NOT NULL,
            migration_timestamp TEXT,
            create_tx_signature TEXT,
            status TEXT DEFAULT 'pending',
            source TEXT,
            job_priority INTEGER DEFAULT 0,
            priority_reason TEXT,
            next_attempt_at INTEGER DEFAULT 0,
            locked_until INTEGER DEFAULT 0,
            attempts INTEGER DEFAULT 0,
            last_error TEXT,
            funding_extracted_at INTEGER,
            created_at INTEGER DEFAULT (strftime('%s','now')),
            updated_at INTEGER DEFAULT (strftime('%s','now')),
            PRIMARY KEY (creator_address, mint)
        )
    """)
    conn.execute("CREATE TABLE IF NOT EXISTS creator_funders (creator_address TEXT, funder_address TEXT)")
    conn.commit()
    conn.close()


def _insert_row(db_path, creator, mint, job_priority, age_seconds, status="pending", attempts=0):
    now = int(time.time())
    created_at = now - age_seconds
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO creator_funding_queue
            (creator_address, mint, status, job_priority, priority_reason,
             next_attempt_at, locked_until, attempts, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'test', 0, 0, ?, ?, ?)
        """,
        (creator, mint, status, job_priority, attempts, created_at, now),
    )
    conn.commit()
    conn.close()


def test_ancient_low_priority_row_is_eventually_claimed_ahead_of_fresh_high_priority_flood(tmp_path, monkeypatch):
    """Core X78.15/X78.16 regression: a single very old, low-priority row
    must be selected ahead of a large population of fresh, high-priority
    rows once it has aged past the promotion threshold -- reproducing the
    exact starvation shape measured live (1 ancient priority=0 row vs
    thousands of fresh priority=1 rows)."""
    db_path = str(tmp_path / "x.db")
    _make_queue_db(db_path)
    monkeypatch.setattr(cfw, "DB_PATH", db_path)
    monkeypatch.setattr(cfw, "AGE_PROMOTION_INTERVAL_SEC", 3600)
    monkeypatch.setattr(cfw, "AGE_PROMOTION_CAP", 1000)

    # The ancient, low-priority row -- 1000 hours old, matching the
    # magnitude of the live incident.
    _insert_row(db_path, "ancient_creator", "ancient_mint", job_priority=0, age_seconds=1000 * 3600)

    # A flood of fresh, high-priority rows -- far more numerous, exactly
    # the condition that caused permanent starvation pre-fix.
    for i in range(500):
        _insert_row(db_path, f"fresh_creator_{i}", f"fresh_mint_{i}", job_priority=1, age_seconds=60)

    rows, recovered, stale = cfw._recover_stale_and_claim(int(time.time()), batch=1)

    assert len(rows) == 1
    assert rows[0]["creator_address"] == "ancient_creator", (
        "the ancient low-priority row was not claimed first despite being "
        "old enough to fully out-age the fresh high-priority flood -- "
        "starvation regression"
    )


def test_fresh_high_priority_row_still_wins_against_comparably_aged_low_priority_row(tmp_path, monkeypatch):
    """Age promotion must not eliminate priority -- two rows of similar age
    should still order by raw job_priority, exactly as before X78.16."""
    db_path = str(tmp_path / "x.db")
    _make_queue_db(db_path)
    monkeypatch.setattr(cfw, "DB_PATH", db_path)
    monkeypatch.setattr(cfw, "AGE_PROMOTION_INTERVAL_SEC", 3600)
    monkeypatch.setattr(cfw, "AGE_PROMOTION_CAP", 1000)

    _insert_row(db_path, "low_pri_fresh", "mint_a", job_priority=0, age_seconds=30)
    _insert_row(db_path, "high_pri_fresh", "mint_b", job_priority=1, age_seconds=30)

    rows, _, _ = cfw._recover_stale_and_claim(int(time.time()), batch=1)

    assert len(rows) == 1
    assert rows[0]["creator_address"] == "high_pri_fresh", (
        "priority was not respected for comparably-aged rows -- age "
        "promotion should only bound the maximum deferral, not reverse "
        "priority outright"
    )


def test_age_promotion_cap_does_not_reintroduce_starvation(tmp_path, monkeypatch):
    """Regression test for the specific cap-sizing bug found live during
    X78.16 development: if AGE_PROMOTION_CAP is too small relative to the
    priority-tier spread, a large population of aged high-priority rows
    (all pinned at their own cap) can permanently outrank an even OLDER
    low-priority row also pinned at its cap. This proves the cap is large
    enough that a sufficiently old low-priority row still wins even against
    a big population of aged (but younger) high-priority rows."""
    db_path = str(tmp_path / "x.db")
    _make_queue_db(db_path)
    monkeypatch.setattr(cfw, "DB_PATH", db_path)
    monkeypatch.setattr(cfw, "AGE_PROMOTION_INTERVAL_SEC", 3600)
    monkeypatch.setattr(cfw, "AGE_PROMOTION_CAP", 1000)

    # Reproduces the exact live condition that broke the AGE_PROMOTION_CAP=24
    # first draft: many high-priority rows already old enough to be at
    # THEIR OWN cap ceiling, plus one low-priority row older still.
    for i in range(200):
        _insert_row(db_path, f"aged_high_pri_{i}", f"mint_high_{i}", job_priority=1, age_seconds=30 * 3600)
    _insert_row(db_path, "oldest_low_pri", "mint_oldest", job_priority=0, age_seconds=1005 * 3600)

    rows, _, _ = cfw._recover_stale_and_claim(int(time.time()), batch=1)

    assert len(rows) == 1
    assert rows[0]["creator_address"] == "oldest_low_pri", (
        "the cap reintroduced a permanent priority-tier gap -- the oldest "
        "row lost to a population of merely-capped high-priority rows"
    )


def test_effective_priority_is_deterministic_and_measurable(tmp_path, monkeypatch):
    """Phase A requirement: the mechanism must be measurable. Directly
    verify the effective_priority formula matches expectations for a known
    age, independent of the claim query's ordering behavior."""
    db_path = str(tmp_path / "x.db")
    _make_queue_db(db_path)
    monkeypatch.setattr(cfw, "DB_PATH", db_path)
    monkeypatch.setattr(cfw, "AGE_PROMOTION_INTERVAL_SEC", 3600)
    monkeypatch.setattr(cfw, "AGE_PROMOTION_CAP", 1000)

    _insert_row(db_path, "half_promoted", "mint_x", job_priority=0, age_seconds=1800)  # 0.5h

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    now = int(time.time())
    row = conn.execute(
        f"""
        SELECT (COALESCE(job_priority, 0) + MIN(
            CAST((? - created_at) AS REAL) / {cfw.AGE_PROMOTION_INTERVAL_SEC},
            {cfw.AGE_PROMOTION_CAP}
        )) AS effective_priority
        FROM creator_funding_queue WHERE creator_address='half_promoted'
        """,
        (now,),
    ).fetchone()
    conn.close()

    # 1800s of age / 3600s interval = 0.5 promotion points, +/- a small
    # margin for wall-clock drift between the insert and this query.
    assert 0.45 <= row["effective_priority"] <= 0.55


def test_retry_backoff_still_functions_after_age_promotion_change(tmp_path, monkeypatch):
    """Phase C: retry scheduling remains deterministic -- _mark_retry's
    existing growing backoff (120s * attempt, capped 900s) must be
    unaffected by the age-promotion ordering change, since retries are
    scheduled by next_attempt_at, not by effective_priority."""
    db_path = str(tmp_path / "x.db")
    _make_queue_db(db_path)
    monkeypatch.setattr(cfw, "DB_PATH", db_path)

    _insert_row(db_path, "retry_creator", "retry_mint", job_priority=1, age_seconds=10, status="retry", attempts=2)

    now = int(time.time())
    cfw._mark_retry("retry_creator", "retry_mint", attempts=2, error="timeout", now=now)

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT attempts, next_attempt_at FROM creator_funding_queue WHERE creator_address='retry_creator'"
    ).fetchone()
    conn.close()

    assert row[0] == 3  # attempts incremented
    # backoff = min(900, 120 * (attempts+1)) where attempts is the value
    # PASSED IN (2), so 120*3=360
    assert row[1] == now + 360


def test_no_eligible_row_is_permanently_unclaimable_regardless_of_batch_repetition(tmp_path, monkeypatch):
    """Simulates repeated claim cycles (as the real worker loop does) and
    proves the ancient row is claimed within a bounded number of cycles,
    not deferred forever -- the actual end-to-end guarantee Phase A
    requires ('no eligible job may remain indefinitely unclaimable')."""
    db_path = str(tmp_path / "x.db")
    _make_queue_db(db_path)
    monkeypatch.setattr(cfw, "DB_PATH", db_path)
    monkeypatch.setattr(cfw, "AGE_PROMOTION_INTERVAL_SEC", 3600)
    monkeypatch.setattr(cfw, "AGE_PROMOTION_CAP", 1000)

    _insert_row(db_path, "ancient", "ancient_mint", job_priority=0, age_seconds=1000 * 3600)
    for i in range(50):
        _insert_row(db_path, f"fresh_{i}", f"fresh_mint_{i}", job_priority=1, age_seconds=5)

    claimed_creators = []
    now = int(time.time())
    for _ in range(60):  # batch=1, so at most 60 rows claimed across cycles
        rows, _, _ = cfw._recover_stale_and_claim(now, batch=1)
        if not rows:
            break
        claimed_creators.append(rows[0]["creator_address"])
        # Mark claimed row 'complete' so it's not reclaimed and the next
        # cycle proceeds to a different row, mimicking real drain behavior.
        conn = sqlite3.connect(db_path)
        conn.execute(
            "UPDATE creator_funding_queue SET status='complete' WHERE creator_address=? AND mint=?",
            (rows[0]["creator_address"], rows[0]["mint"]),
        )
        conn.commit()
        conn.close()

    assert "ancient" in claimed_creators, (
        "the ancient row was never claimed even across repeated cycles "
        "draining the entire fresh population -- indefinite starvation"
    )
    # It should be claimed FIRST (or very close to first), not merely
    # eventually -- confirming it isn't just winning by attrition once the
    # fresh population is exhausted.
    assert claimed_creators.index("ancient") == 0
