from src.ops.watchtower_first_available_mc import collect_first_available


def run(trades, *, tx={"slot": 100, "blockTime": 1000}, supply=1_000_000_000):
    calls = []
    def helius(sig): calls.append(("h", sig)); return tx
    def birdeye(mint, after, before): calls.append(("b", mint, after, before)); return trades
    result = collect_first_available(mint="mint", canonical_create_signature="create", qualified_supply=supply,
                                     helius_get_transaction=helius, birdeye_get_trades=birdeye)
    return result, calls


def trade(slot, price=0.00015, **extra): return {"slot": slot, "price_usd": price, "venue": "pump_amm", **extra}

def test_plus_one_and_exact_window():
    result, calls = run([trade(100), trade(101)])
    assert result.status == "QUALIFIED" and result.evidence["first_available_slot_offset"] == 1
    assert calls == [("h", "create"), ("b", "mint", 999, 1030)]

def test_plus_two(): assert run([trade(100), trade(102)])[0].evidence["first_available_slot_offset"] == 2
def test_plus_three(): assert run([trade(103)])[0].evidence["first_available_slot_offset"] == 3

def test_same_open_slot_prefers_transaction_then_instruction():
    result, _ = run([trade(101, transaction_index=2), trade(101, transaction_index=1, instruction_index=9), trade(101, transaction_index=1, instruction_index=2)])
    assert result.evidence["first_available"]["instruction_index"] == 2

def test_unsorted_provider_result_is_ordered_locally():
    result, _ = run([trade(103), trade(101), trade(100)])
    assert result.evidence["first_available"]["slot"] == 101

def test_no_post_create_trade_fails_closed(): assert run([trade(99), trade(100)])[0].status == "NO_FIRST_AVAILABLE_TRADE_IN_WINDOW"
def test_helius_failure_uses_one_call_only(): assert run([], tx=None)[0].status == "INSUFFICIENT_CREATE_EVIDENCE"
def test_birdeye_failure_uses_one_call_only(): assert run(None)[0].status == "INSUFFICIENT_TRADE_EVIDENCE"
def test_unqualified_supply_makes_zero_calls():
    result, calls = run([trade(101)], supply=None)
    assert result.status == "INSUFFICIENT_SUPPLY_EVIDENCE" and calls == []
