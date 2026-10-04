import pytest

from src.ops import qualified_entry_monitor_bridge as bridge_module
from src.ops.qualified_entry_monitor_bridge import QualifiedEntryMonitorBridge


class Worker:
    def __init__(self): self.calls = []
    def _activate_from_qualified_opening(self, item): self.calls.append(dict(item))


class Queue:
    def __init__(self): self.calls = []; self.seen = set()
    def enqueue_qualified_activation(self, *, qualified_entry):
        key = (qualified_entry['operation_id'], qualified_entry['mint'], qualified_entry['entry_timestamp'])
        self.calls.append(dict(qualified_entry)); self.seen.add(key)
        return 'activation:' + qualified_entry['mint']


def entry(**changes):
    value = {'entry_reference_state': 'QUALIFIED', 'operation_id': 'byzantine', 'mint': 'Ah7xh8F2auwkZWh1KHDEjwuabdhKKJt2sCxqBH8mpump',
             'entry_timestamp': 1791112830, 'entry_mc_usd': 3925.9516827750313}
    value.update(changes); return value


@pytest.fixture(autouse=True)
def capability(monkeypatch):
    monkeypatch.setattr(bridge_module, 'monitor_capability_for_operation', lambda operation: {'enabled': True} if operation in {'watchtower', 'byzantine'} else None)


def test_existing_order_is_activate_then_enqueue_without_price_work():
    worker, queue = Worker(), Queue()
    result = QualifiedEntryMonitorBridge(worker, queue).activate(entry())
    assert result == {'status': 'ACTIVATED', 'activation_id': 'activation:Ah7xh8F2auwkZWh1KHDEjwuabdhKKJt2sCxqBH8mpump', 'universal_status': 'FORWARD_MONITOR_DISABLED'}
    assert len(worker.calls) == len(queue.calls) == 1
    assert worker.calls[0]['entry_timestamp'] == 1791112830
    assert worker.calls[0]['entry_mc_usd'] == 3925.9516827750313


@pytest.mark.parametrize('changes,error', [
    ({'entry_reference_state': 'PENDING'}, 'UNQUALIFIED_ENTRY_REFERENCE'), ({'mint': ''}, 'MISSING_MONITOR_IDENTITY'),
    ({'entry_timestamp': None}, 'MISSING_QUALIFIED_ENTRY_TIMESTAMP'), ({'entry_mc_usd': None}, 'MISSING_QUALIFIED_ENTRY_VALUE'),
    ({'operation_id': 'unknown'}, 'MISSING_OPERATION_MONITOR_CAPABILITY'), ({'terminal': True}, 'TERMINAL_ENTRY_CANNOT_ACTIVATE'),
])
def test_bridge_fails_before_activation_for_invalid_opening(changes, error):
    worker, queue = Worker(), Queue()
    with pytest.raises(ValueError, match=error): QualifiedEntryMonitorBridge(worker, queue).activate(entry(**changes))
    assert not worker.calls and not queue.calls


def test_worker_failure_creates_no_queue_activation():
    class FailingWorker(Worker):
        def _activate_from_qualified_opening(self, item): raise RuntimeError('PRECOMMIT_FAILURE')
    worker, queue = FailingWorker(), Queue()
    with pytest.raises(RuntimeError, match='PRECOMMIT_FAILURE'): QualifiedEntryMonitorBridge(worker, queue).activate(entry())
    assert not queue.calls


def test_replay_delegates_idempotency_to_existing_queue_identity():
    worker, queue = Worker(), Queue(); bridge = QualifiedEntryMonitorBridge(worker, queue)
    assert bridge.activate(entry())['status'] == 'ACTIVATED'
    assert bridge.activate(entry())['status'] == 'ACTIVATED'
    assert len(queue.seen) == 1
