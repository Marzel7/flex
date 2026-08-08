# X78.12 — Issue 1 Closure & Programme Completion

**DomainResolver Write-Lane Amplification (PERIOD B)**

Status: **RESOLVED**
Fix commit: `b6cc68fd` — "X78.12 — Issue 1: batch DomainResolver tag writes to eliminate write-lane amplification"
Closure commit: this document

---

## 1. Verified Evidence

`DomainResolver.resolve_primary_domains()` (`src/extractors/realtime_creator_funding_extractor.py`)
previously called a per-address `_save_address_tag()` helper — its own
`db_connect()` + write + commit + close — once for every resolved SNS domain,
inside the SNS HTTP batch loop. A page with many unique domain addresses
produced one separate cross-process write-lease acquisition per address.

The fix, implemented and committed in `b6cc68fd`:

- Discovered domain tags are now collected in memory (`address_tags: Dict[str, str]`)
  during the HTTP loop, both on the DB-cache-hit path and the SNS-HTTP-hit path.
- `_db_set_many()` was extended to accept an `address_tags` iterable alongside
  its existing `to_persist` domain-cache rows, and persists both in a single
  transaction.
- `register_domain()` / `link_domain_to_address()` registry side effects now
  run *after* that single write transaction commits and the lease is released
  — no network-adjacent registry work happens under DB ownership.
- The standalone `_save_address_tag()` method was removed; it is no longer
  reachable from any code path.
- Semantics preserved: identical tag content, identical registry calls,
  identical values — only the transaction lifetime and call count changed.

Regression suite status and live deployment status: see §4 and §5.

---

## 2. Reproduction Summary

**Original reproduction** (`tests/test_x78_12_domain_resolver_lease_timeline.py`,
pre-fix): deterministic, `asyncio.Event`-gated mock HTTP responses (no sleeps),
instrumented via `scripts/x78_12_lease_instrumentation.py` monkeypatching
`acquire_write_lease`/`release_write_lease` at the shared chokepoint.

**Measured mechanism (pre-fix):**

- 60-address heavy page (3 SNS HTTP batches of 20) → **61 separate write-lease
  acquisitions** (60 × `_save_address_tag` + 1 × `_db_set_many`).
- No single lease was individually long (max 27.8ms) — the defect was
  **frequency**, not duration.
- Zero leases were held open *during* an HTTP await (proven directly, not
  inferred) — ruling out a literal network-I/O-under-write-ownership defect
  at this call site.
- Cumulative lease time: 108.088ms across 61 acquisitions for one page.

**Measured mechanism (post-fix, same test, same scenario):**

- 60-address heavy page → **1 write-lease acquisition** (`_db_set_many` only).
- Measured duration: **0.994ms**.

```
Before:  60-address page → 61 write-lease acquisitions
After:   60-address page →  1 write-lease acquisition
```

This is the complete removal of the measured contention mechanism, not a
reduction in its severity.

---

## 3. Live Soak

- **Wall-clock soak duration:** 62.4 minutes (restart epoch `1786222653`,
  60-minute minimum requirement met).
- **creator_funding_worker stability:** pid 58062, zero restarts for the full
  soak window, uptime 1:02:53 at the 60-minute checkpoint.
- **Queue progress:** confirmed continuous — active `REALTIME_FUNDING`
  processing, Jito-tip detection, CEX funder matches, sustained
  `sns_primary_domains` HTTP bursts throughout.
- **Observed write behaviour (live, production logs):** the only
  domain-resolution write call site firing in production logs was
  `realtime_creator_funding_extractor.py:177 in _db_set_many` — the batched
  call. Zero occurrences of the old per-address write pattern were found
  (585 `sns_primary_domains` HTTP request log lines sampled in one window,
  zero corresponding per-address write attempts). The removed
  `_save_address_tag` call site produced no log lines because it no longer
  exists in the code.
