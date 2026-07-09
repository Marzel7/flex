# VACUUM Maintenance Window — flex_complete_database.db

## Status (2026-07-02)
Rows deleted, ~2.1GB in freelist, VACUUM pending.

Already done (live, no downtime):
- rpc_response_cache: 1,732 rows deleted → ops DB 1.5GB → 628MB
- network_score_history: 4,133,678 rows deleted (kept top 2 build versions only)
- risk_score_history: 756,915 rows deleted

Pending: VACUUM flex_complete_database.db (9.9GB → ~7.8GB)
Target latency improvement: treasury-review 3.1s → sub-second

## Gate conditions (ALL must be true)
- No LIVE_ARMED sessions in wt_ops_v2.db
- ProgramWatcher idle / no active candidates
- Migration traffic low (check pumpfun_curve_listener log cadence)
- No active WATCHTOWER wave in progress

## Procedure

```bash
# 1. Verify gate conditions
sqlite3 database/wt_ops_v2.db "SELECT COUNT(*) FROM wt_active_subprov_sessions WHERE state='LIVE_ARMED'"
sqlite3 database/wt_ops_v2.db "SELECT COUNT(*) FROM wt_candidate_websocket_watches WHERE state='WATCHING' AND expires_at > strftime('%s','now')"

# 2. Stop non-essential readers
kill -TERM 19505   # pumpfun_curve_listener (check PID first: ps aux | grep curve_listener)
# gunicorn can stay up — it will busy-wait on VACUUM, not block it

# 3. Run VACUUM (takes 5-10 min on ~10GB)
sqlite3 database/flex_complete_database.db "PRAGMA busy_timeout=600000; VACUUM;"

# 4. Restart curve_listener
python -u -m src.core.pumpfun_curve_listener &

# 5. Verify size reduction
wc -c database/flex_complete_database.db | awk '{printf "%.2fGB\n", $1/1024/1024/1024}'

# 6. Check treasury-review latency
curl -s -o /dev/null -w "%{time_total}s\n" http://localhost:5002/api/ops-v2/intel/treasury-review
```

## After VACUUM — enable auto_vacuum to prevent recurrence
```sql
-- Run once after VACUUM (requires VACUUM to take effect)
PRAGMA auto_vacuum=INCREMENTAL;
VACUUM;
```

## Tables to monitor for future growth
- rpc_response_cache (ops DB): prune >24h weekly
- network_score_history: keep MAX(build_version)-1 only
- risk_score_history: truncate if writer is inactive
- funder_networks (2.6GB archived to flex_investigation_archive.db): DELETE+VACUUM still pending via reclaim_funder_networks_space.py
