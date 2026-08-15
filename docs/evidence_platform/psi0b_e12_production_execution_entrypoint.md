# PSI0B-E12 committed production execution entrypoint

PSI0B-E12 closes the composition gap exposed by authorization `-07`. The
committed `scripts/run_psi0b_production_shadow.py` inserts its repository root
before every `src` import and now supports both validation-only bootstrap and
an explicit execution mode.

Execution accepts only caller-supplied immutable authorization and preflight
artifacts, existing new-empty consumption and observer-attempt directories, an
exact absent output directory, and a new attempt-audit path. It composes the
E11 production telemetry observer with the replay-verified launcher and bounded
production runner. Authorization consumption remains after PRESTART PASS and
before the sole executor call. Each active checkpoint is recorded before its
source opens. Bundle replay and immediate post-run health occur before the
entrypoint reports success.

Every success or failure writes a canonical fsynced attempt audit. The
entrypoint adds no retry, pagination, failover, widening, write, DDL, provider,
integration, or activation authority.

Contract digest:
`bdb671bebd8f58311efc071a34ea67cf95d7d043c1e8b5b235398d78fe55c485`.