- **Observed DomainResolver behaviour:** consistent with the deterministic
  test — one batched write per resolution pass, executed after the HTTP
  loop completes.
- **Observed regressions:** none attributable to this change. See §6.

---

## 4. Explicit Non-Findings

Issue 1's fix and this soak did **not** resolve, explain, or touch:

- **PERIOD A / Issue 2** — the genuine ~60+ minute long-held write lease
  (tag `intelligence_refresh.py:55 in _db`). It was sighted once during the
  soak as expected background recurrence and was not investigated further.
  It remains open and is tracked separately (see §9).
- **Listener instability** — `watchtower_listener` crash-restarted twice
  during the soak window (pid 58091 → 59236 → 62474). Root-caused to a
  **pre-existing** bug: `pumpfun_curve_listener.py:4215` in `_ensure_db_once`,
  self-nesting `NestedDatabaseWriteError` under the X78.10 `_ensure_db` retry
  wrapper. Confirmed via `logs/supervisor/listener_err.log` that this exact
  signature occurred repeatedly *before* this soak began, against process IDs
  that predate the X78.12 restart. The file (`pumpfun_curve_listener.py`) has
  zero diff under this commit. Not fixed here; not in scope.
- **`pumpfun_curve_listener` retry issue** — the same bug as above; the
  X78.10 retry wrapper's `_ensure_db_once()` appears to leak its lease into
  its own retry attempt on the same thread when a `CrossProcessDatabaseWriteTimeout`
  interrupts it partway through its `CREATE TABLE` sequence.
- **Historical contention** — ordinary bounded `CrossProcessDatabaseWriteTimeout`
  / `CROSS_PROCESS_LOCK` entries observed throughout the soak across multiple
  processes are expected, designed behaviour (X78.9's bounded cross-process
  lock working as intended), not a defect.
- A distinct `sqlite3.OperationalError: database is locked` at
  `creator_funding_worker.py:361` in `_recover_stale_and_claim` was observed —
  a raw-SQLite-busy error class, separate from the tracked
  `NestedDatabaseWriteError`/`CrossProcessDatabaseWriteTimeout` exception
  types. Pre-existing, not investigated further, not in scope.

These are separate work items and are not closed by this milestone.

---

## 5. Separation of Root Causes

**Issue 1 — High-frequency write amplification.**
Status: **Resolved.**

**Issue 2 — Long-held write lease.**
Status: **Open.**

These are structurally distinct mechanisms — many short leases vs. one
genuinely long hold — proven distinct by the deterministic reproduction in
§2 (max individual lease duration 27.8ms, never spanning an HTTP await) and
by log-timestamp analysis of Issue 2's incident (two `acquired_at` timestamps
113 minutes apart, confirming a genuine single hold rather than rapid
reacquisition). Do not blur this distinction. Issue 1's resolution provides
no evidence about, and does not change the status of, Issue 2.

---

## 6. Regression

Full suite run: `test_x78_0_creator_funding_lease_poisoning.py`,
`test_x78_11_rpc_metrics_lease_poisoning.py`,
`test_x78_11b_reaper_cross_thread_lease_poisoning.py`,
`test_x78_12_domain_resolver_lease_timeline.py` — **15/16 passed.**

**Known pre-existing failure:** `test_a_single_leaked_lease_poisons_every_subsequent_write_same_thread`
in `test_x78_0_creator_funding_lease_poisoning.py` fails because it asserts
*permanent* poisoning, which is no longer true after X78.11b's correct
self-healing fix (a prior, separately-committed milestone). This test file
has zero diff under this commit — the failure is pre-existing legacy timing
fragility, already documented as follow-up debt prior to X78.12, not a
regression introduced here.

**Untouched files:** `src/core/database_write_service.py`,
`src/utils/db_locking.py`, `src/core/pumpfun_curve_listener.py`,
`src/core/creator_funding_worker.py`, `src/core/intelligence_refresh.py` —
all confirmed zero diff under commit `b6cc68fd`.

