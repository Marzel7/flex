# X25.9 — Resolve NO_ATTRIBUTION_FOUND Evidence Semantics

Status: Investigation and design complete. **No walkback logic changed.**
This document traces the actual code, enumerates every real cause, and
recommends (does not implement) an evidence-state model. Confirmed by
`git diff --stat` showing only this new document.

---

## Phase 1 — Complete trace of `NO_ATTRIBUTION_FOUND`

Every location in `src/core/walkback_worker.py` capable of producing this
outcome, traced directly from source (not inferred):

| # | Function | Line | Stopping condition | Return / persisted state |
|---|---|---|---|---|
| 1 | `_process_row`, `FULL_WALKBACK` branch | 672-674 | `_recover_creator_from_db()` returns `None` — no creator wallet resolvable from `wt_watchtower_launches`, `token_analysis`, or `migrated_tokens` | `_mark_complete(..., "NO_ATTRIBUTION_FOUND", None, None, 0)` — **`rpc_used=0`, no RPC call ever attempted** |
| 2 | `_process_row`, `FULL_WALKBACK` branch | 678-680 | `_find_funder_via_rpc(creator, ...)` returns the empty tuple for hop 1 | `_mark_complete(..., "NO_ATTRIBUTION_FOUND", None, None, rpc[0])` — `rpc_used` reflects at least 1 `getSignaturesForAddress` call |
| 3 | `_process_row`, `FULL_WALKBACK` branch | 746-748 | Hop 1 resolved to an unrecognised wallet, then `_find_funder_via_rpc(hop1, ...)` for hop 2 returns empty | `_mark_complete(..., "NO_ATTRIBUTION_FOUND", None, None, rpc[0])` |
| 4 | `_mark_exhausted` | 475-486 | A row stayed `status='pending'` and exceeded `MAX_ATTEMPTS` (3) without ever completing (e.g. repeated crashes/timeouts mid-processing) | `intelligence_outcome='NO_ATTRIBUTION_FOUND'` set directly, independent of any single call's result |
| 5 | `_process_row`, `PARTIAL_TREASURY`/`PARTIAL_SUBPROV` branches | 626, 643 | `subprov`/`creator` is `NULL` on the queue row itself | **Does NOT produce `NO_ATTRIBUTION_FOUND`** — calls `_mark_failed()` instead, which sets `status='failed'` but leaves `intelligence_outcome` untouched. Included here only because it's easily confused with the other paths; confirmed structurally distinct. |

**`_find_funder_via_rpc()` itself** (lines 261-338) — the function underlying
causes #2 and #3 — has its own internal branching, all collapsing to the
same empty return `(None, None, None, None, None, None)`:

| Internal path | Line | Condition |
|---|---|---|
| A | 279-283 | `_get_sigs(wallet)` returned `[]` |
| B | 288-289 | Every signature entry in the window had a non-null `err` field (all found transactions failed on-chain) |
| C | 291-292 | A signature entry was missing its `signature` field (malformed RPC response) |
| D | 295-296 | `_get_tx(sig)` returned `None` for a candidate signature |
| E | 298-309 | Every extracted sender was filtered out: self-payment, `_FUNDER_BLOCKLIST` membership, or resolved to a non-wallet program/PDA account |
| F | 329-330 | Candidates were found but all were removed by the filtering in E, leaving `candidates=[]` |

**`_get_sigs()` → `_rpc()`** (lines 73-89) — the true root of path A: `_rpc()`
wraps the entire HTTP call in a bare `except Exception`, meaning **RPC
timeout, network failure, malformed JSON response, HTTP error status, and
Helius-side RPC error objects are all caught identically and converted to
`None`**, which `_get_sigs` then converts to `[]` — **structurally
indistinguishable from a wallet whose `getSignaturesForAddress` call
genuinely, successfully returned zero results.** Only a `print()` log line
is emitted (line 82); nothing is persisted to the database that
distinguishes this case.

## Phase 2 — Every underlying cause enumerated

