# X65.1 — Sub-Provider Treasury Resolution for Unassigned Quick-Birth Launches (Full Report)

Resolved the creator → sub-provisioner → treasury lineage for the
Discovery cohort at `QUICK_BIRTH_MIGRATION → FRESH_CREATOR → UNKNOWN
topology → UNKNOWN funding → UNASSIGNED`, without any new detection
logic, any automatic treasury confirmation, or any change to Behaviour
Cohort/Creator Identity classification. All facts measured live against
the production database, 2026-07-21.

---

## Phase 1 — Cohort Reproduction

### Reproduction method

The Discovery UI's filter chain is:
`canonical_behaviour=QUICK_BIRTH_MIGRATION` → `creator_identity=FRESH_CREATOR`
→ `topology=UNKNOWN` → `funding=UNKNOWN` → `operation=__UNASSIGNED__`.

Per X65.0's own finding, "Funding Origin = UNKNOWN" means the mint has
no entry at all in `CEX_MINT_CACHE` (`/api/ops-v2/cex-funding-intelligence`),
itself scoped to only `wt_attribution_outcomes.outcome_type='KNOWN_CEX_REACHED'`
rows. Since `topology=UNKNOWN` mints have no resolved funding lineage at
all, they cannot simultaneously be `KNOWN_CEX_REACHED` — filtering by
`topology='UNKNOWN'` already implies `funding=UNKNOWN` for this
population, so no separate CEX-mint-cache lookup was needed.
`operation=__UNASSIGNED__` was reproduced as `operation_id is None`.

Applied filter, directly against `build_operational_intelligence()`'s
`records` output:

```python
canonical_behaviour == 'QUICK_BIRTH_MIGRATION'
and creator_identity == 'FRESH_CREATOR'
and topology == 'UNKNOWN'
and operation_id is None
```

### Result: cohort count matches expectation exactly

