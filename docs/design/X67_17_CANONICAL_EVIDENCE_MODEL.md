# X67.17 — Canonical Evidence Model & Shared Predicate Design

Design only. No code, schema, database, or promotion-logic changes made as part of this
document. Grounded entirely in the empirical findings of X67.13 (topology audit),
X67.14 (operator identity audit), X67.15 (on-chain PLAIN_XFER verification, 75/75
transactions), and X67.16 (session architecture audit, including the 4 relay-assisted
cases).

## Established findings this design is built on

- Three genuine creator-funding mechanisms exist and are all RPC-confirmed real:
  `WSOL_WRAP_CLOSE`, `SEEDED_ACCOUNT_CLOSE`, `PLAIN_XFER` (X67.15, 75/75 verified).
- Session topology (how many hops, whether relay-assisted) is independent of funding
  mechanism — a `PLAIN_XFER` transfer can be self-signed by the subprov (94% of cases)
  or signed by an external relay wallet one hop upstream (6% of cases, X67.16 Phase 3).
- Path A (`evaluate_candidate_for_canonical_promotion`,
  `src/ops/provisioning_candidates_workflow.py:429-543`) and Path B
  (`is_canonical_watchtower_outcome`, `src/core/watchtower_registry_promotion.py:78-83`)
  enforce different predicates today — Path B has already promoted 5 rows that Path A's
  mechanism gate would have rejected (X67.14).
- A small relay-assisted variant exists (X67.16, ~6% of the disputed population) but is
  evidenced as an occasional external capital-injection variant, not a second
  WATCHTOWER architecture — it does not currently justify a separate topology class.
- Provisioning Candidates are a workflow/evidence bucket, not a topology class
  (X67.13 Phase 6) — roughly half of a small resolved sample turned out not to be
  WATCHTOWER at all on investigation.

---

## 1. Canonical evidence model

The model separates WATCHTOWER classification into **five independent evidence
dimensions**. "Independent" means each dimension can vary without constraining the
others — a row's identity strength says nothing about its session topology, and its
funding mechanism says nothing about whether a conflict exists. Conflating these
(as Path B currently does, by skipping four of the five) is the root cause of the
contamination risk X67.14 identified.

### 1a. Identity

Whether the treasury/subprovider pairing is genuinely WATCHTOWER-operated capital.

| Field | Values | Source |
|---|---|---|
| `treasury_confirmed` | bool | `wt_confirmed_treasuries` membership |
| `treasury_confidence_tier` | `CERTAIN` / `CONFIRMED` / `MANUAL` / `LOW` | `wt_confirmed_treasuries.provenance` |
| `subprovider_confidence` | `PROVEN` (repeated wrap-close fan-out) / `SESSION_OBSERVED` / `UNPROVEN` | `ws_cascade.py`'s `_proven_subprov` classification |
| `treasury_lineage_continuity` | `EXCLUSIVE` / `SHARED` / `BROKEN` | Does every session funding this subprov trace to a confirmed treasury, with no unexplained gap? |

**Why this is necessary but not sufficient**: X67.16 Phase 3 proved that a subprov
with excellent treasury lineage on its *inbound* (treasury→subprov) leg can still have
its *outbound* (subprov→creator) leg signed by a wallet with zero treasury lineage at
all. Identity strength on one leg does not transfer to the other leg automatically.

### 1b. Session

Whether the funding event(s) form a recognized, coherent provisioning session, and
what shape that session takes.

| Field | Values | Source |
|---|---|---|
| `session_exists` | bool | `wt_active_subprov_sessions` row present |
| `session_state` | `ACTIVE` / `EXPIRED` / `REJECTED` / `RECONSTRUCTED` (no live row, walkback-derived) | session table / `wt_walkback_queue` |
| `session_topology` | `DIRECT` (subprov signs creator funding itself) / `RELAY_ASSISTED` (upstream wallet signs; subprov is the session anchor but not the funding signer) | RPC-confirmed `funder_wallet == subprov` check (X67.16 Phase 3) |
| `relay_wallet_status` | `NONE` / `EXTERNAL_UNCLASSIFIED` / `EXTERNAL_EXCHANGE_PATTERN` (e.g. UUID-memo withdrawal signature) | RPC trace of the relay wallet's own upstream parent, when `session_topology == RELAY_ASSISTED` |

**Why session is independent of mechanism**: a `DIRECT` session can use any of the
three mechanisms; a `RELAY_ASSISTED` session was observed only with `PLAIN_XFER` in
this dataset (X67.16), but nothing in the architecture *requires* that pairing — it is
an empirical correlation from a 4-case sample, not a structural rule, and the model
must not hard-code it as one.

### 1c. Creator funding (mechanism)

The literal on-chain instruction shape that delivered funds to the creator wallet.

| Value | RPC signature | Status |
|---|---|---|
| `WSOL_WRAP_CLOSE` | wrap→sync→close, creator = `closeAccount.destination` | Confirmed real (pre-existing STRICT population) |
| `SEEDED_ACCOUNT_CLOSE` | seeded account created, funded, later closed to creator | Confirmed real (pre-existing STRICT population) |
| `PLAIN_XFER` | single System Program `transfer`, creator = direct recipient | Confirmed real, 75/75 RPC-verified (X67.15) |
| `UNVERIFIED` | mechanism string stored but never RPC-checked | Legacy/candidate rows pending verification |
| `CONFLICTING` | stored mechanism disagrees with an independently-decoded raw mechanism | X67.14's `MECHANISM_EVIDENCE_CONFLICT` case |