| Cause | Distinguishable today? |
|---|---|
| Genuinely unfunded wallet (RPC succeeded, truly zero prior signatures) | **No** — identical persisted state to RPC failure (path A) |
| RPC timeout (`urllib.request.urlopen` raises `socket.timeout`) | **No** — caught by `_rpc()`'s bare `except Exception`, becomes `None`/`[]` |
| RPC empty/malformed response (bad JSON, missing `result` key) | **No** — same collapse |
| HTTP error status (4xx/5xx from Helius) | **No** — `urlopen` raises `HTTPError`, caught the same way |
| History truncation / signature pagination limit (`SIG_LIMIT=20`) | **No** — if the true funder is signature #21+, the call still "succeeds" with 20 results and simply never finds it; this is functionally identical to "wallet has funding history but the funder isn't in the first 20 signatures," and today produces the same empty/no-candidate result as genuine absence |
| Unsupported/version-incompatible transaction (`getTransaction` returns `None` for a valid signature, e.g. a transaction version pump.fun's parser can't handle) | **No** — path D, same collapse as A |
| All found transactions failed on-chain (`err` present) | **Partially** — this is a real, different fact (transactions exist, but none succeeded) but is silently skipped (path B) rather than recorded as its own outcome |
| Every extracted sender was filtered as a non-wallet (program/PDA/AMM pool) or blocklisted | **Partially** — this is arguably not "no evidence" at all; real funding transactions were found, they were just structurally excluded as invalid funder candidates (path E/F) — currently reported identically to "no transactions existed" |
| No creator wallet could be resolved at all (upstream of any RPC call) | **Yes, but only via a side-channel** — `rpc_used=0` on the persisted row is the sole distinguishing signal, and it is not currently surfaced anywhere in Discovery |
| Row exhausted retry attempts without ever completing a pass (crash/restart mid-processing) | **Yes, but only via a side-channel** — `attempts >= MAX_ATTEMPTS` combined with no completed single-call trace; not currently surfaced |

**Measured against the live database** (`database/wt_ops_v2.db`,
`wt_walkback_queue`, 2,575 total `NO_ATTRIBUTION_FOUND` rows):

- **453 rows (17.6%) have `rpc_used=0`** — these are exclusively cause #1
  (no creator ever resolvable; zero RPC calls attempted at all), confirmed
  by cross-checking: all 453 have `attempts=1` and `last_error=NULL`, ruling
  out the `_mark_exhausted` retry-timeout path (which would show
  `attempts=3`) and the `_mark_failed` guard-clause path (which would show
  a non-null `last_error` and a different `status` value).
- **The remaining 2,122 rows (82.4%)** have `rpc_used` values ranging from
  1 to 21+, meaning at least one real RPC round-trip occurred — these are
  causes #2/#3, and within them, internal paths A-F of
  `_find_funder_via_rpc()` are **completely unrecoverable from persisted
  data** — no column distinguishes "genuinely zero signatures" from "RPC
  errored" from "all candidates were filtered out."

## Phase 3 — Evidence currently available vs. discarded

| Evidence | Currently available? | Where |
|---|---|---|
| Whether any RPC call was attempted at all | **Available** | `wt_walkback_queue.rpc_used` (0 vs. >0) |
| Whether the row exhausted retries without completing | **Available, indirectly** | `wt_walkback_queue.attempts` (compare against `MAX_ATTEMPTS=3`), but only meaningful for the `_mark_exhausted` path, and even then not distinguished from a fast single-pass failure |
| RPC error object / exception message | **Discarded** | `_rpc()` only `print()`s it (line 82); never written to any table |
| Whether the failure was a timeout specifically | **Discarded** | Same bare `except Exception` — no distinction between `socket.timeout`, `HTTPError`, `URLError`, or JSON decode errors |
| Zero-signatures vs. RPC-failure distinction | **Discarded entirely** | `_get_sigs()` converts both to `[]` before the caller ever sees them |
| Pagination/history-truncation signal | **Discarded** | `SIG_LIMIT` is applied silently; no flag records "the funder may exist beyond the fetched window" |
| Whether candidates were found but filtered vs. never found at all | **Discarded** | `_find_funder_via_rpc()` returns the same empty tuple whether `sigs` was empty (path A) or every candidate was filtered (path E/F) |
| Which specific `_find_funder_via_rpc()` internal path (A-F) was taken | **Discarded entirely** | No logging or persisted field distinguishes any of the six internal branches |

## Phase 4 — Recommended evidence-state model (design only, not implemented)

**A single `NO_ATTRIBUTION_FOUND` state is not sufficient** — it currently
conflates a genuinely distinguishable case (cause #1, no creator
resolvable, zero RPC attempted) with an entirely undifferentiated blob of
every other RPC/data outcome. Recommend splitting into states the *current
persisted schema can actually support today*, plus states that would
require new capture (not new inference) if pursued later:

**Supportable today, from existing persisted columns, no schema change:**

