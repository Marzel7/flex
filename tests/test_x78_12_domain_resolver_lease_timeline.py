"""X78.12 Phase 9/10 -- deterministic reproduction of the DomainResolver
write+HTTP interleaving pattern, using the X78.12 lease-lifecycle
instrumentation to prove the EXACT shape of write-lane occupancy during a
realistic multi-address domain-resolution pass.

Background: the creator_funding_worker PID 8720 incident (X78.10/X78.11
soak) showed the write lease repeatedly tagged
`realtime_creator_funding_extractor.py:1270 in extract_for_creator` (the
line where extraction_conn is opened) for an extended period, with dozens
of sns_primary_domains RPC_METRICS log lines firing every ~150-200ms in
between. Static tracing (this milestone's Phase 3) found:

- _flush_page_batch commits extraction_conn's batch BEFORE calling
  resolve_primary_domains (correct transaction boundary for THAT specific
  connection).
- resolve_primary_domains batches SNS HTTP calls (20 addresses/request,
  aiohttp, 10s timeout) and accumulates cache/tag writes until all network
  batches have completed, then commits them through one tracked connection.

This test does NOT assert a verdict by inspection -- it uses controlled,
deterministic mock HTTP responses (asyncio.Event-gated, not sleeps) and the
real DomainResolver/write-lease code, instrumented via
scripts/x78_12_lease_instrumentation.py, to produce an exact timeline:
number of write-lane acquisitions, their individual durations, the gaps
between them, and whether any lease remains open while an HTTP await is
in flight. The numbers this test prints/asserts on are the actual evidence
for Phase 16's verdict -- not a restatement of the hypothesis.
"""
import asyncio
import os
import sqlite3
import tempfile
import time
from typing import Dict, List
from unittest import mock

import aiohttp
import pytest

import src.utils.db_locking as db_locking
import scripts.x78_12_lease_instrumentation as instr


@pytest.fixture(autouse=True)
def _write_serialize_enabled(monkeypatch):
    monkeypatch.setenv("DB_WRITE_SERIALIZE", "1")
    monkeypatch.setattr(db_locking, "_DB_WRITE_SERIALIZE", True)
    yield


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "flex.db")
    with sqlite3.connect(path) as conn:
        conn.execute("""
            CREATE TABLE address_domains (
                address TEXT PRIMARY KEY,
                primary_domain TEXT,
                updated_at INTEGER
            )
        """)
        conn.execute("""
            CREATE TABLE address_tags (
                address TEXT,
                tag_type TEXT,
                tag_value TEXT,
                source TEXT,
                first_seen_at INTEGER,
                PRIMARY KEY (address, tag_type, tag_value)
            )
        """)
    return path


class _FakeResponse:
    def __init__(self, status: int, payload: dict):
        self.status = status
        self._payload = payload

    async def json(self):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _ControlledSession:
    """Stands in for aiohttp.ClientSession.get(). Each call blocks on a
    fresh asyncio.Event that the test controls explicitly (set() to release
    it) -- deterministic, not a sleep-based approximation of network
    latency. Records call order/timing for assertions."""

    def __init__(self, addresses_have_domains: bool = True):
        self.calls: List[dict] = []
        self._addresses_have_domains = addresses_have_domains

    def get(self, url, timeout=None):
        pubkeys = url.rsplit("/", 1)[-1].split(",")
        event = asyncio.Event()
        call_record = {"url": url, "pubkeys": pubkeys, "t_start": time.monotonic(), "event": event}
        self.calls.append(call_record)

        async def _wait_then_respond():
            await event.wait()
            call_record["t_unblocked"] = time.monotonic()
            payload = {
                pk: (f"domain{i}" if self._addresses_have_domains else None)
                for i, pk in enumerate(pubkeys)
            }
            return _FakeResponse(200, payload)

        return _AsyncCtxFromCoro(_wait_then_respond())


class _AsyncCtxFromCoro:
    """Adapts a coroutine returning an async-context-manager-like object
    into something usable directly with `async with session.get(...) as r`."""
    def __init__(self, coro):
        self._coro = coro
        self._resp = None

    async def __aenter__(self):
        self._resp = await self._coro
        return self._resp

    async def __aexit__(self, *a):
        return False


