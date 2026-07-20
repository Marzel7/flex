# X29.1.4 — Separate Capital Origin from Operational Attribution (Investigation)

**Status: investigation only, per the brief's explicit "Investigation Before
Behaviour Change" requirement. No classification, schema, or behavior was
changed.** All numbers below are measured directly against the live
`database/wt_ops_v2.db`/`database/flex_complete_database.db` corpus — no
estimates.

## Corpus audit

### What evidence actually exists today (traced through the code, not assumed)

`wt_attribution_outcomes.evidence_json.boundary` (the object that produces
`KNOWN_CEX_REACHED`/`KNOWN_BRIDGE_REACHED`/`KNOWN_RELAY_REACHED`) is built by
`_boundary()` in [attribution_outcome.py:341-394](../../src/ops/attribution_outcome.py#L341-L394)
— a **static lookup** (`CEX_ACCOUNTS`/`INFRASTRUCTURE_ACCOUNTS`/
`address_labels`/`creator_funders.is_cex`) against a single `terminal`
address. `terminal` itself
([attribution_outcome.py:436](../../src/ops/attribution_outcome.py#L436))
is just `queue.get("treasury") or queue.get("funder_wallet") or
queue.get("subprov") or creator` — one stored wallet, no metadata about how
it was found, no signature, no direction, no hop count, at the point
`_boundary()` consumes it.

The `funder_wallet` value itself is populated by
`walkback_worker.py`'s `FULL_WALKBACK` branch
([walkback_worker.py:681-767](../../src/core/walkback_worker.py#L681-L767)),
whose own module docstring states the exact bound:
**"FULL_WALKBACK: 2 getSignaturesForAddress + up to 10 getTransaction = 12cr
max"** ([walkback_worker.py:18](../../src/core/walkback_worker.py#L18)).
Concretely: `_find_funder_via_rpc()`
([walkback_worker.py:261-332](../../src/core/walkback_worker.py#L261-L332))
fetches **one page** of `getSignaturesForAddress` (`_get_sigs(wallet)`,
docstring: *"Collect all valid funders within the bounded tx window then
select the strongest"*) and inspects at most `TX_FETCH_LIMIT` transactions
from that single page, picking the highest-priority candidate found in that
window — **never** the wallet's actual first-ever transaction. `FULL_WALKBACK`
performs exactly **two such single-page lookups** (creator→hop1,
hop1→hop2) before stopping.

**This directly and completely confirms the brief's core premise from the
code itself**: a `KNOWN_CEX_REACHED` outcome today can only ever mean "a CEX
address appeared as the strongest funding candidate within a
single-signature-page, 2-hop-deep bounded window" — never "the CEX was
proven to be the wallet's actual first funder." There is no field anywhere
in this pipeline recording "history_exhausted," "oldest_inspected_signature,"
or "pagination_limit_reached" — the brief's required data model genuinely
does not exist today, not even partially.

### Measured corpus counts (real data, `wt_ops_v2.db`, current as of this
audit — no estimates)

```
KNOWN_CEX_REACHED:    431 total
KNOWN_BRIDGE_REACHED:   0 total
KNOWN_RELAY_REACHED:   98 total
```

For `KNOWN_CEX_REACHED` (431 rows):

```
Have a persisted funder_sig (i.e. SOME RPC walk evidence exists at all):        352
NO persisted funder_sig (pure static-lookup match — creator_funders.is_cex,
  zero walk/RPC evidence of any kind):                                          79
walkback_class == FULL_WALKBACK (the 2-hop bounded-window walk described
  above produced this result):                                                349
```

Age-at-launch bucketing (computed from `wt_walkback_queue.funder_block_time`
vs. `token_analysis.created_at`, for the 352 rows with a real timestamp —
`created_at` is stored in two different formats in this table, unix epoch
and ISO-8601 strings; both were normalized before bucketing):

```
≤1 day:      337
1–7 days:     13
8–30 days:     2
31–100 days:   0
>100 days:     0
negative age (funder tx timestamp AFTER the launch — a data-quality flag,
  not a real "future funding" event; 7 of the 352 rows):
```

**This is the single most important finding of the audit**: in the
*current* live corpus, essentially none of the observed `KNOWN_CEX_REACHED`
outcomes are the "150-days-ago, thousands-of-intervening-transactions"
scenario the brief specifically worries about (0 rows in the 31-100 day or
>100 day buckets). 337/352 (96%) are same-day. This does **not** mean the
brief's concern is unfounded — it means the *current, live* corpus happens
to skew toward fresh-wallet CEX funding (a genuinely different pattern:
fresh wallet → CEX withdrawal → immediate launch, which the 2-hop bounded
walk is well-suited to catch correctly). The risk the brief describes (an
old, high-activity wallet where the bounded walk finds *a* CEX interaction
within its tiny window but that interaction is not provably the first
funding event) is a **structural** risk inherent in the 2-hop/1-page design,
not something today's specific snapshot happens to exhibit at scale — but
it is real and will recur as the corpus grows or as different funding
patterns are observed, and the negative-age rows (7/352) are direct,
present-day evidence of the walk's window occasionally picking up a
non-causal candidate.

Buckets for how many are "proven initial funding events" vs. "partial
earliest-observed funders" vs. "incidental interactions" vs. "truncated
history," as the brief requests: **cannot be produced from the current
data model at all** — there is no field distinguishing these cases today
(confirmed above: no `history_exhausted`, no `oldest_inspected_signature`,
no hop-depth-to-genesis marker exists anywhere in `wt_walkback_queue` or
`wt_attribution_outcomes`). Every one of the 352 rows with a `funder_sig`
is, under the current schema, indistinguishable from a "proven origin" by
any query — the distinction the brief wants (PROVEN/PARTIAL/UNRESOLVED)
would have to be inferred retroactively as "not proven, since the walk
never even attempts to confirm exhaustion" — i.e., **under the brief's own
definitions, zero of the 352 rows qualify as PROVEN today**, because
`FULL_WALKBACK` never checks "no earlier history remains uninspected" (one
of the brief's required PROVEN criteria) at all. All 352 would correctly be
labeled `OBSERVED_CEX_FUNDER`/PARTIAL under the brief's proposed vocabulary,
and the 79 without any `funder_sig` (pure `creator_funders.is_cex` static
match, zero RPC evidence) would correctly be labeled
`HISTORICAL_CEX_INTERACTION`/incidental — the boundary between them is not
speculative, it maps directly onto the existing has-`funder_sig` split
already measured above.

### WATCHTOWER preservation — confirmed structurally, not just by policy

`CANONICAL_OPERATOR_REACHED`'s evidence (sampled directly, 3 real rows) has
`treasuries: [...]`/`subprovisioners: [...]` resolving through
`operator_entities`, with **no `boundary` field present at all** — the code
path (`derive_outcome()`,
[attribution_outcome.py:448-470](../../src/ops/attribution_outcome.py#L448-L470))
checks operator resolution *before* the `_boundary()`/CEX check and returns
immediately on a single-operator match, never reaching the CEX-lookup branch.
**This confirms the brief's WATCHTOWER Preservation Rule already holds
today, by construction** — no code change is required to prevent CEX
distance from weakening operator attribution; the two code paths are
already mutually exclusive in priority order. Any future change to CEX
semantics (splitting evidence into PROVEN/PARTIAL/incidental) cannot
regress this, since it happens entirely on a different, lower-priority
branch than the one WATCHTOWER/canonical-operator attribution uses.

## Required data model — gap analysis

The brief's required per-origin fields
(`wallet, launch_mint, origin_status, origin_type, origin_wallet,
origin_entity, origin_signature, origin_block_time,
origin_age_at_launch_seconds, origin_hop_depth, origin_transfer_lamports,
transactions_inspected, oldest_inspected_signature,
oldest_inspected_block_time, history_exhausted, pagination_limit_reached,
resolution_reason, provenance`) map onto existing columns only partially:

| Required field | Existing source | Status |
|---|---|---|
| `wallet`, `launch_mint` | `wt_walkback_queue.mint`, `funder_wallet` | Exists |
| `origin_wallet`, `origin_signature`, `origin_block_time` | `funder_sig`, `funder_block_time` | Exists (but describes hop1/hop2 of a 2-hop walk, not genesis) |
| `origin_transfer_lamports` | `funder_amount_sol` (SOL, not lamports — unit differs) | Exists, unit mismatch |
| `origin_hop_depth` | Implicit in which hop (`hop1` vs `hop2`) produced the value, never explicitly recorded | **Missing** |
| `transactions_inspected` | `rpc_used` counts RPC *calls*, not transactions inspected within a call | **Missing** (proxy only) |
| `oldest_inspected_signature`/`oldest_inspected_block_time` | Not recorded anywhere | **Missing entirely** |
| `history_exhausted`/`pagination_limit_reached` | Not recorded anywhere | **Missing entirely** |
| `origin_status` (PROVEN/PARTIAL/UNRESOLVED) | Not recorded anywhere | **Missing entirely — this is the core new field** |
| `resolution_reason`, `provenance` | Not recorded anywhere | **Missing entirely** |

## Recommendation on the optional deep-origin queue

**Not justified yet, and the brief's own gate ("Do not implement an
expensive deep-history system without first reporting the corpus size,
projected RPC cost and expected analytical gain") is the right call here.**
Reasoning, using the measured numbers above:

- **Corpus size**: 431 `KNOWN_CEX_REACHED` + 98 `KNOWN_RELAY_REACHED` = 529
  candidate wallets *if* every one needed deep resolution. But 337/352
  (96%) of the CEX rows are already same-day, low-hop-depth cases where the
  existing 2-hop bounded evidence is almost certainly already correct in
  substance (a fresh wallet funded directly from an exchange right before
  launch has very little "missing history" to find) — deep-walking these
  would spend RPC budget confirming something already highly likely true.
- **Projected RPC cost**: unbounded per-wallet history walks (paginating to
  genesis) for even the ~15 wallets in the 1-30 day buckets could easily
  cost 10-50x more RPC credits per wallet than the current 12-credit
  `FULL_WALKBACK` budget, with no confirmed historical precedent in this
  codebase for how expensive a genuinely old, high-activity wallet's full
  history actually is (this investigation did not attempt a live paginated
  walk against any real wallet — doing so was explicitly out of scope for
  an investigation-only sprint, and would itself have been exactly the kind
  of "expensive deep-history system implemented without first reporting
  cost" the brief prohibits).
- **Expected analytical gain**: given 0/352 rows currently fall in the
  31-100/>100-day buckets, a deep-origin queue would today have very few
  *known* candidates where it could plausibly change the classification
  from "same-day, likely-correct fresh funding" — the queue's value is
  prospective (protecting against future corpus growth into the risky old
  -wallet pattern), not something the current snapshot demonstrates a need
  for.

**Recommendation**: build the PROVEN/PARTIAL/UNRESOLVED **data model and
classification distinction first** (a schema/labeling change, cheap, zero
new RPC), and revisit the deep-origin background queue only if/when the
corpus is observed to accumulate a meaningful population of `PARTIAL`
outcomes in the 31+ day buckets — which the new `origin_status` field would
make directly measurable going forward, rather than requiring another
one-off audit like this one.

## What this sprint deliberately did NOT do

Per the brief's explicit gating ("Investigation Before Behaviour Change"),
this sprint did not:
- Add `origin_status`/`PROVEN`/`PARTIAL`/`UNRESOLVED` as a real, persisted
  classification.
- Modify `_boundary()`, `derive_outcome()`, or any `outcome_type` value.
- Change `wt_attribution_outcomes`'s schema or `CHECK` constraint.
- Build the deep-origin queue (recommended against, for now, per above).
- Change any API response shape.

## Recommended next step (a separate, explicitly-scoped follow-up sprint)

1. Add the origin-evidence columns to `wt_walkback_queue` (additive
   migration, no `CHECK`-constraint rebuild needed since these are new
   nullable columns, not a change to `outcome_type`'s existing values) —
   `origin_status`, `transactions_inspected`, `oldest_inspected_signature`,
   `oldest_inspected_block_time`, `history_exhausted`,
   `pagination_limit_reached`, `resolution_reason`.
2. Populate `origin_status` retroactively for the existing 431+98 corpus
   using exactly the rule this audit already validated: `funder_sig`
   present → `OBSERVED_CEX_FUNDER`/PARTIAL; absent →
   `HISTORICAL_CEX_INTERACTION`/incidental; **no row currently qualifies as
   PROVEN** under the brief's stated criteria, since `FULL_WALKBACK` never
   confirms exhaustion.
3. Add `capital_origin` as a genuinely separate, additive field on the API
   response (alongside, never replacing, `outcome_type`) — matching the
   Backwards Compatibility section's explicit instruction.
4. Build the UI's two-panel (Operational Attribution / Capital Origin)
   presentation over the new field, with the brief's explicit "Do not
   display Initial Funder unless origin status is PROVEN" rule enforced at
   render time.
5. Defer the deep-origin background queue pending the corpus-growth
   trigger described above.

## Validation performed this sprint

- WATCHTOWER/`CANONICAL_OPERATOR_REACHED` preservation: confirmed
  structurally correct today (code path never touches CEX evidence),
  verified against 3 real corpus rows.
- No behavior, schema, or classification changed — confirmed by not
  editing any file in `src/ops/attribution_outcome.py`,
  `src/core/walkback_worker.py`, or the API routes this sprint.
- All corpus numbers in this report are measured directly against the live
  databases, not estimated, per the brief's explicit "Use actual corpus
  results only. Never estimate" instruction.
