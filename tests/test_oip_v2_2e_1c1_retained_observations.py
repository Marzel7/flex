from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import time
import json
from dataclasses import replace
import threading

import pytest

from src.acquisition import factory
from src.acquisition.degraded_accounting import BoundedDegradedJournalHandoff, DegradedAccountingJournal, JournalResult
from src.acquisition.retained_observations import RetainedAcquisitionStore
from src.acquisition.transaction import AcquisitionMetadata, AcquisitionResponse
from src.acquisition.transaction import acquisition_scope
from src.evidence.artifacts import ArtifactStore


MINT = "11111111111111111111111111111111"


def response(*, provider="helius_rpc", body=b'{"result":{}}', correlation="correlation"):
    metadata = AcquisitionMetadata("acquisition", correlation, "creator_funding", "creator", MINT,
        "json_rpc", provider, "getTransaction", 1, None, 10.0, "miss", 0)
    return AcquisitionResponse(200, {"result": {}}, None, {"Content-Type": "application/json"}, metadata, 1.0, body, "EXACT_PROVIDER_ARTIFACT")


def store(root: Path):
    return RetainedAcquisitionStore(root / "retained.db", ArtifactStore(root / "artifacts", enabled=True))


class _Response:
    status = 200
    headers = {"Content-Type": "application/json"}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
        return {"result": {"ok": True}}

    async def read(self):
        return b'{"result":{"ok":true}}'

    async def text(self):
        return '{"result":{"ok":true}}'


class _Session:
    def __init__(self):
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _Response()


class _TrackingStore(RetainedAcquisitionStore):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.outcome_attempts = 0
        self.gap_attempts = 0

    def record_outcome(self, *args, **kwargs):
        self.outcome_attempts += 1
        return super().record_outcome(*args, **kwargs)

    def record_gap(self, *args, **kwargs):
        self.gap_attempts += 1
        return super().record_gap(*args, **kwargs)


class _TrackingJournal(DegradedAccountingJournal):
    def __init__(self, path):
        super().__init__(path)
        self.results = []
        self.write_times_ms = []

    def append(self, *args, **kwargs):
        started = time.monotonic()
        result = super().append(*args, **kwargs)
        self.write_times_ms.append((time.monotonic() - started) * 1000)
        self.results.append(result)
        return result


class _ForcedFailureStore(_TrackingStore):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fail_main_retention = True
        self.failure_times_ms = []

    def retain(self, *args, **kwargs):
        started = time.monotonic()
        if self.fail_main_retention:
            self.failure_times_ms.append((time.monotonic() - started) * 1000)
            raise sqlite3.OperationalError("forced isolated retention failure")
        return super().retain(*args, **kwargs)


class _ForcedFailureJournal(_TrackingJournal):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fail_journal = True

    def append(self, metadata, *, stage, error):
        started = time.monotonic()
        if self.fail_journal:
            result = JournalResult("JOURNAL_PERSIST_FAILED", "forced-journal-failure", stage, "OSError")
            self.write_times_ms.append((time.monotonic() - started) * 1000)
            self.results.append(result)
            return result
        return super().append(metadata, stage=stage, error=error)


def test_retained_observation_reconstructs_same_canonical_envelope_and_redacts_url():
    with TemporaryDirectory() as path:
        value = store(Path(path)); observed = value.retain(response(), http_method="POST", url="https://rpc.test/?api-key=secret&x=1", request_payload={"method":"getTransaction","params":["sig"]})
        rebuilt = value.dry_run_envelope(observed)
        assert rebuilt["state"] == "REPLAYABLE"
        envelope = rebuilt["envelope"]
        assert envelope["acquisition"]["launch"] == MINT
        assert envelope["acquisition"]["correlation_id"] == "correlation"
        assert envelope["artifact"]["digest"] == observed.artifact_digest
        assert "secret" not in envelope["provenance"]["source_metadata"]["request"]["url"]


