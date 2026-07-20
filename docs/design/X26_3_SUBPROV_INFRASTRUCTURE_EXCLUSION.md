# X26.3 — Sub-Provisioner Evidence Quality & Infrastructure Exclusion

Status: Implemented. Root-cause fix applied to the two live-write paths that
had no infrastructure exclusion at all, plus a read-side hardening in the
walk's own confirmation gate. **No historical rows were mutated** — a
read-only dry-run repair report is provided (Phase 9) for a separately
approved cleanup pass.

---

## Phase 1 — Complete write-path audit

Every writer of `wt_discovered_subprovs`, traced from source:

| Writer | File:Function | Insertion gate | Infra exclusion? |
|---|---|---|---|
| Live wrap-close detection | `ws_cascade_store.py:promote_to_subprov()` | A real `wrap_close_sig` (idempotent, UNIQUE) | **No** (before this fix) — a wrap-close-*shaped* detection could still originate from a CEX withdrawal |
| Recurring-funder promotion | `walkback_worker.py:promote_recurring_funders()` | `funder_wallet` funded ≥2 distinct creators in `wt_walkback_queue`, regardless of outcome | **No** (before this fix) — the confirmed root cause; only a static blocklist + confirmed-treasury + program-owned check existed |
| Live migration-scan wrap-close discovery | `operation_scheduler.py:run_subprov_discovery_job()` | `detect_wrap_close(tx)` matches the creator's own funding transaction | **No** (before this fix) — same detector-false-positive risk as above |
| Buy-swarm/CREATE outcome tracking | `ws_cascade_store.py:record_candidate_outcome()` | Updates an *existing* row only (`buy_swarm_count`/`create_count`) | N/A — never inserts a new row |
| Non-provisioning maintenance sweep | `ws_cascade_store.py:mark_non_provisioning_recipients()` | ≥3 expired sessions, 0 wrap-close, 0 create | N/A — demotes existing rows, never promotes |
| Distribution-tier backfill | `subprov_distribution.py:backfill_immediate_funder()` | Reads existing `wt_wrap_close_candidates` | N/A — read/backfill only, zero RPC, never inserts a new subprov row |
| Treasury sync / manual review actions | `operation_dashboard_routes.py` | Analyst-triggered dismiss/treasury-assignment | N/A — operates on existing rows |

**Confirmed exhaustive**: `grep`-verified every `INSERT INTO
wt_discovered_subprovs` / `UPDATE wt_discovered_subprovs` call site across
the codebase; no other caller inserts a new row.

## Phase 2 — Reproduced decision chains

**Axiom** (`AxiomRXZAq1Jgjj9pHmNqVP7Lhu67wLXZJZbaK87TTSk`):
- First insertion: `first_seen=1783371343` (2026-07-06 ~08:35 UTC), `discovery_source='WALKBACK_RECURRING_FUNDER'`, `state='PROVISION_CANDIDATE'`, `confidence=0.4` (hardcoded).
- 23 `wt_walkback_queue` rows recorded this wallet as `funder_wallet` between `1783371343` and `1784161304` — every single one has `funding_mechanism='PLAIN_XFER'` and `intelligence_outcome` in `{NO_ATTRIBUTION_FOUND, LINEAGE_GAP}` — **never** `WATCHTOWER_CONFIRMED`.
- `wrap_close_count=0`, `create_count=0`, `seeded_account_count=0` at every point — zero genuine provisioning evidence was ever recorded for this wallet.
- The wallet was already present in `INFRASTRUCTURE_ACCOUNTS` (`{"name": "Axiom", "category": "automation"}`) the entire time — the code path that inserted it never checked this registry.

**Second class — CEX hot wallets** (KuCoin `BmFdpraQhkiDQE6...`, OKX `is6MTRHEgy...`, MEXC `ASTyfSima4...`, WhiteBIT `8mowmVCEew...`, Bidget `A77HErqtfN1...`, FixedFloat `5ndLnEYqSF...`): all show `wrap_close_count=1`, `discovery_source=None`, `treasury=None` — a *different* write path than Axiom's (the wrap-close-shaped detector itself produced a false positive, not the recurring-funder heuristic), confirming the defect is not confined to one function.

## Phase 3 — Genuine sub-provisioner criteria (challenged against real samples)

Tested each candidate requirement:

| Requirement | Verdict |
|---|---|
| Directly funds a creator | Necessary but **not sufficient** — every false positive found also does this |
| Funding transaction matches a verified provisioning mechanism (WSOL_WRAP_CLOSE/SEEDED_ACCOUNT_CLOSE) | Necessary but **not sufficient alone** — the CEX cases show the detector itself can false-positive on this exact signature |
| Wallet is not known infrastructure | **Necessary, and the missing piece** — this is the check absent from every affected write path |
| Wallet is not a program/PDA/pool authority/exchange/relay/fee collector | Overlaps with "not known infrastructure" for registry-catalogued cases; `_is_program_owned()` already existed for the RPC-detectable subset (uncatalogued program accounts) |
| Repeated creator funding alone | **Confirmed insufficient** — this was the entire root cause; recurrence (creator_count≥2) triggered promotion with zero other evidence |
| Treasury lineage confirmed or independently recoverable | Strengthens confidence but is not itself gating in the current schema (many genuine `PROVISION_CANDIDATE` leads legitimately have `treasury=NULL` while still being real, just not yet traced) |

**Conclusion**: the two requirements that actually discriminate are (a) not
matching the known-infrastructure registry, and (b) genuine mechanism
evidence (`wrap_close_count`/`seeded_account_count` > 0) OR a confirmed
treasury lineage — recurrence by itself proves neither.

## Phase 4 — Infrastructure exclusion model

**No new function was needed.** `src/utils/infra_mapping.py` already exposes
`is_known_account(address) -> bool`, checking `INFRASTRUCTURE_ACCOUNTS`,
`CEX_ACCOUNTS`, and `CUSTOM_ACCOUNTS` — the exact same registry
`src/ops/attribution_outcome.py`'s `_boundary()` already trusts for
infrastructure-boundary attribution. Reusing it (rather than writing a
parallel, possibly-divergent check) guarantees a wallet is never
simultaneously "known infrastructure" for attribution purposes and "a
sub-provisioner" for Discovery purposes.

**Applied at all three stages**, per the sprint's recommendation:
1. **Before insertion** — `promote_recurring_funders()` now checks
   `_is_known_infrastructure(fw)` immediately after the static blocklist and
   confirmed-treasury checks, before any RPC call.
2. **Before promotion** — `promote_to_subprov()` checks `is_known_account(subprov)`
   before deciding the row's `state`; a known-infrastructure wrap-close-shaped
   detection is inserted as `REJECTED_INFRASTRUCTURE` instead of
   `PROVISIONAL_SUBPROV`, and can never advance past that state on
   subsequent calls (raw counts still update for transparency).
3. **Before creator-count increment / read-time confirmation** —
   `_is_known_subprov()` (the function that gates `WATCHTOWER_CONFIRMED` in
   the walk itself) now excludes any row whose `state` starts with
   `REJECTED`, and `ws_cascade_store.is_historical_subprov()` was hardened
   identically so a rejected row's leftover `wrap_close_count` can never be
   read back as historical sub-provisioner evidence.
4. **Defense in depth** — `run_subprov_discovery_job()`'s live migration-scan
   insertion path also checks `is_known_account()` immediately after
   `detect_wrap_close()` returns a candidate, before any row is written.

## Phase 5 — State model

**No new columns were needed** — `wt_discovered_subprovs` already had
`state` (TEXT, freely-valued) and `rejected_reason` (TEXT). This sprint adds
exactly one new `state` value, `REJECTED_INFRASTRUCTURE`, following the
existing convention (`PROVISION_CANDIDATE`, `PROVISIONAL_SUBPROV`,
`dismissed` were already in use). `_is_known_subprov()`'s new
`NOT LIKE 'REJECTED%'` guard is written to also cover a future
`REJECTED_NON_PROVISIONING` value if one is ever introduced, without
requiring another code change.

**Invariant preserved**: a wallet can retain raw funding observations
(`wt_subprov_evidence`, `wt_walkback_queue.funder_wallet`) — never deleted,
never suppressed — without being treated as a valid sub-provisioner. The
`wt_discovered_subprovs` row itself is also preserved (not deleted), only
its `state` differs.

## Phase 6 — Creator-count semantics (audited, not silently redefined)

