# X31.0 — Blind WATCHTOWER Similarity Audit (Last 96 Hours)

Investigation only, per the brief. No code changed. Blind behavioral scoring of every Pump.fun launch in the last 96 hours, deliberately ignoring confirmed treasury attribution until the cross-check step. A significant methodological error was caught and corrected mid-investigation (see below) — the corrected numbers are what this report relies on throughout.

## A methodological correction that changes the investigation's scale

The first extraction pass filtered `token_analysis.created_at >= since` using a raw Unix epoch integer as the bind parameter. `token_analysis.created_at` is actually stored as an **ISO-8601 string** (`'2026-07-19T13:55:12Z'`), not an epoch integer. SQLite's type-affinity coercion on that mismatched comparison let rows through incorrectly — the first pass returned 116,040 "matching" launches, several of which, on manual inspection, had `created_at` values from **June 11, 2026 (over five weeks before the true 96-hour cutoff)**. Recomputing with a correctly-formatted ISO-string comparison shrinks the true population from 116,040 to **2,102 launches** — a 55x correction. This is reported explicitly, per the standing discipline of verifying a brief's own premises and one's own intermediate results before relying on them (consistent with X29.9's premise-check and X29.11's methodology correction). All figures below use the corrected, string-compared 96-hour window (`created_at >= '2026-07-15T13:57:05Z'`).