def test_duplicate_is_idempotent_and_provider_disagreement_is_preserved():
    with TemporaryDirectory() as path:
        value = store(Path(path)); one = value.retain(response(), http_method="POST", url="https://rpc.test", request_payload={})
        duplicate = value.retain(response(), http_method="POST", url="https://rpc.test", request_payload={})
        other = value.retain(response(provider="solana_public_rpc", body=b'{"result":{"other":true}}'), http_method="POST", url="https://rpc.test", request_payload={})
        rows = value.get(mints=[MINT])
        assert one.observation_id == duplicate.observation_id
        assert len(rows) == 2
        assert {row.metadata["provider"] for row in rows} == {"helius_rpc", "solana_public_rpc"}
        assert len({row.artifact_digest for row in rows}) == 2


def test_reopen_is_deterministic_and_missing_input_is_explicit():
    with TemporaryDirectory() as path:
        root = Path(path); value = store(root); observation = value.retain(response(), http_method="POST", url="https://rpc.test", request_payload={})
        reopened = store(root); recovered = reopened.get(observation_ids=[observation.observation_id])[0]
        assert recovered == observation
        broken = object.__new__(type(observation)); object.__setattr__(broken, "observation_id", observation.observation_id); object.__setattr__(broken, "metadata", {"acquisition_id":"x"})
        for field in ("schema_version", "http_method", "url", "request_payload", "response_status", "response_data", "response_text", "response_headers", "raw_body_base64", "artifact_representation", "artifact_digest", "artifact_size_bytes", "artifact_compressed_bytes", "content_type"):
            object.__setattr__(broken, field, getattr(observation, field))
        assert reopened.dry_run_envelope(broken)["state"] == "NOT_REPLAYABLE"


def test_retention_store_busy_wait_is_explicitly_bounded():
    with TemporaryDirectory() as path:
        root = Path(path); value = store(root); value.retain(response(), http_method="POST", url="https://rpc.test", request_payload={})
        lock = sqlite3.connect(root / "retained.db"); lock.execute("BEGIN EXCLUSIVE")
        started = time.monotonic()
        with pytest.raises(sqlite3.OperationalError):
            value.retain(response(correlation="locked"), http_method="POST", url="https://rpc.test", request_payload={})
        assert time.monotonic() - started < 0.25
        lock.rollback(); lock.close()
        value.retain(response(correlation="after-lock"), http_method="POST", url="https://rpc.test", request_payload={})


@pytest.mark.asyncio
async def test_factory_success_is_fail_open_when_main_retention_db_is_locked(tmp_path, monkeypatch):
    """A locked optional retention DB journals once without retrying acquisition."""
    root = Path(tmp_path)
    retained = _TrackingStore(root / "retained.db", ArtifactStore(root / "artifacts", enabled=True))
    journal = _TrackingJournal(root / "journal" / "events")
    # Create the schema before holding the real SQLite write lock.
    connection = retained._connect(); connection.close()
    monkeypatch.setattr(factory, "_configured_mirror", lambda: None)
    monkeypatch.setattr(factory, "_configured_retained_store", lambda: (retained, journal))
    before_lost = factory.retention_degraded_health()["retention_degraded_accounting_lost_total"]
    session = _Session()
    client = factory.build_transaction_acquisition(session)

    lock = sqlite3.connect(retained.path)
    lock.execute("BEGIN EXCLUSIVE")
    try:
        started = time.monotonic()
        with acquisition_scope(purpose="creator_funding", creator="creator-lock", launch=MINT):
            locked_response = await client.request_once(
                http_method="POST", url="https://mainnet.helius-rpc.com/",
                json_payload={"method": "getTransaction", "params": ["signature-lock"]},
                timeout_seconds=1, request_type="json_rpc", method="getTransaction",
            )
        locked_elapsed_ms = (time.monotonic() - started) * 1000

        assert locked_response.status == 200 and locked_response.error is None
        assert len(session.calls) == 1  # retention did not cause provider retry
        assert len(journal.results) == 1
        assert journal.results[0].status == "JOURNAL_PERSISTED"
        assert journal.results[0].failure_stage == "OBSERVATION_WRITE_FAILED"
        events = journal.events()
        assert len(events) == 1
        assert events[0]["acquisition_identity"] == locked_response.metadata.acquisition_id
        assert events[0]["main_store_error_class"] == "OperationalError"
        assert retained.outcome_attempts == 0
        assert retained.gap_attempts == 0
        assert factory.retention_degraded_health()["retention_degraded_accounting_lost_total"] == before_lost
        # The bounded 50ms busy policy is allowed scheduler overhead, but not a
        # default SQLite five-second wait.
        assert 40 <= locked_elapsed_ms < 250
    finally:
        lock.rollback(); lock.close()

    combined = retained.combined_accounting(journal)
    assert combined == {
        "eligible_total": 1, "retained_total": 0, "failed_with_gap_total": 0,
        "not_retainable_total": 0, "failed_gap_write_failed_total": 1,
        "unresolved_durable_total": 0, "invalid_journal_event_total": 0,
        "accounting_residual": 0,
    }

    with acquisition_scope(purpose="creator_funding", creator="creator-healthy", launch=MINT):
        healthy_response = await client.request_once(
            http_method="POST", url="https://mainnet.helius-rpc.com/",
            json_payload={"method": "getTransaction", "params": ["signature-healthy"]},
            timeout_seconds=1, request_type="json_rpc", method="getTransaction",
        )
    assert healthy_response.status == 200 and healthy_response.error is None
    assert retained.outcome_attempts == 1
    assert len(retained.get(mints=[MINT])) == 1
    # A fresh connection proves the exclusive lock and prior transaction closed.
    check = sqlite3.connect(retained.path)
    try:
        assert check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        check.close()
    print(json.dumps({
        "main_lock_wait_ms": round(locked_elapsed_ms, 3),
        "journal_write_ms": round(journal.write_times_ms[0], 3),
        "retention_added_wall_ms": round(locked_elapsed_ms, 3),
    }, sort_keys=True))