| Proposed state | Condition (derivable today) |
|---|---|
| `NO_SUBJECT_WALLET` | `rpc_used=0` — no creator/subprov wallet was ever available to query. This is not an evidence gap at all; it's a precondition failure, and today's `rpc_used=0` column already lets this be split out mechanically from the rest. |
| `NO_ATTRIBUTION_FOUND` (narrowed) | `rpc_used>0` — at least one real RPC round-trip occurred and produced no usable funder. This remains a single bucket under today's implementation, because internal paths A-F are genuinely unrecoverable from what's persisted. |

**Would require new capture (a walkback-logic change, explicitly out of
this sprint's scope, listed here only as a design option for a future
sprint to evaluate):**

| Proposed state | What would need to be persisted |
|---|---|
| `EVIDENCE_UNAVAILABLE` | `_rpc()` would need to distinguish and persist "raised an exception" vs. "returned a well-formed empty result," e.g. by returning a tagged result type instead of collapsing to `None`. |
| `RPC_UNAVAILABLE` | Specifically an HTTP/timeout/network failure, as opposed to a successful-but-empty response. |
| `HISTORY_INCOMPLETE` | A flag recording that `SIG_LIMIT` was reached (i.e., exactly 20 signatures were returned with none usable) versus fewer than 20 (a genuinely short history). |
| `NO_FUNDING_FOUND` | Reserved for the case that can currently never be proven: RPC definitively succeeded, definitively returned fewer than `SIG_LIMIT` signatures, and none were filtered for structural reasons (i.e., a true, high-confidence "this wallet has no funder"). |

**This sprint does not recommend implementing the second table.** Building
it would require changing `_rpc()`'s exception handling and threading a new
tagged evidence type through `_get_sigs`/`_get_tx`/`_find_funder_via_rpc` —
real walkback-logic changes, which the brief explicitly says to avoid
unless "the investigation proves the current evidence model is objectively
ambiguous." **The investigation does prove exactly that** (Phase 1-3,
above) — but proving the ambiguity exists is different from mandating the
fix be built in this sprint. The first table (`NO_SUBJECT_WALLET` split)
is recommended as low-risk and immediately actionable, since it requires
zero new capture — purely a read-time reclassification of an
already-persisted column (`rpc_used`).

## Phase 5 — Analyst semantics for each proposed state

**`NO_SUBJECT_WALLET`** (supportable today):
> No creator or sub-provisioner wallet could be identified for this launch
> from any persisted record, so no funding lineage search was ever
> performed.
>
> This does **not** mean the launch has no funding history — it means the
> platform never had a wallet address to begin searching with.

**`NO_ATTRIBUTION_FOUND` (narrowed)** (supportable today):
> At least one on-chain lookup was performed for this launch's wallet, and
> it did not produce a usable funding relationship.
>
> This does **not** distinguish between: the wallet genuinely has no prior
> funding transaction; the lookup failed (network/RPC error); the wallet's
> funding history extends beyond the window the platform checked; or every
> candidate transaction found was excluded as not being a valid funder
> (e.g. a self-payment or an exchange/pool account). Today's data cannot
> tell these apart.

**`EVIDENCE_UNAVAILABLE`** (design-only, not built):
> The retrieval process itself failed — the platform could not complete
> the lookup needed to determine funding lineage.
>
> This does **not** mean the wallet lacks funding history; it means the
> platform does not currently know either way.

**`HISTORY_INCOMPLETE`** (design-only, not built):
> The wallet's available transaction history was longer than the platform
> checked, and no funder was found within the portion examined.
>
> This does **not** rule out a funder existing further back in the
> wallet's history.

**`NO_FUNDING_FOUND`** (design-only, not built):
> The available history contains no identifiable funding relationship.
>
> This does **not** prove the wallet was never funded — only that no
> funding transaction was found within the confirmed, complete portion of
> history that was actually examined.

## Phase 6 — UI implications

Reviewed `templates/discovery.html`'s `detectionReconciliation()`
(X25.7/X25.8). **"Evidence Inconclusive" is not currently used for
`NO_ATTRIBUTION_FOUND` at all** — as established in X25.8 Phase 4,
`NO_ATTRIBUTION_FOUND` walks never populate `wt_provisioning_sessions`
(since `_capture_provisioning_facts` is only called when a funding
fragment was actually found), so they never enter
`detection_reconciliation.py`'s population and never reach any of the six
UI-facing classification states at all today. **This means the ambiguity
documented in this sprint is currently invisible in Discovery, not
mislabeled** — there is no existing UI sentence to correct, because
`NO_ATTRIBUTION_FOUND` launches don't reach the Detection Provenance
section under today's implementation.

