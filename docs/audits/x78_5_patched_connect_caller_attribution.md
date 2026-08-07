# X78.5 — Raw sqlite3.connect Lease-Leak Root-Cause Audit (in progress)

## Status: root cause NOT yet identified. Diagnostic fix shipped to unblock identification.

## Summary

X78.4's live sanity window revealed a fifth, still-unidentified permanent
`NestedDatabaseWriteError` source: `outer_command=db_locking.py:718 in
_patched_connect`, which never resolves — X78.4's own retry mechanism
correctly exhausts after 8 attempts (~91s) and the self-kill guard
correctly restarts the process, but the underlying leak recurs, causing
a crash-loop (3 restarts observed in ~20 minutes) before the current
instance stabilized into a permanent-but-not-crashing stall (91s wasted
per cycle, zero queue progress, for 56+ minutes observed).

## Phase 1: frozen failure signature

- `outer_command=db_locking.py:718 in _patched_connect`
- `inner_command=creator_funding_worker.py:117 in _db_connect`
- Continuously reproducing across every retry cycle since the process
  started (56+ minutes observed on pid `79908`).
- Immediately preceding context (pid `79908`'s first cycle): job
  `Comonf3EVDhM`/`HbG4QXTcpD9ixBUY` timed out at 90s during domain
  resolution (`sns_primary_domains` RPC calls active), hit the exact
  X78.4 cancellation-grace-period-overrun log line ("did not finish
  cleanup within 10s of cancellation"), retried, then a second job
  (`3CFX8twc3NB2`) completed successfully — and the permanent collision
  began immediately after.

## Phase 6: process boundary — ruled out

`_thread_write_lease` is `threading.local()`, scoped per-OS-thread within
a single process's memory space; it cannot leak across process
boundaries. Confirmed other processes sharing the DB
(`ws_cascade`, `pumpfun_curve_listener`, `walkback_worker`,
`creator_resolution_worker`) are running concurrently but are
structurally incapable of poisoning `creator_funding_worker`'s own
thread-local state. The leak is internal to `creator_funding_worker`'s
own process.

## Phase 5: raw sqlite3.connect census (partial — see gap below)

An AST sweep across every module reachable from the extraction/
enrichment hot path found several functions calling `sqlite3.connect()`
without a `try/finally` or `with` block:

| File | Function | Reachable from creator-funding hot path? | Disposition |
|---|---|---|---|
| `cursor_manager.py` | `get_addresses_due_for_scan`, `mark_failed`, `mark_paused`, `resume`, `get_stats` | No — scheduler-only API, not called during extraction | Not investigated further (out of this bug's path) |
| `blocksec_aml_batcher.py` | `get_cached_label`, `get_batch_stats` | No — unused in this pipeline (only `auto_batch_new_addresses`/`submit_batch` are called, both already fixed in X78.0) | Ruled out for this bug; hygienic issue only |
| `post_launch_automation.py` | `_tag_creator_from_funding_patterns` | No — dead code, `return False` before the leaking block | Ruled out |
| `second_hop_builder.py` | `_is_enabled` | **Yes** — called from `SecondHopExpansionBuilder.build()`, reachable via `_enqueue_second_hop_lite` | SELECT-only query; cannot acquire the write lease per the established rule (SELECT/PRAGMA never trigger `_acquire_write_lane`). Real bug (connection HANDLE leak, contributes to `_open_handle_count()`) but not a match for `NestedDatabaseWriteError` specifically. **Not fixed in this pass — flagged for a hygiene follow-up.** |
| `intelligence_refresh.py` | `_db` | N/A — this is a factory function, not a leak by itself; its only call site reachable from the hot path (`_post_extraction_intelligence_refresh`'s `irc_conn`) already has correct `try/finally` | Ruled out |
| `relationship_events.py` | `get_recent_events` | No — Flask-route-only, not called during extraction | Ruled out |
| `domain_mapping.py` | (not flagged by AST sweep — already correctly guarded) | Yes — `_ensure_table`, `register_domain`, `link_domain_to_address`, `init_domain_registry` all called during extraction | All already correctly fixed (X78.0); re-verified directly, no gap found |
| `build_networks_release.py` | (not flagged — uses a proper `@contextmanager`) | Yes — called from `_post_extraction_intelligence_refresh` | Clean |

**No definitive match for a WRITE-capable, hot-path-reachable, unguarded
raw `sqlite3.connect()` call was found through this static sweep.**
Every candidate that IS reachable from the path where the stall began
(domain resolution → `_flush_page_batch` → post-extraction enrichment)
was already correctly fixed in prior X78.0-X78.4 work, or is read-only
and therefore structurally incapable of triggering
`NestedDatabaseWriteError`.

## Why this audit could not reach Phase 11's verdict this pass

Live process instrumentation (the task's Phase 2 ask) requires either
attaching a debugger to the running process (blocked: no root access for
`py-spy` in this environment) or restarting with instrumentation already
in place and waiting for the next recurrence. Given the leak recurred
reliably within the first 1-2 jobs of each restart observed so far, the
second approach is viable but was not completed within this session —
the instrumentation is now shipped (see below) and the next live
recurrence will surface the actual caller directly in the
`NestedDatabaseWriteError` message itself, without needing a separate
instrumentation pass.

## What was actually fixed: caller-attribution diagnostic gap

`db_connect()`'s caller-detection (`inspect.stack()[1]`) identifies
whoever is one frame above it. When entered via the global `sqlite3.connect()`
monkeypatch (`_patched_connect`), that one frame above is **always**
`_patched_connect` itself — not the real code that wrote
`sqlite3.connect(...)`. This is why every `NestedDatabaseWriteError`
raised against a connection opened this way was tagged
`db_locking.py:718 in _patched_connect`, identifying the interception
point rather than the source, making the error message itself
unactionable — exactly the gap this whole investigation ran into.

Fixed in `db_connect()`: when the immediately-calling frame's function
name is `_patched_connect`, walk one frame further to find the real
caller. This is a pure diagnostic improvement — it does not change any
locking, retry, or write-guard semantics, and does not touch
`_patched_connect` itself (per the task's explicit instruction not to
alter that global mechanism without proof it's faulty — it isn't; it's
correctly redirecting, just poorly attributing).

**Validated**: confirmed directly that a synthetic caller going through
the monkeypatch is now correctly attributed by name, and that direct
`db_connect()` calls (not via the monkeypatch) are unaffected.

## Validation

- `tests/test_x78_5_patched_connect_caller_attribution.py` (2 tests) —
  confirms the fix and non-regression for the direct-call path.
- All 23 tests across X78.2, X78.3, X78.4, and X78.5 pass together in
  one run.
- `git diff` scoped to `db_locking.py` (one function, ~10 lines added)
  plus the new test file; no changes to `_patched_connect`,
  `TrackedConnection`, `_thread_write_lease`, or `NestedDatabaseWriteError`.

## Root-cause ledger (cumulative, X78.0-X78.5)

| Mechanism | Status |
|---|---|
| Individual connection/transaction leaks (25 fixes, X78.0) | FIXED / historical |
| Detached background descendants (X78.2) | FIXED |
| RPCCache same-job nested ownership (X78.3) | FIXED |
| Cancellation grace-period overrun (X78.4) | FIXED (via retry/isolation) |
| Raw sqlite3.connect permanent lease leak, `outer_command=_patched_connect` | **NOT YET IDENTIFIED** — diagnostic gap that made this untraceable is now fixed; root cause is the next thing the improved error message will reveal |
| `SecondHopExpansionBuilder._is_enabled()` connection handle leak (not lease) | Identified, not fixed this pass — hygiene follow-up, does not explain `NestedDatabaseWriteError` |

## Production readiness verdict (Phase 21)

**NOT READY.** The permanent `_patched_connect`-attributed leak remains
unresolved. This pass shipped the diagnostic fix required to identify it
on the next occurrence, but did not reach Phase 11's root-cause verdict
or Phase 12's repair. Per the task's explicit instruction ("No
speculative changes... If failure recurs: NOT READY and report the next
exact source"), no further repair was attempted without proof.

## Next step

Redeploy with the caller-attribution fix. The next live recurrence of
this collision will report the real caller directly in the
`NestedDatabaseWriteError` log line, at which point the actual leak site
can be identified and fixed with the same reproduce-first discipline
used in X78.2-X78.4, without further guessing.

## Commit

Local commit only, not pushed, per task instruction.
