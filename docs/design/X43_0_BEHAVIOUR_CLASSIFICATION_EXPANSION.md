# X43.0 — Behaviour Classification Expansion (Read-Only Classification)

Follows [X42.0](X42_0_DISCOVERY_UI_REFINEMENT.md), which left several Behaviour
categories as disabled "Planned" placeholders because that task prohibited backend work.
This task explicitly authorizes backend implementation, scoped strictly to read-only
behaviour classification — no architecture, schema, attribution, scoring, treasury
confirmation, Operator, Operation, or confidence changes.

## What was implemented

`src/ops/operational_behaviour_tags.py` — extended `build_behaviour_classification()`
with four new additive tags, computed entirely from data already read by this function:

- **`RAPID_MIGRATION`** (<300s), **`MIGRATION_5_TO_15M`** (300–900s),
  **`DELAYED_MIGRATION`** (≥900s) — bucketed from
  `token_analysis.migrated_at - token_analysis.created_at`, both columns already present
  on every row this function already queries (it already fetches `pf_ws_creator`/
  `earliest_tx_creator` from the same table for Repeat Creator). `created_at` is stored
  in two formats in this table (ISO-8601 strings and, for a real ~25% subset of rows,
  epoch-as-text) — `_parse_token_analysis_timestamp()` handles both defensively; a row
  that parses as neither format is excluded from migration-timing classification, never
  guessed at.
- **`CREATOR_RECYCLING`** — the same creator wallet (the identical `creator_of` lookup
  Repeat Creator already builds) appears on more than one distinct mint in the current
  population. Exact wallet-address reuse only, per the task's explicit instruction — no
  clustering, no inferred identity.
- **`PROVISIONING_BURST` was deliberately NOT implemented.** Grepped the entire codebase
  for `PROVISIONING_BURST`/`provisioning_burst`/"Provisioning Burst" — zero hits anywhere.
  No canonical definition exists to reuse, and the task explicitly instructs leaving this
  category hidden rather than inventing a threshold. It is omitted entirely (not stubbed
  with a zero count, not added to `BEHAVIOUR_ORDER`).

The three existing tags (`RAPID_BIRTH_LAUNCH`, `BURST_LAUNCH`, `REPEAT_CREATOR`) are
byte-for-byte unchanged — same lookup functions, same thresholds, same evaluation order.

## Zero new plumbing needed downstream

`src/ops/operational_intelligence.py`'s `build_operational_intelligence()` and
`build_hierarchy()` already iterate `BEHAVIOUR_ORDER`/`BEHAVIOUR_LABELS` dynamically to
build `behaviour_summary` and the Topology→Behaviour tree — extending those two module-
level tuples in `operational_behaviour_tags.py` was sufficient for the new tags to flow
through the existing `/api/ops-v2/operational-intelligence` route with **no route code
changes**. Verified directly: `build_operational_intelligence()` and `build_hierarchy()`
both return the four new tags correctly nested and labeled, with real counts, using
exactly the same function calls as before.

`templates/discovery.html` — removed the X42.0 disabled "Planned" placeholder block
entirely (the four hardcoded labels + non-clickable styling). The Behaviour tree level
now renders purely from whatever `behaviour_summary`/hierarchy children the backend
returns (`count>0` filter unchanged from before X42.0), satisfying the "UI must never
contain hardcoded behaviour names" contract exactly as specified.

## Validation

- **No database writes**: both `ops_conn` and `core_conn` in
  `build_behaviour_classification()` carry `PRAGMA query_only=ON`, unchanged from the
  original — the new code only adds `SELECT`-side columns to an existing query and two
  read-only in-memory dict passes (`migration_seconds_of`, `creator_mint_counts`).
- **Existing tag counts identical, verified by direct A/B run** (not assumed): loaded the
  pre-X43.0 committed version of the module standalone (`git show HEAD:...`) and ran it
  against the real databases with a 1-hour window, then ran the new version the same way.
  `RAPID_BIRTH_LAUNCH=0`, `BURST_LAUNCH=0`, `REPEAT_CREATOR=8`, `total_launches=16` —
  identical in both runs. New tags in the same run: `RAPID_MIGRATION=11`,
  `MIGRATION_5_TO_15M=0`, `DELAYED_MIGRATION=1`, `CREATOR_RECYCLING=2` — purely additive.
- **No attribution/treasury/Operation/Operator changes**: this module has no import path
  into `treasury_bank.py`, `operation_store_v2.py`, or any confirmation/merge logic — it
  only reads `wt_attribution_outcomes` and `token_analysis`, both already-existing tables,
  and was not touched in those other modules.
- **Full targeted test suite** (`behaviour`/`operational_intelligence`/`discovery`
  keyword match, 336 tests): 13 failures, 323 passes. **Verified all 13 are pre-existing
  and unrelated**: swapped the two changed files (`operational_behaviour_tags.py`,
  `discovery.html`) back to their exact last-committed versions via `git show HEAD:...`
  (not `git stash`, to avoid the stash-recovery incident from X41.0/X42.0), re-ran the
  same test files, got the identical 13 failures against the unmodified code, then
  restored my changes and re-confirmed the same 13/323 split. None of the 13 touch
  `operational_behaviour_tags.py`, `operational_intelligence.py`, or the changed template
  sections — they're in unrelated modules (`operational_behaviour.py`'s subprov-facts
  query, `intelligence_refresh.py`'s watchlist logic, and two other discovery-template
  string-assertion tests already known stale from X41.0/X42.0's own verification).

## Answer to the stated success criterion

The Behaviour section now describes real, backend-computed operational behaviour derived
entirely from data already present (`token_analysis.created_at`/`migrated_at` for
migration timing, the existing creator-resolution lookup for recycling), with zero new
tables, cached state, or migration jobs. The UI renders purely from backend output — no
hardcoded behaviour names remain. `PROVISIONING_BURST` was correctly left absent rather
than invented, per the task's explicit instruction. Nothing in the canonical X39–X41
architecture was touched, and the three pre-existing behaviour tags produce byte-identical
counts to before this change, confirmed by direct A/B execution against real data.