Should a future sprint choose to surface `NO_ATTRIBUTION_FOUND` in
Discovery (extending `detection_reconciliation.py`'s population, itself a
backend change out of this sprint's scope), the two-state split recommended
in Phase 4 should map to distinct analyst messages rather than being folded
into "Evidence Inconclusive," since `NO_SUBJECT_WALLET` and the narrowed
`NO_ATTRIBUTION_FOUND` are meaningfully different facts (no wallet to
search vs. searched and found nothing usable) and conflating them would
reintroduce exactly the kind of collapsed-ambiguity this sprint was asked
to resolve.

## Phase 7 — Deliverables

**Complete decision tree:**

```
_process_row() dispatches on wclass
  │
  ├─ PARTIAL_TREASURY, subprov IS NULL
  │    → _mark_failed() — status='failed', intelligence_outcome UNCHANGED
  │      (NOT NO_ATTRIBUTION_FOUND; a distinct guard-clause path)
  │
  ├─ PARTIAL_SUBPROV, creator IS NULL
  │    → _mark_failed() — same as above
  │
  ├─ FULL_WALKBACK, creator IS NULL, _recover_creator_from_db() fails
  │    → NO_ATTRIBUTION_FOUND, rpc_used=0  [Cause #1 — measured 453/2575 = 17.6%]
  │
  ├─ FULL_WALKBACK, hop 1 lookup (_find_funder_via_rpc) returns empty
  │    → NO_ATTRIBUTION_FOUND, rpc_used>0  [Cause #2]
  │         internal path was one of:
  │           A: _get_sigs returned [] (RPC succeeded-empty OR RPC failed — indistinguishable)
  │           B: every signature had an on-chain err
  │           C: malformed signature entry
  │           D: _get_tx returned None for every candidate
  │           E: every sender filtered (self-pay/blocklist/program account)
  │           F: candidates existed but all filtered (same as E, different code path)
  │
  ├─ FULL_WALKBACK, hop 1 resolved but unrecognised, hop 2 lookup returns empty
  │    → NO_ATTRIBUTION_FOUND, rpc_used>0  [Cause #3 — same internal-path ambiguity as #2]
  │
  └─ Any wclass, row exceeds MAX_ATTEMPTS without completing
       → _mark_exhausted() → NO_ATTRIBUTION_FOUND  [Cause #4 — distinguishable via
         attempts>=MAX_ATTEMPTS, but this signal is not currently cross-referenced
         anywhere; measured 0 rows matching this exact signature in the rpc_used=0
         bucket, since those all had attempts=1, confirming #4 is a separate,
         currently-unobserved-in-this-slice population from #1]
```

**Every underlying cause**: Phase 2 table, above (10 distinct causes
enumerated; 2 distinguishable today via `rpc_used`, 8 not).

**Evidence currently available / discarded**: Phase 3, above.

**Recommended evidence-state model**: Phase 4 — a two-state split
(`NO_SUBJECT_WALLET` / narrowed `NO_ATTRIBUTION_FOUND`) achievable today
with zero schema or logic changes (pure read-time reclassification of the
existing `rpc_used` column), plus a documented, not-recommended-for-this-sprint
four-state extension that would require real capture changes in
`_rpc()`/`_find_funder_via_rpc()`.

**Migration impact**: the immediately-actionable split
(`NO_SUBJECT_WALLET`) requires **zero migration** — it is a classification
function reading an existing column (`rpc_used`), analogous to how
`detection_reconciliation.py` already reads `intelligence_outcome` today.
No existing row's persisted state would need to change; only a new
read-time query would need to add a `CASE WHEN rpc_used=0 THEN
'NO_SUBJECT_WALLET' ELSE 'NO_ATTRIBUTION_FOUND' END` distinction if/when
`NO_ATTRIBUTION_FOUND` launches are ever surfaced in Discovery (which, per
Phase 6, they currently are not).

## Explicit confirmation

**This is purely a classification problem, not a logic-change
requirement, for the one recommendation this sprint actually makes**
(`NO_SUBJECT_WALLET` split via `rpc_used`). Backend walkback logic
(`_rpc()`, `_get_sigs()`, `_find_funder_via_rpc()`, `_process_row()`) was
read in full but not modified. The four-state extension in Phase 4's
second table **would** require walkback-logic changes (specifically to
`_rpc()`'s exception handling) — this sprint documents that requirement
precisely but does not implement it, consistent with the brief's "do not
implement" instruction for Phase 4 and "recommend only if objectively
justified" — the recommendation is made, implementation is deferred.

`git diff --stat` for this sprint shows only this new document under
`docs/design/` — no source files modified.
