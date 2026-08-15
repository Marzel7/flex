# PSI0B-E14 executable production-shadow entrypoint

PSI0B-E14 changes only the repository mode of `scripts/run_psi0b_production_shadow.py` from `100644` to `100755`. The script retains the same byte-for-byte content, Python shebang, path-independent repository bootstrap, E12 execution contract, E13 telemetry contract, and query-only authority limits.

The executable boundary permits the committed script to be invoked directly from an arbitrary working directory without an uncommitted wrapper or caller-specific interpreter command. Frozen tests verify the executable mode, direct `--help` invocation, fail-closed validation paths, five-query fixture execution, active stop, executor exception, post-run health, canonical replay, and no-publication failure behavior.

PSI0B-E14 grants no production execution, extraction, integration, or activation authority. Authorization `psi0b-shadow-auth-20260815-09` remains unconsumed and must not be retried.
