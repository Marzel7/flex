import json
from pathlib import Path

from src.ops.byzantine_forward_opening_adapter import execute
from src.ops.universal_assignment_opening_bridge import EXECUTABLE_ADAPTERS


FIXTURE = json.loads((Path(__file__).parents[1] / 'docs/fixtures/byzantine_actual_entry_v1/pepeinu.json').read_text())
ASSIGNMENT = FIXTURE['assignment']
OUTCOME = FIXTURE['outcome']


def adapter(proof='MATCH', candle=True, selected=True):
    chosen = OUTCOME['selected'] if selected else None
    calls = {'identity': 0, 'valuation': 0}
    def identity(event, mint):
        calls['identity'] += 1
        assert event['signature'] == OUTCOME['selected']['signature']
        return {'state': proof, 'event_mint': mint}
    def valuation(event, mint):
        calls['valuation'] += 1
        assert event['timestamp'] == OUTCOME['selected']['timestamp']
        return OUTCOME['selected_candle'] if candle else None
    return execute(ASSIGNMENT, select=lambda _: chosen, identity=identity, valuation=valuation), calls


def test_pepeinu_exact_retained_parity():
    result, calls = adapter()
    assert result['qualified'] is True
    assert result['selected']['signature'] == OUTCOME['selected']['signature']
    assert result['selected']['slot'] == 453241280
    assert result['timestamp'] == 1791112830
    assert result['mc_usd'] == 3925.9516827750313
    assert calls == {'identity': 1, 'valuation': 1}


def test_identity_failures_do_not_value_or_select_again():
    for state in ('NON_MATCH', 'UNPROVEN', 'AMBIGUOUS'):
        result, calls = adapter(proof=state)
        assert result['qualified'] is False and state in result['state']
        assert calls == {'identity': 1, 'valuation': 0}


def test_no_trade_or_candle_fails_closed():
    result, calls = adapter(selected=False)
    assert result['state'] == 'FAIL_CLOSED_NO_QUALIFYING_TRADE' and calls == {'identity': 0, 'valuation': 0}
    result, calls = adapter(candle=False)
    assert result['state'] == 'FAIL_CLOSED_NO_CONTAINING_CANDLE' and calls == {'identity': 1, 'valuation': 1}


def test_registry_label_binds_the_forward_adapter():
    assert EXECUTABLE_ADAPTERS['BYZANTINE_ACTUAL_ENTRY_V2'] is execute
