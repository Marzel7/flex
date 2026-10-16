# DEV043 restoration boundary

Branch: codex/dev043-integration-baseline. Original rich-UI parent cb882091bbd402ad826fc44cc82541a0e3d4b2ca; attempted baseline41bf4dbf; exact writer correctione9745d8d. Source_composition_manifest.json preserves parent tree and exact imports. Latest qualified status is compatibility_status.json, not the historical first-attempt qualification_status.json.

This candidate remains HOLD at Watchtower nullable-price template rendering. No release or clean-checkout restart is qualified. No deployment occurred, so rollback requires no service action. Restore candidate source only into an isolated checkout. Do not touch DEV041 snapshot or existing services. Never activate archived bridge watermark or monitoring sources automatically.

Use the recorded Anaconda algotrader interpreter and observed_dependencies.json; this is an observed environment, not a certified portable lock. Set DEV043_STATE_ROOT to a dedicated disposable directory and DEV043_PORT to unused localhost8143. Run docs/audits/dev043/isolation_bootstrap.py --self-test with env-i first; save its final JSON line as receipt. Then use the same environment, plus DEV043_ISOLATION_RECEIPT pointing to that JSON, to run the bootstrap with --probe-startup (import-only) or without it (API). Recheck guard/receipt hashes after any bootstrap change.

Current bootstrap asserts retained canonical mode OFF, creates only an empty metadata cache from existing exact DDL, and creates empty dedicated queue directories. Canonical core, canonical OPS and retained monitor database are allowlisted immutable read-only main-file views; WAL excluded. No operational workers or live queue consumers. Provider transport denied. Stop isolated API after acceptance. Do not treat startup alone as complete application qualification.


## Nullable-price correction / clean-run reproduction

Tested source revision:02e9e19ed6dfc7b6043b55dd1250d238b7b4976d. Clean detached checkout:/private/tmp/flex-dev043-clean-restart. Fresh disposable state:/private/tmp/dev043-clean-state. Guard receipt:/private/tmp/dev043-clean-state/preflight.json. This source is not a frozen complete recovery baseline; see DEV043_PLAYBOOK_COMPATIBILITY_REPORT.md.

Exact startup command from clean checkout:

```
env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin HOME=/private/tmp/dev043-clean-state DEV043_STATE_ROOT=/private/tmp/dev043-clean-state DEV043_PORT=8143 DEV043_ISOLATION_RECEIPT=/private/tmp/dev043-clean-state/preflight.json /Users/kevinkeaveney/anaconda3/envs/algotrader/bin/python -I -B docs/audits/dev043/isolation_bootstrap.py
```

Generate receipt first with same environment and `--self-test`, retaining its final JSON line. Verify current mode still matches retained OFF and that port8143 is unused. Run test_playbook_nullable_prices.py and check_isolated_api.py with the recorded interpreter. The bootstrap writes a bounded loaded_source_receipt.json in disposable state on shutdown, containing loaded module hashes and denied operations. Validate all project module paths against git ls-files, and require a clean checkout. No operational worker or queue consumer is enabled.

The nullable formatting fix can be rolled back by restoring only templates/operation_artifact_playbook.html from2bf6f970 in an isolated checkout. Do not deploy it or reset other work. Both isolated APIs are stopped. Subsequent active-page read-only side effects must qualify before any frozen baseline or persistent DEV readiness is claimed.