@pytest.mark.asyncio
async def test_resolved_address_tags_use_one_batched_write_lease(db_path):
    """Resolving N domains persists cache and tag rows in one transaction."""
    from src.extractors.realtime_creator_funding_extractor import DomainResolver

    session = _ControlledSession(addresses_have_domains=True)
    resolver = DomainResolver(db_path, session)  # pyright: ignore[arg-type]

    instr.install()
    instr.reset()
    try:
        addresses = [f"addr{i:040d}" for i in range(5)]

        async def run_resolution():
            return await resolver.resolve_primary_domains(addresses)

        task = asyncio.create_task(run_resolution())
        # Let the resolver reach its HTTP call and register it.
        for _ in range(50):
            if session.calls:
                break
            await asyncio.sleep(0)
        assert session.calls, "resolver never reached the HTTP call"

        # Release the controlled HTTP response.
        session.calls[0]["event"].set()
        result = await task

        assert all(result[a] is not None for a in addresses)

        summary = instr.summary()
        assert summary["total_lease_acquisitions"] == 1, summary
        assert summary["still_open_at_summary_time"] == 0
        with sqlite3.connect(db_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM address_domains").fetchone()[0] == len(addresses)
            assert conn.execute("SELECT COUNT(*) FROM address_tags").fetchone()[0] == len(addresses)
    finally:
        instr.uninstall()


@pytest.mark.asyncio
async def test_no_write_lease_held_while_http_call_is_in_flight(db_path):
    """The core X78.12 question: while resolve_primary_domains is BLOCKED
    waiting on the controlled HTTP response, is any write lease open? If
    the answer is no (the observed pattern is short leases INTERLEAVED
    with HTTP, not one lease spanning an HTTP await), that's verdict A
    (network I/O adjacent to but not literally under write ownership at
    the per-request granularity) rather than a single held-open connection
    -- the distinction Phase 16 requires."""
    from src.extractors.realtime_creator_funding_extractor import DomainResolver

    session = _ControlledSession(addresses_have_domains=True)
    resolver = DomainResolver(db_path, session)  # pyright: ignore[arg-type]

    instr.install()
    instr.reset()
    try:
        addresses = [f"addr{i:040d}" for i in range(3)]

        task = asyncio.create_task(resolver.resolve_primary_domains(addresses))
        for _ in range(50):
            if session.calls:
                break
            await asyncio.sleep(0)
        assert session.calls

        # While the HTTP call is deliberately still blocked (event not set),
        # check whether any write lease is currently open.
        await asyncio.sleep(0)  # let any pending callbacks run
        mid_flight_summary = instr.summary()
        assert mid_flight_summary["still_open_at_summary_time"] == 0, (
            "a write lease was left open while the SNS HTTP call was still "
            "in flight -- this WOULD be a direct network-I/O-under-write-"
            "ownership defect at this exact call site"
        )

        session.calls[0]["event"].set()
        await task
    finally:
        instr.uninstall()


@pytest.mark.asyncio
async def test_worst_case_page_uses_one_post_network_write_lease(db_path):
    """Sixty addresses across three HTTP batches produce one write lease."""
    from src.extractors.realtime_creator_funding_extractor import DomainResolver

    session = _ControlledSession(addresses_have_domains=True)
    resolver = DomainResolver(db_path, session)  # pyright: ignore[arg-type]

    instr.install()
    instr.reset()
    ctx_token = instr.current_job_context.set({"creator": "heavy_creator", "page": 1})
    try:
        addresses = [f"addr{i:040d}" for i in range(60)]

        async def release_all_calls_as_they_arrive():
            released = 0
            while released < 3:  # 60 addresses / 20 per batch = 3 HTTP calls
                if len(session.calls) > released:
                    session.calls[released]["event"].set()
                    released += 1
                await asyncio.sleep(0)

        task = asyncio.create_task(resolver.resolve_primary_domains(addresses))
        releaser = asyncio.create_task(release_all_calls_as_they_arrive())
        await asyncio.gather(task, releaser)

        summary = instr.summary(job_filter="heavy_creator")
        print("Phase 9/10 evidence (60-address heavy-page case):", summary)

        assert summary["still_open_at_summary_time"] == 0, (
            "found a write lease left open at the end of a full resolution pass"
        )
        assert summary["total_lease_acquisitions"] == 1, summary
        assert summary["duration_sec"]["max"] < 1.0, (
            f"the batched write lease took {summary['duration_sec']['max']}s"
        )
    finally:
        instr.current_job_context.reset(ctx_token)
        instr.uninstall()
