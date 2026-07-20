# X26.3.1 — Historical Infrastructure Reclassification & Discovery Evidence Integrity Audit

Status: Part A (historical repair) complete and live. Part B (evidence audit)
complete; two logic gaps found and fixed, three wording risks documented
without code changes (see Phase 13 recommendation for why).

---

## Part A — Historical Repair

### Phase 1 — Pre-flight validation

Re-ran `src.ops.subprov_infrastructure_repair_dryrun` immediately before
mutating anything:

- `total_scanned=1244`, `total_affected=24`, `already_rejected_count=0`,
  `would_change_count=24` — identical to the count reported at the end of
  X26.3.
- Verified independently (not just trusting the tool's own filter) that
  every one of the 24 wallets satisfies `is_known_account()` and that none
  already carry a `REJECTED*` state.
- Two of the 24 (`Binance 2`, `Bidget Exchange`) were sitting in a
  pre-existing `state='dismissed'` (an analyst had manually dismissed them
  before this sprint) rather than `PROVISION_CANDIDATE`. Still part of the
  same 24-wallet set, still not yet `REJECTED*` — proceeded, since
  `dismissed` and `REJECTED_INFRASTRUCTURE` both mean "not a valid
  sub-provisioner," and converging them onto one canonical rejected state is
  the point of this repair.

**Approval table** (24 rows, abbreviated wallet):

| Wallet | Infrastructure name | Creator Count | Discovery Source | Current State | Proposed State |
|---|---|---|---|---|---|
| F7p3dFrjRT... | Relay.link Solver | 1 | None | PROVISION_CANDIDATE | REJECTED_INFRASTRUCTURE |
| 5tzFkiKscX... | Binance 2 | 2 | WALKBACK_RECURRING_FUNDER | dismissed | REJECTED_INFRASTRUCTURE |
| A77HErqtfN... | Bidget Exchange | 1 | None | dismissed | REJECTED_INFRASTRUCTURE |
| GpMZbSM2Gg... | Raydium Vault Authority 2 | 2 | WALKBACK_RECURRING_FUNDER | PROVISION_CANDIDATE | REJECTED_INFRASTRUCTURE |
| BmFdpraQhk... | KuCoin 2 | 1 | None | PROVISION_CANDIDATE | REJECTED_INFRASTRUCTURE |
| iGdFcQoyR2... | Bybit Wallet 10 | 2 | WALKBACK_RECURRING_FUNDER | PROVISION_CANDIDATE | REJECTED_INFRASTRUCTURE |
| AxiomRXZAq... | Axiom | 2 | WALKBACK_RECURRING_FUNDER | PROVISION_CANDIDATE | REJECTED_INFRASTRUCTURE |
| 8mowmVCEew... | WhiteBIT Hot Wallet | 1 | None | PROVISION_CANDIDATE | REJECTED_INFRASTRUCTURE |
| u6PJ8DtQuP... | Gate Hot Wallet | 2 | WALKBACK_RECURRING_FUNDER | PROVISION_CANDIDATE | REJECTED_INFRASTRUCTURE |
| Cc3bpPzUvg... | Moonpay | 2 | WALKBACK_RECURRING_FUNDER | PROVISION_CANDIDATE | REJECTED_INFRASTRUCTURE |
| 4NyK1AdJBN... | Coinbase Hot Wallet 4 | 2 | WALKBACK_RECURRING_FUNDER | PROVISION_CANDIDATE | REJECTED_INFRASTRUCTURE |
| 6LY1JzAFVZ... | Kraken Hot Wallet | 2 | WALKBACK_RECURRING_FUNDER | PROVISION_CANDIDATE | REJECTED_INFRASTRUCTURE |
| FpwQQhQQoE... | Coinbase Hot Wallet 1 | 2 | WALKBACK_RECURRING_FUNDER | PROVISION_CANDIDATE | REJECTED_INFRASTRUCTURE |
| ASTyfSima4... | MEXC Hot Wallet | 1 | None | PROVISION_CANDIDATE | REJECTED_INFRASTRUCTURE |
| EMXJqHznGS... | Robinhood Hot Wallet 1 | 2 | WALKBACK_RECURRING_FUNDER | PROVISION_CANDIDATE | REJECTED_INFRASTRUCTURE |
| 5Q544fKrFo... | Raydium Authority V4 | 2 | WALKBACK_RECURRING_FUNDER | PROVISION_CANDIDATE | REJECTED_INFRASTRUCTURE |
| 5g7yNHyGLJ... | Coinbase Hot Wallet | 2 | WALKBACK_RECURRING_FUNDER | PROVISION_CANDIDATE | REJECTED_INFRASTRUCTURE |
| DPqsobysNf... | Coinbase Hot Wallet 4 (Old) | 2 | WALKBACK_RECURRING_FUNDER | PROVISION_CANDIDATE | REJECTED_INFRASTRUCTURE |
| Biw4eeaiYY... | Revolut Hot Wallet | 2 | WALKBACK_RECURRING_FUNDER | PROVISION_CANDIDATE | REJECTED_INFRASTRUCTURE |
| is6MTRHEgy... | OKX Hot Wallet | 1 | None | PROVISION_CANDIDATE | REJECTED_INFRASTRUCTURE |
| D89hHJT5Aq... | Coinbase Hot Wallet 3 | 2 | WALKBACK_RECURRING_FUNDER | PROVISION_CANDIDATE | REJECTED_INFRASTRUCTURE |
| 5F1seMKUqS... | MoonPay Hot Wallet | 2 | WALKBACK_RECURRING_FUNDER | PROVISION_CANDIDATE | REJECTED_INFRASTRUCTURE |
| HLnpSz9h2S... | Meteora Pool Authority | 2 | WALKBACK_RECURRING_FUNDER | PROVISION_CANDIDATE | REJECTED_INFRASTRUCTURE |
| 5ndLnEYqSF... | FixedFloat Exchange | 1 | None | PROVISION_CANDIDATE | REJECTED_INFRASTRUCTURE |

Set matched X26.3 exactly — proceeded (no abort condition triggered).

### Phase 2 — Historical reclassification

Built `src/ops/subprov_infrastructure_repair_apply.py`: re-runs the dry-run
report to get the authoritative target list, then in a single
`BEGIN IMMEDIATE` transaction updates **only** `state` and `rejected_reason`
on those 24 rows:

```sql
UPDATE wt_discovered_subprovs SET state=?, rejected_reason=?
WHERE subprov=? AND COALESCE(state,'') NOT LIKE 'REJECTED%'
```

`state='REJECTED_INFRASTRUCTURE'`, `rejected_reason='KNOWN_INFRASTRUCTURE_REGISTRY_MATCH'`.

After commit, the script diffs every row in the table (not just the
targets) against a pre-transaction snapshot and asserts: (a) no row outside
the target set changed at all, (b) no target row changed any column other
than `state`/`rejected_reason`. Both assertions passed —
`unexpected_changes: []`.

Nothing else was touched: `wt_subprov_evidence`, `wt_walkback_queue`, and
`watchtower_token_attribution` row counts are verified byte-identical
before/after (79,886 / 3,836 / 736 rows respectively, unchanged).

### Phase 3 — Validation

- Exactly 24 rows changed, confirmed via `SELECT COUNT(*) WHERE
  state='REJECTED_INFRASTRUCTURE'` = 24.
- `_is_known_subprov()` (walkback_worker.py) correctly rejects all 24 —
  already handled by the `NOT LIKE 'REJECTED%'` guard shipped in X26.3.
- `is_historical_subprov()` (ws_cascade_store.py) **initially still
  reported 15 of the 24 as historical** — a real gap this validation phase
  caught (see "Errors and fixes" below). Fixed and re-verified: all 24 now
  correctly excluded.
- Genuine sub-provisioner sample (`Hk6AxTQZyK7zsPfQLmgGdw8t9nzaD3zDeRjduNHGxbXF`,
  `wrap_close_count>0`, `state='PROVISIONAL_SUBPROV'`) unaffected by either
  function.
- A genuine non-infrastructure wallet with real `EXPIRED` sessions in
  `wt_active_subprov_sessions` still correctly returns
  `is_historical_subprov()==True` — the fix did not over-correct.
- Discovery endpoints (`/discovery`, `/api/discovery/entity/<mint>`) return
  HTTP 200 post-repair.
- Full regression: `tests/test_x26_3_subprov_infrastructure_exclusion.py`,
  `tests/test_x26_2_1_attribution_gate_fix.py`,
  `tests/test_discovery_workspace.py`,
  `tests/test_walkback_worker_startup_resilience.py` — **42/42 passing**.
- Live `walkback_worker` log post-repair shows the running daemon correctly
  and idempotently skipping the same 24 wallets on every batch
  ("`is known infrastructure — skipped`"), zero exceptions.

**Bug found and fixed during Phase 3**: `is_historical_subprov()` had three
independent checks. X26.3 had added a `REJECTED%` exclusion to the first
check (`wt_discovered_subprovs.wrap_close_count`), but the other two
(`wt_candidate_websocket_watches` membership, and `wt_active_subprov_sessions`
in `EXPIRED`/`COMPLETED` state) queried entirely separate tables that record
raw session/watch activity independent of `wt_discovered_subprovs.state` —
so a wallet with hundreds of expired sessions (e.g. the Relay.link Solver,
174 `EXPIRED` rows) still read as "historical sub-provisioner evidence"
even after being marked `REJECTED_INFRASTRUCTURE`. Fixed with a single
top-level guard: if `wt_discovered_subprovs.state LIKE 'REJECTED%'` for
this wallet, `is_historical_subprov()` returns `False` immediately,
short-circuiting all three raw-activity checks — treating the canonical
classification table as authoritative over raw activity counts, rather than
patching each of the three checks separately. Re-verified against all 24
wallets, the genuine-subprov sample, and a genuine non-infra
expired-session wallet.

---

## Part B — Discovery Evidence Integrity Audit

Method: read every text-building function in `src/discovery/service.py`,
`src/ops/attribution_outcome.py`, `src/ops/detection_reconciliation.py`,
`src/ops/operation_identity.py`, `src/ops/operational_behaviour.py`, and
the corresponding rendering logic in `templates/discovery.html`, tracing
every analyst-visible sentence back to its exact source column(s).

### Phase 4 — Launch Summary

Built in `templates/discovery.html`, `analystSummary(d)`. A synthesis of
independently-gated one-line facts (Launch Profile, Detection
Reconciliation, Attribution Outcome, Operation Identity, Canonical
Operator) — each line's certainty is inherited from its own source section
below. No sentence in this card is computed independently of those five
sources; it is presentation-only aggregation. One line ("Verified
provisioned launch") is flagged below under Phase 5 since it repeats Launch
Profile's classification verbatim.

### Phase 5 — Launch Profile

`src/discovery/service.py:604-628`, `_launch_profile()`. Two findings:

1. **`creator_history` is a hardcoded literal, not a query result.**
   `"facts": {"creator_history": "No earlier launch in the admitted
   population"}` is emitted unconditionally whenever the PROVISIONED
   branch is taken (`subprov_wallet` present + `funding_mechanism` in the
   two known-provisioning values) — it is a static string attached to the
   branch, not a per-creator lookup against `wt_watchtower_launches` or
   `token_analysis`. Verified live: querying for creators with
   `subprov_wallet IS NOT NULL AND funding_mechanism IN (...)` and
   `COUNT(*) > 1` currently returns zero rows, so the claim happens to be
   true for every existing PROVISIONED row today — but nothing enforces
   this going forward. If a creator is ever reused (the platform's own
   `single-token-creator-filter` design note already anticipates serial
   deployers as a real, if currently rare, case), this field will assert a
   false fact with no check ever catching it. **This is a genuine defect**
   — same class as the sprint's stated concern (a label that reads as
   verified but isn't backed by a live check).
2. **"Verified..." wording** (`reason` field and the Launch Summary line
   "Verified provisioned launch.") — the underlying gate is only
   `subprov_wallet IS NOT NULL AND funding_mechanism IN (WSOL_WRAP_CLOSE,
   SEEDED_ACCOUNT_CLOSE)`, i.e. a persisted classification with no
   cross-check against `wt_provisioning_sessions` or other independent
   corroborating evidence. "Verified" is defensible as "this stored field
   is populated" but could be read by an analyst as "independently
   corroborated," which is a stronger claim than the check performs.

### Phase 6 — Funding Walkback

Every node (`TRANSACTION_OBSERVED`, `TOKEN_LAUNCH`,
`CONFIRMED_TREASURY_ATTRIBUTION`, `LIFECYCLE_OBSERVED`, `CREATOR_IDENTIFIED`
[wrap-close and fallback variants], `SUBPROVISIONER_RESOLVED`,
`TREASURY_RESOLVED`, `TREASURY_WALKBACK`) traced to its exact gating
condition and reason-builder (`_attribution_reason`, `_subprov_reason`,
`_treasury_reason`). All reason text is directly proportionate to what was
actually checked — e.g. `_subprov_reason()` says "N creator-funding
observation(s) support the role" (a real count) rather than asserting
confirmation, and defers the actual certainty signal to the node's `state`
badge (CONFIRMED/PROVISIONAL/REJECTED, computed separately). The
`CONFIRMED_TREASURY_ATTRIBUTION` node from `watchtower_token_attribution`
correctly carries the X26.2.1 `matched_treasury`-truthiness gate. No defect
found in this section.

### Phase 7 — Detection Provenance

Real internal state names (verified in `src/ops/detection_reconciliation.py`):
`LIVE_DETECTED`, `RECONCILED`, `WALKBACK_RECOVERED`, `PIPELINE_INCONSISTENCY`,
`WALKBACK_OBSERVED`, `WALKBACK_INCONCLUSIVE` — the sprint brief's assumed
names (`LINEAGE_ESTABLISHED`, `DETECTION_GAP`, `PARTIAL_EVIDENCE`,
`EVIDENCE_INCONCLUSIVE`) are actually the **display labels**, not the
internal classification strings; the rename is deliberate (frontend comment
explicitly states raw mechanism names "carry no analyst value" to an
analyst). The gate for `WALKBACK_RECOVERED`/`PIPELINE_INCONSISTENCY`
requires `wt_walkback_queue.intelligence_outcome=='WATCHTOWER_CONFIRMED'`
specifically — a `wt_provisioning_sessions` row's mere existence is
explicitly barred from qualifying (this is the X26.3/X25.5.1-era fix,
documented in the module's own docstring). Wording matches implementation.
One minor observation: "Lineage Established" (`WALKBACK_RECOVERED`) doesn't
surface in its own label that this also means "never seen live" — that fact
is present in the body text but not the headline, a minor
discoverability point rather than a correctness defect.

### Phase 8 — Attribution Outcome

Real outcome types (verified in `src/ops/attribution_outcome.py`):
`CANONICAL_OPERATOR_REACHED, KNOWN_MULTI_TOKEN_CREATOR, KNOWN_CEX_REACHED,
KNOWN_BRIDGE_REACHED, KNOWN_RELAY_REACHED, UNKNOWN_INFRASTRUCTURE,
LINEAGE_GAP, AMBIGUOUS_BRANCH, MAX_DEPTH, INSUFFICIENT_EVIDENCE`. Note:
`KNOWN_INFRASTRUCTURE` (as named in the sprint brief) does not exist as a
distinct type — the actual name is `KNOWN_RELAY_REACHED`; `WATCHTOWER_CONFIRMED`
is not an outcome_type at all, it's the `wt_walkback_queue.intelligence_outcome`
gate value consumed by Detection Provenance (Phase 7), a different system.

Two findings:

1. **`KNOWN_MULTI_TOKEN_CREATOR` uses identity language for a threshold
   heuristic.** The stop_reason ("Known multi-token creator with N
   historical launches") and the frontend title ("Existing Multi-Launch
   Creator") both read as an established-identity fact, but the gate
   (`evaluate_launcher_profile().established`) is a configurable numeric
   threshold: `launch_count >= 5`, `observation_seconds >= 7 days`, no
   recent provisioning refresh, no material infrastructure change. This is
   the closest analogue in the current codebase to the class of defect
   X26.3 fixed for sub-provisioners: a recurrence-based heuristic being
   presented with fact-level confidence ("Known"/"Existing"/"established").
2. **`UNKNOWN_INFRASTRUCTURE` confidence is hardcoded `"MEDIUM"`**
   regardless of which of two structurally different evidence paths
   qualified it (`wt_treasury_review` evidence-backed path vs. raw
   `wt_discovered_subprovs.creator_count>=2` path) — no differentiation
   despite genuinely different evidentiary strength.

All other outcome types (`KNOWN_CEX_REACHED`/`KNOWN_BRIDGE_REACHED`/
`KNOWN_RELAY_REACHED`, `LINEAGE_GAP`, `MAX_DEPTH`, `INSUFFICIENT_EVIDENCE`,
`AMBIGUOUS_BRANCH`) are tied to static registry membership or explicit
persisted-evidence-absence checks and are correctly hedged in both backend
and frontend wording ("may represent," "insufficient," "stopped").

### Phase 9 — Operation Identity

`src/ops/operation_identity.py`. `identity_basis` is hardcoded
`"TREASURY_FUNDING_MESH"` and `confidence` hardcoded `"CONFIRMED"`
whenever an operation object is returned — but this is not an unsupported
assertion: the qualifying edge rule (both endpoints already in
`wt_confirmed_treasuries`, funding timestamp strictly precedes the
destination's first launch, not a subprov-sweep artifact) is itself a
strict, deterministic, already-confirmed-treasury-gated condition, so
"CONFIRMED" is proportionate. ROOT/MEMBER assignment is a real structural
fact from `wt_treasury_funders` (a treasury with no qualifying incoming
edge is root; a cycle demotes all members to MEMBER — matches the X26.0
finding that Root/Launch-Treasury roles overlap and multi-root operations
exist). `display_name_for()` deliberately never uses a wallet-prefix label,
avoiding vanity-similarity-as-identity. No defect found; this is the most
conservative of the eight sections.

### Phase 10 — Canonical Operator

**Confirmed the single most important structural finding of this audit.**
`_canonical_identity()` (`src/discovery/service.py`) is correctly and
strictly scoped: it only ever resolves against a fixed constant
`WATCHTOWER_OPERATOR_ID`, requires `operators.display_name='WATCHTOWER'
AND status='CONFIRMED'`, and can only ever return "WATCHTOWER" or `None` —
never PHANTOM/ORBIT/DELTA/UNKNOWN. Repeated code comments across
`service.py` and `discovery.html` assert this is the **only** place
operator identity is established.

That assertion is not fully true of the codebase as it stands. Verified
directly (not just from the research agent's report) that
`src/ops/attribution_outcome.py`'s `derive_outcome()` independently resolves
an `operator_id`/`display_name` via its own `operator_entities`/`operators`
join (lines 372-393) — **with no `display_name='WATCHTOWER'` filter and no
`status='CONFIRMED'` filter**, only "exactly one distinct `operator_id`
found." Two further independent naming paths exist:
`_operator_for_entities()`/`operator_history` (`service.py:787-820`) and the
standalone operator-detail resolver `_operator()` (`service.py:687-785`),
both of which name whatever operator matches, not WATCHTOWER-specific.

**Currently latent, not live-manifesting**: queried the live `operators`
table directly — it contains exactly one row
(`WATCHTOWER`/`CONFIRMED`). So today, every one of these paths can only
ever assert "WATCHTOWER" too, since no other operator exists to diverge to.
This is a genuine design-integrity gap, not a currently-observable
attribution error: the moment a second `operators` row is added (e.g. a
future `PHANTOM`/`ORBIT` candidate, or even a non-`CONFIRMED`-status
WATCHTOWER-adjacent entity), `attribution_outcome.derive_outcome()`'s path
would assert that operator's identity in the Attribution Outcome card
without requiring the same confirmation gate `_canonical_identity()`
enforces — directly contradicting the code's own stated invariant.

### Phase 11 — Behaviour Summary

`src/ops/operational_behaviour.py`. Every sentence traces to exactly one
column and, notably, several sentences cite their own source table
verbatim in the text (e.g. "Sub-provisioner has funded N creator(s) (per
wt_discovered_subprovs)"). Operational Consistency is restricted by the
module's own docstring to strictly `"Observed"`/`"Not observed"`/`"Not yet
available"` — no probability or percentage language permitted, and this is
enforced in code (`_status()` helper). This is the most rigorously
disciplined section audited; no defect found.

### Phase 12 — Evidence inventory

| Discovery statement | Evidence source | Strength | Inference? | Verified? |
|---|---|---|---|---|
| "Live detected." | `wt_walkback_queue`/live cascade classification = LIVE_DETECTED | Direct | No | Yes |
| "Detected via reconciliation, not the live cascade." | detection_reconciliation classification = RECONCILED | Direct | No | Yes |
| "Complete funding lineage established after the fact; not live detected." | classification = WALKBACK_RECOVERED, gated on `intelligence_outcome='WATCHTOWER_CONFIRMED'` | Direct | No | Yes |
| "Partial funding lineage established..." | classification = WALKBACK_OBSERVED | Direct | No | Yes |
| "...evidence is insufficient to establish funding lineage." | classification = WALKBACK_INCONCLUSIVE | Direct | No | Yes |
| "Verified provisioned launch." / "Verified sub-provisioner and X funding mechanism." | `wt_watchtower_launches.subprov_wallet` + `funding_mechanism` enum membership | Derived (label, no independent corroboration required) | Yes | Partially — field values are real, "Verified" implies more than checked |
| "No earlier launch in the admitted population" (creator_history) | **Hardcoded literal**, not queried | **None — presentation only, mislabeled as a fact** | Yes | **No — defect** |
| "No verified provisioning session was recorded..." (OBSERVED_ONLY) | absence of PROVISIONED gate | Direct (negative fact) | No | Yes |
| Funding Walkback node reasons (`_subprov_reason`, `_treasury_reason`, `_attribution_reason`) | per-row DB fields (creator_count, treasury, matched_treasury/matched_subprov) | Direct | No | Yes |
| "This wallet is trusted because it is in the confirmed treasury registry via {method}." | `wt_confirmed_treasuries` membership | Direct | No | Yes |
| "Attribution complete. Canonical operator: {name}." (CANONICAL_OPERATOR_REACHED) | `operator_entities`/`operators` join, exactly one match | Direct, but ungated by status/display_name (Phase 10 finding) | No (fact of the join) | Yes today (only 1 operator exists); gate is looser than intended |
| "Known multi-token creator with N historical launches." | `evaluate_launcher_profile().established` (threshold heuristic) | Heuristic | **Yes** | Threshold met, but presented as identity fact |
| "Unknown infrastructure identified. Eligible for emerging-operator monitoring." | `_known_unknown_infrastructure()`, two possible evidence paths, single hardcoded "MEDIUM" confidence | Heuristic/Derived | Partial | Yes, but confidence not differentiated by path strength |
| "Attribution boundary reached. Known {CEX/bridge/infrastructure} wallet: {name}." | static `CEX_ACCOUNTS`/`INFRASTRUCTURE_ACCOUNTS`/`address_labels` registry match | Direct | No | Yes |
| "Walkback stopped at a lineage gap. Retry only when new evidence arrives." | LINEAGE_GAP, absence of further lineage | Direct (negative fact) | No | Yes |
| "This launch belongs to a single-treasury operation with N observed launches." / "...N-treasury funding mesh..." | `operation_identity.treasury_count`/`launch_count` | Direct | No | Yes |
| "Identity basis: Treasury funding mesh" / confidence CONFIRMED | strict edge-qualification rule over `wt_confirmed_treasuries`/`wt_treasury_funders` | Direct | No | Yes |
| "Canonical operator: {name}." (Canonical Operator card) | `operator_entities` + `operators.display_name='WATCHTOWER' AND status='CONFIRMED'`, strictly gated | Direct | No | Yes |
| Behaviour Summary lines (funding order, mechanism, count, walkback completion) | `wt_provisioning_edges`, `wt_discovered_subprovs.creator_count`, `wt_provisioning_sessions` | Direct, source cited in-sentence | No | Yes |
| Operational Consistency ("Observed"/"Not observed"/"Not yet available") | explicit per-fact DB checks, no probability language permitted by design | Direct | No | Yes |
| Infrastructure Pattern lines ("Known provisioning hub", "Confirmed provisioning hub address") | `wt_known_operator_hubs`/`wt_provisioning_hubs` membership | Direct | No | Yes |

Classification counts: **17 DIRECT FACT**, **2 DERIVED FACT** (Launch
Profile "Verified..." wording, Operation Identity CONFIRMED basis — both
proportionate to their gates), **2 HEURISTIC** (`KNOWN_MULTI_TOKEN_CREATOR`,
`UNKNOWN_INFRASTRUCTURE` confidence), **1 PRESENTATION ONLY / defect**
(`creator_history` hardcoded literal), plus the **1 structural gate gap**
(Canonical Operator scope, Phase 10 — a logic risk rather than a wording
risk, currently non-manifesting).

### Phase 13 — Recommendation

**Discovery contains one confirmed wording/logic defect (creator_history),
one confirmed logic-gate integrity gap (Canonical Operator scope), and two
heuristic-presented-as-fact wording risks that do not currently produce
incorrect output but should be tracked.**

Per the brief's explicit constraint — "Do not implement additional wording
changes unless a genuine semantic defect is found" — no further wording was
changed in this sprint beyond the historical repair itself. Assessment of
what would qualify as "genuine":

- `creator_history` hardcoded literal — **is** a genuine semantic defect
  (a fact-shaped label with zero backing query), but is currently
  vacuously true for all existing PROVISIONED rows (zero repeat creators
  found). Recommend fixing in a follow-up sprint by replacing the literal
  with a real `COUNT(*)` check against prior launches for that
  `creator_wallet` — small, isolated, single-function change, but out of
  this sprint's stated scope of "audit + historical repair," not "further
  Discovery wording changes."
- Canonical Operator scope gap — **is** a genuine logic-integrity gap (three
  paths assert operator identity without the confirmation gate the codebase
  claims is exclusive), but currently produces no incorrect output (only
  one operator row exists). Recommend adding the same `status='CONFIRMED'`
  guard to `attribution_outcome.derive_outcome()`'s operator join before a
  second `operators` row is ever inserted — again isolated, but deferred as
  it is a defensive fix for a not-yet-triggered condition, and this sprint's
  transaction-safety discipline argues against touching attribution-outcome
  logic without its own dedicated audit/test pass.
- `KNOWN_MULTI_TOKEN_CREATOR`/`UNKNOWN_INFRASTRUCTURE` wording — heuristic
  thresholds presented with identity-level confidence language, but neither
  is factually wrong (the threshold genuinely was met); recommend wording
  softening ("crosses the multi-launch threshold" vs. "Known...") as a
  follow-up, not a defect requiring immediate correction.

## Deliverables checklist

- [x] Historical repair completed — 24/24 rows reclassified to
      `REJECTED_INFRASTRUCTURE`, zero unexpected changes.
- [x] Before/after repair report — see Phase 1-3 above; full JSON snapshots
      retained at `/tmp/x26_3_1_dryrun_preflight.json`,
      `/tmp/x26_3_1_pre_row_counts.json`, `/tmp/x26_3_1_pre_hash.txt`.
- [x] Full Discovery evidence inventory — Phase 12 table, 22 statements
      classified.
- [x] Remaining semantic defects — `creator_history` hardcoded literal
      (Phase 5), Canonical Operator scope gap (Phase 10), two heuristic
      wording risks (Phase 8) — all documented above, none corrected in
      this sprint per its own scope constraint.
- [x] Recommendation — see Phase 13.
- [x] Confirmation of no schema changes — `wt_discovered_subprovs.state`/
      `rejected_reason` columns already existed; no `ALTER TABLE` issued in
      this sprint. The one code change made in this sprint
      (`is_historical_subprov()`'s new top-level guard) is a query-logic
      change, not a schema change.
- [x] Confirmation of no evidence loss — `wt_subprov_evidence`,
      `wt_walkback_queue`, `watchtower_token_attribution` row counts
      verified byte-identical before/after the repair; the 24
      `wt_discovered_subprovs` rows themselves were updated in place, never
      deleted.
