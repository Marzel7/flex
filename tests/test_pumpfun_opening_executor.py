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
    def __init__(self, *, creation_slot=10, creation_signatures=("birth",)):
        self.calls = []
        self.creation_slot = creation_slot
        self.creation_signatures = creation_signatures
    def get_transaction(self, signature):
        self.calls.append(("transaction", signature)); return {"result": {"slot": 10}}
    def get_block(self, slot):
        self.calls.append(("block", slot))
        if slot == self.creation_slot:
            return {"result": {"transactions": [
                {"transaction": {"signatures": [signature]}}
                for signature in self.creation_signatures
            ]}}
        return {"result": {"transactions": []}}


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
        opening_slot_trade_count=len(kwargs["actions"]),
    ))


def run(tmp_path, monkeypatch, *, block_actions, store=None, alchemy=None, helius=None):
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
    assert result["creation_slot"] == 10 and result["opening_trading_slot"] == 11
    assert result["calls"]["helius_get_block"] == 5 and result["early_stop_used"]
    assert float(result["first_1s_peak_mc"]) > float(result["opening_slot_peak_mc"])


def test_global_order_key_is_slot_then_transaction_then_action():
    ordered = sorted([action(11, 2, 0), action(10, 9, 0), action(11, 1, 3),
                      action(11, 1, 1)], key=executor._order_key)
    assert [(entry["slot"], entry["transaction_index"], entry["action_index"]) for entry in ordered] == [
        (10, 9, 0), (11, 1, 1), (11, 1, 3), (11, 2, 0)
    ]


def test_creation_slot_excludes_pre_create_actions_and_includes_post_create_actions(tmp_path, monkeypatch, context):
    helius = Helius(creation_signatures=("pre-create", "birth", "post-creator", "post-independent"))
    result = run(tmp_path, monkeypatch, helius=helius, block_actions={
        10: [
            action(10, 0, 0, buyer="outside", reserve=31_000_000_000),
            action(10, 1, 0, buyer="outside", reserve=32_000_000_000),
            action(10, 2, 0, buyer="creator", reserve=33_000_000_000),
            action(10, 3, 0, buyer="outside", reserve=34_000_000_000),
        ],
        11: [action(11, 0, 0, reserve=35_000_000_000)],
        12: [action(12, 0, 0, reserve=36_000_000_000)],
        13: [action(13, 0, 0, reserve=37_000_000_000)],
    })
    assert result["status"] == "QUALIFIED"
    assert result["create_tx_index_in_creation_block"] == 1
    assert result["opening_trading_slot"] == 10
    assert result["first_independent_buyer"] == "outside"
    # The post-create independent buy is the one at index 3; indices 0/1 cannot leak in.
    assert result["first_independent_buy_mc"] == str(executor.market_cap_sol(
        virtual_sol_reserves=34_000_000_000,
        virtual_token_reserves=900_000_000_000_000,
        supply_raw=executor.PUMP_TOTAL_SUPPLY_RAW, decimals=executor.PUMP_DECIMALS,
    ))


def test_creation_slot_actions_are_not_double_counted_from_transaction_path(tmp_path, monkeypatch, context):
    # The executor has deliberately no transaction-action extraction path: creation
    # block actions are the single source for same-slot post-create activity.
    assert not hasattr(executor, "actions_from_transaction")
    result = run(tmp_path, monkeypatch, block_actions={
        10: [action(10, 1, 0, buyer="outside")], 11: [action(11, 0, 0)],
        12: [action(12, 0, 0)], 13: [action(13, 0, 0)],
    })
    assert result["first_1s_trade_count"] == 4


@pytest.mark.parametrize(
    ("mint", "creation_slot", "first_post_create_index"),
    [
        ("3jW73wn4skHyLMEDTZ5dzGzcF9C1YEbYuk9y61pUpump", 444229984, 12),
        ("zRoYAdLYogsS491qazGakt2BDvgjMMiE8xQWpSVpump", 444333372, 11),
    ],
)
def test_latest_byzantine_shapes_start_with_creation_slot_block(
    tmp_path, monkeypatch, mint, creation_slot, first_post_create_index,
):
    """Sanitized retained shapes: a create has no trade, later same-slot actions may."""
    monkeypatch.setattr(executor, "creation_context", lambda transaction, mint: {
        "creation_slot": creation_slot, "creator": "creator", "bonding_curve": "curve",
    })
    helius = Helius(creation_slot=creation_slot, creation_signatures=("other", "birth", "later"))
    result = run(tmp_path, monkeypatch, helius=helius, block_actions={
        creation_slot: [
            action(creation_slot, 0, 0, buyer="outside", reserve=30_000_000_000),
            action(creation_slot, first_post_create_index, 0, buyer="outside", reserve=31_000_000_000),
        ],
        creation_slot + 1: [action(creation_slot + 1, 0, 0, reserve=32_000_000_000)],
        creation_slot + 2: [action(creation_slot + 2, 0, 0, reserve=33_000_000_000)],
        creation_slot + 3: [action(creation_slot + 3, 0, 0, reserve=34_000_000_000)],
    })
    assert result["status"] == "QUALIFIED"
    assert result["creation_slot_block_included"] is True
    assert result["opening_trading_slot"] == creation_slot


