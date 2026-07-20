# X26.7 — Discovery Evidence Presentation Refresh

Status: Audited (Phases 1-7), minimal implementation shipped (Phase 8), tested
and live-verified (Phase 9). No detection, attribution, operation identity,
walkback, or schema logic changed — this sprint only changes what evidence
already-persisted rows produce on the presentation layer.

**Headline finding**: the single biggest "information vacuum" was not a
missing feature, it was a **coverage gap in timeline-node construction**.
`DiscoveryService._entity()` only ever built `TOKEN_LAUNCH`/
`CREATOR_IDENTIFIED` nodes from `wt_watchtower_launches`,
`wt_wrap_close_candidates`, `migrated_tokens`, or `wt_token_lifecycle` —
never from `wt_walkback_queue`, even when that was the *only* record of the
launch that existed. Measured live: **every one of 590 sampled
infrastructure-boundary attribution outcomes** (`KNOWN_RELAY_REACHED`/
`KNOWN_CEX_REACHED`/`KNOWN_BRIDGE_REACHED`/`UNKNOWN_INFRASTRUCTURE`) had a
genuine `wt_walkback_queue` row with a real `creator`, yet the timeline was
empty for all of them — sending the page into its whole-page "No
historical discovery evidence is available" fallback and silently
dropping Launch Profile, Funding Walkback, and Evidence Groups, even
though `attribution_outcome` and `launch_profile` were both genuinely
populated and available.

---

## Phase 1 — Evidence inventory (card matrix)

| Card | Question answered | Backend field(s) | Backend function | Duplicates? |
|---|---|---|---|---|
| Identity Header | What is the subject and its resolved state? | `d.summary`, `d.subject`, `d.state` | `_summary()` (service.py:924-934), overwritten by canonical_identity/attribution_outcome text | Yes — `d.summary` is frequently a one-line compression of `outcome()`'s or `canonicalIdentity()`'s own fields |
| Discovery Result (`outcome()`) | Where did attribution terminate (typed outcome)? | `d.attribution_outcome.*` | raw `wt_attribution_outcomes` row fetch (service.py:263-269); written by `derive_outcome()` | Yes — same underlying fields as Identity Header's summary |
| Treasury Review Lead | Does this structurally resemble a known operator's infra, without claiming ownership? | `d.treasury_expansion.*` | `TreasuryExpansionResolver.evaluate()` (service.py:526-534) | No — explicitly scoped additive |
| Emerging Candidate | Is this an active not-yet-canonical monitoring candidate? | `d.emerging_candidate.*` | `EmergingOperatorService.get()` (service.py:508-518) | No |
| Recorded Relationship Chain (`visualChain`) | What's the deduped role chain (token→creator→subprov→treasury→operator)? | `d.timeline` (deduped) + synthetic outcome node | `_dedupe_sort()` (service.py:831) | Yes — overlaps with Raw Provenance and Attribution Chain chips |
| Launch Profile | How was the launch structurally provisioned? | `d.launch_profile.*` | `_launch_profile()` (service.py:612-639) | No |
| Funding Walkback | Ordered hop-by-hop chain and where it stopped | `d.walkback.*`, `d.canonical_identity`, `d.attribution_outcome.terminal_entity` | `_hop()` calls + `_stop_reason()` (service.py:908-921) | Yes — endpoint re-renders attribution_outcome/canonical_identity facts already shown elsewhere |
| Detection Provenance | How/when was this actually detected? | `d.detection_reconciliation.*` | `classify_walkback_confirmed_launches()` | No |
| Operation Identity | Does this belong to a resolved treasury-funding-mesh operation? | `d.operation_identity.*` | `operation_for_treasury()` | No |
| Canonical Operator / Identity | Is this treasury confirmed WATCHTOWER? | `d.canonical_identity.*` | `_canonical_identity()` (service.py:641-685) | Yes — the card's own two cells ("Canonical Operator"/"Identity") repeat `operator_name` twice within itself, and again duplicates Identity Header's summary when set |
| Operational Behaviour | How did this behave operationally? | `d.operational_behaviour.*` | `OperationalBehaviourService.build()` | No (see X26.6's separate audit) |
| Attribution Chain (chips) | Compact kind/confidence list | `d.timeline[].{kind,confidence}` | same `timeline` | Yes — condensed re-listing of Raw Provenance |
| Evidence Groups | What evidence items bucket into IDENTITY/SUPPORTING/CONTEXT/CONTRADICTIONS? | `d.evidence_groups.*`, `d.cross_operation` | `_groups()` (service.py:850), `_operator_for_entities()` | No |
| Promotion Lineage | History of operator-record association over time | `d.operator_history[]` | `_operator_for_entities()` | No |
| Raw Provenance | Full unsummarized evidence-node list | `d.timeline` (full) | same `timeline` | Yes — full detail of what #5/#12 show condensed |

## Phase 2 — Empty-state audit

Every card's precise empty condition was traced (full detail in the
research trace); the consequential finding is the **whole-page** empty
branch, not any single card:

