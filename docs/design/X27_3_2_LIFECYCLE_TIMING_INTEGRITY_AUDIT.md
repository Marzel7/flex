# X27.3.2 — Lifecycle Timing Integrity Audit

**Investigation only. No code, schema, detection, or attribution changes were made.**

Triggered by X27.4 (Behavioural Investigation Queue) discovering an
implausible birth→migration lifecycle distribution (median 1 second,
p90 11 seconds) in `token_analysis.created_at`/`migrated_at` — this audit
determines the canonical, trustworthy source for every lifecycle timestamp
before any behavioural archetype is defined on top of it.

## Q1 — What is the authoritative CREATE timestamp?

**There is no single authoritative, broadly-covered CREATE timestamp.**
Two candidate sources exist, with sharply different reliability and
coverage:

| Source | Reliability | Coverage (last 24h) |
|---|---|---|
| `token_analysis.created_at` | **Unreliable for ~96% of rows** | 515/515 (always populated) |
| `wt_watchtower_launches.create_time` | **Reliable** (derived from `tx.get("blockTime")`, `src/core/ws_cascade.py:2821,3201` — no wall-clock fallback found) | 42/43 rows total, live-cascade-scoped only |

`token_analysis.created_at` is populated by
`_extract_birth_timestamp()` (`src/core/pumpfun_curve_listener.py:5489-5497`):
tries `tx_data.get("blockTime")` first, but **silently falls back to
`datetime.utcnow()`** when no CREATE transaction was captured. Measured
live: only **19 of 515** last-24h migrated rows (3.7%) have a non-null
`create_tx_signature` at all — meaning for the other 96.3%, `created_at`
was never derived from a real transaction and reflects whatever moment
the row was first written (typically at migration-detection time, via the
same `_create_minimal_token_entry` bootstrap that fires when a migration
is first seen — this is why `created_at` and `migrated_at` land within
about a second of each other for most rows).

Even restricting to the 19 rows with a genuine `create_tx_signature`, only
9 of 19 (47%) show a plausible **positive**, multi-second-to-hour
birth→migration gap; the other 10 show a **negative or near-zero** gap —
`created_at` was written *after* `migrated_at` for those rows, which is
physically impossible for a true CREATE→migration relationship. This
indicates `created_at` can be asynchronously backfilled/overwritten even
on rows that eventually acquire a real signature, so `create_tx_signature
IS NOT NULL` alone is not a sufficient trust filter — the gap must also be
sane (positive) before treating `created_at` as reliable for that row.

## Q2 — What is the authoritative migration timestamp?

**`token_analysis.migrated_at` is reliable.** It is set to `int(time.time())`
at the moment the live WS listener detects the migration signal
(`src/core/pumpfun_curve_listener.py:8197`), immediately before the
minimal-entry and DB-write calls. Live cross-check: for one sampled mint
(`EJcLECjeLUkbyh8s1ux4LQHPKtendBnYb7GLV2KESyDe`), the stored
`migrated_at=1784240122` versus the true on-chain `getBlockTime` for its
recorded `migration_slot=433357980` was **1784240116** — a 6-second
difference, consistent with normal real-time detection lag, not a
backfill artifact. `migrated_at` is populated for effectively 100% of
migrated rows (it is the defining condition of "migrated").

## Q3/Q4 — Which tables hold ingestion timestamps vs. on-chain timestamps?

| Table.column | Type | Basis |
|---|---|---|
| `token_analysis.created_at` | **Mixed** — on-chain when `create_tx_signature` exists *and* the gap to `migrated_at` is positive (~1.75% of rows); ingestion time otherwise (~96%+) | `_extract_birth_timestamp()` fallback confirmed in code |
| `token_analysis.migrated_at` | **On-chain-accurate proxy** (ingestion time, but empirically within single-digit seconds of true block time) | `int(time.time())` at WS-detection, verified against `getBlockTime` |
| `token_analysis.first_observed_at` | **Ingestion time**, and a different concept entirely (first valid price/market-cap snapshot, not birth) | `src/core/price_service.py:534-535`, write-once-if-null |
| `wt_watchtower_launches.create_time` | **On-chain** | `tx.get("blockTime")`, `src/core/ws_cascade.py:2821/3201` |
| `wt_watchtower_launches.create_to_migration_secs` / `birth_to_launch_seconds` / `fanout_*` | **On-chain-derived** (computed from the above) | Same table; only 43 rows exist total |
| `creator_funders.first_detected_at` | **Ingestion time**, uniformly | All 3 write sites use SQLite `CURRENT_TIMESTAMP` (`main.py:31729-31732`, `funder_helius_extractor.py:203-207`, `realtime_creator_funding_extractor.py:770-774`); confirmed by an existing code comment at `main.py:31910`: *"first_detected_at reflects index time"* |
| `wt_provisioning_sessions.*_block_time` | **On-chain** (RPC-derived `blockTime`, confirmed in `walkback_worker.py`) | Reliable, but this is funding/provisioning evidence — out of scope for behavioural archetype definition per X27.4's governing principle ("no funding, no attribution") |

