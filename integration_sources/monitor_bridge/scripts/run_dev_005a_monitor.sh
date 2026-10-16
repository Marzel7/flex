#!/bin/sh
# Bounded DEV-005A Monitor launcher.  It intentionally sources only the DEV
# credential file, then reconstructs a minimal environment with DEV-only
# writable state.  It never forwards the parent Supervisor environment.
set -eu

SELECTION_PATH=""
if [ "$#" -gt 0 ]; then
  if [ "$#" -ne 2 ] || [ "$1" != "--dev-soak-selection" ]; then
    echo "usage: $0 [--dev-soak-selection PATH]" >&2; exit 64
  fi
  SELECTION_PATH="$2"
fi

# Resolve from this script's checkout rather than a shared mutable workspace.
# The Supervisor entrypoint may be switched to a pinned worktree without
# allowing its imports or writable DEV state to drift back to another clone.
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)"
STATE_ROOT="${MONITOR_RUNTIME_STATE_ROOT:-$ROOT}"
ENV_FILE="${MONITOR_ENV_FILE:-$ROOT/.env}"
PREFLIGHT_NO_TRANSPORT="${MONITOR_PREFLIGHT_NO_TRANSPORT:-0}"
CLAIM_AUTHORITY_DB_PATH="${MONITOR_CLAIM_AUTHORITY_DB_PATH:-}"
MONITOR_MAX_ITERATIONS_VALUE="${MONITOR_MAX_ITERATIONS:-}"
MONITOR_PROVIDER_GLOBAL_LIMIT_VALUE="${MONITOR_PROVIDER_GLOBAL_LIMIT:-}"
MONITOR_PROVIDER_TOKEN_LIMIT_VALUE="${MONITOR_PROVIDER_TOKEN_LIMIT:-}"
DEFAULT_SELECTION_PATH="$STATE_ROOT/docs/audits/dev_005a_final_population_selection.v1.json"
SELECTION_PATH="${SELECTION_PATH:-$DEFAULT_SELECTION_PATH}"

# Explicit selection is an authority boundary: validate it before credentials
# or the isolated runtime are constructed, and never fall back silently.
/Users/kevinkeaveney/anaconda3/envs/algotrader/bin/python - "$SELECTION_PATH" <<'PY'
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as source: payload=json.load(source)
    assert payload.get("mode") == "DEV_005_ISOLATED_SOAK"
    assert isinstance(payload.get("allowlist"), list) and all(isinstance(x, str) and x for x in payload["allowlist"])
except Exception:
    raise SystemExit("INVALID_DEV_SOAK_SELECTION_PATH")
PY

# Python puts the current directory ahead of PYTHONPATH.  Normalize it before
# any interpreter invocation so a Supervisor wrapper or manual caller cannot
# shadow the pinned runtime with an unrelated checkout.
cd "$ROOT"

# Provider-free predeployment proof: import only the actual Monitor service
# closure from this checkout.  It intentionally neither reads credentials nor
# constructs a queue, provider binding, or worker loop.
if [ "${MONITOR_LAUNCHER_IDENTITY_ONLY:-0}" = "1" ]; then
  exec /usr/bin/env -i \
    PATH="/Users/kevinkeaveney/anaconda3/envs/algotrader/bin:/usr/bin:/bin" \
    PYTHONPATH="$ROOT" \
    MONITOR_RUNTIME_ROOT="$ROOT" \
    MONITOR_RUNTIME_STATE_ROOT="$STATE_ROOT" \
    /Users/kevinkeaveney/anaconda3/envs/algotrader/bin/python -c '
import json, os
import src.ops.operation_monitor_service as service
import src.ops.operation_monitor_worker as worker
print(json.dumps({"runtime_root": os.environ["MONITOR_RUNTIME_ROOT"], "state_root": os.environ["MONITOR_RUNTIME_STATE_ROOT"], "service": service.__file__, "worker": worker.__file__}, sort_keys=True))
'
fi

set -a
. "$ENV_FILE"
set +a

: "${BIRDEYE:?BIRDEYE_REQUIRED}"
: "${HELIUS_RPC_URL:?HELIUS_RPC_URL_REQUIRED}"

exec /usr/bin/env -i \
  PATH="/Users/kevinkeaveney/anaconda3/envs/algotrader/bin:/usr/bin:/bin" \
  PYTHONPATH="$ROOT" \
  WT_OPS_DB_PATH="$STATE_ROOT/.dev_runtime/monitor/dev_005a/wt_ops_v2.dev.db" \
  DATABASE_PATH="$STATE_ROOT/.dev_runtime/monitor/dev_005a/wt_ops_v2.dev.db" \
  DB_PATH="$STATE_ROOT/.dev_runtime/monitor/dev_005a/monitor_aux.dev.db" \
  FLEX_DB_PATH="$STATE_ROOT/.dev_runtime/monitor/dev_005a/monitor_aux.dev.db" \
  RPC_METRICS_DB="$STATE_ROOT/.dev_runtime/monitor/dev_005a/monitor_aux.dev.db" \
  OPERATION_MONITOR_QUEUE_PATH="$STATE_ROOT/.dev_runtime/monitor/dev_005a/queue" \
  OPERATION_MONITOR_CANONICAL_MIGRATION_DB_PATH="$STATE_ROOT/database/flex_complete_database.db" \
  OPERATION_MONITOR_CANONICAL_BIRTH_DB_PATH="$STATE_ROOT/database/wt_ops_v2.db" \
  OPERATIONS_MODE="MONITOR" \
  MONITOR_RUNTIME="dev" \
  OPERATION_MONITOR_PRICE_MODE="HISTORICAL" \
  OPERATION_MONITOR_FAIR_SCHEDULER="true" \
  DEV_005_SOAK_SELECTION_PATH="$SELECTION_PATH" \
  MONITOR_MAX_ITERATIONS="$MONITOR_MAX_ITERATIONS_VALUE" \
  MONITOR_PROVIDER_GLOBAL_LIMIT="$MONITOR_PROVIDER_GLOBAL_LIMIT_VALUE" \
  MONITOR_PROVIDER_TOKEN_LIMIT="$MONITOR_PROVIDER_TOKEN_LIMIT_VALUE" \
  MONITOR_PREFLIGHT_NO_TRANSPORT="$PREFLIGHT_NO_TRANSPORT" \
  MONITOR_CLAIM_AUTHORITY_DB_PATH="$CLAIM_AUTHORITY_DB_PATH" \
  SQLITE_LIFECYCLE_LOG="$STATE_ROOT/.dev_runtime/monitor/dev_005a/logs/sqlite_lifecycle.jsonl" \
  DB_CONNECTION_LIFECYCLE_LOG="$STATE_ROOT/.dev_runtime/monitor/dev_005a/logs/connections.jsonl" \
  DB_WRITE_DIAGNOSTICS_PATH="$STATE_ROOT/.dev_runtime/monitor/dev_005a/logs/writes.jsonl" \
  OPERATION_MONITOR_IDLE_SECONDS="5" \
  BIRDEYE="$BIRDEYE" \
  BIRDEYE_CREDENTIAL_LABEL="BIRDEYE" \
  HELIUS_RPC_URL="$HELIUS_RPC_URL" \
  HELIUS_API_KEY="${HELIUS_API_KEY:-}" \
  /Users/kevinkeaveney/anaconda3/envs/algotrader/bin/python -m src.ops.operation_monitor_service
