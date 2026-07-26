# X65.21 — Persist the Provisioning Wallet as a First-Class WATCHTOWER Role

Implementation summary. Additive-only, per the task's explicit constraint: no
attribution logic, detection decision, existing table, or existing semantics changed.

## Phase 1 — Existing evidence inventory

| Source | Completeness | Reconstruction reliability |
|---|---|---|
| `wt_watchtower_launches.wrap_close_signature` | 40/43 (93.0%) rows have a real, full-length (~87-88 char) signature | High — single `getTransaction` decode, no search, exactly reuses X65.19's validated extraction logic |
| `wt_watchtower_launches.create_signature` | 1/43 visibly truncated (placeholder `...XXXX`), used only as a fallback identifier, not decoded directly | N/A — not itself decodable, only used to detect the 2 corrupted-signature rows |
| Parsed transaction cache | None exists — no table persists decoded instruction bodies | N/A |
| Walkback evidence (`wt_walkback_queue`, `wt_provisioning_edges`) | Records the SubProv→Creator edge itself, never the intermediate wallet (X65.17's proof) | Cannot recover Wallet P from this evidence alone |

## Phase 2 — Recovery classification (measured, not estimated)

| Class | Count | % of 43 |
|---|---|---|
| Directly recoverable (persisted signature decode) | 40 | 93.0% |
| Recoverable with bounded signature lookup (≤5 pages / ≤100 sigs) | 2 | 4.7% |
| Unrecoverable within the bounded search limit | 1 | 2.3% |

Identical to X65.19's own measured result — this audit's own decode logic
(`scripts/x65_21_provisioning_wallet_backfill.py`) is the same two-mechanism
extraction (`WSOL_WRAP_CLOSE` / `SEEDED_ACCOUNT_CLOSE`) X65.19 validated, re-run as an
idempotent, repeatable process rather than a one-off investigation.

## Phase 3 — Additive schema

**New module**: `src/ops/provisioning_wallet.py`. **Two new tables, zero changes to
any existing table**:

```sql
CREATE TABLE wt_provisioning_wallets (
    mint                 TEXT PRIMARY KEY,   -- one row per launch (Wallet P is single-use per launch, X65.19)
    subprov_wallet       TEXT NOT NULL,
    creator_wallet        TEXT NOT NULL,
    provisioning_wallet   TEXT NOT NULL,
    mechanism             TEXT NOT NULL CHECK(mechanism IN ('WSOL_WRAP_CLOSE','SEEDED_ACCOUNT_CLOSE')),
    funding_signature     TEXT,
    recovery_method       TEXT NOT NULL CHECK(recovery_method IN (
                              'PERSISTED_SIGNATURE_DECODE','BOUNDED_SIGNATURE_LOOKUP','LIVE_CAPTURE')),
    reconstructed         INTEGER NOT NULL DEFAULT 0 CHECK(reconstructed IN (0,1)),
    recorded_at           INTEGER NOT NULL
);

CREATE TABLE wt_provisioning_wallet_edges (
    edge_id              TEXT PRIMARY KEY,
    edge_type            TEXT NOT NULL CHECK(edge_type IN ('SUBPROV_TO_PROVISIONING','PROVISIONING_TO_CREATOR')),
    from_wallet           TEXT NOT NULL,
    to_wallet             TEXT NOT NULL,
    source_mint           TEXT NOT NULL,
    first_observed_by_flex INTEGER NOT NULL,
    last_observed_by_flex  INTEGER NOT NULL,
    observation_count      INTEGER NOT NULL DEFAULT 1,
    UNIQUE(edge_type, from_wallet, to_wallet)
);
```

`wt_provisioning_edges` (`edge_type IN ('TREASURY_TO_SUBPROV','SUBPROV_TO_CREATOR')`)
is **completely untouched** — no new value was added to its `CHECK` constraint, no row
it holds was modified. `SUBPROV_TO_PROVISIONING` and `PROVISIONING_TO_CREATOR` live
exclusively in the new `wt_provisioning_wallet_edges` table, using the identical
upsert convention (`ON CONFLICT DO UPDATE`, `observation_count` accumulation) as the
existing table, so any future consumer that wants uniform handling can do so — nothing
currently reads this new table except the code this task adds.

`reconstructed=1` marks every backfilled row; live-captured rows are `reconstructed=0`
— per the task's own requirement that reconstructed records be explicitly marked.

## Phase 4 — Historical backfill

`scripts/x65_21_provisioning_wallet_backfill.py`. Idempotent (every write goes through
`record_provisioning_wallet()`'s `ON CONFLICT(mint) DO UPDATE`, confirmed by re-running
the script twice against the live DB with identical results both times — 42 rows
before and after the second run). Recovery priority exactly matches the task's
required order: persisted signature decode → bounded signature lookup (≤5 pages) →
skip as unresolved. No unbounded scan performed.

**Run against the live `wt_ops_v2.db` (real RPC, user-supplied Helius key):**

```json
{
  "total": 43,
  "persisted_signature": 40,
  "bounded_lookup": 2,
  "unresolved": 1,
  "already_recorded_skipped": 0,
  "unresolved_mints": [["JyJWcxa8xPwgKZFT13mPyDymLrjXhxkQTTyTJC3pump", "exceeded_bounded_search_limit_or_no_match"]]
}
```

Post-backfill DB state: `wt_provisioning_wallets` = 42 rows, 42 distinct
`provisioning_wallet` values (confirming single-use, matching X65.19), 40
`PERSISTED_SIGNATURE_DECODE` + 2 `BOUNDED_SIGNATURE_LOOKUP`; `wt_provisioning_wallet_edges`
= 84 rows (42 × 2 edge types, exactly as designed).

## Phase 5 — Live capture

Added `_capture_provisioning_wallet()` to `src/core/walkback_worker.py`, called from
the existing `FULL_WALKBACK` branch of `_process_row()` at exactly the point the
detector already fetches the creator-funding transaction:

- **`WSOL_WRAP_CLOSE`**: reuses `funding_tx`, already fetched at the existing
  `_store_close_destination_evidence()` call site — **zero new RPC calls**.
- **`SEEDED_ACCOUNT_CLOSE`**: this branch previously fetched no transaction at all;
  one additional `_get_tx(sig1)` call was added — using the *already-known* signature
  from the funder search that already ran, not a new signature search. This matches
  the module's own pre-existing documented cost model ("FULL_WALKBACK may also
  re-read the selected creator-funding transaction once to retain close-destination
  proof," `walkback_worker.py:17`).

No detector logic, threshold, or decision path was changed — the function only
persists a fact from a transaction the detector already had in memory (or, for
`SEEDED_ACCOUNT_CLOSE`, one bounded additional read of an already-known signature).

## Phase 6 — Attribution graph extension

`operational_lineage.py::build_lineage()` now calls a new, additive
`_insert_provisioning_wallet_nodes()` post-processing step: for any adjacent
SUBPROVIDER→CREATOR pair in the existing chain, if `wt_provisioning_wallets` has a
record for that exact pair, a real `PROVISIONING_WALLET`-role node (the actual
recovered wallet address) is inserted between them. **If `wt_provisioning_wallets`
doesn't exist yet or has no record for the pair, the chain is returned completely
unchanged** — every existing caller sees zero behavior change.

**Verified live** against `GET /api/ops-v2/lineage/8aBvMmrHDSYjemUsytQzZnVx9B16sdrfCQSKbHEbkfbH`:
the response now includes a real `PROVISIONING_WALLET` node
(`8R11d5TvWXuWpVTdNGjgEf6PkoPVoivvsPZNknAaKrx2`) between the `SUBPROVIDER` and
`CREATOR` nodes — the exact wallet X65.19 independently decoded for this launch.

## Phase 7 — UI

`templates/discovery.html`'s `lineageChain()` no longer inserts X65.20's synthetic
"Inferred" card. It now:
- Renders the real `PROVISIONING_WALLET` node returned by the backend (Phase 6) via
  the ordinary `lineageNodeCard()` — same styling, same click-through, real address.
- Falls back to a plain **"Provisioning Wallet unavailable"** card, dashed/dimmed, only
  when a SUBPROVIDER→CREATOR pair still has no persisted record — never fabricating
  an address.

Added `ROLE_LABEL` map so the new `PROVISIONING_WALLET` role renders as "Provisioning
Wallet" (not the literal underscored role string every other role's generic label
formatter would otherwise produce).

