# Watchtower in-place Supervisor composition

`scripts/compose_watchtower_supervisor_config.py` is an offline renderer. It
accepts the current root config, writes a byte-identical backup, and emits a
complete candidate that replaces only the `watchtower_api`,
`operation_monitor_worker`, and `operation_monitor_bridge` program sections. It preserves the listener section
and all unrelated root-config bytes exactly.

The candidate keeps capture and evaluation disabled. The worker remains
disabled (`autostart=false`, `autorestart=false`). Every offline check must
first pass `scripts/validate_supervisor_config_isolated.py` against a flattened
temporary copy whose server and control sockets are both unique and guarded;
offline validation never invokes `supervisorctl`. A later live-transition
approval must review the generated candidate/backup hashes, then apply it
atomically and update only API, worker, and bridge. The listener is not restarted or
re-sourced by this composition.

When the live root has no operation_monitor_worker stanza, the explicit
--api-only mode replaces only watchtower_api. It requires exactly one API and
listener stanza, preserves every non-API byte, and never creates or changes a
worker definition. Adding a worker requires a separately reviewed configuration
authority.
