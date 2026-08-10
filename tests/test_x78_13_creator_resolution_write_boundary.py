import inspect


def _assert_schema_commit_precedes_population_read(function, marker):
    source = inspect.getsource(function)
    schema_at = source.index("ensure_schema(conn)")
    commit_at = source.index("conn.commit()", schema_at)
    read_at = source.index(marker, schema_at)
    assert schema_at < commit_at < read_at


def test_creator_resolution_population_scans_release_schema_write_first():
    from src.core import creator_resolution_queue as queue

    _assert_schema_commit_precedes_population_read(
        queue.enqueue_missing_migrated_tokens,
        "SELECT mint",
    )
    _assert_schema_commit_precedes_population_read(
        queue.enqueue_missing_funding_jobs,
        "SELECT\n                ta.mint",
    )
    _assert_schema_commit_precedes_population_read(
        queue.process_queue,
        "SELECT mint, attempts, source, priority",
    )


def test_bulk_creator_enqueue_reuses_verified_schema():
    from src.core import creator_resolution_queue as queue

    source = inspect.getsource(queue.enqueue_missing_migrated_tokens)
    assert "schema_ready=True" in source
