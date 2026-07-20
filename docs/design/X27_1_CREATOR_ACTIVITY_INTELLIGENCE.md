# X27.1 — Creator Activity Intelligence

## Objective

Introduce a dedicated Creator Activity section to Discovery that summarises the
historical behaviour of the creator wallet itself, independent of funding
provenance, attribution, infrastructure, and operation identity. Answers
exactly one question: **"What do we already know about this creator?"**

## Phase 1 — Audit of existing creator data

Sources considered and their verdicts:

| Source | Verdict |
|---|---|
| `token_analysis` (core DB) | **Used.** Most complete per-token record; ~96.7% `pf_ws_creator` fill, ~97.3% `earliest_tx_creator` fill; carries `created_at`, `migrated_at`, `market_cap_highest`. |
| `wt_creator_launches` | Rejected — scoped to WATCHTOWER's staged-wallet detection artifact only, not a universal creator ledger. |
| `wt_watchtower_launches` | Rejected — live-cascade-only per its own module docstring; systematically misses historical/backfilled launches; lives in a separate DB file. |
| `migrated_tokens.creator` | Rejected — no confirmed writer path found for this column. |
| `docs/creator_activity_redesign.md` planned schema | Rejected as a source — that design is about funding-extraction coverage/scan-state bookkeeping, a different concern entirely. |

Verified live: wallets with extremely high launch counts (15,775+) are not in
the known-infrastructure registry (`is_known_account()`/`get_funder_label()`)
— almost certainly bot/factory wallets or shared program-derived-authority
artifacts, not genuine individual creators. Creator Activity does not attempt
to classify or filter these; it reports counts honestly and lets the analyst
judge, per the "describe only what's persisted" principle.

## Phase 2 — Canonical metrics (token_analysis-derived only)

| Metric | Derivation |
|---|---|
| `first_observed` | MIN(created_at) |
| `last_activity` | MAX(created_at) |
| `launches_created` | COUNT(*) |
| `successful_migrations` | COUNT(migrated_at IS NOT NULL) |
| `migration_rate_pct` | successful_migrations / launches_created × 100 |
| `active_lifetime_seconds` | last_activity − first_observed |
| `average_launch_cadence_seconds` | active_lifetime_seconds / (launches_created − 1), when > 1 launch |
| `peak_market_cap` (best) | MAX(market_cap_highest), NULLs excluded |
| `peak_market_cap_coverage` | "N of M launches" — honest NULL-coverage disclosure |

No invented metrics. No median-migration-time metric (redundant with cadence).
Market-cap absence is reported explicitly ("not captured"), never treated as
zero.

## Phase 3 — Section boundaries

Creator Activity **never**: infers attribution, implies WATCHTOWER
involvement, implies operational identity, or discusses funding sources. It
reads only `token_analysis`, keyed on the creator wallet address.

## Phase 4 — Implementation

- `src/ops/creator_activity.py` — new `CreatorActivityService`, read-only
  (`PRAGMA query_only=ON`), single `core_db_path` connection (no ops-DB
  dependency, since `token_analysis` lives in the core DB only).
- Creator-identity resolution mirrors `evaluate_launcher_profile()`'s trust
  rule (`src/ops/attribution_outcome.py`): `pf_ws_creator` used exclusively
  when the column exists; `earliest_tx_creator` only as a fallback when
  `pf_ws_creator` doesn't exist as a column — never merged via COALESCE,
  since `earliest_tx_creator` alone can be a shared transaction authority.
- Wired into `src/discovery/service.py::_entity()` following the exact
  three-point `operational_behaviour` pattern: `None` default in `_empty()`,
  the real computed value, and a second `None` in the operator-resolution
  return path. Runs unconditionally whenever `creator` is resolved, exactly
  like `operational_behaviour`.
- UI: `templates/discovery.html` adds `creatorActivity()`, rendered as its
  own Level 2 disclosure ("Creator activity"), appended after Operational
  Behaviour and before Attribution chain — a separate card, not folded into
  an existing one.

## Phase 5 — Repeat-creator classification (measurable thresholds)

| Status | Rule |
|---|---|
| Single launch | `launches_created <= 1` |
| Repeat creator | `launches_created > 1`, not Highly Active |
| Highly active creator | `launches_created >= 10` **and** `last_activity` within the last 7 days |

Highly Active requires both a volume bar and a recency bar so a creator with
10+ launches, all long in the past, correctly reads as Repeat Creator, not
Highly Active — "highly active" means currently active, not merely
historically prolific. No arbitrary/unmeasurable labels.

## Phase 6 — Historical performance

Peak market cap (`market_cap_highest`) is surfaced only from persisted data,
with an honest coverage line when some or all launches lack the field. No new
market-cap calculation was introduced; where unavailable, the UI explicitly
states "not captured" rather than displaying zero or omitting the fact of
absence.

## Phase 7 — Cross-platform duplication audit

| Section | Question answered | Data source |
|---|---|---|
| Funding Walkback | Who funded this creator, by what mechanism | `wt_wrap_close_candidates`, `wt_walkback_queue` |
| Operational Behaviour | How did this specific launch behave (timing, infra pattern) | `wt_active_subprov_sessions`, provisioning facts |
| Attribution Outcome | Where did attribution terminate | `wt_attribution_outcomes` |
| **Creator Activity** | **What has this creator wallet done, historically** | **`token_analysis`** |

No field overlap; Creator Activity's output was grepped to confirm it never
emits the strings "treasury", "sub-provisioner"/"subprov", "watchtower",
"attribution", "wrap-close", or "funding" (enforced by
`test_report_never_mentions_funding_or_infrastructure_terms`).

## Phase 8 — Future-proofing

The implementation is pure SQL aggregation over `token_analysis` with no
row-count assumptions or hardcoded limits — it scales to a creator with
dozens or thousands of launches unchanged. Funding provenance is never read
or used to classify the creator (no join to any funding/treasury/subprov
table). Column-existence checks (`_columns()`) mean the service degrades
gracefully (returns `None` or omits a metric) rather than erroring if
`token_analysis`'s schema evolves.

## Phase 9 — Tests

`tests/test_x27_1_creator_activity_intelligence.py` — 15 tests: no-creator/
missing-table handling, zero-launch explicit report, single/repeat/highly-
active classification (including the volume-without-recency edge case),
migration-rate and cadence arithmetic, honest peak-market-cap absence
reporting, `pf_ws_creator`-over-`earliest_tx_creator` precedence (including
the shared-authority non-merge case), column-absent fallback, zero DB
mutation (SHA-256 before/after), forbidden-terminology check, Discovery
`_empty()` key wiring, and template-level rendering/placement assertions.

## Live verification

`JD6rVaerbyz6wjQ433nrw6bFTgFrp46MiYmi8EtUAfsG`: 9 launches, 9 migrations
(100%), cadence ≈111,336s, peak market cap $205,569.90 (5 of 9 launches
covered), classified `REPEAT_CREATOR` (high volume but `last_activity` outside
the 7-day recency window) — matches all Phase 2/5/6 rules exactly.

## Confirmation

No detection, walkback, attribution, operation-identity, or schema logic was
changed. `src/ops/creator_activity.py` is new and additive; the only existing
files touched are `src/discovery/service.py` (three-point wiring, mirroring
the `operational_behaviour` precedent) and `templates/discovery.html`
(new independent card). All existing X26.x/X20.6 test assertions pass
unchanged.