```js
if(!d.timeline||!d.timeline.length){
  $('dw-content').innerHTML = identityHeader+summaryCard+infra+leads
    +detectionReconciliation(...)+operationIdentity(...)+canonicalIdentity(...)
    +'<div class="dw-empty">...No historical discovery evidence is available.
       The workspace does not run a live walkback.</div>';
  return;
}
```
(`templates/discovery.html:622`, prior to this sprint's fix)

This branch is reached whenever `d.timeline` is empty — which, measured
live, was **every** infrastructure-boundary case sampled (590/590), because
`_entity()` never read `wt_walkback_queue.creator` into a timeline node.
`infra=outcome(d.attribution_outcome)` **is** still concatenated in this
branch, so the Discovery Result card did show — but `launch_profile`,
`walkback` (full hop chain), Evidence Groups, and Raw Provenance all
vanished, despite `launch_profile` being fully computed and genuinely
`OBSERVED_ONLY` for every one of these mints.

Other genuinely-empty cases audited (rejected sub-provisioner, no treasury,
no operator) were already correctly handled by earlier sprints (X26.6.1) or
are legitimate absences (no operator really is unconfirmed) — the
walkback-queue gap was the one case where **real, persisted evidence was
being silently withheld**, not a case of "nothing exists to show."

## Phase 3 — Raw Provenance review

**Raw Provenance does not have its own independent empty-state** — contrary
to how the brief's example framed it, the exact string "No historical
discovery evidence is available" is not scoped to the Raw Provenance
`<details>` block at all; it is the **entire bottom-of-page fallback**
(line 622), replacing `flow`+`wb`+`provenance`+`operationCard`+
`operatorIdentity`+`opBehaviour`+`attribution`+`evidence`+`lineage`+`raw`
as one unit. So the question "is this text factually correct for launches
that possess creator/funding-edge/infrastructure-match/walkback evidence"
resolves to: **it was factually incorrect** — those launches did possess
exactly that evidence (in `wt_walkback_queue`, in `attribution_outcome`),
it just wasn't being surfaced into `d.timeline` at all.

**Resolution taken**: rather than replace the placeholder text (which
would have been treating the symptom), Phase 8 closes the actual gap —
once `wt_walkback_queue` evidence is turned into real timeline nodes, the
early-return branch is no longer reached for these mints, and the
placeholder text becomes accurate again (it only fires when `d.timeline`
is genuinely empty, which now correctly means no persisted record of the
launch exists at all under any of the four+ tables `_entity()` checks).

## Phase 4 — Historical Evidence model

Given Phase 3's finding, a separate "Historical Discovery Evidence" /
"Historical Attribution Evidence" section is **not needed as a new
concept** — the existing Raw Provenance section already is exactly that
(a full list of persisted evidence nodes), and its previous "empty" reading
was a data-coverage bug, not a semantic-model gap. The fix restores Raw
Provenance's own contract ("list every persisted evidence node") by
ensuring `wt_walkback_queue`-only launches actually produce nodes, rather
than introducing a parallel section. No new section was added — this
directly follows the brief's own instruction not to invent evidence or
introduce new concepts, and the audit found no genuine need for one.

## Phase 5 — Identity vs. Discovery Result duplication audit

Confirmed a real overlap, but **not implemented as a consolidation** in
this sprint (per Phase 8's "do not implement changes beyond what the
audit justifies," and per the explicit instruction not to redesign the
page): `identityHeader`'s `d.summary` is, in the common case, literally
overwritten to equal `attribution_outcome.stop_reason` (service.py:497-499)
or `"{operator_name} · Confirmed Treasury · Confidence {confidence}"`
(service.py:492-496) — the same fields `outcome()`/`canonicalIdentity()`
render independently. They are not answering genuinely independent
questions in practice, despite the code's own comment claiming they
should. This is flagged as a real, documented duplication for a future
consolidation decision, not fixed here — changing which card "owns" this
text is a design call, not a bug with one obviously correct fix, and the
brief's Phase 5 explicitly says "do not implement unless the overlap is
genuine" without mandating a specific resolution.

## Phase 6 — Positive evidence audit

Directly addressed by Phase 8's implementation: instead of an
Axiom-funded (or any CEX/relay-funded) launch showing nothing but the
Discovery Result card, the platform's own persisted `wt_walkback_queue`
row now surfaces as real positive evidence:
- `creator` → a `CREATOR_IDENTIFIED` node ("identified by the recorded
  walkback funding evidence")
- `funding_mechanism`/`funder_wallet`/`funder_sig` → evidence fields on
  both the `TOKEN_LAUNCH` and `CREATOR_IDENTIFIED` nodes
- This is enough for the page to also render Launch Profile ("Funding was
  reconstructed retrospectively...") and the full Funding Walkback hop
  chain, ending at the already-correct "Infrastructure Boundary" endpoint.

Nothing was invented — every field written is one already persisted in
`wt_walkback_queue`, mirroring the exact pattern the wrap-close and launch
branches already use for their own tables.

## Phase 7 — Card ordering

Reviewed against the brief's suggested flow (Launch Summary → Discovery
Conclusion → Identity Boundary → Launch Profile → Detection Provenance →
Funding Walkback → Historical Evidence → Technical Details). The current
order (`identityHeader+summaryCard+infra+leads+flow+wb+provenance+
operationCard+operatorIdentity+opBehaviour+attribution+evidence+lineage+raw`)
already substantially matches this flow — Identity/Summary first,
Discovery Result (`infra`) second, Funding Walkback (`wb`, which contains
Launch Profile) before Detection Provenance... one minor deviation:
Detection Provenance (`provenance`) currently renders *after* Funding
Walkback (`wb`), whereas the brief's suggested order puts it before. This
was **not reordered** in this sprint: the existing order is the product of
a deliberate X24.8/X25.3 investigation-flow decision (documented in the
code's own comment at discovery.html:610-617), and reordering two
already-shipped, independently-audited cards is a page redesign decision
outside "presentation of evidence already available" — flagged as a minor
ordering observation for a future decision, not acted on here.

## Phase 8 — Minimal implementation

Two changes, both additive, both using only already-persisted fields:

**1. `src/discovery/service.py` — walkback-queue-derived timeline nodes.**
- Declared `walk` at the top-level scope (previously only assigned inside
  `if subject_type == "token":`, which would have raised `NameError` if
  referenced from the shared creator-resolution block below it — a latent
  bug this sprint's addition exposed and fixed as a side effect).
- Added `(walk or {}).get("creator")` as the last fallback in the `creator`
  resolution chain (previously `walk` was read for `subprov`/`treasury`
  fallback but never for `creator`, even though the column exists).
- Added a `TOKEN_LAUNCH`-equivalent node when `walk and walk.get("creator")`
  but no `launch`/`migration`/`lifecycle` row exists — using
  `funding_mechanism`, `funder_wallet`, `funder_sig`, `completed_at`/
  `enqueued_at`, exactly mirroring the existing pattern for the other three
  source tables.
- Added a `CREATOR_IDENTIFIED` node (in the shared `wrap`/`launch`/`walk`
  `elif` chain used regardless of subject type) for the same case, using
  `funder_block_time`/`completed_at`, `funding_mechanism`, `funder_wallet`,
  `funder_sig` — mirroring the existing wrap-close and launch branches.

**2. `templates/discovery.html` — "infrastructure infrastructure" wording.**
`terminal_entity_type` values observed live include `INFRASTRUCTURE`,
`RELAY`, `AUTOMATION`, `CEX`, `CUSTODY` — the analyst summary line
unconditionally appended the literal word "infrastructure" after the
lowercased noun, producing "Attribution terminated at infrastructure
infrastructure." specifically for the `INFRASTRUCTURE` value. Fixed to
skip the redundant suffix when the noun itself is already "infrastructure".

No schema change, no detection/attribution/walkback/operation-identity
logic touched — both changes are pure read-path additions in the
presentation layer, using fields the database already stores.

## Phase 9 — Tests

`tests/test_x26_7_evidence_presentation_refresh.py` — 8 tests, all passing:
- `test_rejected_infrastructure_launch_surfaces_walkback_evidence_not_empty`
  — the exact Axiom-class scenario: walkback evidence now surfaces, but
  X26.6.1's `SUBPROVISIONER_RESOLVED` suppression still holds.
- `test_walkback_only_launch_no_longer_empty` — a non-rejected,
  walkback-only launch also benefits.
- `test_provisioned_launch_unaffected` — a genuine wrap-close+launch row
  still uses its original detector/reason, proving the fallback never
  overrides real evidence.
- `test_treasury_confirmed_launch_unaffected` — treasury resolution
  untouched.
- `test_no_operator_leakage_from_walkback_only_node` — the new nodes never
  mention an operator; `canonical_identity` stays `None` as expected.
- `test_no_treasury_no_operator_case` — creator still surfaces even with
  minimal walkback data (no funder/mechanism fields at all).
- `test_no_database_mutation` — SHA-256 before/after.
- `test_infrastructure_infrastructure_wording_fixed` — asserts the
  conditional-suffix fix is present in the template source.

**Full regression**: 73/74 passing across
`test_x26_7_evidence_presentation_refresh.py`,
`test_discovery_workspace.py`, `test_x26_2_1_attribution_gate_fix.py`,
`test_x26_3_subprov_infrastructure_exclusion.py`,
`test_x26_5_1_attribution_health_window_integrity.py`,
`test_x26_6_1_reject_state_aware_provenance.py`,
`test_ops_x20_6_discovery_prioritisation.py`. The single failure
(`test_x26_3_subprov_infrastructure_exclusion.py::test_dry_run_report_performs_no_mutation`)
is a pre-existing flaky test that hashes the **live, actively-written**
`database/wt_ops_v2.db` directly rather than an isolated fixture —
reproduced as flaky in isolation too (fails/passes depending on whether
the live `walkback_worker`/`ws_cascade` daemons wrote to the DB during the
test), unrelated to this sprint's changes.

## Live verification

- `7hZSYroo8CkdZ1xJDKCaxvxLYtD9JeEWUjUxmi8Qpump` (`KNOWN_CEX_REACHED`,
  previously `timeline_len=0`) now returns `timeline_len=2`
  (`CREATOR_IDENTIFIED` + `TOKEN_LAUNCH`, both walkback-queue-derived);
  `launch_profile.classification=OBSERVED_ONLY`; `canonical_identity=None`
  — all correctly populated, page will render the normal (non-empty)
  branch.
- `BWkBeRcEKtBMSy3ejaQ9nbZu5NvAS2zczmGexkM5pump` (Axiom-funded,
  `KNOWN_RELAY_REACHED`/`terminal_entity_type=AUTOMATION`) — same result,
  confirming the fix generalizes across infrastructure sub-types.
- `/discovery` and `/api/discovery/entity/<mint>` both return HTTP 200.
- `git status --porcelain -- database/*.db` empty — no DB mutation from
  this sprint's work.

## Confirmation of unchanged systems

No file under `src/core/` (detection), `src/ops/attribution_outcome.py`,
`src/ops/operation_identity.py`, `src/core/walkback_worker.py`, or any
database schema was modified. The two files touched
(`src/discovery/service.py`, `templates/discovery.html`) are both
presentation-layer read paths; `wt_walkback_queue` itself is only read,
never written, by this change.
