# X30.1 — Operation Role Attribution Audit

Investigation only, per the brief. No code changed. Traces every code path in `ws_cascade.py`, `ws_cascade_store.py`, `treasury_bank.py`, and `operation_dashboard_routes.py` that assigns, promotes, or persists an operational role — not the read-only Discovery presentation layer already covered in X30.0.

## 1. Inventory of every role assignment

| # | Role assigned | Source file : function | Triggering evidence | Confidence level | Persistence target |
|---|---|---|---|---|---|
| 1 | **TREASURY** (confirmed) | `treasury_bank.py:327,508` + `operation_dashboard_routes.py:3232,3865` | Analyst-driven confirmation action (dashboard route), backed by a `method`/`provenance` field (e.g. `CONFIRMED_SEED`) — **not** an automatic on-chain trigger | Human-confirmed (`confidence` column, analyst-supplied) | `wt_confirmed_treasuries` |
| 2 | **TREASURY** (in-memory fast-path) | `ws_cascade.py:2649,2677-2679` (`_classify_recipient`) | Wallet already present in `wt_confirmed_treasuries` (loaded via `_confirmed_treasuries(conn)`) | Inherited from #1 — no new evidence, pure cache | `self._wallet_profile` (in-memory only, not persisted) |
| 3 | **CDC** (Capital Distributor Candidate) | `ws_cascade.py:3172-3184` → `ws_cascade_store.py:903-917` (`register_cdc`) | `gain >= CDC_MIN_SOL` (threshold, default 50 SOL) AND recipient not a confirmed treasury AND `wrap_close_count==0` | `OBSERVING` (a workflow state, not a confidence score) | `wt_capital_distributor_candidates` |
| 4 | **SUBPROVIDER** (promotion from CDC) | `ws_cascade.py:3278-3297` (`_handle_cdc_tx`) → `ws_cascade_store.py:999-1081` (`promote_to_subprov`) | The CDC wallet is observed performing a **wrap-close** (`extract_close_destinations(tx)` finds a `closeAccount` whose destination ≠ itself) | `MIN(0.74, 0.20 + evidence_rows*0.08)` — a hardcoded linear formula, capped at 0.74 | `wt_discovered_subprovs` (state `PROVISION_CANDIDATE`→`PROVISIONAL_SUBPROV`), `wt_subprov_evidence` (raw evidence, always written) |
| 5 | **SUBPROVIDER** (direct treasury-as-subprov) | `ws_cascade.py:3189-3218` | A treasury's own outbound tx contains a wrap-close whose destination is neither itself nor another confirmed treasury | Same formula as #4 (same `promote_to_subprov` call, `subprov=treasury`) | Same tables as #4 |
| 6 | **REJECTED_INFRASTRUCTURE** (role veto) | `ws_cascade_store.py:1031-1032,1039,1053-1066` | `is_known_account(subprov)` — a manually-maintained infra registry lookup (`src/utils/infra_mapping.py`) | Locked — state can never advance past this, confidence never raised | `wt_discovered_subprovs.state` |
| 7 | **BUY_SWARM_PROVISIONER** (role reclassification, not promotion) | `ws_cascade.py:2659-2665,2686-2691` (`_classify_recipient`) | `buy_swarm_ratio > 0.7` AND `n_obs >= 10` AND `creator_count < 5` — a statistical behavior threshold over the subprov's own prior fan-out history | Threshold-based, no numeric confidence stored | Read-only reclassification for routing logic; not itself a new persisted role row (reads existing `wt_discovered_subprovs` fields) |
| 8 | **CONTINUING_OPERATION** / **SUBPROV_REACTIVATED** | `ws_cascade.py:2712-2742` (`_classify_known_subprov`) | Dormancy-gap timing (`now - last_operational_activity < _DORMANCY_THRESHOLD_S`) | Binary timing rule, no confidence score | Not persisted as a separate role — used to enrich `meta`/logging and (elsewhere) session records |
| 9 | **HISTORICAL_SUBPROV_DISCOVERED** | `ws_cascade.py:2668-2669,2706-2708` | `store.is_historical_subprov(conn, recipient)` — a lookup against pre-WATCHTOWER-era evidence | Passed through to `_classify_known_subprov`, same as #8 | Same tables as #4/#8 |
| 10 | **NON_PROVISIONING_RECIPIENT** | `ws_cascade.py:2683-2685` | `wt_discovered_subprovs.subprov_type == 'NON_PROVISIONING_RECIPIENT'` — a prior manual/derived classification read back | Inherited, not re-derived here | Cache only (`self._wallet_profile`) |
| 11 | **CREATOR** | `ws_cascade.py:2696-2704` (`_classify_recipient`, cache-miss path) + `ws_cascade_store.py:2190-2237` (`record_launch`, the authoritative write) | For the cache-miss classifier: `token_analysis.pf_ws_creator` lookup. For the authoritative record: the pump.fun program's own `create` instruction naming this wallet, matched against a `closeAccount` destination (`creator_extraction_method`) | `confidence` param, default `"STRICT"` — passed through, not computed by a formula | `wt_watchtower_launches` (state `FIRED_CREATE`) |
| 12 | **Candidate wallet** (pre-Creator observation state) | `ws_cascade_store.py:1792` (`open_candidate_watch`) | A wrap-close's `closeAccount` destination, recorded the instant the wrap-close transaction is seen — before any CREATE is confirmed | N/A — a workflow state (`state`, `close_reason`, `expires_at`), not a confidence-scored role | `wt_candidate_websocket_watches` |

