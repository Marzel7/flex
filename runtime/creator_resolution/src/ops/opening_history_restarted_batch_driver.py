"""Fresh-process, manifest-driven continuation loop for durable opening-history batches."""
from __future__ import annotations

import json
from pathlib import Path

from .opening_history_batch_executor import ManifestError
from .opening_history_continuation import execute_next_action_from_durable_state
from .opening_history_resume_dispatcher import next_action

VERSION = 'OPENING_HISTORY_RESTARTED_BATCH_DRIVER_V1'


def _active_rows(manifest, state):
    batch_id = state['current_batch_id']
    batch = manifest['batches'][batch_id - 1]['mints']
    by_mint = {row['mint']: row for row in manifest['rows']}
    return batch_id, [by_mint[mint] for mint in batch]


def _handoff(state, mint, predicate):
    for entry in reversed(state.get('ledger', [])):
        if entry['mint'] != mint or entry.get('handoff_digest') not in state.get('compact_handoffs', {}):
            continue
        payload = state['compact_handoffs'][entry['handoff_digest']]['payload']
        if predicate(payload):
            return payload
    raise ManifestError('REQUIRED_COMPACT_HANDOFF_MISSING')


def _materialize_missing_intent(executor, state, row, adapter):
    """Persist the exact request before any transport invocation."""
    mint, phase = row['mint'], state['rows'][row['mint']]
    entries = [entry for entry in state.get('ledger', []) if entry['mint'] == mint]
    if phase == 'PENDING_CREATE_SLOT' and not entries:
        method, params = adapter.build_create_request(row)
        executor.dispatch(state, mint, 'CREATE_TX', method, {'params': params})
        return True
    if phase in {'CREATE_SLOT_QUALIFIED', 'PENDING_CREATE_BLOCK'} and not any(entry['phase'] == 'CREATE_BLOCK' for entry in entries):
        create = _handoff(state, mint, lambda payload: payload.get('signature') == row['create_signature'])
        method, params = adapter.build_block_request(row, create['slot'], 'CREATE_SLOT')
        executor.dispatch(state, mint, 'CREATE_BLOCK', method, {'params': params})
        return True
    if phase.startswith('PENDING_EXTENSION'):
        ordinal = int(phase.rsplit('_', 1)[1])
        if not any(entry['phase'] == f'EXTENSION_{ordinal}' for entry in entries):
            create = _handoff(state, mint, lambda payload: payload.get('signature') == row['create_signature'])
            reservation = state['extension_reservations'].get(f'{mint}:{ordinal}')
            if reservation is None:
                reservation = executor.reserve_extension_record(state, mint, create['slot'], ordinal, f'{mint}:extension:{ordinal}')
            method, params = adapter.build_reserved_extension_request(row, reservation)
            entry = executor.dispatch(state, mint, reservation['request_phase'], method, {'params': params})
            executor.bind_extension_dispatch(state, reservation, entry)
            return True
    return False


def _reserve_needed_extension(executor, state, row):
    """Persist the next reservation only after the prior compact handoff exists."""
    mint = row['mint']
    create = _handoff(state, mint, lambda payload: payload.get('signature') == row['create_signature'])
    blocks = [
        state['compact_handoffs'][entry['handoff_digest']]['payload']
        for entry in state.get('ledger', [])
        if entry['mint'] == mint and entry.get('handoff_digest') in state.get('compact_handoffs', {})
        and 'target_relevant_transactions' in state['compact_handoffs'][entry['handoff_digest']]['payload']
    ]
    ordinal = len(blocks)
    if ordinal not in {1, 2}:
        raise ManifestError('EXTENSION_RESERVATION_ORDINAL_INVALID')
    key = f'{mint}:{ordinal}'
    if key not in state['extension_reservations']:
        executor.reserve_extension_record(state, mint, create['slot'], ordinal, f'{mint}:extension:{ordinal}')
    return key


def resume_batch_until_gate(manifest_path, expected_sha, state_path, executor_factory, adapter_factory,
                            transport_factory, operation_profile, failure_injector=None, *, max_iterations=10000):
    """Reload from disk before every classified action and stop at a durable gate."""
    previous = None
    actions = []
    for _ in range(max_iterations):
        executor = executor_factory(manifest_path, expected_sha, state_path, failure_injector)
        try:
            state = executor.resume()
        except ManifestError as error:
            if str(error) != 'NON_REPLAYABLE_RESPONSE_BOUNDARY':
                raise
            state = json.loads(Path(state_path).read_text())
            return {
                'outcome': 'FAIL_CLOSED',
                'reason': 'NON_REPLAYABLE_RESPONSE_BOUNDARY',
                'actions': actions + ['FAIL_CLOSED'],
                'state': state,
            }
        manifest = executor.verify()
        if state['run_state'] == 'HOLD':
            return {'outcome': 'HOLD', 'actions': actions, 'state': state}
        if state['run_state'] == 'COMPLETE':
            return {'outcome': 'COMPLETE', 'actions': actions, 'state': state}
        batch_id, rows = _active_rows(manifest, state)
        unfinished = next((row for row in rows if state['rows'][row['mint']] != 'TERMINAL'), None)
        if unfinished is None:
            gate = executor.batch_gate(state, batch_id)
            actions.append('BATCH_GATE')
            return {'outcome': gate['decision'], 'actions': actions, 'state': state, 'gate': gate}
        transport = transport_factory()
        adapter = adapter_factory(transport, operation_profile)
        action = next_action(state, unfinished['mint'])
        if action == 'HOLD':
            return {'outcome': 'HOLD', 'actions': actions + [action], 'state': state}
        if action == 'FAIL_CLOSED':
            return {'outcome': 'FAIL_CLOSED', 'reason': 'NON_REPLAYABLE_RESPONSE_BOUNDARY', 'actions': actions + [action], 'state': state}
        if action == 'TERMINAL_NOOP':
            # Selection always skips terminal rows, so this is an invariant failure.
            state['run_state'] = 'HOLD'; state['hold_reasons'] = ['DRIVER_STALLED_TERMINAL_SELECTION']; executor.commit(state)
            return {'outcome': 'FAIL_CLOSED', 'reason': 'DRIVER_STALLED_TERMINAL_SELECTION', 'actions': actions + [action], 'state': state}
        before = state['checkpoint_digest']
        materialized = _materialize_missing_intent(executor, state, unfinished, adapter)
        if materialized:
            actions.append(action)
            continue
        result = execute_next_action_from_durable_state(executor, state, unfinished, adapter, transport)
        actions.append(action)
        if isinstance(result, dict) and result.get('result', {}).get('needs_extension'):
            _reserve_needed_extension(executor, state, unfinished)
        persisted = json.loads(Path(state_path).read_text())
        progress = persisted['checkpoint_digest'] != before
        marker = (before, action)
        if not progress and marker == previous:
            persisted['run_state'] = 'HOLD'; persisted['hold_reasons'] = ['DRIVER_STALLED_NO_DURABLE_PROGRESS']
            executor.commit(persisted)
            return {'outcome': 'FAIL_CLOSED', 'reason': 'DRIVER_STALLED_NO_DURABLE_PROGRESS', 'actions': actions, 'state': persisted}
        previous = marker
    executor = executor_factory(manifest_path, expected_sha, state_path, failure_injector)
    state = executor.resume(); state['run_state'] = 'HOLD'; state['hold_reasons'] = ['DRIVER_ITERATION_LIMIT']; executor.commit(state)
    return {'outcome': 'FAIL_CLOSED', 'reason': 'DRIVER_ITERATION_LIMIT', 'actions': actions, 'state': state}