**Why mechanism is independent of topology (explicit, since the task asks for this
explanation)**: mechanism describes *what instruction moved the money*; topology
describes *how many hops and which wallet signed*. These answer genuinely different
questions. A `WSOL_WRAP_CLOSE` transaction is *always* `DIRECT` by construction (the
close-account destination pattern collapses treasury-hop and creator-hop into one
atomic instruction with no room for an external relay to insert itself mid-mechanism).
A `PLAIN_XFER`, being a generic instruction, has no such structural constraint — it can
be signed by the subprov itself or by any upstream wallet the subprov's own capital
passed through. This is exactly why 100% of the account-close population is `DIRECT`
(X67.16 Phase 4) while 6% of the plain-transfer population is `RELAY_ASSISTED` — the
mechanism's own instruction shape determines whether relay-assistance is even
*possible*, but does not determine whether it *occurred*.

### 1d. Evidence quality

How the mechanism/identity facts were established, ordered by decreasing reliability.

| Tier | Description | Example |
|---|---|---|
| `RPC_VERIFIED` | Live `getTransaction` decode performed this session or a prior audit, with instruction-level citation | X67.15's 75 transactions |
| `WALKBACK_RECOVERED` | Retrospective funding-lineage reconstruction, stored signature present but not independently RPC-re-verified | Most `wt_walkback_queue` rows |
| `HISTORICAL_RECONSTRUCTION` | Pre-dates detection-provenance tooling; anchor recovered from `token_analysis`/`creator_funding_queue` fallback, weaker chain-of-custody | The 13 legacy `CLOSE_ACCOUNT_DESTINATION`+NULL-`detection_source` rows from X67.10 |
| `MANUAL_CONFIRMATION` | A human reviewer attested to the fact directly, no automated signature chain | `MANUAL_ATTESTATION` confidence rows |

