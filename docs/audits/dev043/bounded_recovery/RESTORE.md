# Bounded recovery restoration candidate

Source: Git revision containing this document. Parent8475b337 preserves writer,nullable-price,read-only schema/cache/trace and UNKNOWN budget fixes. Request correction aaaaec30 changes only treasury_review.html request lifecycle,not the query or classifications. Final acceptance and baseline tag are recorded in material handoff; this file alone is not a PASS.

Use a clean detached worktree of DEV043_BOUNDED_RECOVERY_BASELINE only once qualified. Interpreter: /Users/kevinkeaveney/anaconda3/envs/algotrader/bin/python (Python3.11). Observed dependencies: Flask3.1.2,Werkzeug3.1.4,Jinja2 3.1.3,requests2.31.0,urllib3 2.2.1. Exact loaded external files: dependency_hashes.json; project files: project_modules.json. This is a local-environment recovery reference,not a portable package lock.

Create a fresh disposable state directory and use unused localhost8145. Run the following from checkout,substituting a fresh STATE path via explicit environment values. First run the unchanged bootstrap with --self-test and retain only its last JSON line in STATE/preflight.json. Then start:

    env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin HOME=/private/tmp/dev043-pending-clean-state DEV043_STATE_ROOT=/private/tmp/dev043-pending-clean-state DEV043_PORT=8145 DEV043_ISOLATION_RECEIPT=/private/tmp/dev043-pending-clean-state/preflight.json /Users/kevinkeaveney/anaconda3/envs/algotrader/bin/python -I -B docs/audits/dev043/isolation_bootstrap.py

Preflight command is identical,with --self-test and without DEV043_ISOLATION_RECEIPT. Do not import app unless receipt matches state,port and bootstrap hash. Source bootstrap guards are unchanged. Canonical OPS/core and retainedDEVmonitor authority are immutable mode=ro/query_only main-file reads (WAL excluded). Queue is empty disposable STATE/monitor/queue; no operational workers. Outbound socket connections denied. Writes limited to STATE,max single process-written file8MiB. No provider credentials loaded. No large copies/raw payload retention.

Acceptance routes: registry,Watchtower/Byzantine/Nexus details,playbooks,ops-os,SystemHealth. Check retained twenty-row chronology and opening/peak evidence; test manual filters. PendingReview must show UNAVAILABLE at3s with UNKNOWN counts; Confirmed remains usable. API cancellation does not stop server SQL. No automatic retries/polling/overlap; manual navigation/filtering can overlap outstanding server queries. Stop owned API after bounded acceptance. No deployment to8100/Production.

Excluded: MissingCREATE24hcards,Pendingqueryperformance,incompleteactivity/coverage,no currentLIVErows,incompletehistoricalprices,DEV042worker/bridge reproducibility. Legacy/ops missingtemplate remains outside accepted pages; MissionControl's legacyinvestigation link is degraded. Discovery/emerging diagnostics may be unavailable; Nexus navigation can be slow. Retained prices are historical,no freshness claim. Complete application recovery/persistentDEV readiness is not declared.

Rollback: stop only owned isolated API; retain source/reference/state receipts. Parent8475b337 is source rollback only,with known unbounded Pending loading. Never rollback Production/persistentDEV or restore oldmonitor watermark. FutureDEV uses qualified tag or documents anotherparent.

## Detail resource-safety boundary

Byzantine/Nexus HTML and API detail requests now have a three-second server budget in isolated qualification mode. At most one expensive detail executes, zero wait for capacity, and each admits at most four request-owned SQLite connections. Excess capacity and deadline return HTTP503/UNAVAILABLE. SQLite progress/busy budgets and cooperative Python projection checks discard incomplete results, close owned connections and release existing Nexus locks. Client disconnect need not be detected to reclaim detail work at its budget. HTTP transport still uses existing thread-per-request serving; this is not global flood/slow-client protection or an OS-level hard I/O kill. No new worker/cache/queue exists.

Run tests/test_dev043_detail_resource_safety.py and the existing Node Pending lifecycle suite. Bounded baseline accepts explicit detail unavailable states rather than full historical detail functionality. No query/filter/evidence ordering changes. Original funding last-row overwrite/tie limitations remain. Legacy/ops and unavailable historical Health/activity remain excluded/degraded. Pending SQL is still not canceled by browser abort; no automatic retry exists.
