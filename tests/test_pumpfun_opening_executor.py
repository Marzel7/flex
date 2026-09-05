from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.evidence.artifacts import ArtifactStore
from src.ops import pumpfun_opening_executor as executor


def action(slot, tx_index, action_index, *, buyer="buyer", reserve=35_000_000_000):
    return {"slot": slot, "transaction_index": tx_index, "action_index": action_index,
            "signature": f"s-{slot}-{tx_index}-{action_index}", "action_type": "BUY",
            "buyer": buyer, "post_virtual_sol_reserves": reserve,
            "post_virtual_token_reserves": 900_000_000_000_000}


class Helius:
    def __init__(self): self.calls = []
    def get_transaction(self, signature):
        self.calls.append(("transaction", signature)); return {"result": {"slot": 10}}
    def get_block(self, slot):
        self.calls.append(("block", slot)); return {"result": {}}


class Alchemy:
    def __init__(self, response=None):
        self.calls = []; self.response = response if response is not None else {"result": {"value": "state"}}
    def get_account_info(self, account, slot):
        self.calls.append((account, slot)); return self.response


class RecordingStore:
    def __init__(self, fail_at=None): self.records = []; self.fail_at = fail_at
    def put(self, data, *, metadata=None):
        self.records.append((data, metadata))
        if self.fail_at == len(self.records): raise OSError("durability failure")
        return SimpleNamespace(digest=f"d{len(self.records)}")
    def verify(self, digest): return True


@pytest.fixture
def context(monkeypatch):
    monkeypatch.setattr(executor, "creation_context", lambda transaction, mint: {
        "creation_slot": 10, "creator": "creator", "bonding_curve": "curve",
    })


def _stub_replay(monkeypatch):
    monkeypatch.setattr(executor, "reconstruct_opening_impulse", lambda **kwargs: SimpleNamespace(
        first_independent_buy_mc_sol=1, opening_slot_peak_mc_sol=1, opening_slot_end_mc_sol=1,
    ))


def run(tmp_path, monkeypatch, *, block_actions, transaction_actions=(), store=None, alchemy=None, helius=None):
    monkeypatch.setattr(executor, "actions_from_transaction", lambda *args, **kwargs: list(transaction_actions))
    monkeypatch.setattr(executor, "actions_from_block", lambda block, *, mint, slot: list(block_actions.get(slot, ())))
    _stub_replay(monkeypatch)
    return executor.execute_birth_anchored_opening_analysis(
        operation_id="op", mint="mint", rich_birth={"signature": "birth"}, helius=helius or Helius(),
        alchemy=alchemy, artifact_store=store or ArtifactStore(tmp_path, enabled=True),
    )


def qualified_blocks():
    return {11: [action(11, 0, 0, buyer="outside")], 12: [action(12, 0, 0)],
            13: [action(13, 0, 0)], 14: [action(14, 0, 0)]}


def test_bounded_failure_never_exceeds_eight_blocks(tmp_path, monkeypatch, context):
    result = run(tmp_path, monkeypatch, block_actions={})
    assert result["status"] == "INSUFFICIENT_EVIDENCE_BOUND_EXHAUSTED"
    assert result["calls"] == {"helius_get_transaction": 1, "helius_get_block": 8, "alchemy_get_account_info": 0}


def test_retention_failure_stops_before_decode(tmp_path, monkeypatch, context):
    monkeypatch.setattr(executor, "creation_context", lambda *args: pytest.fail("decoded before retained"))
    with pytest.raises(OSError):
        executor.execute_birth_anchored_opening_analysis(operation_id="op", mint="mint", rich_birth={"signature": "birth"},
            helius=Helius(), alchemy=None, artifact_store=RecordingStore(fail_at=1))


def test_multiblock_ordering_later_buy_metrics_separate_and_early_stop(tmp_path, monkeypatch, context):
    result = run(tmp_path, monkeypatch, block_actions={
        11: [action(11, 2, 1, buyer="creator"), action(11, 3, 0, buyer="outside", reserve=36_000_000_000)],
        12: [action(12, 0, 0, reserve=39_000_000_000)], 13: [action(13, 0, 0, reserve=42_000_000_000)],
        14: [action(14, 0, 0, reserve=45_000_000_000)], 15: [action(15, 0, 0, reserve=99_000_000_000)],
    })
    assert result["status"] == "QUALIFIED"
    assert result["first_1s_start_slot"] == 11 and result["first_1s_end_slot"] == 14
    assert result["calls"]["helius_get_block"] == 4 and result["early_stop_used"]
    assert float(result["first_1s_peak_mc"]) > float(result["opening_slot_peak_mc"])


def test_global_order_key_is_slot_then_transaction_then_action():
    ordered = sorted([action(11, 2, 0), action(10, 9, 0), action(11, 1, 3),
                      action(11, 1, 1)], key=executor._order_key)
    assert [(entry["slot"], entry["transaction_index"], entry["action_index"]) for entry in ordered] == [
        (10, 9, 0), (11, 1, 1), (11, 1, 3), (11, 2, 0)
    ]


def test_creation_slot_independent_buy_uses_no_block_for_opening(tmp_path, monkeypatch, context):
    result = run(tmp_path, monkeypatch, transaction_actions=[action(10, None, 0, buyer="outside")],
                 block_actions={11: [action(11, 0, 0)], 12: [action(12, 0, 0)], 13: [action(13, 0, 0)]})
    assert result["status"] == "QUALIFIED" and result["calls"]["helius_get_block"] == 3


