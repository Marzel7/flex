# X65.16 — Evidence Coverage Boundary: Creator vs. Buy-Swarm Discrimination (Full Report)

Read-only investigation. No code changes, no database writes, no UI changes, **no live
RPC calls made**. This report demonstrates, from direct schema and data inspection
alone, exactly why the creator-vs-buy-swarm question X65.15 left open cannot currently
be resolved from persisted evidence for the launches where it matters most — and
specifies the minimum future RPC work that would resolve it, without performing that
work now. This scope was set explicitly by the user after reviewing this audit's own
Phase-1 findings mid-investigation (documented below), superseding the original
X65.16 task brief's Phases 3–9.

## Contents

1. [Define the Validation Population](#phase-1--define-the-validation-population)
2. [Per-Table Evidence Coverage](#phase-2--per-table-evidence-coverage)
3. [Why Each Table Cannot Resolve the Question](#phase-3--why-each-table-cannot-resolve-the-question)
4. [Minimum Future RPC Validation Specification](#phase-4--minimum-future-rpc-validation-specification)
5. [Conclusion](#phase-5--conclusion)

---

## Phase 1 — Define the Validation Population

### Selection method

The 219-launch Category-3 (Strong Candidate) population (X65.14) reduces to **exactly
11 distinct subprovs** (many launches share a subprov). Every subprov was ranked by
independent fan-out size (`wt_provisioning_edges` sibling-edge count, the same measure
used in X65.14/X65.15) and by absence of `wt_candidate_websocket_watches` coverage —
the two factors the task's own Phase 2 priorities name as defining "hardest cases."

### Full population, ranked

| SubProvider | Sibling edges (fan-out) | Candidate watches | Mechanism(s) across its launches | Launches produced |
|---|---|---|---|---|
| `5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9` | **68** | 0 | PLAIN_TRANSFER, UNKNOWN | 86 |
| `BmFdpraQhkiDQE6SnfG5omcA1VwzqfXrwtNYBwWTymy6` | **33** | 0 | PLAIN_TRANSFER, UNKNOWN | 50 |
| `Dv34prGm2BT7Ph2n6qKLgzeLgjnii87RJJ7Db6ZQQvKM` | **18** | 0 | PLAIN_TRANSFER, UNKNOWN | 23 |
| `A77HErqtfN1hLLpvZ9pCtu66FEtM8BveoaKbbMoZ4RiR` | **13** | 0 | PLAIN_TRANSFER, UNKNOWN | 16 |
| `HBQ2TC2gmX9qeNuCsY9gRTk9hiZLRZaKhvHGj2ZbVoWB` | 11 | 0 | WSOL_WRAP_CLOSE | 1 |
| `iGdFcQoyR2MwbXMHQskhmNsqddZ6rinsipHc4TNSdwu` | 10 | 0 | PLAIN_TRANSFER, UNKNOWN | 12 |
| `8mowmVCEewZ9W2cEaQyQeQEeSxhGr1hvRviLwozwNtBt` | 8 | 0 | MIXED, PLAIN_TRANSFER, UNKNOWN | 7 |
| `B48kNVXs4YK4amkBCH2XokQiv1SeiVQGHDR17xDeKAAn` | 7 | 0 | PLAIN_TRANSFER, UNKNOWN | 8 |
| `u6PJ8DtQuPFnfmwHbGFULQ4u4EgjDiyYKjVEsynXq2w` | 5 | 0 | PLAIN_TRANSFER, UNKNOWN | 14 |
| `4RSp4PaartLaZV3idUb3gGfQNm3YkNE8jyEXSPYiTh4f` | 1 | **5** | WSOL_WRAP_CLOSE | 1 |
| `BWwpES2oYug1SsLKPyFXekdJK99dHtdPgBjNk1SPRMDu` | 1 | **22** | WSOL_WRAP_CLOSE | 1 |

**9 of 11 subprovs (representing 217 of 219 launches, 99.1%) have `candidate_watches
== 0`** — meaning zero raw, pre-filter recipient-level observations exist for them at
all. Only the last 2 subprovs (2 launches total) have any live-cascade candidate-watch
coverage, and even that is thin (5 and 22 candidates respectively).

### Why creator-vs-buy-swarm cannot already be resolved for these 9 subprovs

The evidence this project has previously used as a fan-out proxy — the sibling-edge
count from `wt_provisioning_edges` — was directly verified in this audit to be
**creator-pre-filtered by construction, not raw fan-out** (Phase 2/3 below spell this
out in full). This means every prior fan-out number quoted for these subprovs in
X65.14/X65.15 (68, 33, 18, 13, 10, 8, 7, 5) already answers "how many *confirmed
creators* did this subprov fund," not "how many total recipients did this subprov
fund, some fraction of which may be buy-swarm participants." **The raw recipient set
these 9 subprovs actually produced has never been persisted anywhere in this
project's schema.** This is the population selected for this audit: the 9 subprovs
(217 launches) where the creator-vs-buy-swarm question is open specifically because
the raw recipient list itself was never recorded — not because it was recorded and is
ambiguous.

---

## Phase 2 — Per-Table Evidence Coverage

Every table this project could plausibly hold recipient-level evidence in, checked
directly against the 11 subprovs.

| Table | Rows for these 11 subprovs | What it would need to contain to answer the question |
|---|---|---|
| `wt_provisioning_edges` (`SUBPROV_TO_CREATOR`) | 219 rows (exactly matches the launch count) | Every `to_wallet` recipient, whether or not it went on to create — but see Phase 3, this table structurally cannot contain a non-creator row |
| `wt_candidate_websocket_watches` | 27 rows total, covering only 2 of 11 subprovs | Every raw wrap-close destination the subprov produced, before any creator/non-creator determination — this is the correct table in principle, but has essentially zero coverage here |
| `wt_swarm_buys` | 3 rows, covering only 1 of 11 subprovs (`4RSp4PaartLa…`, itself only 1 launch) | Every recipient wallet observed making a purchase rather than a CREATE — exists as a concept in this schema but is populated for almost none of this specific population |
| `wt_walkback_queue` | 219 rows (one per mint, matching launches, not subprovs) | Records only the already-resolved treasury/mechanism for the **specific mint under investigation** — has no field for a subprov's *other* recipients at all; not designed to answer a fan-out-composition question |
| `wt_attribution_outcomes` | 219 rows (one per mint) | Same limitation as `wt_walkback_queue` — mint-scoped, not subprov-fan-out-scoped |

---

## Phase 3 — Why Each Table Cannot Resolve the Question

### `wt_provisioning_edges` — the critical, previously-unstated finding

Direct verification performed in this audit: for the top subprov
(`5tzFkiKscXHK…`, 68 sibling edges), **100% of its 68 `SUBPROV_TO_CREATOR` recipients
are independently confirmed to be a `token_analysis.earliest_tx_creator`** (a real
on-chain creator of at least one token). This is not evidence that the subprov's fan-out
is "68 creators, 0 buy-swarm" — it is proof that **this table cannot record anything
else**. Per this project's own established schema discipline (project memory:
`treasury-vs-subprov-fingerprint.md`, `wt_provisioning_edges` is "written exclusively by
`capture_provisioning_relationship()`... only when a creator is already known via the
walkback path — structurally cannot represent non-creator sibling recipients"). Any
buy-swarm wallet the subprov also funded would, by this table's own write path, simply
never appear here. **Using this table's row count as a "fan-out" or "creator ratio"
metric — as X65.14/X65.15 both did — silently assumes the answer to the exact question
this audit was opened to test.** This is not a flaw in those two prior audits'
conclusions (their conclusions concerned classifier precision given available
evidence, which remains valid), but it does mean neither audit actually measured
buy-swarm composition, and this report corrects that framing going forward.

### `wt_candidate_websocket_watches` — the right table, near-zero coverage

This is the one table in the schema actually designed to record raw,
pre-creator-filter recipients (per X65.4/X65.8/X65.10's own design: it accumulates
"every distinct wrap-close destination a subprov has ever produced... persisted
independently of whether that candidate went on to become a confirmed creator"). It
is populated **only by the live WS cascade daemon**, which — per X65.11/X65.13's
already-established finding — has essentially zero coverage of this walkback-resolved
219-launch population (confirmed again directly here: 9 of 11 subprovs have exactly
zero rows). The table exists and would answer the question if populated; it simply
was never populated for these specific subprovs, because they were never observed by
the live cascade.

### `wt_swarm_buys` — exists, essentially unpopulated for this cohort

Structurally capable of recording exactly the "Buy Swarm" classification this task's
Phase 4 requires (`swarm_wallet`, `mint`, `subprov_wallet`, `swap_signature`) — but has
only 3 rows across all 11 subprovs, all for one already-marginal subprov. This
confirms the buy-swarm detection pathway that would populate this table (per project
memory `buy-swarm-vs-creator.md`) has itself not run against this population.

### `wt_walkback_queue` / `wt_attribution_outcomes` — wrong grain entirely

Both tables are keyed one row per **mint** (the launch under investigation), recording
that specific launch's own resolved treasury/mechanism/outcome. Neither has any
mechanism to record a subprov's *other*, non-launch-producing recipients — they were
never designed to answer a fan-out-composition question, only a single-launch
attribution question. No amount of re-querying these tables differently would surface
the missing recipient data, because the data itself was never captured by the
process that writes them.

### Summary: this is an observational gap, not an analytical one

Every one of the five checked tables was inspected on its own terms — schema,
population process, and actual row coverage — and each independently confirms the
same fact: **no process has ever recorded the raw, unfiltered recipient set for 9 of
the 11 subprovs underlying 217 of the 219 Strong Candidate launches.** This is
consistent with, and now directly confirms in the specific case of buy-swarm
discrimination, X65.11/X65.13's broader established finding that this
walkback-resolved cohort has near-zero live-cascade evidentiary coverage.

---

## Phase 4 — Minimum Future RPC Validation Specification

Specified for a future, separately-authorized follow-up. **No RPC calls were made in
this audit.**

### Target population for the follow-up

The 9 subprovs with zero raw-recipient coverage, prioritized by fan-out size (largest
first, since they cover the most launches per subprov queried):

1. `5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9` (68 confirmed-creator edges, 86 launches)
2. `BmFdpraQhkiDQE6SnfG5omcA1VwzqfXrwtNYBwWTymy6` (33, 50 launches)
3. `Dv34prGm2BT7Ph2n6qKLgzeLgjnii87RJJ7Db6ZQQvKM` (18, 23 launches)

These 3 subprovs alone underlie 159 of 219 Category-3 launches (72.6%) — the smallest
RPC footprint that would meaningfully move the classifier-precision estimate.

### Minimum call sequence per subprov (per this project's own RPC discipline —
project memory `rpc-investigation-discipline.md`: 1cr `getSignatures`+`getTransaction`
only, never the 100cr enhanced-tx endpoint, user-supplied temp key, cache from call #1)

1. `getSignatures(subprov_wallet, limit=1000)` — one call per subprov, to enumerate
   every outbound transaction, not just the ones that already resolved to a confirmed
   creator.
2. For each outbound transfer/wrap-close-destination found that is **not already** in
   `wt_provisioning_edges` for this subprov (i.e., the previously-invisible recipients):
   `getTransaction(destination_wallet's next signature)` to check whether that
   recipient wallet's own subsequent activity contains a `CREATE` instruction
   (Creator), a `swap`/`buy` instruction only (Buy Swarm), or neither yet observed
   (Unknown).
3. Cache every raw result immediately (per the project's own hard-won discipline —
   memory `rpc-investigation-discipline.md` records a 2.5M-credit burn from failing to
   do this previously).

### Estimated scope

For the top 3 subprovs, the recipient sets are almost certainly larger than the
68/33/18 creator-only counts already known (those counts are a *floor*, not the true
total, per Phase 3's finding) — so the true call count cannot be stated precisely
without first running step 1. Step 1 alone (3 `getSignatures` calls) would establish
the true total-recipient denominator directly and should be run **before** committing
to any larger subsequent scope, exactly matching the staged-escalation approach
already used across X65.15/X65.16.

---

## Phase 5 — Conclusion

**Can the creator-vs-buy-swarm question be resolved from persisted evidence?** No —
not for 217 of the 219 Strong Candidate launches (9 of 11 underlying subprovs). This
was demonstrated directly, table by table, not assumed: `wt_provisioning_edges` is
creator-pre-filtered by its own write path and cannot contain the answer;
`wt_candidate_websocket_watches` is the correct table in principle but has near-zero
coverage of this population; `wt_swarm_buys` is likewise almost entirely unpopulated
here; `wt_walkback_queue`/`wt_attribution_outcomes` operate at the wrong grain
entirely (per-launch, not per-subprov-fan-out).

**Is this a finding against WATCHTOWER attribution, or a coverage gap?** A coverage
gap, consistent with every prior X65.11–X65.15 audit's own repeated finding that this
walkback-resolved population lacks live-cascade instrumentation. No evidence
encountered in this audit contradicts X65.14/X65.15's conclusions about classifier
precision — those conclusions concerned whether independently-reconstructed evidence
was *consistent* with WATCHTOWER, which it was. This audit narrows what "fan-out
evidence" in those reports actually proved: creator-count among the wallets that
*did* create, not total-recipient composition.

**What should happen next?** Per the user's own explicit direction mid-audit: nothing
further from persisted-data analysis alone — continued DB-only re-querying of the same
tables would not surface new information, since the underlying gap is observational
(no data was ever recorded), not analytical (the data exists but hasn't been
correctly queried). The path forward, if this specific question is judged worth
resolving, is the small, staged RPC follow-up specified in Phase 4 (3 subprovs,
starting with a single `getSignatures` call each) — not a larger classifier redesign
and not a further no-RPC audit of the same evidence.

### Deliverables

Precise identification of the 217-launch (9-subprov) population where creator-vs-buy-swarm
discrimination is unresolved and exactly why (Phase 1); a corrected understanding that
`wt_provisioning_edges`'s prior use as a "fan-out" measure in X65.14/X65.15 was
creator-count, not raw recipient count (Phase 1/3, the audit's central finding);
table-by-table evidence-coverage inspection for every plausible source (Phase 2);
detailed, schema-level explanation of why each table cannot answer the question
(Phase 3); and a concrete, staged, minimum-scope RPC specification for a future
follow-up, not performed here (Phase 4). No code was changed; no database writes
occurred; no UI was modified; no live RPC calls were made.