@pytest.mark.asyncio
async def test_factory_reports_process_local_loss_when_main_and_journal_both_fail(tmp_path, monkeypatch, caplog):
    """Both optional durable channels may fail without changing acquisition success."""
    root = Path(tmp_path)
    retained = _ForcedFailureStore(root / "retained.db", ArtifactStore(root / "artifacts", enabled=True))
    journal = _ForcedFailureJournal(root / "journal" / "events")
    connection = retained._connect(); connection.close()
    monkeypatch.setattr(factory, "_configured_mirror", lambda: None)
    monkeypatch.setattr(factory, "_configured_retained_store", lambda: (retained, journal))
    original_state = dict(factory._RETENTION_DEGRADED_STATE)
    factory._RETENTION_DEGRADED_STATE.update({
        "retention_degraded_accounting_lost_total": 0,
        "last_degraded_accounting_lost_at": None,
        "last_degraded_accounting_lost_stage": None,
        "last_degraded_accounting_lost_error_class": None,
    })
    session = _Session()
    client = factory.build_transaction_acquisition(session)
    try:
        started = time.monotonic()
        with acquisition_scope(purpose="creator_funding", creator="creator-loss", launch=MINT):
            failed_retention_response = await client.request_once(
                http_method="POST", url="https://mainnet.helius-rpc.com/",
                json_payload={"method": "getTransaction", "params": ["signature-loss"]},
                timeout_seconds=1, request_type="json_rpc", method="getTransaction",
            )
        retention_elapsed_ms = (time.monotonic() - started) * 1000
        health = factory.retention_degraded_health()
        assert failed_retention_response.status == 200 and failed_retention_response.error is None
        assert len(session.calls) == 1
        assert len(journal.results) == 1
        assert journal.results[0].status == "JOURNAL_PERSIST_FAILED"
        assert retained.outcome_attempts == 0 and retained.gap_attempts == 0
        assert journal.events() == []
        assert health["retention_degraded_accounting_lost_total"] == 1
        assert health["last_degraded_accounting_lost_at"] is not None
        assert health["last_degraded_accounting_lost_stage"] == "OBSERVATION_WRITE_FAILED"
        assert health["last_degraded_accounting_lost_error_class"] == "OSError"
        assert any(record.levelname == "CRITICAL" and record.event == "RETENTION_DEGRADED_ACCOUNTING_LOST" for record in caplog.records)
        # Both durable channels failed: this must not be reconstructed as a durable gap.
        assert retained.combined_accounting(journal)["failed_gap_write_failed_total"] == 0

        retained.fail_main_retention = False
        journal.fail_journal = False
        with acquisition_scope(purpose="creator_funding", creator="creator-recovered", launch=MINT):
            healthy_response = await client.request_once(
                http_method="POST", url="https://mainnet.helius-rpc.com/",
                json_payload={"method": "getTransaction", "params": ["signature-recovered"]},
                timeout_seconds=1, request_type="json_rpc", method="getTransaction",
            )
        assert healthy_response.status == 200 and healthy_response.error is None
        assert retained.outcome_attempts == 1
        assert factory.retention_degraded_health()["retention_degraded_accounting_lost_total"] == 1
        check = sqlite3.connect(retained.path)
        try:
            assert check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            check.close()
        # This is deliberately process-local catastrophic-loss telemetry: with
        # no journal event and no main outcome, a new process starts at zero.
        captured_loss = factory.retention_degraded_health()
        factory._RETENTION_DEGRADED_STATE.update({
            "retention_degraded_accounting_lost_total": 0,
            "last_degraded_accounting_lost_at": None,
            "last_degraded_accounting_lost_stage": None,
            "last_degraded_accounting_lost_error_class": None,
        })
        assert captured_loss["retention_degraded_accounting_lost_total"] == 1
        assert factory.retention_degraded_health()["retention_degraded_accounting_lost_total"] == 0
        assert journal.events() == []
        print(json.dumps({
            "main_failure_ms": round(retained.failure_times_ms[0], 3),
            "journal_failure_ms": round(journal.write_times_ms[0], 3),
            "retention_added_wall_ms": round(retention_elapsed_ms, 3),
        }, sort_keys=True))
    finally:
        factory._RETENTION_DEGRADED_STATE.clear()
        factory._RETENTION_DEGRADED_STATE.update(original_state)


