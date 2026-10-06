#!/bin/sh
set -eu
mode=$1; expected=$2; selection=${3:-}
root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd -P)
PYTHONPATH="$root" /Users/kevinkeaveney/anaconda3/envs/algotrader/bin/python "$root/scripts/verify_watchtower_final_runtime.py" "$expected" >/dev/null
case "$mode" in
 worker) exec "$root/scripts/run_dev_005a_monitor.sh" --dev-soak-selection "$selection" ;;
 ui) : "${WATCHTOWER_OFFSET_AUDIT_LEDGER_PATH:=$root/docs/audits/watchtower-opening-offset-audit.v2.json}"; export WATCHTOWER_OFFSET_AUDIT_LEDGER_PATH; exec /Users/kevinkeaveney/anaconda3/envs/algotrader/bin/python -m src.dev_runtime ;;
 *) exit 64 ;;
esac