## 2. Evidence vs. interpretation — direct assignments highlighted

Per the brief's two-pattern test (`Observed evidence → Role interpretation` vs. `Observed evidence → Role assigned directly`):

**Direct assignments (no intermediate interpretation step) — the ones to flag:**
- **#4/#5, wrap-close → Subprovider.** `promote_to_subprov` does not compute an intermediate "this looks like provisioning behavior" score before assigning the role — observing one wrap-close transaction directly writes `PROVISIONAL_SUBPROV` (gated only by the infra-registry veto, #6). This is the single clearest direct-assignment case in the pipeline, and it is the one X30.0 already flagged as the mechanism→role conflation.
- **#3, threshold → CDC.** `gain >= CDC_MIN_SOL` directly creates a CDC record; there is no interpretive layer between "we saw a ≥50 SOL transfer" and "this wallet is now a Capital Distributor Candidate."
- **#11, `create` instruction → Creator.** Also direct, but appropriately so — the pump.fun program's `create` instruction naming a wallet is about as close to ground truth as on-chain evidence gets for this specific role. This is a direct assignment that is *correct* to be direct, unlike #3/#4 which are direct assignments over a threshold/pattern-match that could, in principle, have false positives (and does, per #6's infra-registry carve-out proving false positives are a known, real occurrence for this exact rule).

**Evidence → interpretation → role (the more cautious pattern), for comparison:**
- **#7, BUY_SWARM_PROVISIONER.** Requires accumulated statistical evidence (`buy_swarm_ratio`, `n_obs >= 10`, `creator_count < 5`) — a genuine interpretive threshold over a history of observations, not a single transaction.
- **#8, CONTINUING_OPERATION vs. SUBPROV_REACTIVATED.** An explicit interpretation step over a timing gap, with its own named function (`_classify_known_subprov`) separate from the raw evidence lookup.
- **#6, REJECTED_INFRASTRUCTURE.** A genuine interpretation layer sitting *between* the raw wrap-close evidence and the final state — this is architecturally the right shape (evidence always recorded in `wt_subprov_evidence` regardless; role advancement gated by a separate check).

## 3. Mechanism dependence — does mechanism merely contribute evidence, or does it determine the role?