def test_creation_slot_independent_buy_is_acquired_from_its_block(tmp_path, monkeypatch, context):
    result = run(tmp_path, monkeypatch, block_actions={
        10: [action(10, 1, 0, buyer="outside")], 11: [action(11, 0, 0)],
        12: [action(12, 0, 0)], 13: [action(13, 0, 0)],
    })
    assert result["status"] == "QUALIFIED"
    assert result["opening_trading_slot"] == 10
    assert result["calls"]["helius_get_block"] == 4


def test_empty_intermediate_slots_before_opening_are_allowed(tmp_path, monkeypatch, context):
    result = run(tmp_path, monkeypatch, block_actions={13: [action(13, 0, 0, buyer="outside")],
                 14: [action(14, 0, 0)], 15: [action(15, 0, 0)], 16: [action(16, 0, 0)]})
    assert result["status"] == "QUALIFIED"
    assert result["creation_slot"] == 10 and result["opening_trading_slot"] == 13
    assert result["first_1s_start_slot"] == 13


def test_creator_opening_trade_then_later_independent_buy(tmp_path, monkeypatch, context):
    result = run(tmp_path, monkeypatch, block_actions={
        13: [action(13, 0, 0, buyer="creator")], 14: [action(14, 0, 0, buyer="outside")],
        15: [action(15, 0, 0)], 16: [action(16, 0, 0)], 17: [action(17, 0, 0)],
    })
    assert result["status"] == "QUALIFIED"
    assert result["opening_trading_slot"] == 13 and result["first_1s_start_slot"] == 14
    assert result["first_independent_buyer"] == "outside"


def test_pilot_shape_delayed_opening_slot_is_not_rejected(tmp_path, monkeypatch, context):
    # Sanitized form of the real pilot: create=443645220, no target trade +1/+2, first target buy +3.
    monkeypatch.setattr(executor, "creation_context", lambda transaction, mint: {
        "creation_slot": 443645220, "creator": "creator", "bonding_curve": "curve",
    })
    result = run(tmp_path, monkeypatch, helius=Helius(creation_slot=443645220), block_actions={
        443645223: [action(443645223, 1025, 0, buyer="outside")],
        443645224: [action(443645224, 323, 0)], 443645225: [action(443645225, 0, 0)],
        443645226: [action(443645226, 0, 0)],
    })
    assert result["status"] == "QUALIFIED"
    assert result["opening_trading_slot"] == 443645223
    assert result["calls"]["helius_get_block"] == 7


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
    monkeypatch.setattr(executor, "actions_from_block", lambda block, *, mint, slot: (events.append(("decode", slot)) or []))
    executor.execute_birth_anchored_opening_analysis(operation_id="op", mint="mint", rich_birth={"signature":"birth"},
        helius=OrderedHelius(), alchemy=None, artifact_store=OrderedStore())
    assert events == [item for slot in range(10, 18) for item in (("request", slot), ("retain", slot), ("decode", slot))]


def test_result_retention_failure_prevents_qualified_return(tmp_path, monkeypatch, context):
    # transaction (2) + creation block plus four successors (10); result is thirteenth.
    with pytest.raises(OSError):
        run(tmp_path, monkeypatch, store=RecordingStore(fail_at=13), block_actions=qualified_blocks())


def test_call_budget_replay_identity_and_evidence_version_coexist(tmp_path, monkeypatch, context):
    first = run(tmp_path / "one", monkeypatch, block_actions=qualified_blocks())
    second = run(tmp_path / "two", monkeypatch, block_actions=qualified_blocks())
    assert first["calls"]["helius_get_transaction"] <= 1 and first["calls"]["helius_get_block"] <= 8
    assert first["calls"]["alchemy_get_account_info"] <= 1 and sum(first["calls"].values()) <= 10
    assert first["logical_id"] == second["logical_id"]
    changed = run(tmp_path / "three", monkeypatch, block_actions={**qualified_blocks(), 14: [action(14, 0, 0, reserve=46_000_000_000)]})
    assert changed["logical_id"] != first["logical_id"]


def test_explicit_fdv_semantics_preserve_legacy_mc_compatibility(tmp_path, monkeypatch, context):
    result = run(tmp_path, monkeypatch, block_actions=qualified_blocks())
    assert result["valuation_semantics_version"] == "v2_fdv_vs_mcap"
    assert result["canonical_valuation_name"] == "FDV"
    assert result["first_independent_entry_fdv_sol"] == result["first_independent_buy_mc"]
    assert result["first_1s_peak_fdv_sol"] == result["first_1s_peak_mc"]
    assert result["circulating_supply"] is None and result["market_cap_sol"] is None


def test_valuation_semantics_version_participates_in_result_identity(tmp_path, monkeypatch, context):
    current = run(tmp_path / "current", monkeypatch, block_actions=qualified_blocks())
    monkeypatch.setattr(executor, "VALUATION_SEMANTICS", "legacy-mc.v1")
    legacy = run(tmp_path / "legacy", monkeypatch, block_actions=qualified_blocks())
    assert current["logical_id"] != legacy["logical_id"]


def test_artifact_metadata_contains_no_provider_secret(tmp_path, monkeypatch, context):
    store = RecordingStore(); run(tmp_path, monkeypatch, store=store, block_actions=qualified_blocks())
    assert all("api_key" not in str(meta).lower() and "authorization" not in str(meta).lower()
               for _, meta in store.records)