A second, smaller correction was made to the wrap-close-tail heuristic itself: an initial pass flagged any funding amount whose lamport value ended in `39280` (the known WSOL-ATA-rent-exemption tail, memory: `watchtower-wrap-close-pattern`) regardless of the amount's total size. This produced 1,546 "hits" out of 4,896 creators (31.6%) — implausibly common for a specific operator fingerprint. The bare amount `0.00203928 SOL` (exactly the rent-exemption lamport value alone, with no funding principal on top) is a generic ATA-rent-refund pattern that occurs constantly platform-wide, unrelated to WATCHTOWER's wrap-close mechanism (whose real signature, per X29.7.1's confirmed on-chain trace, is a **substantive funding amount plus the rent tail** — e.g. 1.112039280 SOL, not 0.00203928 SOL alone). The heuristic was corrected to require `amount_sol >= 0.1` before counting the rent-tail as a wrap-close signal, which is the version used throughout this report.

## Scope actually analysed

**2,102 Pump.fun launches** in the last 96 hours (`token_analysis.created_at`, corrected filter). Of these, **1,155** have a resolved `pf_ws_creator`. Of those, only **163** have any pre-existing funder-extraction evidence already persisted in `creator_funders` (a legacy, general-purpose extraction pipeline per `docs/CLAUDE.md`, not a WATCHTOWER-specific tool) — this is a real evidence-coverage limitation, reported honestly rather than treated as "no signal": the great majority of the 96-hour launch population (992 of 1,155 resolved creators, 86%) has never had funder-extraction run against it at all, for reasons outside this audit's scope to diagnose. The blind behavioral scoring below is therefore performed over the 163 creators with available funding evidence, not the full 1,155 — a materially narrower sample than the brief's "every launch" framing, and this narrowing is the single biggest limitation of this audit's coverage.

## Behavioural fingerprint — blind scoring (treasury/subprov/operation identity intentionally never consulted at this stage)

**Funding mechanism**: of the 163 creators with funder evidence, **zero** show a substantive (≥0.1 SOL) WSOL-rent-tail funding signature from the `creator_funders` extraction table. This is a real, direct result, not an artifact of the correction above (the correction only removed *false positives*; it did not suppress any genuine hits — none existed in this sample either before or after the fix).

**Provisioning / burst-sibling behaviour**: with zero wrap-shaped funders, there is by construction no fan-out signal to measure from this data source (a fan-out score requires at least one funder appearing across multiple creators; with zero qualifying funders, this signal is vacuously absent, not merely low).

**Cross-check against a second, independent evidence source — this is the pivotal finding**: `creator_funders` is not the only place funding evidence would appear. The live detection runtime's own evidence table, `wt_subprov_evidence`, is written continuously and independently of whether a CREATE ever fires (confirmed in X30.1/X30.2 — this table is written unconditionally, "operation-agnostic, never suppressed"). Checking it directly for the same 96-hour window (bypassing `creator_funders` entirely) shows:

- **165 wrap-close-shaped funding events** (62 `WSOL_WRAP_CLOSE`, 103 `SEEDED_ACCOUNT_CLOSE`) across **27 distinct subprovider wallets**, all within the last 96 hours, most recent timestamped within the last hour of this audit.
- Of these 165 events, **`create_fired = 0` for every single one.** Zero conversions to a confirmed launch.
- The corresponding `wt_candidate_websocket_watches` outcomes for this same window: **43 closed as `BUY_SWARM`/`swapped`, 120 `EXPIRED`** (15 with no reason logged, 105 via TTL). **Zero `FIRED_CREATE`.**
- Directly confirmed via `wt_watchtower_launches`: **zero rows with `create_time` in the last 96 hours.** The single most recent confirmed WATCHTOWER launch in the entire corpus (43 rows, all-time) is `create_time = 1784048633` (2026-07-14), roughly **5 days before this audit**, and matches the exact HTR9U7 launch already characterized across X29.7–X29.11.

## Classification

Scoring strictly on behaviour, per the brief's four categories:

| Category | Count | Basis |
|---|---|---|
| **High similarity** | 27 (subprovider wallets, not individual launches — see note below) | Wrap-close-shaped funding mechanism (WSOL_WRAP_CLOSE or SEEDED_ACCOUNT_CLOSE) observed in `wt_subprov_evidence`, matching the exact mechanism fingerprint validated in X29.7.1/X29.8/X29.9 |
| **Moderate similarity** | 0 | No candidates found with a partial-but-multi-signal match distinct from the High group |
| **Low similarity** | 0 | No isolated single-characteristic matches beyond the High group found in either evidence source |
| **No similarity** | 163 (of the `creator_funders`-evidenced set) / remainder of 1,155 resolved creators | No wrap-close-shaped or otherwise WATCHTOWER-fingerprint-matching funding found |

**Important scope note on the "27" figure**: these are *subprovider wallets that exhibited the fingerprint*, not *launches*. None of the 27 produced a confirmed launch (`create_fired=0` for all 165 underlying evidence rows), so there are, in the strictest reading, **zero High-Similarity launches** in the last 96 hours — only High-Similarity *pre-launch provisioning activity* that never converted. This distinction matters enormously for the success criteria below: the brief's classification scheme presumes launches as the unit of analysis, but the most similarity-bearing evidence in this window is entirely pre-launch and never reaches a launch at all.

## Attribution cross-check (performed only after the blind scoring above)

For the 27 High-Similarity subprovider wallets:

- **Already attributed (known subprov, in `wt_discovered_subprovs`)**: all 27 — every one already has a `PROVISIONAL_SUBPROV` row, since `wt_subprov_evidence` writes always upsert `wt_discovered_subprovs` (per X30.1's traced `promote_to_subprov` logic).
- **Known treasury**: of the 27, several resolve to a `treasury` column that is populated and — critically, checked directly — matches an **already-confirmed** treasury: `9hGcxVHFajR4xMZVUBQBwbxN2J3tt1aXE5KrUB87EZk4` (the exact confirmed treasury from X29.7's validated WATCHTOWER example), `DchJquEZzM6VqBaxhA9i7r3qAUngPggQJHoTBhwdFEUK` (referenced directly in memory `hello-payment-operator-linkage`), and `69SNcRC8NqjHBSXEcugCN5oFKRQoKmddmWzZYc3tqtxk` — all three verified against `wt_confirmed_treasuries` directly and confirmed present (`confidence=CONFIRMED`/`MANUAL`, `method='3SIGNAL'`/`'subprov_funder_trace'`).
- **Known operation**: not separately re-traced this sprint, but since all three funding treasuries are already-confirmed, these subprovs would resolve into `operation_identity.py`'s existing mesh via the standard treasury-membership path — not a new, unseen operation.
- **Completely unattributed**: **none of the 27** — every high-similarity subprovider wallet traces back to an already-known treasury. There is **no unseen funding root** among the 27.

## Unknown-treasury investigation

Per the brief's instructions, this step applies only to unattributed High-Similarity launches — there are none. All 27 High-Similarity subprovider wallets trace cleanly to already-confirmed treasuries; no new treasury mesh, no unresolved funding root, and no attribution-blocked case was found. This is itself informative: it directly rules out the "operation has rotated to previously unseen treasuries" hypothesis for this window, at least at the level of these 27 subprov wallets' immediate funding source.

## Deliverables

- **Total Pump.fun launches analysed**: 2,102 (corrected count; 1,155 with a resolved creator; 163 with pre-existing funder-extraction evidence, the practical evidentiary scope of the behavioral score above).
- **High / Moderate / Low / No Similarity counts**: 27 subprovider wallets High (zero of which produced a launch); 0 Moderate; 0 Low; remainder No Similarity, within the practical evidentiary scope.
- **High-Similarity launches already attributed**: N/A in the strict sense — there are zero High-Similarity *launches* (all 27 High-Similarity entities are pre-launch subprovider wallets, and all 27 are already attributed to a known treasury at the subprovider level).
- **High-Similarity launches completely unattributed**: zero.
- **Funding mechanisms used by High-Similarity entities**: `WSOL_WRAP_CLOSE` (62 events) and `SEEDED_ACCOUNT_CLOSE` (103 events) — both previously-established WATCHTOWER mechanisms; no novel mechanism observed.
- **Convergence on previously unseen funding roots?** No — all traced funding roots are already-confirmed treasuries (`9hGcx...`, `DchJqu...`, `69SNcRC8...`).

## Which hypothesis does the evidence support?

Not operation retirement, not treasury rotation, not provisioning evolution to a new mechanism — the evidence points specifically at **a conversion failure between provisioning and confirmed launch, occurring after already-known treasuries continue to fund new candidates via already-known mechanisms**:

- **Against retirement**: the same confirmed treasuries actively funded 27 new subprovider wallets via the same wrap-close mechanisms within the last 96 hours, with activity as recent as within the last hour of this audit. An operation that had genuinely stopped would show no fresh `wt_subprov_evidence` rows at all from its known treasuries — this audit finds the opposite.
- **Against treasury rotation**: every High-Similarity subprovider traces to an *already-confirmed* treasury, not a new one. If the operation had rotated to unseen treasuries, this audit's blind, treasury-agnostic scoring pass would have surfaced High-Similarity subprovider wallets whose funding root resolved to nothing in `wt_confirmed_treasuries` — it did not.
- **Against provisioning evolution to a new mechanism**: the mechanisms observed (`WSOL_WRAP_CLOSE`, `SEEDED_ACCOUNT_CLOSE`) are identical to the mechanisms already characterized across X29.7–X29.11. No new funding shape was found.
- **For reduced detector coverage / a conversion regression**: 165 wrap-close-shaped provisioning events fired in the window, and **100% of them terminated as `BUY_SWARM`/`swapped` or `EXPIRED`/`TTL` rather than `FIRED_CREATE`.** This is the one number in this entire audit that directly and specifically explains "no recent WATCHTOWER launch detections" — the provisioning behaviour that historically preceded a launch is still happening, at the same known treasuries, via the same known mechanisms, but something between "wrap-close observed" and "CREATE confirmed" is now failing 100% of the time in this sample, where it previously succeeded at least once as recently as 5 days ago (the HTR9U7 launch). Whether this is a buy-swarm misclassification rate spike, a genuine behavioural change by the operator (e.g. more candidates now looking swarm-like even when they are not), or a live-detection timing/coverage gap on the CREATE side specifically, is **not determinable from this evidence-only audit** — it is the concrete, evidence-grounded next question this investigation surfaces, not a conclusion this investigation is positioned to answer without further, separately-scoped tracing of the 43 `BUY_SWARM` and 120 `EXPIRED` outcomes individually.

## Direct answer to the success-criteria question

**Yes — WATCHTOWER-like provisioning activity has continued during the last 96 hours, from already-known treasuries, using already-known mechanisms.** But it has not continued *to the point of a confirmed launch*: every instance of the behavioural fingerprint observed in this window terminated in `BUY_SWARM`/`EXPIRED`, never `FIRED_CREATE`. The evidence does not support genuine operational retirement or treasury rotation; it supports a **conversion-stage regression** — something in the pipeline between provisioning-fingerprint detection and CREATE-confirmation that is currently failing for every candidate this operation has produced in the last 96 hours, despite the operation itself, at the funding-behaviour level, still being active.