**Confidence ordering** (highest to lowest, for the purpose of "does this evidence
clear the promotion bar"): `RPC_VERIFIED` > `MANUAL_CONFIRMATION` >
`WALKBACK_RECOVERED` > `HISTORICAL_RECONSTRUCTION`. Manual confirmation is placed
above walkback-recovered because it reflects a human decision after review, whereas
walkback-recovered is an automated reconstruction that has not been independently
re-verified — a distinction the current schema does not make (both currently
collapse into `confidence='WALKBACK'` or `'MANUAL_ATTESTATION'` without a shared
ordering).

### 1e. Conflicts

Any positively-demonstrated fact that contradicts a clean WATCHTOWER classification.

| Conflict | Fatal? | Evidence required | Example |
|---|---|---|---|
| `EXCHANGE_BOUNDARY` | **Fatal** | Immediate funder RPC-confirmed as a known exchange wallet (structured evidence, not just a closure note — X67.14 found the "Binance 2" claim behind 5 candidate rows was unverifiable from any table) | X67.13's 5 `EXCHANGE_BOUNDARY` candidates |
| `MULTI_SOURCE_RELAY` | **Reviewable, not automatically fatal** | Immediate funder inconsistent with the wallet's other launches, but the wallet's *treasury-level* parent remains consistent (X67.16 Phase 7 showed this can still be a genuine WATCHTOWER relay pattern) | X67.13's 3 `MULTI_SOURCE_RELAY` candidates |
| `ROLE_COLLISION` | **Fatal** | Same wallet appears as treasury/subprov/creator within one launch, or (the cross-mint variant X67.14 Phase 2 found) as subprov in one launch and creator in another | `CVdByCD7...`'s cross-mint overlap |
| `MECHANISM_CONFLICT` | **Fatal** | Stored mechanism disagrees with an independently-decoded raw mechanism | X67.14's `HqDzBCPHMNKu...`/`9Pp8MeVxT5ku...` |
| `LINEAGE_CONFLICT` | **Fatal** | Walkback-recovered subprov disagrees with the subprov being evaluated | Existing Path A gate, `provisioning_candidates_workflow.py:530-536` |
| `RELAY_UNCLASSIFIED` | **Informational** | Session is `RELAY_ASSISTED` but the relay wallet's own upstream parent could not be classified as exchange-pattern or otherwise | X67.16's `5tzFkiKscXHK...` case |
| `SHARED_RELAY_SESSION_VOLUME` | **Informational (soft signal only)** | Subprov's session count exceeds `SHARED_RELAY_SESSION_THRESHOLD` (currently 50) | Existing Path A soft flag, never a hard rejection per X67.4's finding that confirmed subprovs range up to 288 sessions |

Distinguishing fatal from reviewable matters because X67.13/X67.14 found that
`MULTI_SOURCE_RELAY` in particular is not always disqualifying — the treasury-level
lineage can remain intact even when a downstream comparison is ambiguous. Treating it
as automatically fatal would re-introduce the same kind of over-rejection Path A's
`SHARED_RELAY_SESSION_THRESHOLD` was deliberately designed to *avoid* (a soft signal,
not a hard gate).

---

## 2. Canonical eligibility matrix

Columns: **Identity** (treasury confirmed? Y/N) · **Session** (topology) · **Mechanism**
(verified value) · **Conflict** (none / reviewable / fatal) · **Result**.

| Identity | Session | Mechanism | Conflict | Result |
|---|---|---|---|---|
| Confirmed | DIRECT | WSOL_WRAP_CLOSE (RPC-verified) | None | **Accepted** |
| Confirmed | DIRECT | SEEDED_ACCOUNT_CLOSE (RPC-verified) | None | **Accepted** |
| Confirmed | DIRECT | PLAIN_XFER (RPC-verified) | None | **Accepted** |
| Confirmed | RELAY_ASSISTED | PLAIN_XFER (RPC-verified) | None, relay unclassified (informational only) | **Accepted, flagged for audit trail** |
| Confirmed | RELAY_ASSISTED | PLAIN_XFER (RPC-verified) | Relay shows exchange-pattern signature (e.g. UUID memo) | **Requires Review** — identity of the *relay*, not the subprov, is now in question |
| Confirmed | DIRECT | any | Mechanism conflict | **Rejected** |
| Confirmed | DIRECT | any | Role collision | **Rejected** |
| Confirmed | DIRECT | any | Lineage conflict | **Rejected** |
| Confirmed | any | any | Exchange boundary | **Rejected** |
| Confirmed | DIRECT | any | Multi-source relay | **Requires Review** |
| Confirmed | any | UNVERIFIED (no RPC check yet, no conflict) | None | **Insufficient Evidence** (this is exactly the 4 Class-A candidates from X67.14, "RECOVERABLE" — pending a routine RPC call, not rejected) |
| Confirmed | any | any | Conflict = high session-volume soft flag only | **Accepted** (soft signal alone never blocks, per established X67.4 precedent) |
| Not confirmed | any | any | any | **Rejected** (`IDENTITY_UNCONFIRMED`) |
| Confirmed | Session absent/reconstructed only, no funding evidence at all | — | — | **Insufficient Evidence** |
| Confirmed | DIRECT | WSOL_WRAP_CLOSE / SEEDED_ACCOUNT_CLOSE, evidence tier = HISTORICAL_RECONSTRUCTION | None | **Accepted** (the 13 legacy rows — X67.10 proved these are pre-migration legacy, not active-writer defects; historical evidence tier is sufficient for already-confirmed account-close mechanisms, since those mechanisms carry their own strong internal proof — an atomic wrap-close instruction is self-verifying by shape) |
| Confirmed | DIRECT | PLAIN_XFER, evidence tier = WALKBACK_RECOVERED only (no RPC verification ever performed) | None | **Requires Review** — unlike account-close mechanisms, PLAIN_XFER's instruction shape is not self-verifying (a generic transfer could theoretically hide many things), so X67.15's standard of requiring actual RPC verification before trusting a stored PLAIN_XFER label should be the bar, not merely no proof, and not merely a stored label |

This is deliberately **not** a flat truth table over every dimension's cross-product —
several combinations are structurally impossible (e.g. `WSOL_WRAP_CLOSE` +
`RELAY_ASSISTED` never occurred in any of the 143 canonical rows or 75 verified
transactions, consistent with §1c's explanation of why the mechanism's own shape
constrains topology) and are omitted rather than populated with a speculative result.

---

## 3. Shared predicate specification

### Name
`evaluate_watchtower_canonical_eligibility()`

This is deliberately **not** named to match either existing function
(`evaluate_candidate_for_canonical_promotion` or `is_canonical_watchtower_outcome`) —
per the task's explicit instruction not to simply make Path B call Path A, this is a
new, extracted, workflow-agnostic function that both existing call sites would adopt.

### Inputs
A single evidence object (not a database connection, not a live RPC client — see
§6 for why this matters for Path A/B unification):

```
CanonicalEvidenceInput:
  mint: str
  treasury_wallet: str | None
  subprov_wallet: str | None
  creator_wallet: str | None
  treasury_confirmation: TreasuryConfirmationEvidence   # from wt_confirmed_treasuries lookup
  session_evidence: SessionEvidence                     # from wt_active_subprov_sessions / wt_walkback_queue
  mechanism_evidence: MechanismEvidence                 # stored mechanism + independent cross-check value + RPC verification status
  conflict_evidence: list[ConflictSignal]               # every conflict check already run, with its own evidence citation
  evidence_tier: EvidenceQualityTier
```

The caller (Path A or Path B) is responsible for *gathering* this evidence from
whatever source it already has (workflow table, walkback queue, RPC client) — the
predicate itself performs **no I/O**. This is the key architectural decision that
avoids "Path B calling Path A" (which the task explicitly warns against, since Path A
embeds workflow-specific assumptions like `wt_provisioning_candidate_workflow` row
shapes): both paths build the same evidence object from their own data source, then
hand it to one shared, pure decision function.

### Decision flow
1. **Identity gate**: is `treasury_confirmation.confirmed == True`? If not →
   `IDENTITY_UNCONFIRMED`, terminal rejection, no further checks run.
2. **Fatal conflict gate**: does `conflict_evidence` contain any conflict marked fatal
   in §1e (exchange boundary, role collision, mechanism conflict, lineage conflict)?
   If so → rejection with the specific reason code, terminal.
3. **Mechanism verification gate**: is the mechanism one of the three known values,
   and is its evidence tier at least `WALKBACK_RECOVERED`? If mechanism is
   `UNVERIFIED` or evidence tier is below the required bar for that mechanism (see
   the matrix row distinguishing account-close vs. plain-transfer evidence bars) →
   `EVIDENCE_INSUFFICIENT`, held for review, not rejected.
4. **Session topology check**: is `session_evidence.topology == DIRECT`, or
   `RELAY_ASSISTED` with the relay itself showing no exchange-pattern signature? If
   `RELAY_ASSISTED` with an exchange-pattern relay → `MANUAL_REVIEW_REQUIRED`
   (identity of the relay is now the open question, not the subprov's).
5. **Reviewable-conflict gate**: does `conflict_evidence` contain a
   `MULTI_SOURCE_RELAY` conflict? If so → `MANUAL_REVIEW_REQUIRED`, not automatic
   rejection (per §1e's distinction).
6. **Accept**: if all gates pass → `CANONICAL_CONFIRMED`.

### Failure points
Every gate above produces a terminal or non-terminal outcome — there is no code path
that silently falls through to acceptance without a gate explicitly passing. This is
the direct fix for X67.14's finding that Path B's writer "never validates mechanism
against `VALID_MECHANISMS`" — under this design, mechanism verification is gate 3,
unconditionally run for both paths.

### Review states
`MANUAL_REVIEW_REQUIRED` and `EVIDENCE_INSUFFICIENT` are **not** rejections — they are
a third, explicit outcome distinct from accept/reject, matching Policy D's
"review-only" idea from X67.15's policy menu, applied narrowly to the specific cases
that warrant it (relay-with-exchange-signature, unverified mechanism) rather than to
all PLAIN_XFER rows indiscriminately.

---

## 4. Structured decision output

```
CanonicalEligibilityResult:
  eligible: bool                        # true only for CANONICAL_CONFIRMED
  decision: Literal["ACCEPTED", "REJECTED", "REVIEW_REQUIRED", "INSUFFICIENT_EVIDENCE"]
  identity_status: Literal["CONFIRMED", "UNCONFIRMED"]
  session_status: Literal["ACTIVE", "EXPIRED", "RECONSTRUCTED", "ABSENT"]
  session_topology: Literal["DIRECT", "RELAY_ASSISTED", "UNKNOWN"]
  creator_funding_mechanism: Literal["WSOL_WRAP_CLOSE", "SEEDED_ACCOUNT_CLOSE",
                                      "PLAIN_XFER", "UNVERIFIED", "CONFLICTING"]
  evidence_strength: Literal["RPC_VERIFIED", "WALKBACK_RECOVERED",
                              "HISTORICAL_RECONSTRUCTION", "MANUAL_CONFIRMATION"]
  conflicts: list[str]                  # every conflict code detected, even non-fatal ones
  missing_evidence: list[str]           # what specifically would resolve an
                                         # INSUFFICIENT_EVIDENCE or REVIEW_REQUIRED result
  review_required: bool
  decision_reason: str                  # the single primary reason code (§5)
  relay_wallet: str | None              # NEW FIELD — populated only when session_topology == RELAY_ASSISTED
  relay_classification: Literal["NONE", "UNCLASSIFIED", "EXCHANGE_PATTERN"] | None  # NEW FIELD
```

### Additional fields beyond the task's suggested list
Two fields (`relay_wallet`, `relay_classification`) are added beyond the task's
suggested schema, because X67.16 is the first audit to establish that "who signed the
creator-funding transaction" and "who the labelled subprov is" can genuinely diverge.
Without these fields, a `RELAY_ASSISTED` session's most important fact — which wallet
to actually scrutinize — would be lost inside the opaque `conflicts`/`missing_evidence`
free-text lists. This directly operationalizes X67.16's recommendation to record
`intermediate_hop_detected` as its own metadata fact, orthogonal to mechanism.

---

## 5. Reason-code catalogue

Every code below is stable (never renumbered/reused) and every decision path in §3
terminates in exactly one of these:

| Code | Decision | Meaning |
|---|---|---|
| `CANONICAL_CONFIRMED` | ACCEPTED | All gates passed |
| `IDENTITY_UNCONFIRMED` | REJECTED | Treasury not in `wt_confirmed_treasuries` |
| `CONFLICT_EXCHANGE_BOUNDARY` | REJECTED | Immediate funder RPC-confirmed as a known exchange wallet |
| `CONFLICT_ROLE_COLLISION` | REJECTED | Treasury/subprov/creator overlap, within-mint or cross-mint |
| `CONFLICT_MECHANISM` | REJECTED | Stored mechanism disagrees with independently-decoded raw mechanism |
| `CONFLICT_LINEAGE` | REJECTED | Walkback-recovered subprov disagrees with evaluated subprov |
| `CREATOR_FUNDING_UNSUPPORTED` | REJECTED | Mechanism is none of the three known values, with no plausible reclassification |
| `EVIDENCE_INSUFFICIENT` | INSUFFICIENT_EVIDENCE | Mechanism value known but never RPC-verified, and no fatal conflict present |
| `SESSION_INVALID` | INSUFFICIENT_EVIDENCE | No session evidence of any kind exists (fully unrecoverable state) |
| `SESSION_RELAY_ASSISTED_UNCLASSIFIED` | REVIEW_REQUIRED | Relay-assisted session, relay wallet's own nature not yet determined |
| `SESSION_RELAY_ASSISTED_EXCHANGE_SIGNATURE` | REVIEW_REQUIRED | Relay-assisted session, relay's upstream parent shows exchange-pattern evidence (e.g. UUID memo) |
| `CONFLICT_MULTI_SOURCE_RELAY` | REVIEW_REQUIRED | Immediate funder inconsistent with wallet's other launches, but treasury lineage intact |
| `MANUAL_REVIEW_REQUIRED` | REVIEW_REQUIRED | Generic catch-all only used when a reviewable condition exists but doesn't map to a more specific code above |

Every reason code maps to exactly one `decision` value — there is no code that could
legitimately appear under two different decisions, which is what makes automated
regression testing of the reason-code catalogue itself possible (§11).

---

## 6. Path A / Path B unification design

### Current architecture
- **Path A** (`evaluate_candidate_for_canonical_promotion`,
  `provisioning_candidates_workflow.py:429-543`): reads
  `wt_provisioning_candidate_workflow` row fields directly, performs its own RPC
  balance-delta verification inline (`verify_candidate`, lines 263-390), enforces
  `VALID_MECHANISMS = {WSOL_WRAP_CLOSE, SEEDED_ACCOUNT_CLOSE}` (line 134 — notably,
  `PLAIN_XFER` is not yet in this set; see §12 for the sequencing implication), role
  separation, lineage conflict, mechanism cross-check.
- **Path B** (`is_canonical_watchtower_outcome`,
  `watchtower_registry_promotion.py:78-83`): a two-field check against
  `wt_attribution_outcomes`, with zero mechanism/role/lineage validation of its own.

### Unified architecture
Both paths gather evidence from their respective sources (Path A already has
everything it needs from the candidate workflow row plus its own RPC verifier; Path B
would gain a new evidence-gathering step reading `wt_walkback_queue`,
`wt_confirmed_treasuries`, and `wt_active_subprov_sessions` for the mint in question)
and both call `evaluate_watchtower_canonical_eligibility()` with the resulting
`CanonicalEvidenceInput`. Neither path embeds decision logic directly anymore —
`promote_eligible_candidate()` and `promote_walkback_confirmed_watchtower()` both
become thin wrappers: gather evidence → call shared predicate → if `eligible`, call
the existing, unchanged `record_launch()` writer.

### Benefits
- Closes the exact contamination vector X67.14 demonstrated (5 existing rows that
  Path A's own gate would reject).
- Makes `PLAIN_XFER` acceptance a single, auditable policy decision (§12) rather than
  an accidental side effect of Path B's permissiveness.
- The `relay_wallet`/`relay_classification` fields become available to *both* paths
  for the first time — today, Path B has no way to even detect a relay-assisted
  session, since it performs no session-topology check at all.

### Backward compatibility
The unchanged, existing `record_launch()`/`promote_walkback_confirmed_watchtower()`
registry-write mechanics are preserved exactly — only what happens *before* that write
changes. No schema migration is implied by this design (see §10 for what new columns
would eventually be needed, all additive).

### Migration risks
- **Retroactive disagreement**: applying the unified predicate to the 5 existing
  PLAIN_XFER rows will very likely produce `REVIEW_REQUIRED` or `ACCEPTED` (not
  `REJECTED`, per X67.15's evidence — these 5 are RPC-verified, `DIRECT`-topology,
  genuine `PLAIN_XFER` transfers from confirmed treasuries) rather than clean
  `CANONICAL_CONFIRMED` — because their evidence tier is `WALKBACK_RECOVERED`, not
  `RPC_VERIFIED`, at the row level (the RPC verification exists as an *audit finding*
  in X67.15, not as a field persisted back onto the row itself). This is a genuine
  design tension addressed in §8.
- **No automatic reclassification**: per the task's explicit constraint and X67.14's
  prior recommendation, no existing row should change state as a side effect of
  deploying the shared predicate — any reclassification must be a separate, reviewed,
  explicit action.

---

## 7. Shadow-evaluation plan

Run the shared predicate in shadow mode (compute-only, no writes, no promotion side
effects) against four populations, cross-tabulated by (a) current registry
membership and (b) shared-predicate decision:

| | Predicate: ACCEPTED | Predicate: REJECTED / REVIEW / INSUFFICIENT |
|---|---|---|
| **Currently Canonical** (in `wt_watchtower_launches`) | **Canonical + Pass** — expected majority (138 of 143 STRICT/account-close rows; see §8 for the 5 PLAIN_XFER rows' expected placement) | **Canonical + Fail** — the population §8 must explain row-by-row; MUST NOT silently exist without a documented reason |
| **Currently a Provisioning Candidate** (`wt_provisioning_candidate_workflow`) | **Candidate + Pass** — expected to include the 4 Class-A rows from X67.14 (pending only a routine RPC check) | **Candidate + Fail** — expected to include the 5 `EXCHANGE_BOUNDARY` + 3 `MULTI_SOURCE_RELAY` rows, now correctly routed to `REVIEW_REQUIRED` rather than left in an ambiguous closed state |

Two additional shadow populations per the task's phase 7 requirement:
- **Known Non-WATCHTOWER controls**: any mint with a confirmed non-WATCHTOWER
  attribution outcome (e.g. `wt_attribution_outcomes.outcome_type` resolving to a CEX
  or repeat-creator group instead of `CANONICAL_OPERATOR_REACHED`). Expected result:
  100% `IDENTITY_UNCONFIRMED` rejections — any exception here is a **serious** finding
  requiring immediate investigation, since it would mean the shared predicate can be
  fooled by non-WATCHTOWER capital.
- **Unresolved exchange/relay candidates** (the same 8 rows as above, listed
  separately per the task's phrasing): expected to land in `REVIEW_REQUIRED`, not
  silently `REJECTED` and not silently `ACCEPTED` — the shadow run's job here is to
  confirm the predicate produces the *reviewable* outcome rather than collapsing this
  nuance the way today's binary accept/reject Path A does.

**Every disagreement between the old and new systems must be individually explained**,
not aggregated away — a per-mint diff report (old classification vs. new decision vs.
reason code) is the primary shadow-run artifact.

---

## 8. Existing canonical impact assessment

Applying the matrix (§2) to today's 143 canonical rows:

| Category | Count (estimate, grounded in X67.13/X67.14/X67.15 data) | Explanation |
|---|---|---|
| **Pass unchanged** | 120 (WSOL_WRAP_CLOSE) + 18 (SEEDED_ACCOUNT_CLOSE) − 13 (the legacy NULL-detection rows, evaluated separately below) = **125** | Account-close mechanisms are self-verifying by instruction shape (§2); their `HISTORICAL_RECONSTRUCTION` or `WALKBACK_RECOVERED` evidence tier is sufficient given the mechanism's own strength |
| **Pass, legacy evidence tier (13 rows)** | 13 | The X67.10-identified pre-migration legacy rows (`CLOSE_ACCOUNT_DESTINATION`+NULL `detection_source`) — proven non-defective by an 8,152-second clean timestamp boundary in X67.10; pass under the same account-close self-verification logic as the 125 above, explicitly carved out here so the count is auditable rather than silently folded in |
| **Requires manual review** | 5 (the PLAIN_XFER rows) | Per §6's migration-risk note: genuinely `DIRECT`-topology, RPC-verified-as-a-fact-of-this-audit `PLAIN_XFER` transfers, BUT the verification currently lives in X67.15's audit output, not as a persisted per-row field. Under a strict reading of the matrix (evidence tier must be `RPC_VERIFIED`, not merely "an audit once checked a sample containing this exact row"), these 5 land in `REVIEW_REQUIRED` with reason `EVIDENCE_INSUFFICIENT` until the RPC-verified status is persisted onto the row itself — a data-quality gap, not a topology or identity problem. **Recommended resolution**: since X67.15 already RPC-verified these exact 5 signatures by exact signature match, the correct near-term action is to persist `evidence_strength=RPC_VERIFIED` onto these 5 specific rows (an additive metadata write, not a reclassification) so they cleanly pass on the very next predicate run, rather than requiring a second live RPC re-check of transactions already decoded. |
| **Fails** | 0 (expected) | No canonical row is expected to hit a fatal conflict code — if the shadow run finds one, that is a genuine, unexpected discovery requiring its own investigation before any further rollout step, not something this design can pre-explain |

Every one of the 5 "requires review" exceptions is explained above — none are
unexplained.

---

## 9. Provisioning candidate impact assessment

Applying the matrix to the 17 rows in `wt_provisioning_candidate_workflow` (per
X67.14's re-derivation, not X67.13's original 8-row estimate):

| Category | Mints | Reason |
|---|---|---|
| **Confirmed** | `HJQC4xW9k3gx...`, `Af72QENbvRee...` | Already `PROMOTED_TO_MODEL_1` — full RPC-verified topology, no conflicts |
| **Needs Review** | `7z4cgsb7eg...`, `3gosQAi7WAK...`, `9QLyikZbyjmv9...`, `wGEyTQEyhE5...` (X67.14's Class A, "RECOVERABLE") | `EVIDENCE_INSUFFICIENT` — mechanism and identity otherwise clean, purely awaiting the routine RPC balance-delta check Path A's own verifier already knows how to run |
| **Needs Review** | `HqDzBCPHMNKu...`, `9Pp8MeVxT5ku...` (X67.14's Class C) | `CONFLICT_MECHANISM` — reviewable in the sense that re-decoding the raw close-instruction (X67.14's own recommended next step) could resolve which of the two disagreeing labels is correct, but this conflict code is listed as fatal in §1e/§2, so today's design would REJECT these two, not merely flag them, unless the re-decode is performed first. This is called out explicitly as a design tension: a "fatal" conflict that is nonetheless plausibly resolvable with one more RPC call sits awkwardly between REJECTED and REVIEW_REQUIRED. **Recommendation**: keep `CONFLICT_MECHANISM` fatal only when both a stored AND an independently-decoded value exist and PERMANENTLY disagree after a live re-check; treat the current, not-yet-re-decoded state as `EVIDENCE_INSUFFICIENT` rather than `REJECTED`, deferring the fatal classification until the re-decode is actually attempted. |
| **Rejected** | 5 `EXCHANGE_BOUNDARY` rows | `CONFLICT_EXCHANGE_BOUNDARY` — **but** X67.14 found this claim is unverifiable from any table (no structured evidence backs the "Binance 2" closure note). Per the matrix, an exchange-boundary conflict requires **RPC-confirmed** exchange attribution, not a free-text closure note. **These 5 should NOT be rejected under this design as currently evidenced — they should land in `REVIEW_REQUIRED`** with `missing_evidence` noting that the exchange claim needs independent RPC/labeled-address confirmation before it can be treated as fatal. This is a direct, explicit correction versus how these rows are handled today (closed, treated as settled). |
| **Still Unknown** | 3 `MULTI_SOURCE_RELAY` rows, 1 `INSUFFICIENT_EVIDENCE` row | `CONFLICT_MULTI_SOURCE_RELAY` (reviewable, not fatal, per §1e) and `SESSION_INVALID` respectively |

No candidate is promoted by this assessment — it is purely a classification exercise
under the proposed matrix.

---

## 10. Metadata requirements

| Field | Classification | Rationale |
|---|---|---|
| `session_topology` (`DIRECT`/`RELAY_ASSISTED`/`UNKNOWN`) | **Required** | Core new axis from X67.16; without it the predicate cannot distinguish the 94% direct case from the 6% relay case |
| `creator_funding_verified` (bool, was this mechanism RPC-checked) | **Required** | Directly resolves §8's PLAIN_XFER review-state tension — this is the single missing field blocking the 5 existing rows from cleanly passing |
| `creator_funding_source` (the actual signer wallet, which may differ from `subprov_wallet`) | **Required** | X67.16 Phase 3's core finding — without this field, a relay-assisted session's most important fact is unrecoverable from the registry row alone |
| `relay_detected` (bool) | **Required** | Feeds `session_topology`; simplest possible boolean gate for query/filtering purposes even before the richer `relay_classification` field is populated |
| `intermediate_hop_detected` | **Useful** (arguably redundant with `relay_detected` — recommend collapsing into one field rather than keeping both, to avoid two near-synonymous booleans drifting apart over time) | X67.16's own suggested name; keep the concept, pick one field name |
| `evidence_strength` | **Required** | Directly drives the matrix's evidence-tier gate (§2, §8) |
| `decision_reason` | **Required** | The stable reason code (§5) — without persisting this, every future audit has to re-derive why a row passed/failed from scratch, exactly the problem X67.13-X67.16 existed to solve once and shouldn't need solving again |
| `review_state` | **Useful** | Distinct from `decision_reason` — tracks whether a `REVIEW_REQUIRED` row has since been manually resolved, and by whom/when; not required for the predicate itself to function, but required for any operational workflow built on top of it |

---

## 11. Test specification

| # | Test | Expected outcome |
|---|---|---|
| 1 | Genuine account-close (WSOL_WRAP_CLOSE), confirmed treasury, DIRECT topology, no conflicts | `CANONICAL_CONFIRMED` |
| 2 | Genuine seeded-account-close, confirmed treasury, DIRECT topology, no conflicts | `CANONICAL_CONFIRMED` |
| 3 | Genuine plain-transfer, confirmed treasury, DIRECT topology, `evidence_strength=RPC_VERIFIED`, no conflicts | `CANONICAL_CONFIRMED` |
| 4 | Plain-transfer, confirmed treasury, RELAY_ASSISTED topology, relay classified NONE/benign, sufficient evidence | `CANONICAL_CONFIRMED` with `relay_wallet` populated |
| 5 | Plain-transfer, confirmed treasury, RELAY_ASSISTED topology, relay shows exchange-pattern signature | `MANUAL_REVIEW_REQUIRED` / `SESSION_RELAY_ASSISTED_EXCHANGE_SIGNATURE` |
| 6 | Exchange-boundary conflict, RPC-confirmed (not merely a closure-note claim) | `REJECTED` / `CONFLICT_EXCHANGE_BOUNDARY` |
| 7 | Exchange-boundary conflict, only a free-text closure note, no structured RPC evidence | `REVIEW_REQUIRED` / `EVIDENCE_INSUFFICIENT` (NOT `REJECTED` — regression-guards §9's correction) |
| 8 | Role collision (within-mint: treasury==subprov) | `REJECTED` / `CONFLICT_ROLE_COLLISION` |
| 9 | Role collision (cross-mint: same wallet is subprov in mint X, creator in mint Y) | `REJECTED` / `CONFLICT_ROLE_COLLISION` — regression-guards the `CVdByCD7...` finding from X67.14 |
| 10 | Mechanism conflict (stored vs. independently-decoded disagree, re-decode not yet attempted) | `REVIEW_REQUIRED` / `EVIDENCE_INSUFFICIENT`, per §9's recommended tension-resolution (NOT immediately fatal) |
| 11 | Mechanism conflict, re-decode attempted and still disagrees | `REJECTED` / `CONFLICT_MECHANISM` |
| 12 | Insufficient evidence (mechanism value present, never RPC-verified, no conflict) | `INSUFFICIENT_EVIDENCE` / `EVIDENCE_INSUFFICIENT` |
| 13 | Path A and Path B given equivalent evidence inputs for the same hypothetical mint | Identical `CanonicalEligibilityResult` — this is the direct equivalence test X67.14 found completely absent from the test suite |
| 14 | Existing canonical registry, full 143-row replay in shadow mode | Matches §8's breakdown exactly: 138 pass unchanged (125 + 13 legacy), 5 review-required, 0 rejected |
| 15 | Known non-WATCHTOWER control (confirmed different attribution outcome) | `REJECTED` / `IDENTITY_UNCONFIRMED`, 100% of the control population — any exception is a P0 finding |
| 16 | High session-volume soft flag alone (subprov session count > `SHARED_RELAY_SESSION_THRESHOLD`), no other conflict | `CANONICAL_CONFIRMED` — regression-guards that the soft signal alone never blocks, per X67.4's established precedent |
| 17 | Idempotent evaluation — same evidence input evaluated twice | Identical result both times (the predicate is pure/deterministic, no hidden state) |

---

## 12. Migration roadmap (no implementation)

```
Design approval (this document)
        ↓
Shared predicate implementation (evaluate_watchtower_canonical_eligibility())
  + its own isolated unit tests (test spec §11, items 1-12, 16-17)
        ↓
Evidence-gathering adapters written for Path A and Path B
  (translate each path's existing data sources into CanonicalEvidenceInput
   — no change yet to either path's actual promotion behavior)
        ↓
Shadow evaluation (§7) run against:
   - all 143 canonical rows
   - all 17 provisioning-candidate rows
   - a known non-WATCHTOWER control sample
   - the 8 exchange/relay-disputed rows specifically
        ↓
Result comparison — produce the per-mint diff report (§7);
every disagreement individually explained, none aggregated away
        ↓
Manual reconciliation:
   - persist evidence_strength=RPC_VERIFIED onto the 5 already-audited
     PLAIN_XFER rows (§8's recommended additive metadata write — NOT a
     reclassification, no registry row's eligible status changes as a
     result, only a provenance field is added)
   - decide, as an explicit policy call, whether to independently
     RPC-verify the "Binance 2" exchange claim for the 5 EXCHANGE_BOUNDARY
     candidates before their REVIEW_REQUIRED status can resolve either way
        ↓
Unified deployment — both Path A and Path B call the shared predicate
for all NEW promotions going forward; existing rows are NOT touched
automatically
        ↓
Monitoring — track decision/reason-code distribution over the following
weeks; alert on any REJECTED or REVIEW_REQUIRED rate change that doesn't
match the shadow-run baseline
        ↓
Legacy predicate removal — only after a monitoring period with zero
unexplained divergence between old and new systems; Path A's inline
mechanism/role/conflict checks and Path B's bare two-field check are
both retired in favor of the one shared function
```

---

## Risk assessment

- **Data-quality risk (moderate)**: the 5 existing PLAIN_XFER rows' evidence currently
  lives only in this session's/X67.15's audit output, not as a persisted field —
  deploying the predicate without first performing the additive metadata write in the
  migration roadmap would cause an unnecessary, avoidable regression (previously
  "canonical," now "review required") for rows this project has already, rigorously,
  proven correct.
- **Policy-ambiguity risk (moderate)**: §9's mechanism-conflict tension (fatal vs.
  reviewable, pending a re-decode) is a genuine open design question this document
  flags but does not fully resolve — recommend a small, explicit follow-up decision
  before implementation, not a silent default either way.
- **Evidence-verification risk (low-moderate)**: the exchange-boundary claim behind
  5 candidate rows remains unverified by any structured data (X67.14). This design's
  correction (treat as `REVIEW_REQUIRED`, not settled) is safer than the status quo,
  but the underlying uncertainty about those 5 rows' true identity is not resolved by
  this document — only reclassified as explicitly open rather than implicitly closed.
- **Regression risk on existing writers (low)**: `record_launch()` itself is
  untouched by this design; the blast radius is strictly limited to what happens
  before that call.

---

## Final verdicts

**Verdict 1 — Evidence model completeness: B — Minor gaps.**
The five-dimension model (identity, session, mechanism, evidence quality, conflicts)
covers everything X67.13-X67.16 empirically found, including the newly-discovered
relay-assisted topology and its distinct evidence needs. The gaps are narrow and
already named explicitly in this document rather than hidden: (a) the mechanism-
conflict fatal-vs-reviewable tension (§9), (b) the exchange-boundary evidence
standard needing a structured (not free-text) source before it can be trusted as
fatal (§9), and (c) the practical question of persisting `RPC_VERIFIED` status onto
already-audited rows rather than requiring a second live check (§8). None of these
gaps require new investigation to close — they require a follow-up policy decision,
which is why this is "minor gaps" rather than "significant gaps."

**Verdict 2 — Readiness for implementation: B — Minor design refinement.**
The core predicate, decision output, reason codes, and matrix are concrete and
directly implementable as specified. Two small decisions should be made explicitly
before writing code (not discovered mid-implementation): the mechanism-conflict
fatality question (§9) and whether the exchange-boundary claims get a dedicated
RPC-verification pass before or after initial deployment (§9, §12). Neither blocks
starting implementation of the predicate function and its unit tests (§11 items
1-4, 6, 8-12, 15-17 do not depend on either open decision) — only the two
specific test cases tied to those decisions (items 5, 7, 10, 11) need the policy
call settled first.

**Verdict 3 — Expected implementation risk: A — Low.**
The predicate itself is a pure function with no I/O, matching the architecture that
makes it independently unit-testable without a live database or RPC access (§11).
The only external-system risk is the evidence-gathering adapters for Path A/Path B,
and even that risk is bounded because both paths' existing data sources
(`wt_provisioning_candidate_workflow`, `wt_walkback_queue`, `wt_confirmed_treasuries`,
`wt_active_subprov_sessions`) are already read by existing code today — no new data
source needs to be built, only a new function to translate existing reads into a
shared shape. The existing-registry impact (§8) is fully accounted for with zero
unexplained "fails," and the migration roadmap explicitly defers any row
reclassification to a separate, human-reviewed step — meaning the riskiest possible
action (silently changing what's canonical) is designed out of the initial rollout
entirely.