def test_empty_intermediate_slots_fail_closed_without_bad_replay(tmp_path, monkeypatch, context):
    result = run(tmp_path, monkeypatch, block_actions={13: [action(13, 0, 0, buyer="outside")],
                 14: [action(14, 0, 0)], 15: [action(15, 0, 0)], 16: [action(16, 0, 0)]})
    assert result["status"] == "INSUFFICIENT_EVIDENCE_BOUND_EXHAUSTED"
    assert result["reason"] == "NO_OPENING_SLOT_ACTIONS"


def test_alchemy_zero_call_when_prestate_not_required(tmp_path, monkeypatch, context):
    archive = Alchemy(); result = run(tmp_path, monkeypatch, block_actions=qualified_blocks(), alchemy=archive)
    assert result["status"] == "QUALIFIED" and archive.calls == []


def test_alchemy_missing_prestate_is_retained_before_continuing(tmp_path, monkeypatch, context):
    monkeypatch.setattr(executor, "creation_context", lambda transaction, mint: {
        "creation_slot": 10, "creator": "creator", "bonding_curve": "curve", "requires_archived_prestate": True,
    })
    archive, store = Alchemy(), RecordingStore()
    result = run(tmp_path, monkeypatch, store=store, alchemy=archive, block_actions=qualified_blocks())
    assert result["status"] == "QUALIFIED" and archive.calls == [("curve", 10)]
    assert any(meta.get("provider") == "alchemy" for _, meta in store.records)


@pytest.mark.parametrize("fail_at", [3, 4])
def test_alchemy_retention_failure_never_decodes_or_returns_qualified(tmp_path, monkeypatch, context, fail_at):
    monkeypatch.setattr(executor, "creation_context", lambda transaction, mint: {
        "creation_slot": 10, "creator": "creator", "bonding_curve": "curve", "requires_archived_prestate": True,
    })
    monkeypatch.setattr(executor, "actions_from_transaction", lambda *args, **kwargs: pytest.fail("decoded after archive retention failure"))
    with pytest.raises(OSError):
        executor.execute_birth_anchored_opening_analysis(operation_id="op", mint="mint", rich_birth={"signature": "birth"},
            helius=Helius(), alchemy=Alchemy(), artifact_store=RecordingStore(fail_at=fail_at))


def test_one_archive_lookup_without_value_fails_closed(tmp_path, monkeypatch, context):
    monkeypatch.setattr(executor, "creation_context", lambda transaction, mint: {
        "creation_slot": 10, "creator": "creator", "bonding_curve": "curve", "requires_archived_prestate": True,
    })
    archive = Alchemy({"result": None})
    result = run(tmp_path, monkeypatch, alchemy=archive, block_actions={})
    assert result["reason"] == "ARCHIVE_PRESTATE_UNAVAILABLE" and len(archive.calls) == 1


def test_each_block_is_retained_before_decode_and_before_next_request(tmp_path, monkeypatch, context):
    events = []
    class OrderedHelius(Helius):
        def get_block(self, slot): events.append(("request", slot)); return super().get_block(slot)
    class OrderedStore(RecordingStore):
        def put(self, data, *, metadata=None):
            if metadata.get("method") == "getBlock": events.append(("retain", metadata["request"]["slot"]))
            return super().put(data, metadata=metadata)
    monkeypatch.setattr(executor, "actions_from_transaction", lambda *args, **kwargs: [])
    monkeypatch.setattr(executor, "actions_from_block", lambda block, *, mint, slot: (events.append(("decode", slot)) or []))
    executor.execute_birth_anchored_opening_analysis(operation_id="op", mint="mint", rich_birth={"signature":"birth"},
        helius=OrderedHelius(), alchemy=None, artifact_store=OrderedStore())
    assert events == [item for slot in range(11, 19) for item in (("request", slot), ("retain", slot), ("decode", slot))]


def test_result_retention_failure_prevents_qualified_return(tmp_path, monkeypatch, context):
    # transaction (2) + four blocks (8); the result is the eleventh artifact.
    with pytest.raises(OSError):
        run(tmp_path, monkeypatch, store=RecordingStore(fail_at=11), block_actions=qualified_blocks())


def test_call_budget_replay_identity_and_evidence_version_coexist(tmp_path, monkeypatch, context):
    first = run(tmp_path / "one", monkeypatch, block_actions=qualified_blocks())
    second = run(tmp_path / "two", monkeypatch, block_actions=qualified_blocks())
    assert first["calls"]["helius_get_transaction"] <= 1 and first["calls"]["helius_get_block"] <= 8
    assert first["calls"]["alchemy_get_account_info"] <= 1 and sum(first["calls"].values()) <= 10
    assert first["logical_id"] == second["logical_id"]
    changed = run(tmp_path / "three", monkeypatch, block_actions={**qualified_blocks(), 14: [action(14, 0, 0, reserve=46_000_000_000)]})
    assert changed["logical_id"] != first["logical_id"]


def test_artifact_metadata_contains_no_provider_secret(tmp_path, monkeypatch, context):
    store = RecordingStore(); run(tmp_path, monkeypatch, store=store, block_actions=qualified_blocks())
    assert all("api_key" not in str(meta).lower() and "authorization" not in str(meta).lower()
               for _, meta in store.records)