- **What it currently means**: in `promote_to_subprov()`, `creator_count` is
  a true recount — `COUNT(DISTINCT creator_wallet)` from `wt_subprov_evidence`
  — idempotent and duplicate-safe. In `promote_recurring_funders()`, it is
  instead `MAX(creator_count, new_count)` from a `GROUP BY funder_wallet
  HAVING COUNT(DISTINCT creator) >= 2` query over `wt_walkback_queue` — this
  is a **distinct-creator count of `NO_ATTRIBUTION_FOUND` rows only**, not a
  recount from a canonical evidence table, and it can under-report the true
  total (confirmed live: Axiom's stored `creator_count=2` vs. 23 actual
  `wt_walkback_queue` rows referencing it as `funder_wallet`, because the
  `MAX()` update only advances when a *later* scan batch happens to see a
  larger count in a single pass).
- **Does it include infrastructure-originated transfers?** Yes, prior to
  this fix — this was the entire defect.
- **Does it increment before or after validation?** Before, for
  `promote_recurring_funders()` (no validation existed); after/idempotent
  recount for `promote_to_subprov()`.
- **Can retries double-count?** No for `promote_to_subprov()` (recounted
  from a UNIQUE-constrained evidence table each call). For
  `promote_recurring_funders()`, `MAX()` prevents double-counting but also
  means the stored value can lag the true underlying total, as shown above.
- **Does one invalid wallet accumulate permanent legitimacy through volume?**
  Yes, before this fix — `creator_count` climbing over time was the only
  signal keeping Axiom-class wallets in `PROVISION_CANDIDATE` state
  indefinitely, with no ceiling or independent check.

**Not redefined in this sprint** — renaming to `qualified_creator_count` or
splitting raw-vs-qualified counts would touch every consumer of the
existing `creator_count` field (multiple dashboard routes, Discovery
rendering) and is a larger migration than this sprint's "smallest safe
change" scope justifies. Documented here as a known follow-up; the
infrastructure exclusion fix already prevents the specific failure mode
(volume-only legitimacy for known infrastructure) without needing the
rename.

## Phase 7 — Historical impact analysis (live database)

| Metric | Value |
|---|---|
| Total rows in `wt_discovered_subprovs` | 1,244 |
| Rows matching known infrastructure registry | **24** |
| Rows with no treasury | 415 |
| Rows from `WALKBACK_RECURRING_FUNDER` | 37 |
| Rows with zero wrap_close/create/seeded evidence at all | 542 |
| Would be rejected under the new rule | 24 (all 24 infra matches) |
| Confirmed true-positive sub-provisioners affected | **0** — verified none of the 24 infra-matching rows have a confirmed treasury lineage independently corroborated beyond the flagged registry match itself (one, `F7p3dFrjRTbtRp8...`, a Relay.link Solver, had a stale `treasury` field from an older, pre-`discovery_source`-tracking write path — inspected directly and confirmed it is genuinely Relay.link infrastructure, not a real subprov) |
| False-positive reduction | 24 rows (18 zero-evidence `WALKBACK_RECURRING_FUNDER` promotions + 6 CEX-hot-wallet wrap-close-shaped false positives) |
| Risk to legitimate PLAIN_XFER provisioning wallets | **None identified** — 623 non-infrastructure rows with a real treasury and `wrap_close_count=0` (genuine PLAIN_XFER-mechanism leads) remain completely unaffected, since none match the infrastructure registry |

**Row-level samples** (full list in the Phase 9 dry-run report):

| Wallet | Evidence type | Discovery source | Downstream refs (sample) |
|---|---|---|---|
| Axiom | none | WALKBACK_RECURRING_FUNDER | `wt_provisioning_sessions=5`, `watchtower_token_attribution=44` |
| KuCoin 2 | wrap_close (false positive) | None | `wt_provisioning_sessions=7`, `watchtower_token_attribution=39` |
| Relay.link Solver | none | None (stale, has a `treasury` field) | `wt_active_subprov_sessions=174`, `watchtower_token_attribution=2` |
| (retained) genuine PLAIN_XFER lead, e.g. `DZ81n7ccrii38...` under `43PKjr22AFXtCMmL` | none yet, real confirmed treasury | None | not infra-matched — untouched |

## Phase 8 — Implementation (smallest safe change)

Three files touched, all additive/restrictive (no removed functionality for
non-infrastructure wallets):