## Q5 — Can birth→CREATE be reconstructed?

Not generally. "Birth" (a creator wallet's first appearance/first funding)
is only resolvable via `creator_funders.first_detected_at`, which is
ingestion-time only and has just 17.8% coverage against the last-24h
migrated-creator population (63 of 354 distinct creators). Even where a
row exists, the timestamp does not represent a genuine on-chain moment.
**Birth→CREATE cannot currently be reliably reconstructed for the general
population.**

## Q6 — Can CREATE→migration be reconstructed?

Only for the ~1.75% of rows (9 of 515 in the sampled 24h window) where
`token_analysis.created_at` has a real signature and a positive gap to
`migrated_at`, or for the 42-of-43 rows in `wt_watchtower_launches` with
`create_time`/`create_to_migration_secs` already computed. **Reliable, but
severely coverage-limited** — well under 10% of the general population in
either source.

## Q7 — Can birth→migration be reconstructed?

No — this compounds Q5's near-total unavailability with Q6's severe
coverage gap. There is currently no broadly-covered, trustworthy source
for the full birth→migration lifecycle duration.

## Q8 — Available for all launches or only observed launches?

**Only a small, non-representative subset of "observed" launches** (those
where the live cascade or curve listener happened to capture a genuine
CREATE transaction) have trustworthy lifecycle timing at all:

| Population | n | % of 24h migrated total (515) |
|---|---|---|
| Migrated (baseline population) | 515 | 100% |
| `create_tx_signature IS NOT NULL` | 19 | 3.7% |
| ...and gap to `migrated_at` is positive/sane | 9 | 1.75% |
| `wt_watchtower_launches` rows (any) | 43 | 8.3%* |
| ...with `create_time` populated | 42 | 8.2%* |
| Creator has a `creator_funders` row | 63 of 354 distinct creators | 17.8% of creators, not launches |

*`wt_watchtower_launches` is a separate, live-cascade-scoped table and its
43 rows are not guaranteed to be a subset of the exact same 515-launch
population measured from `token_analysis` in this same window — this
figure is presented for scale comparison only, not a validated overlap
count.

## Conclusion and Recommendation

**`token_analysis.migrated_at` is the one broadly-covered, trustworthy
timestamp available for essentially the entire migrated-launch
population.** No column currently provides trustworthy, broadly-covered
birth or CREATE timing — "Rapid Birth → Migration" and "Delayed Migration"
archetypes as originally conceived in X27.4 Phase 2 cannot be built on
`token_analysis.created_at` without inheriting a ~96%-unreliable signal.

Recommended path for X27.4:
1. **Do not use `token_analysis.created_at`** as a lifecycle-duration input for archetype classification.
2. Where `wt_watchtower_launches` provides genuine `birth_to_launch_seconds`/`create_to_migration_secs` for a mint, that evidence may be used with confidence — but this will cover a small minority of launches (this audit measured 8.2% scale-for-scale; exact overlap with any given 24h population was not independently verified and would need re-checking at implementation time).
3. For the remaining majority of launches lacking trustworthy CREATE-side timing, any birth/lifecycle-duration archetype should route to **Unclassified Behaviour** rather than guess from an unreliable timestamp — consistent with the platform's existing "describe only what's persisted, honestly disclose absence" discipline (as applied in X27.1's Creator Activity peak-market-cap handling).
4. Archetypes that depend only on `migrated_at` (e.g. "Burst Launches" — clustering of migration events in time) remain soundly buildable on existing data without this caveat.
5. If a genuine "birth→migration" archetype is wanted in the future, it would need a new, purpose-built, on-chain-derived birth signal with real population coverage — out of scope for this audit or for X27.4's immediate implementation.

No code, schema, or data was changed in this investigation.
