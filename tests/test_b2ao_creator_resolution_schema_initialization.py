import inspect


def test_worker_initializes_schema_once_before_loop():
    from src.core import creator_resolution_worker as worker

    source = inspect.getsource(worker.run_loop)
    initialize_at = source.index("initialize_schema(DB_PATH)")
    loop_at = source.index("while not _STOP:")

    assert initialize_at < loop_at
    assert source.count("initialize_schema(DB_PATH)") == 1


def test_worker_marks_every_steady_state_queue_call_schema_ready():
    from src.core import creator_resolution_worker as worker

    source = inspect.getsource(worker.run_loop)

    assert "enqueue_missing_migrated_tokens(\n                DB_PATH, limit=ENQUEUE_LIMIT, source=\"crq_worker\", schema_ready=True" in source
    assert "enqueue_missing_funding_jobs(\n                DB_PATH, limit=ENQUEUE_LIMIT, source=\"crq_worker\", schema_ready=True" in source
    assert "promote_recent_missing_creators(DB_PATH, schema_ready=True)" in source
    assert "process_queue(DB_PATH, limit=batch, schema_ready=True)" in source


def test_process_queue_error_paths_do_not_repeat_schema_work():
    from src.core import creator_resolution_queue as queue

    source = inspect.getsource(queue.process_queue)

    assert source.count("initialize_schema(db_path)") == 1
    assert "ensure_schema(conn)" not in source
    assert source.index("if not schema_ready:") < source.index("STALE-RUNNING REAPER")


def test_external_callers_keep_default_schema_assurance():
    from src.core import creator_resolution_queue as queue

    for function in (
        queue.enqueue_missing_migrated_tokens,
        queue.enqueue_missing_funding_jobs,
        queue.promote_recent_missing_creators,
        queue.process_queue,
    ):
        signature = inspect.signature(function)
        assert signature.parameters["schema_ready"].default is False
