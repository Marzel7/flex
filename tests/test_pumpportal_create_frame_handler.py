import asyncio
import json

import pytest

from src.core.pumpfun_curve_listener import PumpFunCurveListener
from src.ops.byzantine_birth_signal import ByzantineBirthSignalEmitter


class _Audit:
    def __init__(self, calls):
        self.calls = calls

    def record(self, **_kwargs):
        self.calls.append("audit")


class _Signal:
    def __init__(self, calls):
        self.calls = calls

    def observe(self, **_kwargs):
        self.calls.append("signal")


class _Ws:
    def __init__(self):
        self.sent = []

    async def send(self, message):
        self.sent.append(json.loads(message))


class _Latency:
    def observe_pumpportal(self, **_kwargs):
        pass


class _Listener:
    _handle_pumpportal_create_frame = PumpFunCurveListener._handle_pumpportal_create_frame

    def __init__(self, signal):
        self.calls = []
        self._eb_birth_audit = _Audit(self.calls)
        self._byzantine_birth_signal = signal
        self._pumpfun_delivery_latency = _Latency()
        self._portal_vsol = {}
        self.completed_launches = set()
        self.seen_mints = set()

    def _remember_bonding_curve_token(self, *_args):
        self.calls.append("remember")

    async def _insert_bonding_curve_token(self, *_args, **_kwargs):
        self.calls.append("insert")

    async def _ensure_pf_ws_creator(self, *_args, **_kwargs):
        self.calls.append("ensure")


def _frame(mint="mint", creator="creator", signature="signature", v_sol=0):
    return {
        "mint": mint,
        "traderPublicKey": creator,
        "signature": signature,
        "bondingCurveKey": "curve",
        "symbol": "SYM",
        "name": "Name",
        "vSolInBondingCurve": v_sol,
        "marketCapSol": 1,
    }


@pytest.mark.asyncio
async def test_positive_byzantine_signal_precedes_birth_insert_and_creator_task(monkeypatch):
    listener = _Listener(_Signal([]))
    listener._byzantine_birth_signal.calls = listener.calls
    scheduled = []
    monkeypatch.setattr(asyncio, "create_task", scheduled.append)

    await listener._handle_pumpportal_create_frame(
        _frame(), signature="signature", mint="mint", receive_utc_ns=1,
        receive_monotonic_ns=2, tracked_trade_mints=set(), migration_sol_threshold=60,
        ws=_Ws(),
    )

    assert listener.calls == ["audit", "signal", "remember", "insert"]
    await scheduled.pop()
    assert listener.calls == ["audit", "signal", "remember", "insert", "ensure"]


@pytest.mark.asyncio
async def test_feature_disabled_keeps_normal_ingestion(monkeypatch):
    listener = _Listener(None)
    monkeypatch.setattr(asyncio, "create_task", lambda coro: coro.close())

    await listener._handle_pumpportal_create_frame(
        _frame(), signature="signature", mint="mint", receive_utc_ns=1,
        receive_monotonic_ns=2, tracked_trade_mints=set(), migration_sol_threshold=60,
        ws=_Ws(),
    )

    assert listener.calls == ["audit", "remember", "insert"]


@pytest.mark.asyncio
async def test_startup_signal_load_failure_keeps_normal_ingestion(monkeypatch):
    # __init__ intentionally turns a failed strict-creator load into None.
    # The handler must retain the normal birth path for that startup state.
    listener = _Listener(None)
    monkeypatch.setattr(asyncio, "create_task", lambda coro: coro.close())

    await listener._handle_pumpportal_create_frame(
        _frame(), signature="signature", mint="mint", receive_utc_ns=1,
        receive_monotonic_ns=2, tracked_trade_mints=set(), migration_sol_threshold=60,
        ws=_Ws(),
    )

    assert listener.calls == ["audit", "remember", "insert"]


def test_latest_five_byzantine_signals_replay_in_order():
    creators = {"byzantine": {"mint": "proof", "signature": "proof", "creator": "byzantine"}}
    emitter = ByzantineBirthSignalEmitter(creators, enabled=True, capacity=5)
    signals = [
        emitter.observe(mint=f"mint-{n}", creator="byzantine", signature=f"sig-{n}", receive_utc_ns=n)
        for n in range(5)
    ]
    assert [signal["mint"] for signal in signals if signal] == [f"mint-{n}" for n in range(5)]
    assert [emitter.events.get_nowait()["mint"] for _ in range(5)] == [f"mint-{n}" for n in range(5)]


def test_non_byzantine_creator_emits_no_signal():
    emitter = ByzantineBirthSignalEmitter({}, enabled=True, capacity=5)
    assert emitter.observe(mint="control", creator="other", signature="control", receive_utc_ns=6) is None
    assert emitter.events.empty()


def test_signal_queue_overflow_drops_only_extra_signal():
    creators = {"byzantine": {"mint": "proof", "signature": "proof", "creator": "byzantine"}}
    emitter = ByzantineBirthSignalEmitter(creators, enabled=True, capacity=5)
    for n in range(5):
        assert emitter.observe(mint=f"mint-{n}", creator="byzantine", signature=f"sig-{n}", receive_utc_ns=n)
    assert emitter.observe(mint="overflow", creator="byzantine", signature="overflow", receive_utc_ns=7) is None
    assert emitter.dropped == 1


@pytest.mark.asyncio
async def test_websocket_loop_delegates_create_frames_to_extracted_handler(monkeypatch):
    delegated = []

    class _Context:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def send(self, _message):
            pass

        async def recv(self):
            return json.dumps({"txType": "create", **_frame()})

    listener = object.__new__(PumpFunCurveListener)
    listener._pumpportal_connected = False

    async def handle(data, **kwargs):
        delegated.append((data, kwargs))
        raise asyncio.CancelledError

    listener._handle_pumpportal_create_frame = handle
    monkeypatch.setattr("src.core.pumpfun_curve_listener.websockets.connect", lambda *_a, **_kw: _Context())
    monkeypatch.setattr("src.core.pumpfun_curve_listener.asyncio.create_task", lambda coro: coro.close())
    monkeypatch.setattr("src.core.pumpfun_curve_listener.record_wss", lambda *_a, **_kw: None)

    with pytest.raises(asyncio.CancelledError):
        await listener.listen_pumpportal_websocket()

    assert delegated[0][0]["txType"] == "create"
    assert delegated[0][1]["migration_sol_threshold"] == 60.0
