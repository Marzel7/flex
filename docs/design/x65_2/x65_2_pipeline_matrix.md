# X65.2 — Phase 2: Capture Pipeline Coverage Matrix

Read-only, per-launch, per-stage evidence check against live production
tables and logs. `YES` = direct persisted evidence found. `NO` =
checked, zero evidence found. `NOT_APPLICABLE` = stage cannot logically
apply given an upstream `NO` (none occur in this cohort, since every
launch has at least the Program-CREATE-observed and Birth-persisted
stages satisfied).

## Stages checked, and how

| Stage | Evidence source |
|---|---|
| Program CREATE observed | `token_analysis.pf_ws_creator`/`earliest_tx_creator` populated, or `[PUMPPORTAL] 🟢 Birth]` log line |
| Birth persisted | `token_analysis.analyzed_at` populated + `migration_signal_source='birth'` |
| CREATE ledger | `wt_create_event_ledger` row count for the mint |
| Funding captured | `creator_funders` row count for the creator address |
| Walkback queued | `wt_walkback_queue` row + `status` |
| SubProv identified | `wt_active_subprov_sessions` row count for the resolved `terminal_entity`/funder wallet |
| Treasury linked | `wt_confirmed_treasuries` match for the same funder wallet (via `treasury_resolution.py`) |
| Topology derived | `operational_intelligence.py` topology field |
| Funding Origin | Discovery's Funding Origin stage (equivalent to topology per X65.1 Phase 1) |
| Operation Attribution | `operation_id` in the cohort record |

## Full matrix

| Mint | Program CREATE observed | Birth persisted | CREATE ledger | Funding captured | Walkback queued | SubProv identified | Treasury linked | Topology derived | Funding Origin | Operation Attribution |
|---|---|---|---|---|---|---|---|---|---|---|
| B3Fq8SqBtsxsWw... | YES | YES | NO | NO | YES | NO | NO | NO | NO | NO |
| CmoCuZ9J2YT1QH... | YES | YES | NO | NO | YES | NO | NO | NO | NO | NO |
| HHcXBLbnuSWdYi... | YES | YES | NO | NO | YES | NO | NO | NO | NO | NO |
| EQZfBpWpQc5BEU... | YES | YES | NO | NO | YES | NO | NO | NO | NO | NO |
| DpTtRHY6PSuxxJ... | YES | YES | NO | NO | YES | NO | NO | NO | NO | NO |
| CvP9vVUCpoDuMd... | YES | YES | NO | NO | YES | NO | NO | NO | NO | NO |
| 4WfoYERYFw3AQW... | YES | YES | NO | NO | YES | NO | NO | NO | NO | NO |
| EDNvjVDjKVfRsq... | YES | YES | NO | NO | YES | NO | NO | NO | NO | NO |
| 71TKvknpvwRcjd... | YES | YES | NO | NO | YES | NO | NO | NO | NO | NO |
| c5Zye8yFd1AGrS... | YES | YES | NO | NO | YES | NO | NO | NO | NO | NO |
| 9Mn2t7yX2TmSSM... | YES | YES | NO (see note) | NO | YES | NO | NO | NO | NO | NO |
| FzNgpR11RYACas... | YES | YES | NO | NO | YES | NO | NO | NO | NO | NO |

## Notes

- **Program CREATE observed = YES for all 12**: proven by
  `pf_ws_creator`/`earliest_tx_creator` being populated with the
  correct creator wallet for every launch (Phase 1's per-launch
  table). This is independent of, and does not require, a persisted
  `create_tx_signature` — the creator value itself is direct evidence
  the CREATE event reached the application layer.
- **Birth persisted = YES for all 12**: `token_analysis.analyzed_at`
  is populated and `migration_signal_source='birth'` for every launch
  — the row genuinely originated from the birth handler, not a
  migration-time fallback insert. This is "birth persisted" in the
  sense the health metric (`[PREMIG_BIRTH_SEED]`/`births.persisted`)
  measures — a row exists and is attributed to a birth event. It is
  **not** the same claim as "every field the birth handler tried to
  write survived" — `create_tx_signature` specifically did not, which
  is why CREATE ledger is `NO` despite Birth persisted being `YES`.
- **CREATE ledger = NO for all 12, including `9Mn2t7yX2TmSSM...`**:
  `wt_create_event_ledger` has zero rows for every one of the 12. The
  one exception noted in the prior investigation
  (`wt_walkback_queue.create_anchor_signature` independently recovered
  for this one mint) lives in a *different* table
  (`wt_walkback_queue`, not `wt_create_event_ledger`) and was never
  propagated — so `CREATE ledger` is correctly `NO` here; the anchor
  signature's existence is a Phase 6/recoverability fact, not a ledger
  fact.
- **Funding captured = NO for all 12**: `creator_funders` has zero
  rows for every creator address in the cohort.
- **Walkback queued = YES for all 12**: every mint has a
  `wt_walkback_queue` row with `status='complete'`.
- **SubProv identified = NO for all 12**: `wt_active_subprov_sessions`
  has zero rows for any of the 12 resolved funder wallets
  (`wt_attribution_outcomes.terminal_entity`).
- **Treasury linked / Topology derived / Funding Origin / Operation
  Attribution = NO for all 12**: each is a direct, expected consequence
  of SubProv identified being `NO` — there is no subprov to walk
  further from, so nothing downstream can resolve.

## Cross-check against the 7 already-resolved (KNOWN_TREASURY) launches

For contrast, the same matrix applied to the 7 resolved launches shows
an identical pattern through "Funding captured" (all `NO` — `creator_funders`
is empty for those creators too) but diverges at "SubProv identified"
(`YES` — their funder wallets **do** have `wt_active_subprov_sessions`
rows), which is what allows Treasury linked / Topology / Funding
Origin / Operation Attribution to resolve `YES` for those 7 despite
also lacking `creator_funders` and CREATE-ledger evidence. This
confirms the earlier investigation's finding that `creator_funders`
emptiness and CREATE-ledger absence are **not** what blocks
attribution — the walkback path bypasses both — and the true
differentiator between the 7 resolved and 12 unresolved launches is
solely whether the resolved funder wallet has an indexed
`wt_active_subprov_sessions` row.
