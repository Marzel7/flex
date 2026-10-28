"""Explicit, forward-discontinuity-only recovery through the existing gate owner.

No provider transport, worker control, allowance initialization or automatic retry.
Caller preserves private immutable preimages before invoking this operator action.
"""
import copy
import hashlib


def clock_sample(gate):
    state = gate.read()
    return dict(generation=gate.generation, wall=int(gate.clock()),
                mono=int(gate.monotonic()), anchor_wall=state['rpc_budget']['wall'],
                anchor_mono=state['rpc_budget']['mono'],
                state_sha256=hashlib.sha256(gate.path.read_bytes()).hexdigest())


def reconcile_forward_clock(gate, *, expected_sha256, first_sample):
    """CAS two anchors only, after all time-dependent RPC protections elapsed.

    Pending/uncertain reservations are never released. No outcome is inferred.
    Genuine restart/reboot, backward time, moving clocks or live probes refuse.
    """
    def refuse(reason):
        raise RuntimeError('HELIUS_CLOCK_RECOVERY_' + reason)
    if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
        refuse('AUTHORITY')
    with gate.locked():
        state = gate.read()  # Existing checksum, schema, generation and quota checks.
        before_raw = gate.path.read_bytes()
        if hashlib.sha256(before_raw).hexdigest() != expected_sha256:
            refuse('STATE_CHANGED')
        budget = state['rpc_budget']
        if budget.get('version') != 2 or budget['policy']['lifetime_credits'] != 100000:
            refuse('POLICY')
        if state['state'] != 'OPEN' or budget['inflight']:
            refuse('NOT_IDLE_OPEN')
        now = int(gate.clock()); mono = int(gate.monotonic())
        if not isinstance(first_sample, dict) or first_sample.get('generation') != gate.generation:
            refuse('SAMPLE_AUTHORITY')
        if first_sample.get('state_sha256') != expected_sha256 or (first_sample.get('anchor_wall'), first_sample.get('anchor_mono')) != (budget['wall'], budget['mono']):
            refuse('SAMPLE_AUTHORITY')
        if any(type(first_sample.get(k)) is not int for k in ('wall','mono')):
            refuse('SAMPLE_CLOCK')
        dw = now-first_sample['wall']; dm = mono-first_sample['mono']
        if min(dw, dm) < 5 or abs(dw-dm) > 1:
            refuse('UNSTABLE_CLOCK')
        wall_elapsed = now-budget['wall']; mono_elapsed = mono-budget['mono']
        if min(wall_elapsed, mono_elapsed) < 0 or wall_elapsed < mono_elapsed:
            refuse('BACKWARD_OR_REBOOT')
        if abs(wall_elapsed-mono_elapsed) <= 5:
            return dict(status='ALREADY_VALID', mutated=False)
        # Do not weaken request freshness or rolling request-rate protections.
        # With no in-flight request, every retained request was admitted/finished
        # no later than the last qualified budget anchor. Use the lesser elapsed
        # clock and a five-second safety margin, never the unproven wall gap.
        horizon = max([120] + [max(60, c['freshness_seconds']) for c in budget['policy']['costs'].values()])
        if min(wall_elapsed, mono_elapsed)-5 < horizon:
            refuse('COOLDOWN_UNPROVEN')
        if budget['rate']['start'] > budget['wall'] or any(k['at'] > budget['wall'] for k in budget['keys'].values()):
            refuse('TIMESTAMP_AUTHORITY')
        if any(state[k] > now for k in ('next_at','probe_until','recover_until')):
            refuse('ACTIVE_GATE_TIMER')
        previous = copy.deepcopy(state)
        budget['wall'] = now; budget['mono'] = mono
        expected = copy.deepcopy(previous)
        expected['rpc_budget'].update(wall=now, mono=mono)
        if state != expected:
            refuse('UNEXPECTED_DELTA')
        gate.save(state)  # Existing PENDING/READY, fsync and atomic publication.
        after = gate.read()
        if after != expected:
            refuse('READBACK_MISMATCH')
        return dict(status='RECONCILED_ANCHORS_ONLY', mutated=True,
                    before_sha256=expected_sha256,
                    after_sha256=hashlib.sha256(gate.path.read_bytes()).hexdigest(),
                    old_wall=previous['rpc_budget']['wall'], old_mono=previous['rpc_budget']['mono'],
                    new_wall=now, new_mono=mono,
                    minimum_proven_elapsed_seconds=min(wall_elapsed,mono_elapsed),
                    maximum_cooldown_seconds=horizon)
