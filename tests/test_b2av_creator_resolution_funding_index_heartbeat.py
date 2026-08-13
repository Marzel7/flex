import inspect
import sqlite3


def test_schema_manages_mint_leading_funding_queue_index(tmp_path):
    from src.core import creator_resolution_queue as queue

    db_path = str(tmp_path / "coverage.db")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE creator_funding_queue (
                creator_address TEXT NOT NULL,
                mint TEXT NOT NULL,
                PRIMARY KEY (creator_address, mint)
            )
            """
        )
    queue.initialize_schema(db_path)

    with sqlite3.connect(db_path) as conn:
        indexes = {
            row[1]
            for row in conn.execute(
                "PRAGMA index_list(creator_funding_queue)"
            ).fetchall()
        }

    assert "idx_creator_funding_queue_mint" in indexes
    assert queue.schema_ready(db_path) is True


def test_missing_funding_plan_uses_mint_index(tmp_path):
    from src.core import creator_resolution_queue as queue

    db_path = str(tmp_path / "plan.db")
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE creator_funding_queue (
                creator_address TEXT NOT NULL,
                mint TEXT NOT NULL,
                PRIMARY KEY (creator_address, mint)
            );
            CREATE TABLE token_analysis (
                mint TEXT PRIMARY KEY,
                lifecycle_stage TEXT,
                pf_ws_creator TEXT,
                earliest_tx_creator TEXT,
                create_tx_signature TEXT,
                migrated_at INTEGER,
                created_at INTEGER,
                analyzed_at INTEGER
            );
            CREATE TABLE creator_funders (creator_address TEXT);
            CREATE INDEX idx_creator_funders_creator
                ON creator_funders(creator_address);
            """
        )
    queue.initialize_schema(db_path)
    with sqlite3.connect(db_path) as conn:
        plan = conn.execute(
            """
            EXPLAIN QUERY PLAN
            SELECT ta.mint
            FROM token_analysis ta
            WHERE ta.lifecycle_stage='migrated'
              AND NOT EXISTS (
                  SELECT 1
                  FROM creator_funding_queue cfq
                  WHERE cfq.mint = ta.mint
              )
            """
        ).fetchall()

    detail = " ".join(str(row[3]) for row in plan)
    assert "idx_creator_funding_queue_mint" in detail


def test_startup_and_cycle_heartbeats_precede_population_work():
    from src.core import creator_resolution_worker as worker

    source = inspect.getsource(worker.run_loop)
    startup_at = source.index('"phase": "startup_ready"')
    loop_at = source.index("while not _STOP:")
    cycle_at = source.index('"phase": "cycle_start"')
    population_at = source.index("enqueue_missing_migrated_tokens(", cycle_at)
    outcome_at = source.index('"resolution_eff_pct": eff_pct', population_at)

    assert startup_at < loop_at < cycle_at < population_at < outcome_at


def test_ready_check_requires_funding_mint_index(tmp_path):
    from src.core import creator_resolution_queue as queue

    db_path = str(tmp_path / "missing-index.db")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE creator_funding_queue (
                creator_address TEXT NOT NULL,
                mint TEXT NOT NULL,
                PRIMARY KEY (creator_address, mint)
            )
            """
        )
    queue.initialize_schema(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP INDEX idx_creator_funding_queue_mint")

    assert queue.schema_ready(db_path) is False