def test_mixed_durable_accounting_is_acquisition_keyed_and_restart_deterministic(tmp_path):
    root = Path(tmp_path)
    value = store(root)
    journal = DegradedAccountingJournal(root / "journal" / "events")

    def item(acquisition_id, mint):
        base = response(correlation=f"correlation-{acquisition_id}")
        return replace(base, metadata=replace(base.metadata, acquisition_id=acquisition_id, launch=mint))

    # A/B deliberately share a mint: durable accounting keys only acquisition_id.
    retained = item("acquisition-A", MINT)
    failed_with_gap = item("acquisition-B", MINT)
    not_retainable = item("acquisition-C", "22222222222222222222222222222222")
    journal_only = item("acquisition-D", "33333333333333333333333333333333")
    value.record_outcome(retained, "RETAINED")
    value.record_outcome(failed_with_gap, "FAILED_WITH_GAP")
    value.record_outcome(not_retainable, "NOT_RETAINABLE")
    # A has both durable sources; primary main-store outcome must win.
    assert journal.append(retained.metadata.__dict__, stage="OBSERVATION_WRITE_FAILED", error=OSError()).status == "JOURNAL_PERSISTED"
    # D is journal-only; the second append is an idempotent duplicate.
    assert journal.append(journal_only.metadata.__dict__, stage="OBSERVATION_WRITE_FAILED", error=OSError()).status == "JOURNAL_PERSISTED"
    assert journal.append(journal_only.metadata.__dict__, stage="OBSERVATION_WRITE_FAILED", error=OSError()).status == "JOURNAL_DUPLICATE_ALREADY_PRESENT"
    # Explicitly surface, but do not count, a partial independent-journal file.
    (journal.path.parent / "partial.json").write_text('{"event_id":"partial"}')

    before = value.combined_accounting(journal)
    assert before == {
        "eligible_total": 4, "retained_total": 1, "failed_with_gap_total": 1,
        "not_retainable_total": 1, "failed_gap_write_failed_total": 1,
        "unresolved_durable_total": 0, "invalid_journal_event_total": 1,
        "accounting_residual": 0,
    }
    # Reopening both readers is the only input to post-restart reconstruction.
    reopened = store(root)
    reopened_journal = DegradedAccountingJournal(root / "journal" / "events")
    after = reopened.combined_accounting(reopened_journal)
    assert after == before
    assert len([event for event in journal.events() if event.get("acquisition_identity") == "acquisition-D"]) == 1
    assert len({"acquisition-A", "acquisition-B"}) == 2