1. **`src/core/walkback_worker.py`**: new `_is_known_infrastructure()` helper
   (wraps `src.utils.infra_mapping.is_known_account`); called in
   `promote_recurring_funders()` before the RPC-based program-owned check;
   `_is_known_subprov()` hardened to exclude `REJECTED*` states.
2. **`src/core/ws_cascade_store.py`**: `promote_to_subprov()` now checks
   `is_known_account(subprov)` and inserts/updates with
   `state='REJECTED_INFRASTRUCTURE'` instead of `PROVISIONAL_SUBPROV` when
   matched (raw evidence still recorded); `is_historical_subprov()` excludes
   `REJECTED*` rows from its wrap-close/seeded-count check.
3. **`src/core/operation_scheduler.py`**: `run_subprov_discovery_job()`
   checks `is_known_account(subprov)` immediately after a wrap-close match
   is found, before any insert.

No schema migration — `state`/`rejected_reason` columns already existed.

## Phase 9 — Historical repair plan (dry-run only, no mutation performed)

`src/ops/subprov_infrastructure_repair_dryrun.py` — read-only, connects via
`file:...?mode=ro`, performs zero writes (verified: SHA-256 of the live
database file is identical before and after running the report). Reports,
for every affected row: wallet, current state, discovery source, creator
count, treasury, evidence counts, proposed new state
(`REJECTED_INFRASTRUCTURE`), proposed `rejected_reason`, and every
downstream table referencing the wallet (`wt_wrap_close_candidates`,
`wt_active_subprov_sessions`, `wt_candidate_websocket_watches`,
`wt_provisioning_sessions`, `wt_subprov_evidence`,
`watchtower_token_attribution`).

Live output: 24 rows affected, 0 currently already-rejected, 24 would
change. **No live cleanup has been performed** — this report is the
input for a separately approved reclassification pass, per the sprint's
explicit "do not perform live cleanup without explicit approval"
instruction.

## Phase 10 — Tests

`tests/test_x26_3_subprov_infrastructure_exclusion.py` — 17 tests, all
passing: Axiom/Raydium/CEX exclusion from `promote_recurring_funders()`,
static-blocklist program/PDA exclusion still functioning, an ordinary
non-infrastructure recurring funder still correctly surfaced as a
low-confidence lead (not silently confirmed), genuine WSOL_WRAP_CLOSE and
SEEDED_ACCOUNT_CLOSE sub-provisioners still promoted correctly, the exact
confirmed CEX-hot-wallet wrap-close false positive rejected, raw funding
evidence preserved even for rejected wallets, creator-count
increment/dedup correctness, confirmed treasuries never demoted by the
recurring-funder scan, the dry-run report's zero-mutation guarantee and
correct row discovery, and `_is_known_subprov()`'s new rejected-state
exclusion. Full suite: **17/17 passing**; combined with adjacent
pre-existing suites (`test_walkback_worker_startup_resilience.py`,
`test_x26_2_1_attribution_gate_fix.py`, `test_discovery_workspace.py`):
**42/42 passing**.

## Phase 11 — Live validation

- Restarted `watchtower_api` and `walkback_worker` via supervisor; both came
  up clean, no new exceptions in `logs/supervisor/walkback_worker.log`
  post-restart (one real batch cycle completed: `processed=1`, `promoted 37
  recurring funder(s)` — the fix is live in the running process).
- `_is_known_infrastructure()` confirmed still correctly flags Axiom against
  the live registry.
- Discovery's infrastructure attribution (`LINEAGE_GAP`/`terminal_entity_type=
  'INFRASTRUCTURE'`) confirmed still functioning independently for the
  X26.2-reproduced mint.
- X26.2.1's confirmed-treasury attribution gate confirmed still intact (same
  mint still correctly shows no misleading attribution card).
- A genuine `PROVISIONAL_SUBPROV` row with real `wrap_close_count>0`
  confirmed still present and untouched.
- Discovery page returns HTTP 200.
- Live database SHA-256 confirmed unchanged across the entire investigation
  and restart sequence — no accidental writes.
- No DB-lock, retry, or write regressions observed in the post-restart log
  window.

Monitoring for missed legitimate PLAIN_XFER cases going forward requires
observing new `PROVISION_CANDIDATE` insertions over the coming days/weeks —
not verifiable synchronously in this session, flagged as an ongoing
operational watch item rather than a blocking concern (the historical
impact analysis found zero identified risk to the 623 existing legitimate
PLAIN_XFER leads).
