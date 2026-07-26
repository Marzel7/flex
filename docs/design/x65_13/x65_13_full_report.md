# X65.13 — Reconcile WATCHTOWER Population Definitions (Full Report)

Read-only investigation. No code changes, no database writes, no UI changes.
Sources: `database/wt_ops_v2.db` (direct SQL), live API
`GET /api/ops-v2/operational-intelligence` with `window=all`,
`campaign=WATCHTOWER` and `operation=WATCHTOWER`, 2026-07-22.

## Contents

1. [Identify Every WATCHTOWER Population](#phase-1--identify-every-watchtower-population)
2. [Validate Launch Counts](#phase-2--validate-launch-counts)
3. [Reconcile Historical Numbers](#phase-3--reconcile-historical-numbers)
4. [Compare Populations](#phase-4--compare-populations)
5. [Canonical Model Validation](#phase-5--canonical-model-validation)
6. [Campaign Evaluation](#phase-6--campaign-evaluation)
7. [Recommendations](#phase-7--recommendations)

---

## Phase 1 — Identify Every WATCHTOWER Population

Five genuinely distinct populations are in active use. None is a naming variant of
another — each has its own table, its own inclusion rule, and (per Phase 3) a
different actual set of mints.

### Population A — Cascade-Confirmed Launches (`wt_watchtower_launches`)

- **Definition**: every launch the live WebSocket cascade daemon
  (`src/core/ws_cascade.py`) directly observed on-chain: a SubProv wrap-close whose
  `closeAccount.destination` then itself issued a `CREATE` instruction.
- **SQL source**: `SELECT * FROM wt_watchtower_launches`
- **Inclusion criteria**: real-time WS observation only, `funding_mechanism` defaults to
  `WSOL_WRAP_CLOSE` (this table is definitionally wrap/close-detected, per its own
  `DEFAULT 'WSOL_WRAP_CLOSE'` column and `creator_extraction_method DEFAULT
  'CLOSE_ACCOUNT_DESTINATION'`).
- **Exclusion criteria**: anything resolved only via walkback/session-lineage
  (a separate, non-cascade path) is never written here.
- **Total records**: **43**
- **Total unique mints**: **43** — this is the number referenced throughout the
  project as "the 42–43 confirmed launches" / "the historical canonical corpus"
  (X65.4, X65.8, X65.10).

### Population B — Operation-Confirmed Launches (`is_watchtower` / `operation_id=WATCHTOWER`)

- **Definition**: the older, stricter X65.1-era flag. A launch is `is_watchtower=True`
  if *any* of four independent sub-sources fires (`operational_intelligence.py:390-403`):
  1. `wt_attribution_outcomes.operator_id = '04265d9f-6eb2-568c-a49e-9253091a4dbb'`
     (the canonical WATCHTOWER operator UUID — confirmed as the correct constant via
     `watchtower_alignment.py:22`; this is the same UUID X65.11 flagged as an
     unexpected `terminal_entity` value, now confirmed to be expected, not an anomaly)
  2. `mint` present in `wt_watchtower_launches` (Population A, by mint)
  3. `mint` present in `watchtower_token_attribution WHERE reviewed_status='CONFIRMED'`
  4. `mint`'s resolved treasury is in `wt_confirmed_treasuries` (`has_confirmed_path`)
  5. `mint` present via `wt_ops_v2_creators`+`wt_ops_v2 WHERE op_type='WATCHTOWER' AND
     status IN ('CONFIRMED','ACTIVE')`
- **SQL source (dominant sub-source, verified directly)**:
  `SELECT DISTINCT mint FROM wt_attribution_outcomes WHERE operator_id='04265d9f-…'`
- **Total records / unique mints**: **80** (sub-source counts: source 1 → 80 rows;
  source 3 → 0 rows, the table exists but has zero `CONFIRMED` rows, confirmed by
  direct query, not a broken lookup; source 5 → 0 rows, `wt_ops_v2` currently has
  **no rows with status CONFIRMED or ACTIVE at all** — only `FORMING` — confirmed by
  direct `GROUP BY op_type,status`; source 4/2 contribute indirectly via the union but
  add nothing beyond source 1 for the mints actually returned in this window, verified
  directly below)
- **This is "the 51 confirmed operations" number's true origin**, but with a caveat:
  51 is **not** the size of Population B itself (80) — it is the size of Population B's
  **intersection with Population D** (Campaign=WATCHTOWER). See Phase 3.

### Population C — Treasury-Confirmed Set (`wt_confirmed_treasuries`)

- **Definition**: confirmed **treasury wallets**, not launches at all.
- **SQL source**: `SELECT * FROM wt_confirmed_treasuries`
- **Total records**: 61 treasuries. **This must never be quoted as a launch count** —
  it is a wallet-identity population, one row per treasury, with no `mint` column in
  its schema (`treasury TEXT PRIMARY KEY, transfer_pct, out_sol, recipients,
  micro_pings, method, confidence, confirmed_at, provenance, no_subscribe`).

### Population D — Campaign=WATCHTOWER (`campaign_classification.py`, X65.7)

- **Definition**: the newest, independent aggregation-layer classifier. A launch is
  `campaign=WATCHTOWER` if `creator_identity=FRESH_CREATOR` AND wrap-close-or-plain-transfer
  evidence is present (X65.7's mandatory criteria) — explicitly does **not** require a
  confirmed treasury (by design).
- **SQL/API source**: `build_campaign_classification()`, surfaced via
  `GET /api/ops-v2/operational-intelligence?campaign=WATCHTOWER`
- **Total records / unique mints**: **291**, at `window=all`.
- **Critical caveat discovered in this audit**: `window=all` is **not** truly
  unbounded. `src/ops/discovery_window.py:36` defines
  `WINDOW_ALL: 365 * 86400` — exactly 365 days. Every population size reported by this
  API (including X65.11's 19 and X65.12's 291) is implicitly bounded to a trailing
  365-day window, even when the caller believes they requested "all time." This is
  reported here as a discovered fact requiring no code change (the naming is
  arguably misleading but the audit's own no-changes constraint applies) — but it
  materially affects the reconciliation in Phase 3.

### Population E — Manually Curated Validation Sets

- **Definition**: none currently exists as a first-class, separately-tagged
  population. The closest analogue is `watchtower_token_attribution`'s
  `reviewed_status` column (values found: `AUTO` 1355, `NEEDS_REWALK` 24, `REVIEW` 3,
  `CONFIRMED` **0**) — a manual-review workflow that has never actually recorded a
  `CONFIRMED` row. **No manually curated WATCHTOWER validation set currently exists
  with any members.**

---

## Phase 2 — Validate Launch Counts

| Population | Total rows | Distinct mints | Duplicate mints | Distinct creators | One row = one launch? |
|---|---|---|---|---|---|
| A: `wt_watchtower_launches` | 43 | 43 | 0 | 43 | **Yes** — `UNIQUE(creator_wallet, create_signature)` constraint; verified `COUNT(*) = COUNT(DISTINCT mint) = COUNT(DISTINCT creator_wallet)` |
| B: `wt_attribution_outcomes` (operator=WATCHTOWER) | 80 | 80 | 0 | not separately queried (mint uniquely determines the row) | **Yes** — `mint TEXT PRIMARY KEY` on the table itself structurally forbids >1 row per mint |
| C: `wt_confirmed_treasuries` | 61 | N/A (no mint column) | N/A | N/A | **N/A — this is a treasury population, not a launch population at all** |
| D: Campaign=WATCHTOWER | 291 | 291 | 0 | 291 | **Yes** — verified directly on the pulled record set (`len(mints)==len(set(mints))==291`) |
| E: manual validation set | 0 | 0 | — | — | N/A — empty |

### Funding-activity vs. launch-count tables, shown for contrast

To directly demonstrate the row-meaning distinction the task asks for, two genuinely
transaction-level (not launch-level) tables were queried for comparison:

| Table | Row count | What one row represents |
|---|---|---|
| `wt_wrap_close_candidates` | 949 | one observed wrap-close **transaction candidate** (may include non-launch-producing wrap-closes, siblings, retries) |
| `wt_subprov_topups` | 0 (currently empty) | one top-up **transaction** when populated |

**Direct answer to Phase 2's required question**: for every population reported in
this project as a "WATCHTOWER launch count" (A, B, D), **one row equals one launch**
(one mint, one creator, one CREATE event) — verified via schema constraints
(`PRIMARY KEY`/`UNIQUE`) and direct `COUNT(*)` vs `COUNT(DISTINCT mint)` equality, not
assumption. Population C is not a launch count and must be relabelled wherever it
appears as such. No population inspected in this audit was found to conflate
transaction-level rows with launch-level rows.

---

## Phase 3 — Reconcile Historical Numbers

### Where each number comes from

| Number | Population | What it represents |
|---|---|---|
| **~42–43** | Population A (`wt_watchtower_launches`) | All-time cascade-confirmed launches. Exactly 43 today; X65.4's "43" and X65.8/X65.10's "43" are the same live, growing table queried at different points in time — not different definitions. (The "~42" phrasing elsewhere is the same table one row earlier.) |
| **51** | **Population B ∩ Population D** (operation-confirmed launches that are *also* Campaign=WATCHTOWER) | Not the size of any single population — see below, this is an intersection, and X65.12's Phase 1 reported it correctly as such ("Also has a confirmed operation_id: 51 of 291") but this task's brief lists it as if it might be a fourth standalone population. It is not; confirmed directly. |
| **291** | Population D (Campaign=WATCHTOWER, `window=all` = 365-day trailing window) | X65.7's classifier output, X65.11/X65.12's working population |

### Full pairwise set relationships (derived directly from mint-level set operations, not assumed)

| Relationship | Result |
|---|---|
| A ⊆ B (cascade-confirmed ⊆ operation-confirmed)? | **No.** 22 of 43 (51%) |
| A ⊆ D (cascade-confirmed ⊆ Campaign=WATCHTOWER)? | **No.** 21 of 43 (49%) |
| B ⊆ D (operation-confirmed ⊆ Campaign=WATCHTOWER)? | **No.** 51 of 80 (64%) |
| D ⊆ B? | No (converse also false — 240 of 291 Campaign launches are *not* operation-confirmed) |

**None of the three populations nests inside another.** The task's own example
hierarchy (`All launches → Campaign(291) → Confirmed Operation(51) → Canonical
Validation(43)`) is **not what the data shows** and must be explicitly discarded.

### Why A (43) is NOT a subset of B or D: the 365-day window is the cause

21–22 of the 43 cascade-confirmed launches are **older than 365 days** — this is the
exact same fact X65.8's Phase 3 already found independently ("21 of the 43 confirmed
launches are older than the 365-day window this [component] currently scans"), now
traced to its root: `WINDOW_ALL = 365 * 86400` in `discovery_window.py`, the single
shared constant every Discovery-facing route (including the ones used to build
Populations B and D) resolves `window=all` through. Population A itself has no such
window restriction (`SELECT * FROM wt_watchtower_launches` is genuinely unbounded),
which is precisely why it contains launches the other two, 365-day-bounded
populations cannot see.

### Actual (measured) relationship diagram

```
wt_watchtower_launches (A, unbounded, 43 total)
    │
    ├── 22 mints ALSO appear in operation-confirmed (B) [within 365d window]
    │       └── 21 of those 22 ALSO appear in Campaign=WATCHTOWER (D)
    │               (1 of the 22 classifies as OTHER_CAMPAIGN instead)
    │
    └── 21 mints do NOT appear in B or D at all
            (created >365 days ago — outside every windowed classifier's reach,
             NOT misclassified; visible only via direct, unbounded SQL on A itself)

Campaign = WATCHTOWER (D, 365d window, 291 total)
    │
    ├── 51 mints ALSO have a confirmed operation_id (B ∩ D)
    │       └── 21 of those 51 ALSO are cascade-confirmed (A ∩ B ∩ D)
    │       └── 30 of those 51 are operation-confirmed WITHOUT cascade evidence
    │               (resolved via walkback/treasury-path/lineage instead)
    └── 240 mints have NO confirmed operation_id
            (Campaign's own design: it never requires a confirmed treasury or
             operation link — X65.7's explicit constraint)

is_watchtower = True (B, 365d window, 80 total)
    │
    ├── 51 mints ALSO classify Campaign=WATCHTOWER
    └── 29 mints classify Campaign=OTHER_CAMPAIGN instead
            (operation-confirmed, but Campaign's own independent evidence
             rules — creator_identity/wrap-close/plain-transfer — disagree
             with the operation-level attribution; this is a genuine,
             measured divergence between two independent classifiers,
             not an error in either one — see Phase 4/6)

wt_confirmed_treasuries (C, 61 rows)
    — a treasury-identity table, not part of the launch hierarchy at all;
      referenced only via has_confirmed_path in B's own union logic
```

---

## Phase 4 — Compare Populations

Per-population dimension breakdowns, computed directly from the live API payloads
(no reuse of X65.12's already-computed 291-population numbers without re-verifying
they match this task's population definitions — confirmed identical).

### Campaign=WATCHTOWER (D, n=291)

| Dimension | Breakdown |
|---|---|
| Mechanism | UNKNOWN 128 (44.0%), PLAIN_TRANSFER 119 (40.9%), WSOL_WRAP_CLOSE 25 (8.6%), MIXED 8 (2.7%), SEEDED_ACCOUNT_CLOSE 11 (3.8%) |
| Topology | MULTI_LEVEL_FAN_OUT 177 (60.8%), FAN_OUT 95 (32.6%), LINEAR 14 (4.8%), UNKNOWN 5 (1.7%) |
| Treasury tier | CONFIRMED 240 (82.5%), UNKNOWN 51 (17.5%) |
| Creator freshness | FRESH_CREATOR 291 (100%) — by construction, X65.7's mandatory criterion |
| Confidence | MEDIUM 259 (89.0%), HIGH 21 (7.2%), BASELINE 11 (3.8%) |

### is_watchtower=True / operation-confirmed (B, n=80)

| Dimension | Breakdown |
|---|---|
| Mechanism | UNKNOWN 46 (57.5%), WSOL_WRAP_CLOSE 15 (18.8%), SEEDED_ACCOUNT_CLOSE 11 (13.8%), MIXED 5 (6.3%), PLAIN_TRANSFER 3 (3.8%) |
| Topology | FAN_OUT 46 (57.5%), LINEAR 19 (23.8%), MULTI_LEVEL_FAN_OUT 9 (11.3%), UNKNOWN 6 (7.5%) |
| Treasury tier | UNKNOWN 51 (63.8%), None/not computed 29 (36.3%, these are the OTHER_CAMPAIGN-classified subset, for which Campaign's treasury-tier field is not populated) |
| Creator freshness | FRESH_CREATOR 68 (85.0%), SINGLE_USE_CREATOR 7 (8.8%), REPEAT_CREATOR 3 (3.8%), DORMANT_REACTIVATED 1, UNKNOWN_CREATOR_IDENTITY 1 |
| Confidence | HIGH 20 (25.0%), MEDIUM 20 (25.0%), BASELINE 11 (13.8%), None 29 (36.3%) |

**Note the sharp contrast with Campaign=WATCHTOWER: only 85% of this population is
even `FRESH_CREATOR`** — 12 launches (15%) have repeat/single-use/dormant/unknown
creator identity, which would have **structurally disqualified** them from Campaign
membership under X65.7's mandatory criteria regardless of any other evidence. This is
a direct, material reason the two populations cannot be treated as interchangeable.

### Cascade-confirmed (A, n=43), restricted to its 22 mints visible within the 365-day window

| Dimension | Breakdown |
|---|---|
| Mechanism | WSOL_WRAP_CLOSE 11 (50.0%), SEEDED_ACCOUNT_CLOSE 11 (50.0%) |
| Topology | **FAN_OUT 21 (95.5%)**, UNKNOWN 1 |
| Treasury tier | UNKNOWN 21 (95.5%), None 1 |
| Creator freshness | FRESH_CREATOR 21 (95.5%), SINGLE_USE_CREATOR 1 |
| Confidence | MEDIUM 11 (50.0%), HIGH 10 (45.5%), None 1 |

The remaining 21 of 43 cascade-confirmed launches are outside the 365-day window
entirely and simply do not appear in either windowed API payload — not misclassified,
not contradicted, genuinely absent from the queryable range (Phase 3).

### Do conclusions transfer across populations?

**No, not automatically.** Three concrete, measured reasons:

1. **Mechanism mix differs sharply.** Campaign=WATCHTOWER is 41% PLAIN_TRANSFER-dominant;
   the cascade-confirmed subset visible in-window is a clean 50/50
   WSOL_WRAP_CLOSE/SEEDED_ACCOUNT_CLOSE split with **zero** PLAIN_TRANSFER. Any
   PLAIN_TRANSFER-specific finding (as in X65.11/X65.12) is inherently untestable
   against Population A, because Population A structurally does not contain
   PLAIN_TRANSFER launches in this window.
2. **Topology skews oppositely.** The cascade-confirmed subset is 95.5% FAN_OUT; the
   full Campaign population is 60.8% MULTI_LEVEL_FAN_OUT. A conclusion like "WATCHTOWER
   launches are typically MULTI_LEVEL_FAN_OUT" is true for D but false for A's
   observable subset — these are not interchangeable ground truths.
3. **Treasury confirmation status is inverted.** 95.5% of the visible cascade-confirmed
   subset has an **UNKNOWN** treasury tier, while 82.5% of Campaign=WATCHTOWER has a
   **CONFIRMED** treasury. A treasury-focused conclusion drawn from one population
   would be actively misleading if applied to the other.

---

## Phase 5 — Canonical Model Validation

**Which population is ground truth for "what is the canonical WATCHTOWER topology /
funding mechanism / operational invariants"?**

**Population A (`wt_watchtower_launches`, the cascade-confirmed set) is the correct
ground-truth population for canonical operational-model questions.** Justification:

- It is the **only** population whose membership rule is a direct, mechanical
  on-chain observation (`_handle_subprov_tx()` watching for a wrap-close whose
  destination then itself CREATEs) — not a downstream classifier's inference over
  walkback/session-lineage evidence (as B and D both are, at least partially).
- It is the population every prior canonical-model-defining task (X65.4's replay,
  X65.8's evidence-alignment audit, X65.10's implementation) was **built and validated
  against**. Changing the ground-truth population now would silently invalidate that
  prior validation work without re-running it.
- It carries **no window restriction** and is not subject to the 365-day
  `WINDOW_ALL` boundary discovered in Phase 3 — the most complete and least
  artifact-prone population currently available.

**However — a caveat this audit must state plainly**: Population A currently has only
**43 launches total**, and (per Phase 3/4) its structural composition is narrow —
100% wrap-close/seeded-account-close mechanism, 95%+ FAN_OUT topology, 0%
PLAIN_TRANSFER. Any canonical-model claim about PLAIN_TRANSFER-mechanism WATCHTOWER
behavior (X65.11, X65.12) is **not answerable from Population A** at all — that
question can currently only be investigated using Population D (Campaign=WATCHTOWER),
with the explicit understanding (Phase 6) that D is a classifier output, not
independently confirmed ground truth for the PLAIN_TRANSFER-mechanism cases.

**How should new launches be compared against WATCHTOWER?**
Against Population A's recorded pattern (Treasury→SubProvider→wrap-close→Creator)
where a launch is cascade-confirmed; against Population D (Campaign classification)
where it is not, with results **explicitly labelled as classifier output, not
ground-truth confirmation** (Phase 6).

---

## Phase 6 — Campaign Evaluation

Campaign (Population D) must be evaluated as a **classifier output**, not a confirmed
ground-truth set, per this audit's own findings:

- **Confirmed vs. unconfirmed proportion**: 240 of 291 (82.5%) have `treasury_tier=CONFIRMED`;
  51 (17.5%) have `treasury_tier=UNKNOWN` — meaning **17.5% of Campaign=WATCHTOWER
  membership rests on zero treasury confirmation at all**, by explicit design (X65.7:
  Campaign must never require a confirmed treasury).
- **Confidence distribution**: only 21 of 291 (7.2%) are `HIGH` confidence; 89.0% are
  `MEDIUM`. High-confidence membership is the small minority, not the norm.
- **Evidence quality**: Campaign's own mandatory criteria (creator_identity=FRESH_CREATOR
  + wrap-close-or-plain-transfer evidence) are necessary but explicitly **not**
  sufficient to prove operational membership in the same sense Population A's direct
  on-chain observation does — fan-out/single-use/treasury-tier are confidence-raising
  signals only, never gating (by design).
- **Precision (measurable against Population A as a proxy ground truth)**: of the 22
  cascade-confirmed launches visible in-window, 21 (95.5%) are also `campaign=WATCHTOWER`
  — a strong, directly measured **21/22 = 95.5% agreement rate** between the two
  classifiers where they overlap. The one disagreement (`campaign=OTHER_CAMPAIGN`) is a
  genuine, single-launch divergence, not a systemic failure.
- **Recall is not fully measurable**: Population A (the nearest available ground truth)
  covers only 22 in-window launches, all wrap-close/seeded-account-close — there is no
  ground-truth set at all for the PLAIN_TRANSFER majority of Population D, so recall
  against that portion cannot be computed from currently available data.

**Conclusion: Campaign is a classifier output with strong measured agreement (95.5%)
against ground truth where a ground truth exists (wrap-close launches), and
unverifiable — not unreliable, simply unverified — for the PLAIN_TRANSFER majority of
its membership, where no independent ground-truth population currently exists.** It
should be presented and used accordingly: suitable for broad discovery/aggregation
work, not yet suitable as a substitute ground truth for canonical-model claims.

---

## Phase 7 — Recommendations

| Task | Recommended population | Why |
|---|---|---|
| **Deriving operational signatures / canonical model definition** | **Population A** (`wt_watchtower_launches`) | Only directly-observed, non-inferential population; the one prior canonical-model work (X65.4/X65.8/X65.10) was already validated against |
| **Validating new hypotheses about wrap-close-mechanism behavior** | Population A | Direct ground truth exists for this mechanism |
| **Validating new hypotheses about PLAIN_TRANSFER-mechanism behavior** | Population D, **with explicit "classifier output, not confirmed" labelling** | No ground-truth population currently covers PLAIN_TRANSFER launches at all; D is the only available evidence, but conclusions must be presented as provisional |
| **Historical research (long-running trend analysis)** | Population A for the wrap-close subset; note the 365-day `WINDOW_ALL` cap explicitly if using any windowed API for anything else | A is the only population without an undisclosed time bound |
| **Reporting / dashboards / general population-size statements** | State the **exact population name** (A/B/C/D) every time a number is reported | Prevents exactly the ambiguity this task was opened to resolve — "WATCHTOWER has 43 launches" and "WATCHTOWER has 291 launches" are both true statements about two different, non-nesting populations |
| **Discovery UI (Campaign stage, X65.6/X65.7)** | Population D (Campaign=WATCHTOWER) | This is what the UI is explicitly designed to surface — an aggregation/discovery layer, not a ground-truth claim; continue presenting it as such (confidence tiers already do this correctly) |
| **Future X-series investigations generally** | State inclusion criteria + population name in the report's own Phase 1, as this and X65.11/X65.12 now do | Makes every future report's numbers independently auditable without re-deriving the reconciliation done here |

### A note correcting X65.12's framing

X65.12 is not wrong in its own terms — its 291-launch analysis and rejection of the
two-mode hypothesis stands. But X65.12's population (D) is **not** the same
population X65.4/X65.8/X65.10 built the canonical model from (A), and X65.12 did not
state this distinction explicitly. Any reader treating X65.12's 291 as validating or
revising the *original* 43-launch canonical model was working from a false equivalence.
This report supplies the missing distinction; no correction to X65.12's own internal
conclusions is required.

### Deliverables

This single report constitutes the complete reconciliation: identification of all five
populations with exact inclusion criteria and SQL/API sources (Phase 1), schema-level
proof that reported counts are unique launches rather than transaction-level rows
(Phase 2), the actual measured (non-hierarchical) relationship diagram and root-cause
explanation via the 365-day `WINDOW_ALL` constant (Phase 3), full dimensional
comparison across populations (Phase 4), identification of Population A as the
canonical ground truth with an explicit PLAIN_TRANSFER coverage gap (Phase 5),
evaluation of Campaign as a classifier output with a measured 95.5% agreement rate
against ground truth (Phase 6), and per-task population recommendations (Phase 7). No
code was changed; no database writes occurred; no UI was modified.
