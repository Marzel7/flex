# Watchtower in-place Supervisor composition

`scripts/compose_watchtower_supervisor_config.py` is an offline renderer. It
accepts the current root config, writes a byte-identical backup, and emits a
complete candidate that replaces only the `watchtower_api` and
`operation_monitor_worker` program sections. It preserves the listener section
and all unrelated root-config bytes exactly.

The candidate keeps capture and evaluation disabled. The worker remains
disabled (`autostart=false`, `autorestart=false`). Every offline check must
first pass `scripts/validate_supervisor_config_isolated.py` against a flattened
temporary copy whose server and control sockets are both unique and guarded;
offline validation never invokes `supervisorctl`. A later live-transition
approval must review the generated candidate/backup hashes, then apply it
atomically and update only API and worker. The listener is not restarted or
re-sourced by this composition.