@pytest.mark.asyncio
async def test_bounded_handoff_decouples_slow_journal_fsync_and_exposes_health(tmp_path, monkeypatch):
    class _SlowJournal(DegradedAccountingJournal):
        def __init__(self, path):
            super().__init__(path); self.started = threading.Event(); self.release = threading.Event()
        def append(self, *args, **kwargs):
            self.started.set(); self.release.wait(1)
            return super().append(*args, **kwargs)

    root = Path(tmp_path)
    retained = _ForcedFailureStore(root / "retained.db", ArtifactStore(root / "artifacts", enabled=True))
    # Create a read-only health/reconstruction target before forcing main failure.
    connection = retained._connect(); connection.close()
    slow = _SlowJournal(root / "journal" / "events")
    handoff = BoundedDegradedJournalHandoff(slow, max_pending=2, on_persist_failure=factory._journal_failure)
    monkeypatch.setattr(factory, "_configured_mirror", lambda: None)
    monkeypatch.setattr(factory, "_configured_retained_store", lambda: (retained, handoff))
    client = factory.build_transaction_acquisition(_Session())
    started = time.monotonic()
    with acquisition_scope(purpose="creator_funding", creator="creator-slow", launch=MINT):
        result = await client.request_once(http_method="POST", url="https://mainnet.helius-rpc.com/",
            json_payload={"method": "getTransaction"}, timeout_seconds=1,
            request_type="json_rpc", method="getTransaction")
    factory_elapsed_ms = (time.monotonic() - started) * 1000
    assert result.status == 200 and result.error is None
    assert factory_elapsed_ms < 50
    assert slow.started.wait(0.2)
    pending = factory.retention_health(retained, handoff, enabled=True)
    assert pending["status"] == "DEGRADED_PENDING_JOURNAL"
    assert pending["journal_pending_depth"] in {0, 1}
    slow.release.set()
    assert handoff.drain()
    durable = factory.retention_health(retained, handoff, enabled=True)
    assert durable["status"] == "DURABLE_DEGRADED_ACCOUNTING"
    assert durable["failed_gap_write_failed_total"] == 1
    assert factory.retention_health(enabled=False)["status"] == "DISABLED"
    handoff.stop()


def test_bounded_journal_clean_shutdown_drains_and_crash_loss_is_not_durable(tmp_path):
    root = Path(tmp_path)
    journal = DegradedAccountingJournal(root / "journal" / "events")
    handoff = BoundedDegradedJournalHandoff(journal, max_pending=2)
    metadata = response().metadata.__dict__
    assert handoff.append(metadata, stage="OBSERVATION_WRITE_FAILED", error=OSError()).status == "JOURNAL_ACCEPTED_FOR_PERSISTENCE"
    shutdown = handoff.stop(1)
    assert shutdown["drained"] and shutdown["pending_remaining"] == 0
    assert len(journal.events()) == 1

    # An abrupt process loss before worker persistence has no durable event;
    # restart accounting must leave it unknown, never invent an outcome.
    class _Blocked(DegradedAccountingJournal):
        def append(self, *args, **kwargs):
            threading.Event().wait(0.2)
            return super().append(*args, **kwargs)
    blocked = _Blocked(root / "crash" / "events")
    pending = BoundedDegradedJournalHandoff(blocked, max_pending=1)
    assert pending.append(metadata, stage="OBSERVATION_WRITE_FAILED", error=OSError()).status == "JOURNAL_ACCEPTED_FOR_PERSISTENCE"
    assert pending.health()["journal_pending_depth"] >= 1
    assert DegradedAccountingJournal(root / "crash" / "events").events() == []
    pending.stop(0)