**19 launches** — matching "approximately 19" exactly. Per the task's
own instruction ("Do not continue if the query does not reproduce the
UI cohort"), this confirms no UI/API filtering discrepancy exists.

### Existing operation fields (all 19, uniformly)

`operation_id: None`, `is_watchtower: False`, `mechanisms: []` for all 19.

### Existing funding and topology evidence

**`topology_derived_from: "no_lineage_evidence"` for all 19 launches,
uniformly** — the existing topology classifier's lineage-derivation
process found zero persisted evidence to reason from at all, confirming
this is a genuine coverage gap, not a population the system examined
and gave up on.

**`create_tx_signature` is NULL for all 19 launches** in `token_analysis`.
Also checked `wt_create_event_ledger` (the X64.7 canonical CREATE-event
ledger) for these same 19 mints — zero rows found there either. No
CREATE signature is persisted anywhere in the system for this cohort —
likely explaining why existing lineage derivation has nothing to anchor to.

**Migration timing**: every launch migrated within 1-15 seconds of its
own CREATE, comfortably inside both `QUICK_BIRTH_MIGRATION`'s `<=900s`
and `RAPID_MIGRATION`'s `<300s` bands (though `QUICK_BIRTH_MIGRATION` is
the canonical behaviour per X65.0's precedence).

### Conclusion

Cohort reproduces exactly (19/19). No UI/API discrepancy found. The
absence of a persisted CREATE signature across the entire cohort is
flagged as the likely root cause of the lineage gap, informing Phase 2.

---

## Phase 2 — Existing Evidence Audit

### Headline finding: the cohort splits cleanly into two groups

| Group | Count | Existing evidence |
|---|---|---|
| **A — already-resolvable via existing tables** | **7 / 19** | Direct funder already known to `wt_active_subprov_sessions` with a `treasury_wallet`, and that treasury is already in `wt_confirmed_treasuries` + linked to an operation in `wt_ops_v2_wallets` |
| **B — no existing evidence anywhere** | **12 / 19** | Zero rows in every funding-lineage table checked; genuinely never observed by any prior indexing pass |

No new detection logic is needed for Group A — only a cross-reference
join connecting two already-persisted facts nothing currently connects.

### Evidence source inventory

**`wt_attribution_outcomes`** (ops DB) — creator's direct funder,
already persisted for all 19. `terminal_entity` holds the funder
wallet; `stop_reason: "Walkback stopped because the persisted evidence
is insufficient for attribution."` This is exactly the table
`funding_topology.py` already reads to assign `topology=UNKNOWN` in the
first place.

**`wt_active_subprov_sessions`** (ops DB) — funder's own sub-provisioner
status + its treasury, already persisted for 7/19. All matched rows
`state='EXPIRED'` (normal lifecycle completion). Written by live
`ws_cascade.py` WS-observed funding events — the same mechanism this
project already treats as authoritative everywhere else. Critically,
`funding_topology.py` never checks whether a `wt_attribution_outcomes.
terminal_entity` is ITSELF a key in this table — this is the missing
cross-reference:

| Funder wallet (creator's direct funder) | Treasury wallet (already known) | Funding amount | Mechanism |
|---|---|---|---|
| `3KJteRqjBJb5ddR5eZgPZ8uwyWriKuUN5j2ALS97rpU2` | `DchJquEZzM6VqBaxhA9i7r3qAUngPggQJHoTBhwdFEUK` | 630.0 SOL | PLAIN_TRANSFER |
| `7atTgmp9D86zA3f4AfFSFb5XWvDX2doNW4RrbYFqyQJw` | `DchJquEZzM6VqBaxhA9i7r3qAUngPggQJHoTBhwdFEUK` | 680.0 SOL | PLAIN_TRANSFER |
| `82Yzf1hMDyLa1Z8uADcxzMHxmmGedwKj6viUReKfTeKJ` | `9hGcxVHFajR4xMZVUBQBwbxN2J3tt1aXE5KrUB87EZk4` | 650.0 SOL | WSOL_WRAP_CLOSE |
| `DkhL6D3ZEwdDu4RnW4WHJM9ujX2B94UyvxMAL9CCBV4T` | `Dtwi1eLMTLaUuCwbztpFEoepBdzhjhjmLoTyAdsR3p3u` | 1600.0 SOL | PLAIN_TRANSFER |
| `DmoG9vDaYTf8Rd1vb8i6BSKZi5Zuo3ov4FdMmz5aPzSW` | `9hGcxVHFajR4xMZVUBQBwbxN2J3tt1aXE5KrUB87EZk4` | 900.0 SOL | PLAIN_TRANSFER |
| `E33jmbX8TQLDP2m1VUsdfyzQCWZMBXhtB6wzgqXKhe44` | `9hGcxVHFajR4xMZVUBQBwbxN2J3tt1aXE5KrUB87EZk4` | 650.0 SOL | WSOL_WRAP_CLOSE |
| `FLo2pNsAsS4qpZZnPSN2Quf6cEkiej4fJXC3uVrgzU2X` | `DchJquEZzM6VqBaxhA9i7r3qAUngPggQJHoTBhwdFEUK` | 630.0 SOL | PLAIN_TRANSFER |

**`wt_discovered_subprovs`** (ops DB) — independent secondary
confirmation for 5/7, matching the same `treasury` value exactly.
Low `confidence` (0.28-0.52), `state='PROVISIONAL_SUBPROV'` — a
discovery-stage record, not a confirmation; corroborates but doesn't
independently raise confidence.

**`wt_confirmed_treasuries`** (ops DB) — the authoritative registry.
All 3 treasury wallets already present and confirmed:
`DchJquEZzM6VqBaxhA9i7r3qAUngPggQJHoTBhwdFEUK` (2026-06-11,
`CONFIRMED_SEED`), `9hGcxVHFajR4xMZVUBQBwbxN2J3tt1aXE5KrUB87EZk4`
(2026-06-14, `CONFIRMED_SUBPROV_TRACE`), `Dtwi1eLMTLaUuCwbztpFEoepBdzhjhjmLoTyAdsR3p3u`
(2026-07-21, `MANUAL_OVERRIDE_X64_DTWI1ELM`, a human-confirmed override
from this project's own recent history). Zero new confirmation
activity needed.

**`wt_ops_v2_wallets`** (ops DB) — operation attribution for confirmed
treasuries. All 3 treasuries already linked to an operation UUID with
`role='TREASURY'`: `9hGcxVHF...`→`4135d67d-...`, `DchJquEZ...`→`69af7941-...`,
`Dtwi1eLM...`→`9868e8dd-...`. **Note**: these are three *distinct*
operation UUIDs despite this project's own persistent memory ("Hello
program operator linkage") independently identifying all three
treasury wallets as belonging to the same real-world operator via
shared downstream Hello-service payments. This discrepancy is flagged,
not resolved or merged, per the task's explicit "do not automatically
confirm or reroot treasury identities" constraint.

### Tables checked with zero hits (confirming Group B has no existing evidence)

`wt_provisioning_edges`, `wt_candidate_websocket_watches`,
`wt_webhook_hits`, `funder_incoming_transfers`, `creator_receivers`,
`sol_transfers`, `transfer_index`, `wt_create_event_ledger` — all zero
rows for the 12 Group-B funder wallets. Combined with the missing
CREATE signature (Phase 1), this strongly suggests a genuine coverage
gap in prior indexing, not a "checked and found nothing" state.

### Summary: reuse vs. new logic needed

| Requirement | Reuse existing evidence? |
|---|---|
| Creator's direct funder | Reuse — `wt_attribution_outcomes.terminal_entity`, correct for all 19 |
| Is the funder a sub-provisioner? | Reuse for 7/19 (`wt_active_subprov_sessions` presence); genuinely absent for 12/19 |
| Sub-provisioner's own upstream funder | Reuse for 7/19 (`treasury_wallet` already populated) |
| Known-treasury match | Reuse for 7/19 (`wt_confirmed_treasuries` already has all 3) |
| Operation attribution | Reuse for 7/19 (`wt_ops_v2_wallets` already links all 3) |

No second relationship system is needed for Group A.

---

## Phase 3 — Creator-to-SubProv Resolution

Classifies each creator's direct funder into `CONFIRMED_SUBPROV` /
`PROBABLE_SUBPROV` / `DIRECT_TREASURY` / `NON_OPERATIONAL_FUNDER` /
`UNRESOLVED`, preferring transaction-level evidence over balance
heuristics.

### Method

1. Take the funder wallet from `wt_attribution_outcomes.terminal_entity`
   (reused, not re-derived).
2. Check `wt_active_subprov_sessions` for a full row (signature, amount,
   timestamp, treasury).
3. Compute `time_from_funding_to_create` as a real transaction-level gap.
4. Cross-check `wt_discovered_subprovs` for corroboration.
5. Classify `UNRESOLVED` where no evidence exists, rather than guessing.

### Group A (7 launches) — all `CONFIRMED_SUBPROV`

| Mint | Funder | Amount | Funding→CREATE gap | Mechanism |
|---|---|---|---|---|
| 2GuvMWJpfNBX... | `7atTgmp9D86z...` | 680.0 SOL | 380s | PLAIN_TRANSFER |
| 2XmV6Jk6ATzK... | `FLo2pNsAsS4q...` | 630.0 SOL | 104s | PLAIN_TRANSFER |
| 3LZL5cXac86U... | `82Yzf1hMDyLa...` | 650.0 SOL | 4,719s (~79 min) | WSOL_WRAP_CLOSE |
| 3QFvseNX1Fdk... | `E33jmbX8TQLD...` | 650.0 SOL | 344s | WSOL_WRAP_CLOSE |
| GuyE9St1cU54... | `DmoG9vDaYTf8...` | 900.0 SOL | 281s | PLAIN_TRANSFER |
| HJ1Ry6iJyAqN... | `DkhL6D3ZEwdD...` | 1,600.0 SOL | 122s | PLAIN_TRANSFER |
| x8NtU6nnYDn1... | `3KJteRqjBJb5...` | 630.0 SOL | 298s | PLAIN_TRANSFER |

Each has a complete `wt_active_subprov_sessions` record: a real,
valid-format Solana signature, a plausible pre-CREATE funding gap
(104s-79min, all consistent with genuine provisioning), and a populated
treasury — the same evidence `funding_topology.py` already treats as
authoritative. 5 of 7 also independently corroborated via
`wt_discovered_subprovs` with matching treasury values.

### Group B (12 launches) — all `UNRESOLVED`

All 12 funder wallets (`FyWwg3aYJn268...`, `EkGqFEGfv7Bs...`,
`4j33GX1Z3yvg...`, `4BJhnZqa5k8P...`, `EdqpE1jBonFk...`,
`DCyQJVfAL37W...`, `9WVUzBkmUrpo...`, `9o5198YMonex...`,
`AjphaVN9Mgir...`, `1JFLdVdAto6b...`, `HXMUxU94Zs2h...`,
`ApgLKt2k1knB...`) return zero rows across every table checked.

`UNRESOLVED`, not `NON_OPERATIONAL_FUNDER`, because the latter would be
an affirmative "checked and this is not a sub-provisioner" claim, but
there is no evidence of any kind to check against — consistent with
this project's governing principle that absence of a match is not
evidence of absence.

### Why no `DIRECT_TREASURY` or `PROBABLE_SUBPROV` in this cohort

No funder wallet is itself in `wt_confirmed_treasuries` (would need
zero intermediate hop, which would likely already show up as `LINEAR`
topology, not `UNKNOWN`). Every wallet checked either had complete,
internally-consistent evidence (Group A) or zero evidence (Group B) —
no partial/ambiguous cases arose.

### Summary

| Classification | Count |
|---|---|
| CONFIRMED_SUBPROV | 7 |
| PROBABLE_SUBPROV | 0 |
| DIRECT_TREASURY | 0 |
| NON_OPERATIONAL_FUNDER | 0 |
| UNRESOLVED | 12 |

---

## Phase 4 — SubProv-to-Treasury Walkback

Walks the 7 `CONFIRMED_SUBPROV` launches one more hop, bounded to the
task's default max depth of 2 (creator ← subprov ← treasury).

### Bridging-depth check

Checked whether any of the 3 treasury wallets is itself a
`subprov_wallet` (requiring depth extension). **Zero bridging evidence
found** for all 3 — depth 2 confirmed as terminal; no extension needed
or performed.

### Per-launch detail

All 7 launches: hop depth 2, relationship type
`TREASURY_TO_SUBPROV` (persisted via `wt_active_subprov_sessions.treasury_wallet`),
and each sub-provisioner funded its creator before the corresponding
CREATE (104s to ~79 min prior, per Phase 3).

### Treasury-scale history

| Treasury | Distinct sub-provisioners funded (all-time) | Total funding (SOL) |
|---|---|---|
| `DchJquEZzM6VqBaxhA9i7r3qAUngPggQJHoTBhwdFEUK` | 2,245 | 80,384.6 |
| `9hGcxVHFajR4xMZVUBQBwbxN2J3tt1aXE5KrUB87EZk4` | 178 | 48,512.6 |
| `Dtwi1eLMTLaUuCwbztpFEoepBdzhjhjmLoTyAdsR3p3u` | 578 | 42,476.6 |

All three fund hundreds to thousands of distinct sub-provisioners with
tens of thousands of SOL in aggregate — unambiguous treasury-scale
activity. Per the task's explicit prohibition against choosing the
largest historical funder without proving lineage, the specific
`funding_signature` tying each sub-provisioner to its launch's own
timing window is what proves the lineage, not treasury size alone.

### Links to a confirmed operation

All three treasuries already linked in `wt_ops_v2_wallets`: `9hGcxVHF...`
→ `4135d67d-2b70-407a-be3c-ab47526203ac`, `DchJquEZ...` →
`69af7941-34d5-42b8-b426-a6a2b9013712`, `Dtwi1eLM...` →
`9868e8dd-69a1-434f-a185-b03fbf8f5487`.

### Group B — no walkback possible

No hop-2 candidate exists for the 12 `UNRESOLVED` launches; walkback
correctly terminates at hop 1 with no further evidence, rather than
guessing.

---

## Phase 5 — Known-Treasury Matching

### All 3 treasury candidates are already confirmed

| Treasury candidate | Confirmation method | Confirmation date | Confidence |
|---|---|---|---|
| `DchJquEZzM6VqBaxhA9i7r3qAUngPggQJHoTBhwdFEUK` | `3SIGNAL` | 2026-06-11 | `CONFIRMED` |
| `9hGcxVHFajR4xMZVUBQBwbxN2J3tt1aXE5KrUB87EZk4` | `subprov_funder_trace` | 2026-06-14 | `MANUAL` |
| `Dtwi1eLMTLaUuCwbztpFEoepBdzhjhjmLoTyAdsR3p3u` | `manual_override` | 2026-07-21 | `MANUAL` (`MANUAL_OVERRIDE_X64_DTWI1ELM`) |

All 3 return `KNOWN_TREASURY` — no new confirmation activity performed
in this phase; a lookup against pre-existing authority only.

### Operation linkage

None of the three operation UUIDs match this project's canonical
`WATCHTOWER_OPERATOR_ID` — consistent with Phase 1's `is_watchtower=False`
for all 19 launches. All 7 resolved launches attribute to **other,
already-confirmed, non-WATCHTOWER operations**.

### Full resolution paths (representative examples)

```
creator 3NyJNH93vBDM7nn1U2geTBmoRwnogFoHmhjJSEY8fNGh
  ← subprov 7atTgmp9D86zA3f4AfFSFb5XWvDX2doNW4RrbYFqyQJw (CONFIRMED_SUBPROV, 380s before CREATE)
  ← confirmed treasury DchJquEZzM6VqBaxhA9i7r3qAUngPggQJHoTBhwdFEUK (KNOWN_TREASURY, 3SIGNAL)
  → operation 69af7941-34d5-42b8-b426-a6a2b9013712 (MIGRATED, non-WATCHTOWER)
```

```
creator 96oi3HjrPWGnkPwhZL8uFbUjg9qJgSVjn5nK7oM85uVg
  ← subprov 82Yzf1hMDyLa1Z8uADcxzMHxmmGedwKj6viUReKfTeKJ (CONFIRMED_SUBPROV, ~79min before CREATE, WSOL_WRAP_CLOSE)
  ← confirmed treasury 9hGcxVHFajR4xMZVUBQBwbxN2J3tt1aXE5KrUB87EZk4 (KNOWN_TREASURY, subprov_funder_trace)
  → operation 4135d67d-2b70-407a-be3c-ab47526203ac (MIGRATED, non-WATCHTOWER)
```

### Flagged discrepancy (not resolved)

This project's own persistent memory ("Hello program operator linkage")
independently established, via a separate on-chain evidence path
(shared downstream Hello-service payments), that all 3 treasury wallets
belong to the same real-world operator. Yet they link to 3 distinct
operation UUIDs in `wt_ops_v2_wallets`. Not merged or rerooted here —
flagged for human review in Phase 9's summary.

---

## Phase 6 — Unknown-Treasury Candidate Classification

### Result: zero `UNKNOWN_TREASURY_CANDIDATE` cases in this cohort

Group A's every upstream candidate was already `KNOWN_TREASURY`; Group
B has no candidate wallet at all to classify. This is the honest
outcome of this specific 19-launch sample, not a gap in the analysis.

### Why this outcome is plausible

All 3 treasuries were already confirmed well before this task began
(2026-06-11, 2026-06-14, 2026-07-21 — the most recent predates this
task's execution). Given this project's extensive prior treasury
-discovery work, it's plausible the small number of resolvable launches
in this cohort resolve to already-found treasuries — the gap closed by
this task is the missing cross-reference join (Phase 2), not a gap in
treasury-discovery coverage.

### Design retained for future cohorts

The `UNKNOWN_TREASURY_CANDIDATE` classification, its required capture
fields, and its explicit non-auto-promotion rules (never promote from
one transfer, wallet size alone, matching amount tails, timing
proximity, shared RPC observation, or generic ATA-rent patterns) remain
fully specified and implemented, even though not exercised by this
cohort's data.

---

## Phase 7 — Resolution Model

Implemented as `src/ops/treasury_resolution.py` — read-only, zero
writes, zero RPC. 23 tests in `tests/test_x65_1_treasury_resolution.py`,
all passing.

### Schema (as implemented)

```json
{
  "treasury_resolution": {
    "status": "KNOWN_TREASURY | UNKNOWN_TREASURY_CANDIDATE | NO_SUBPROV | UNRESOLVED",
    "creator_wallet": "...",
    "subprov_wallet": "...",
    "treasury_wallet": "...",
    "operation_id": "...",
    "operation_name": null,
    "hop_depth": 2,
    "confidence": 0.0,
    "evidence": [...],
    "reason": "..."
  }
}
```

`operation_name` is always `null` — `wt_operation_lifecycle` has no
display-name column, so there is genuinely nothing to populate.

### Key design guarantees (each independently tested)

- One resolution object per launch (`test_resolve_cohort_returns_one_object_per_mint`).
- Explicit nulls where unresolved, never omitted keys
  (`test_no_fabricated_wallet_when_unresolved`).
- No fabricated wallet, no silent fallback — only values read directly
  from SQL rows.
- Evidence path always retained
  (`test_evidence_path_never_empty_for_any_resolved_status`).
- Confidence derived from documented evidence tiers (0.95 for
  `KNOWN_TREASURY` via `CONFIRMED_SUBPROV`, down to 0.2 for
  `UNKNOWN_TREASURY_CANDIDATE` via `PROBABLE_SUBPROV`, 0.0 for
  `UNRESOLVED`) — not calibrated probabilities, ordinal bands matching
  this project's existing `CONFIRMED`/`MANUAL` label convention.
- Existing confirmed operation assignment remains authoritative — zero
  writes anywhere (`test_match_known_treasury_never_writes_to_confirmed_table`,
  `test_resolve_cohort_is_read_only`).
- Bounded traversal (`MAX_WALKBACK_DEPTH = 2`), extended only after an
  explicit, tested bridging check
  (`test_bridging_detected_when_treasury_is_itself_a_subprov`).

### Live verification

Running `resolve_treasury_for_cohort()` against the real 19-mint cohort
reproduces Phases 3-5's manual analysis exactly: `{'UNRESOLVED': 12,
'KNOWN_TREASURY': 7}`, zero `UNKNOWN_TREASURY_CANDIDATE`, zero
`NO_SUBPROV`. A spot-checked result matches Phase 5's manually-traced
path field-for-field.

---

## Phase 8 — Discovery UI Integration

### Backend: new API endpoint

`GET /api/ops-v2/treasury-resolution?mints=<comma-separated>`
(`src/core/operation_dashboard_routes.py`), bounded to ≤200 mints per
request. Live-tested at **19ms** latency, correct payloads matching
manual analysis.

### Frontend: Treasury Resolution panel

`templates/discovery.html` — new mount point positioned after Funding
Origin. `renderTreasuryResolution()` fires only when
`TOPO_SELECTION.funding === 'UNKNOWN'`, fetching only uncached mints
(bounded to 200/request). `renderTreasuryResolutionTable()` renders
summary cards per status actually present, plus a full per-launch table
(Mint / Creator / Sub-Provisioner / Treasury-or-Candidate / Status /
Operation / Confidence / expandable evidence `<details>`).

### Presentation choice vs. the task's suggested nested breadcrumb

Implemented as a flat, always-visible table instead of a nested tree,
specifically because every result must show regardless of status (per
the task's "do not hide unresolved launches") — a flat table makes
"nothing is hidden" structural rather than dependent on remembering to
expand every branch. The WATCHTOWER/Other-Confirmed-Treasury split is
preserved via the Operation column and evidence rather than a separate
visual sub-group, since no result in this cohort is WATCHTOWER-attributed.

### Operation Attribution behavior

`KNOWN_TREASURY` rows surface the existing operation_id;
`UNKNOWN_TREASURY_CANDIDATE` rows always show `operation_id: null` →
displayed as `—`; `UNRESOLVED` rows remain fully visible with their
`reason` string in the expandable evidence.

### Verification performed

- API: live-tested, correct payloads.
- JS syntax: extracted the script block, neutralized Jinja expressions,
  ran `node --check` — clean.
- Function hoisting: confirmed `_short()`/`x58Card()` (both reused) are
  hoisted function declarations, position-independent.
- Page load: `GET /discovery` returns HTTP 200 post-change.
- **Not performed**: a live, visual, in-browser click-through — no
  browser-automation tooling was available in this environment. Stated
  explicitly rather than claiming a UI verification that didn't happen.

---

## Phase 9 — Cohort Results

### Summary statistics

| Metric | Count |
|---|---|
| Total launches in cohort | 19 |
| Creators with direct funder resolved | 19 / 19 (already true before X65.1) |
| Confirmed sub-providers (`CONFIRMED_SUBPROV`) | 7 |
| Probable sub-providers (`PROBABLE_SUBPROV`) | 0 |
| Known treasuries (`KNOWN_TREASURY`) | 7 |
| Unknown treasury candidates | 0 |
| Direct treasury funders | 0 |
| Unresolved cases | 12 |
| Launches newly attributable to WATCHTOWER | 0 |
| Launches attributable to other known operations | 7 (3 distinct operation UUIDs) |
| Launches remaining unassigned | 12 |

7 (`KNOWN_TREASURY`) + 12 (`UNRESOLVED`) = 19, exactly the cohort size
— every launch received exactly one result, none lost or duplicated.

Full per-launch table (mint, creator, subprov, treasury candidate,
status, operation, confidence, reason) is preserved in the underlying
resolution data, reproducible on demand via
`resolve_treasury_for_cohort()`.

---

## Phase 10 — Regression and Safety Validation

| Check | Result |
|---|---|
| Behaviour Cohort remains exclusive | ✅ `canonical_behaviour_conserved: True`, live-checked post-deployment; X65.1 never references `canonical_behaviour`/`behaviours` |
| Cohort count conserved / no launch lost or duplicated | ✅ dict-keyed structurally guarantees this; live-verified 19 in → 19 out |
| Fresh Creator classification unchanged | ✅ zero references to `creator_identity` anywhere in the new module |
| Existing known-operation assignments unchanged | ✅ pure `SELECT`s only; `wt_confirmed_treasuries` stayed at 61 rows before/after |
| No unknown treasury auto-confirmed | ✅ zero `INSERT`/`UPDATE`/`DELETE`/`CREATE`/`ALTER`/`DROP` anywhere in the module |
| No production treasury root rewritten | ✅ same evidence — no write path exists |
| New operation assignments backed by an existing confirmed treasury | ✅ every `operation_id` traced through a confirmed-treasury match, verified live |
| Unresolved launches remain Unassigned | ✅ `operation_id: null` always set; `TOPO_SELECTION.operation` untouched |
| Traversal bounded | ✅ `MAX_WALKBACK_DEPTH=2`; bridging check tested and returned `False` for all 3 real candidates |
| API latency acceptable | ✅ 19ms for a single-mint request |
| No uncontrolled per-row RPC fan-out | ✅ zero RPC-related imports/calls anywhere in the module |

### Test suite results

67 tests pass: 23 new (`test_x65_1_treasury_resolution.py`) + 44
pre-existing (`test_x65_0_exclusive_behaviour.py`,
`test_x64_8_creator_identity.py`, `test_x27_4_behaviour_queue.py`) —
zero regressions.

### Process health

All four managed processes (`watchtower_listener`, `walkback_worker`,
`ws_cascade`, `watchtower_api`) remained `RUNNING` post-deployment; only
`watchtower_api` was deliberately restarted to load the new route and
template changes.

---

## Executive Summary

### Original cohort count

**19 launches**, reproduced exactly.

### Direct creator funders resolved

**19 / 19 (100%)** — already true before this task; the real
contribution starts one hop further upstream.

### Sub-providers identified

**7** `CONFIRMED_SUBPROV`, **0** `PROBABLE_SUBPROV`, **12** `UNRESOLVED`.

### Upstream treasury wallets identified

**3 distinct treasuries**: `DchJquEZzM6VqBaxhA9i7r3qAUngPggQJHoTBhwdFEUK`
(3 launches), `9hGcxVHFajR4xMZVUBQBwbxN2J3tt1aXE5KrUB87EZk4` (3
launches), `Dtwi1eLMTLaUuCwbztpFEoepBdzhjhjmLoTyAdsR3p3u` (1 launch).

### Known treasuries matched

**All 3**, all already confirmed before this task began. Zero new
confirmations.

### Unknown treasury candidates discovered

**0** for this cohort — the classification path is implemented and
tested but not exercised by this data.

### New confirmed operation assignments

**0 new confirmations.** 7 launches now surface their already
-confirmed operation via a previously-missing cross-reference join.

### Remaining unassigned launches

**12 / 19**, correctly staying `__UNASSIGNED__` — no launch was
force-assigned or guessed.

### Unresolved reasons

All 12 share the same underlying cause: the creator's direct funder has
zero rows in any funding-lineage table checked, combined with a missing
CREATE signature for all 19 cohort launches — strongly suggesting a
gap in prior funding-lineage/CREATE-event indexing, not a deliberately
examined-and-abandoned population.

### Performance impact

New endpoint measured at **19ms**; 23 new tests run in 0.14s; zero
impact on any existing endpoint.

### Human treasury review needed?

**No new candidates require review** from this cohort (zero
`UNKNOWN_TREASURY_CANDIDATE` results). However, the pre-existing
discrepancy — 3 treasuries independently linked to the same real-world
operator via this project's own "Hello program operator linkage"
memory, yet attributed to 3 distinct operation UUIDs in
`wt_ops_v2_wallets` — is surfaced here for human review, not silently
resolved.

### Success criteria — final status

| Criterion | Status |
|---|---|
| Every launch receives an explicit treasury-resolution result | ✅ 19/19 |
| Creator → sub-provider → treasury lineage preserved | ✅ full evidence path per launch |
| Confirmed treasuries distinguished from unknown candidates | ✅ `KNOWN_TREASURY` requires an existing confirmed row |
| Unknown wallets not promoted automatically | ✅ zero writes anywhere |
| Known operation attribution only through existing confirmed treasury relationships | ✅ verified |
| Cohort resolves into measurable groups | ✅ 7 Known Treasury, 0 Unknown Candidate, 0 No Sub-Provider, 12 Unresolved |
| Discovery interface exposes treasury address and evidence path | ✅ new panel, live-tested API |

### Deliverables

`docs/design/x65_1/` (11 files, now consolidated here);
`src/ops/treasury_resolution.py` (new module, 23 tests); new API route
(`src/core/operation_dashboard_routes.py`); new Discovery UI panel
(`templates/discovery.html`) — deployed live (`watchtower_api`
restarted, pid 64992).

---

## Provenance note

This report consolidates the 11 original per-phase documents
(`x65_1_cohort_reproduction.md` through `x65_1_summary.md`) into one
file. All measurements were taken live against the production database
and the running `watchtower_api` process; the implementation is
deployed and all 67 relevant tests pass. The per-phase files remain
available individually if needed.