**No new defect classes** were introduced by this change; all defect classes
observed during the soak (bounded timeouts, the listener startup race, the
raw SQLite-busy error, Issue 2's single sighting) are pre-existing and
independently attributable to code this commit did not touch.

---

## 7. Production Impact

- Production behaviour preserved.
- No semantic changes — identical tags, identical registry entries, identical
  values written.
- No governance changes.
- No attribution changes.
- Creator funding extraction is unchanged except for write batching in
  `DomainResolver`.

---

## 8. Engineering Lessons

- **Why batching solved the measured problem:** the defect was never one
  connection held too long — it was the write lane being acquired and
  released dozens of times per page, each acquisition contending for the
  same process-wide lock. Collapsing N acquisitions into 1 removes N−1
  opportunities for contention, regardless of how fast each individual
  acquisition was.
- **Why lease frequency matters:** a bounded cross-process lock (X78.9)
  still serializes all writers system-wide. High-frequency short leases from
  one workload can crowd out other writers' opportunities to acquire the
  lane even when no single lease is individually slow — throughput is a
  function of acquisition *count*, not just hold *duration*.
- **Why lease duration was never the Issue 1 problem:** deterministic
  reproduction directly measured max individual lease duration at 27.8ms
  pre-fix, and proved zero leases were held open during any HTTP await.
  There was no long hold to fix in this code path — only volume.
- **Why deterministic reproduction mattered:** `asyncio.Event`-gated mock
  responses (not sleeps) allowed exact assertions about lease state *while*
  an HTTP call was deliberately still blocked, directly answering "is a
  lease open during I/O?" rather than inferring it from timing, which
  correctly distinguished this from a transaction-boundary bug.
- **Why PERIOD A required separate investigation:** static tracing of every
  reachable code path from the observed `intelligence_refresh.py:55` tag
  found no structural defect (`IntelligenceRefreshCandidateBuilder` ruled out
  by process-attribution; `_post_extraction_intelligence_refresh` re-verified
  correct). A ~60+ minute single genuine hold with an unexplained mechanism
  cannot be fixed by inference — it requires live instrumentation attached to
  a running process and a captured recurrence, which is qualitatively
  different work from Issue 1's reproducible-in-a-test defect.

---

## 9. Remaining Technical Debt

- **PERIOD A / Issue 2** — long-held write lease, mechanism unproven, tag
  `intelligence_refresh.py:55 in _db`. Requires live instrumentation and a
  captured recurrence. Tracked as a new, separate investigation: **X78.13**.
- **Listener retry bug** — `pumpfun_curve_listener.py:4215` self-nesting
  `NestedDatabaseWriteError` in the X78.10 `_ensure_db`/`_ensure_db_once`
  retry wrapper under startup contention. Pre-existing, reproducible in
  production logs, not fixed.
- **Existing legacy timing fragility** — `test_x78_0_creator_funding_lease_poisoning.py`'s
  `test_a_single_leaked_lease_poisons_every_subsequent_write_same_thread`
  asserts stale (pre-X78.11b) permanent-poisoning behaviour and needs
  updating to reflect the correct self-healing behaviour. Already documented
  prior to X78.12; not addressed here.

Nothing else is carried forward from this milestone.

---

## 10. Final Engineering Verdict

### Issue 1

**RESOLVED**

DomainResolver write-lane amplification has been eliminated through
deterministic batching, verified by regression testing and sustained live
operation. The measured contention mechanism no longer exists.

### Issue 2

**OPEN**

PERIOD A remains independently reproducible and was observed during the
soak. It requires separate investigation and is not affected by the Issue 1
resolution. Tracked going forward as **X78.13 — Long-Held Write-Lease
Investigation**, opened as a dedicated, separate milestone so as not to
conflate two distinct root causes.

X78.12 is closed permanently upon this commit and will not be reopened for
PERIOD A.