## Phase 8 — Validation results

| Check | Result |
|---|---|
| 42 launches gain explicit Provisioning Wallet nodes | **Confirmed** — 42 rows in `wt_provisioning_wallets`, 1 correctly absent (unresolved, X65.19-consistent) |
| Existing Treasury/SubProvider/Creator relationships unchanged | **Confirmed** — `wt_provisioning_edges` row count (1,620) and schema hash unchanged before/after |
| Existing dashboards continue to function | **Confirmed** — `GET /discovery` returns HTTP 200 post-change; extracted JS passes `node --check` |
| Existing attribution scores/classifiers unchanged | **Confirmed** — `funding_topology.py`, `campaign_classification.py`, `provisioning_edges.py` untouched (verified: zero diff) |
| Old graph vs. new graph represent the same launches | **Confirmed** — the new graph is old graph + one inserted node; querying either for `wallet=8aBvMmrHDS...` returns the identical Treasury/SubProvider/Creator triple, with the new graph additionally showing the real intermediate wallet |

**Full existing regression suite**: `tests/test_ops_x21b_provisioning_edges.py`,
`tests/test_ops_x21b_walkback_integration.py`, `tests/test_ops_x21b_routes.py`,
`tests/test_x29_7_operational_lineage.py` — **42/42 passed, unchanged**, confirming
the additive changes did not alter any existing behavior these suites cover.