| Mechanism | Role-determining, or evidence-only? | Where |
|---|---|---|
| `WSOL_WRAP_CLOSE` | **Role-determining.** Observing this specific instruction pattern is the sole and sufficient trigger for CDC→Subprovider promotion (#4) and for treasury-as-subprov detection (#5). No other corroborating signal is required. | `ws_cascade.py:3290-3297`, `ws_cascade_store.py:999-1081` |
| `SEEDED_ACCOUNT_CLOSE` | **Role-determining, identically to WSOL_WRAP_CLOSE.** `promote_to_subprov`'s own docstring calls this "Mechanism B" and routes it through the exact same promotion call — `is_mech_b` only affects which counter column increments (`seeded_account_count` vs `wrap_close_count`), not whether promotion happens. | `ws_cascade_store.py:1005,1036` |
| `PLAIN_TRANSFER` / `PLAIN_XFER` | **Evidence-only, and in fact insufficient on its own** — confirmed directly in X29.8: `wt_provisioning_edges` has no edge type that can represent a plain-transfer branch, "0% by construction." A wallet receiving a large plain transfer can become a CDC (#3, mechanism-agnostic threshold) but can **never** be promoted to Subprovider through plain-transfer evidence alone — only a wrap-close-shaped transaction can trigger `promote_to_subprov`. This means an operation family whose intermediate wallets move capital exclusively via plain transfers would never produce a single Subprovider role assignment, no matter how much genuine intermediary activity occurred. | Confirmed via `promote_to_subprov`'s call sites — both call sites (#4, #5) require `extract_close_destinations` to find a `closeAccount`, which by definition excludes plain transfers. |

**Direct answer to the brief's example question**: today, for two of the three known mechanisms, the mechanism *does* directly determine the role (not merely contribute evidence toward it) — this is not a subtle inference, it is the literal branch condition in `promote_to_subprov`'s two call sites. `PLAIN_TRANSFER` is the mechanism-agnostic exception, but by exclusion rather than by design — it simply cannot reach the promotion function at all, which is a stronger and more concerning form of coupling than "contributes weighted evidence": an entire mechanism class currently has **zero path** to producing a Subprovider role.

## 4. Family dependence — would the same logic hold for hypothetical operations A/B/C?

- **Example A (Treasury → Creator direct, no subprovider).** Unaffected by any of the promotion machinery above — this path never triggers `promote_to_subprov` at all, and `record_launch` (#11) writes `treasury_wallet` with `subprov_wallet=NULL` directly. **Fully valid, no family-specific rule required.**
- **Example B (Treasury → Long-lived distributor → Creator).** Valid *only if* the distributor happens to move capital via wrap-close or seeded-account-close. If the long-lived distributor uses plain transfers (a materially plausible design for a distributor that doesn't need WSOL-wrap machinery at all, since it isn't disposable/single-use the way a WATCHTOWER wrap wallet is), **it would never be promoted to Subprovider, ever** — it would sit as a CDC indefinitely (subject to `CDC_INACTIVITY_TTL_SEC` eventually marking it `INACTIVE`, per `expire_inactive_cdcs`) or, if below `CDC_MIN_SOL`, never even be observed as a CDC. This is the single most concrete case of the mechanism-determines-role coupling actually breaking a plausible alternate operation family.
- **Example C (Treasury → Rotating intermediaries → Creator).** Independently of the mechanism question, each rotation would be evaluated as its own fresh wallet by `_classify_recipient` — there is no state anywhere in this pipeline that links successive rotation-wallets into one collective role (confirmed already in X30.0's generalisation test for the read-only lineage layer; the same gap exists here, one layer earlier, in the assignment pipeline itself). If each rotation *does* wrap-close, each independently earns its own `PROVISIONAL_SUBPROV` row with `confidence` capped by its own evidence count (starting back at 0.20 each time) — meaning a genuinely well-established rotating-distributor operation would perpetually present as a series of low-confidence, freshly-discovered Subprovider records rather than one accumulating one.

## 5. Evidence-confidence classification

| Assignment | Classification | Basis |
|---|---|---|
| #1 Treasury confirmation | **Operation-specific rule** (in the sense of being an analyst/process decision, not an automated inference) — but the underlying *evidence standard* behind it (out of scope of this audit; the confirmation criteria live in `treasury_bank.py`/dashboard workflow, not the detection runtime) | `treasury_bank.py` |
| #3 CDC registration | **WATCHTOWER heuristic** — a single tunable threshold (`CDC_MIN_SOL`) with no corroborating signal | `ws_cascade.py:3172` |
| #4/#5 Subprovider promotion | **WATCHTOWER heuristic**, not strong inference — one wrap-close observation is sufficient; the confidence formula (`0.20 + n*0.08`, capped 0.74) is a hardcoded linear function with no stated derivation or validation, and the cap (0.74) notably never reaches a "confirmed" tier under this formula alone | `ws_cascade_store.py:1077-1078` |
| #6 REJECTED_INFRASTRUCTURE veto | **Direct evidence** — a manually-maintained, explicit registry lookup (`is_known_account`), the strongest-grounded rule in this entire inventory precisely because it is a hand-curated exception list, not a statistical inference | `src/utils/infra_mapping.py` |
| #7 BUY_SWARM_PROVISIONER | **Strong inference** — requires ≥10 observations and a >70% ratio before reclassifying, a genuinely more conservative statistical bar than #3/#4 | `ws_cascade.py:2664` |
| #8 CONTINUING_OPERATION / SUBPROV_REACTIVATED | **WATCHTOWER heuristic** — `_DORMANCY_THRESHOLD_S` is a single tunable constant with no per-operation-family basis | `ws_cascade.py:2740-2742` |
| #11 Creator (via `create` instruction) | **Direct evidence** — the pump.fun program's own instruction data naming the creator, the second-strongest-grounded rule in the inventory | `ws_cascade_store.py:2190-2215`, `creator_extraction_method` |
| #12 Candidate wallet (wrap-close destination) | **Direct evidence** for "this wallet received wrap-close proceeds" but **WATCHTOWER heuristic** for what that implies operationally (that it will become a Creator) — the two are conflated in naming ("candidate wallet") even though only the first half is directly evidenced | `ws_cascade_store.py:1792` |

## 6. Generic role engine — evidence / interpreter / role, or embedded?

The architecture is **not** currently a clean three-stage pipeline. It is closer to:

```
Raw on-chain transaction
        │
        ▼
_classify_recipient() / _handle_cdc_tx()  ← WATCHTOWER-specific interpreter,
        │                                    hardcoded thresholds and mechanism
        │                                    checks live INLINE in this function,
        │                                    not in a separate pluggable module
        ▼
Role written directly to wt_* tables
        (wt_discovered_subprovs, wt_capital_distributor_candidates,
         wt_watchtower_launches, wt_candidate_websocket_watches)
        │
        ▼
Discovery reads the SAME tables (operational_lineage.py, funding_topology.py)
```

There genuinely is a separation between "evidence recording" and "role writing" in one place — `promote_to_subprov` always writes `wt_subprov_evidence` first, unconditionally, before deciding whether to advance `wt_discovered_subprovs.state` (the infra-registry veto, #6, operates entirely on the second step, never touching the first). This is the one clean evidence/interpretation boundary in the whole pipeline, and it is exactly the pattern the brief's success criteria describes as desirable. But it is the exception, not the rule: `_classify_recipient`'s CDC threshold (#3), the wrap-close-triggers-promotion rule (#4/#5), and the dormancy-gap interpretation (#8) are all **inline within the single monolithic `WsCascade` class in `ws_cascade.py`**, using module-level constants (`CDC_MIN_SOL`, `_DORMANCY_THRESHOLD_S`, the `0.20+n*0.08` formula) that are not namespaced, versioned, or swappable per operation family. There is no interpreter abstraction (e.g. no `AttributionRule` base class, no per-family config object, no registry of pluggable classifiers) — a second operation family's attribution logic would today have to be added as new `if` branches inside these same functions, or as parallel functions manually wired into the same call sites.

## 7. Deliverables

**Complete inventory**: 12 distinct role/state assignments enumerated above (§1), spanning `treasury_bank.py` (1), `ws_cascade.py`/`ws_cascade_store.py` (10), and one veto rule sourced from `src/utils/infra_mapping.py` (1).

**Family-specific assignments**: #3 (CDC threshold), #4/#5 (wrap-close→Subprovider promotion, and its confidence formula), #7 (buy-swarm ratio thresholds), #8 (dormancy-gap constant) — all tuned specifically around WATCHTOWER's observed wrap-close-burst behavior pattern (the ~7-second funding burst and single-use wrap wallets X29.7.1/X29.8/X29.11 already characterized).

**Operation-neutral assignments**: #1 (treasury confirmation is a human-driven process, not shape-dependent), #6 (the infrastructure veto — a manually-maintained exception list generalizes to any operation family), #11 (Creator-via-`create`-instruction — grounded in the pump.fun program itself, not in WATCHTOWER's funding pattern; would hold for *any* WATCHTOWER-adjacent operation that also creates pump.fun tokens, though it would not generalize to a non-token-creating operation family, consistent with X30.0's finding that "Creator" itself is launch-specific vocabulary).

**Where mechanism and role are directly coupled**: `ws_cascade_store.py:999-1081` (`promote_to_subprov`), triggered from two call sites in `ws_cascade.py` (3189-3218, 3278-3297) — this is the single, concrete, load-bearing coupling point in the entire runtime. It is the same site X30.0 identified from the read-only side; this audit confirms it is not merely a labeling issue in Discovery but the actual write path where the conflation originates.

**Pluggable or embedded?** **Embedded.** With the single exception of the evidence-recording/state-advancement split inside `promote_to_subprov` itself, every role-assignment rule in this inventory is inline logic inside `ws_cascade.py`'s monolithic class or its companion `ws_cascade_store.py` module, using unnamespaced module-level constants. There is no attribution-rule abstraction layer today.

**Smallest architectural boundary for future operation families**: the natural seam is `promote_to_subprov`'s existing two-step shape (evidence write, unconditionally → gated interpretation → state write), generalized into: (1) a mechanism-agnostic evidence table (already `wt_subprov_evidence`, which is honestly mechanism-tagged and never suppressed — this part needs no change), (2) a swappable **per-operation-family interpreter function** taking that evidence and returning a role decision — today this is the hardcoded body of `promote_to_subprov` plus the inline thresholds in `_classify_recipient`/`_handle_cdc_tx`; extracting these into named, family-scoped functions (e.g. `watchtower_attribution.py` alongside a future `other_family_attribution.py`, both implementing the same "evidence in, role decision out" signature) would let a new family supply its own rules — including recognizing `PLAIN_TRANSFER`-only intermediaries as legitimate distributors, and recognizing a rotating-intermediary pool as one collective role — without touching Discovery's read-only modules (`operational_lineage.py`, `funding_topology.py`, `funding_mechanism.py`, `operation_identity.py`) at all, since those already read the same generic `wt_provisioning_edges`/`wt_watchtower_launches` shape regardless of which interpreter produced the row.

## Success-criteria answer

The platform today sits **between** "Evidence Collection → Operation-Specific Attribution → Operation-Agnostic Discovery" and "attribution embedded in the core runtime" — closer to the latter than the former, but with one genuine, already-existing seam (`promote_to_subprov`'s evidence/state split) that shows the right shape. WATCHTOWER's attribution rules (the CDC threshold, the wrap-close-triggers-promotion rule, the confidence formula, the dormancy-gap constant) are still embedded directly inside the shared detection runtime (`ws_cascade.py`/`ws_cascade_store.py`) rather than isolated behind a swappable interpretation boundary — Discovery itself (per X30.0) is in noticeably better shape than the attribution pipeline that feeds it. Adding a second operation family today would require editing these shared functions in place, not registering a new interpreter alongside them.
