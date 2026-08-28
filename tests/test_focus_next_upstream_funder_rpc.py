from scripts.check_focus_next_upstream_funders import incoming_source


def test_initial_inbound_parser_uses_native_balance_deltas_without_raw_payload_storage():
    transaction = {
        "slot": 7,
        "blockTime": 8,
        "transaction": {"message": {"accountKeys": ["upstream", "funder"]}},
        "meta": {"preBalances": [1_000, 0], "postBalances": [100, 900]},
    }
    result = incoming_source(transaction, "funder")
    assert result == {
        "status": "INITIAL_INBOUND_IDENTIFIED",
        "upstream_account": "upstream",
        "received_lamports": 900,
        "source_lamports": 900,
        "slot": 7,
        "block_time": 8,
    }