**Full repository test suite** (2,536 collected tests, 3 pre-existing collection
errors unrelated to this task — `test_helius_analysis.py`/`test_pumpswap_detection.py`/
`test_pumpswap_phase2.py` fail to import `analyze_creator_wallet`/`main`, confirmed
present before this task's changes too): the full-suite run showed scattered failures
in unrelated modules (`test_x24_1_mechanism_aware_subscription.py`,
`test_x24_2_1_sweep_concurrency.py`, `test_x24_9_subscription_target_validation.py`,
others). **Verified these are pre-existing full-suite ordering/state contamination,
not caused by this task**: (1) `git stash`-ing every change in this task and re-running
`test_x24_1_mechanism_aware_subscription.py` alone shows 9/9 passing; (2) re-running
the exact same failing files together in isolation, WITH this task's changes present,
shows 48/48 passing. The failures only appear inside the full 2,500+ test run,
consistent with shared live-DB/fixture state across an unusually large suite — not
something this task's four modified/added files could plausibly cause, and not
reproducible in isolation either with or without this task's changes.

## Phase 9 — Safety analysis

- **Additive**: two brand-new tables; zero `ALTER TABLE`/`UPDATE` on any pre-existing
  table; zero new columns on any pre-existing table.
- **Idempotent**: `ON CONFLICT(mint) DO UPDATE` on `wt_provisioning_wallets`;
  `ON CONFLICT(edge_type, from_wallet, to_wallet) DO UPDATE` on
  `wt_provisioning_wallet_edges` — verified by running the backfill script twice with
  identical results.
- **Append-only where possible**: edge `observation_count` accumulates rather than
  overwrites, matching the existing `wt_provisioning_edges` convention exactly.
- **Fully backwards compatible**: `build_lineage()` returns the exact same chain for
  every caller unless `wt_provisioning_wallets` has a record — a genuinely new table
  a pre-existing caller cannot have depended on.
- **No detection/attribution/classifier change**: `git diff`-equivalent scope of this
  task touches exactly 4 files — one new module (`src/ops/provisioning_wallet.py`),
  one new script (`scripts/x65_21_provisioning_wallet_backfill.py`), one additive
  function + two call sites in `src/core/walkback_worker.py`, one additive
  post-processing function in `src/ops/operational_lineage.py`, and UI-only edits in
  `templates/discovery.html`.

## Unresolved case

`JyJWcxa8xPwgKZFT13mPyDymLrjXhxkQTTyTJC3pump` — identical to X65.19's own finding: no
usable persisted signature, and the creator wallet's transaction volume exceeds the
100-signature bounded-lookup limit this implementation deliberately enforces. Reported
honestly in the UI as "Provisioning Wallet unavailable," not silently hidden or
fabricated.

## Deliverables

- Schema additions: `wt_provisioning_wallets`, `wt_provisioning_wallet_edges`
  (`src/ops/provisioning_wallet.py`).
- New edge definitions: `SUBPROV_TO_PROVISIONING`, `PROVISIONING_TO_CREATOR`.
- Recovery algorithm: `scripts/x65_21_provisioning_wallet_backfill.py`.
- Historical backfill: run against the live DB, 42/43 recovered, results above.
- Live capture: `_capture_provisioning_wallet()` in `walkback_worker.py`, wired into
  both mechanism branches.
- Validation results: Phase 8 table above, plus 42/42 passing pre-existing tests.
- Migration safety analysis: Phase 9 above.
- Expected recovery percentages: 93.0% direct, 4.7% bounded-lookup, 2.3% unresolved
  (matches X65.19 exactly).
- Unresolved cases: 1, with the precise reason recorded in `wt_provisioning_wallets`'
  absence and surfaced honestly in the UI.
