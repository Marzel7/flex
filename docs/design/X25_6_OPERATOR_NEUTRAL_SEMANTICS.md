# X25.6 — Operator-Neutral Discovery Semantics

Status: Implemented. Semantic/wording refinement across
`templates/discovery.html` and three targeted `src/discovery/service.py`
strings. No attribution logic, operation-identity logic, launch-profile
logic, or detection logic changed — confirmed by diff and the full
regression suite (see bottom).

---

## Phase 1 — Complete inventory of every WATCHTOWER reference

| # | File:Line | Context | Category | Verdict |
|---|---|---|---|---|
| 1 | `discovery.html:545-546` (pre-fix) | `WALKBACK_RECOVERED`/`PIPELINE_INCONSISTENCY` explain strings: "This launch is part of a WATCHTOWER-tracked operation..." | **Implementation leakage** — asserted a specific operator name for a fact that is actually operator-agnostic (confirmed operation lineage) | **Rewritten** (Phase 3) |
| 2 | `discovery.html:547` (pre-fix) | `WALKBACK_OBSERVED` explain string: "...the walk did not confirm a WATCHTOWER operation" | **Implementation leakage** | **Rewritten** |
| 3 | `discovery.html:573` (pre-fix) | Analyst Summary sentence: "...no WATCHTOWER operation was confirmed" | **Implementation leakage** | **Rewritten** |
| 4 | `discovery.html:544` (pre-fix) | `RECONCILED` explain string: "A wt_watchtower_launches record exists" | **Historical wording** — internal table name leaking into analyst-facing prose | **Rewritten** to "A platform launch record exists" |
| 5 | `discovery.html:230` (`ROLE_ICON`/`intelligenceEvent` stream key `watchtower`) | Internal JS variable/stream key naming | **Historical wording, code hygiene** | **Left unchanged** — X24.8 already ruled stream-key renaming out of scope ("code hygiene, not a semantic defect"); this key drives internal routing, never rendered as text to the analyst |
| 6 | `discovery.html:269` (comment) | Explains why the walkback terminal node reflects the resolved operator name | **Fact** (describes the legitimate gated behavior) | Unchanged — accurate |
| 7 | `service.py:292-294` (pre-fix) | Timeline node `detector="WATCHTOWER attribution"`, `rule="Existing WATCHTOWER attribution result"` | **Implementation leakage** — rendered even when `a_state` is `PROVISIONAL` (no confirmation at all) | **Rewritten** (Phase 4) |
| 8 | `service.py:424-428` (pre-fix) | Timeline node `detector="WATCHTOWER launch attribution"`, reason "...confirmed WATCHTOWER treasury" | **Implementation leakage** — "confirmed" here means confirmed *treasury* (a platform detection-mechanism fact), not confirmed *operator* | **Rewritten** |
| 9 | `service.py:856-858` (pre-fix) | `_attribution_reason()`: "matched a known WATCHTOWER treasury/sub-provisioner" | **Implementation leakage** — same conflation as #8 | **Rewritten** |
| 10 | `service.py:633-663` (`_canonical_identity`) | SQL gate: `display_name = 'WATCHTOWER' AND status = 'CONFIRMED'`, joined against `WATCHTOWER_OPERATOR_ID` | **Fact** — the one legitimate, independently-proven operator assertion | **Unchanged** — this is exactly the gate Phase 5 requires |
| 11 | `service.py:1003-1040` (`recent()` legacy fallback stream) | `kind="WATCHTOWER_LAUNCH"`, `message="WATCHTOWER launch attributed to the canonical operator."` | **Fact, but structurally non-generalizable** — the SQL query itself is hardcoded to `WATCHTOWER_OPERATOR_ID` as a join parameter, so this whole branch can only ever surface WATCHTOWER launches regardless of wording; this is a legacy pre-X20 fallback path (per its own comment, superseded by `wt_attribution_outcomes`) | **Left unchanged** — rewording alone cannot fix a query-level operator hardcode, and query changes are out of this sprint's scope ("backend changes only where a field's semantics force misleading wording" — the wording here is not misleading, since the join genuinely proves WATCHTOWER) |
| 12 | `src/ops/treasury_expansion_resolver.py:132,194` | `candidate_operator_id: WATCHTOWER_OPERATOR_ID if matched else None`, `if operator_identity == "WATCHTOWER"` | **Fact** — already conditionally gated | Unchanged — already correct |
| 13 | `src/ops/watchtower_alignment.py` (module name, `WATCHTOWER_OPERATOR_ID` constant) | Module/constant naming | **Historical wording, code hygiene** | Left unchanged — internal identifier, not rendered text; renaming would be a much larger refactor with no analyst-facing benefit |

---

## Phase 2 — Per-section question definitions (confirmed, some restated for clarity)

| Section | Question it answers |
|---|---|
| Launch Profile | How was this launch structurally established? |
| Funding Walkback | What funding relationships were reconstructed? |
| Detection Provenance | How did the platform discover this launch? |
| Infrastructure Attribution (`outcome()`) | Where did attribution terminate? |
| Operation Identity | Which operation does this launch belong to? |
| Canonical Operator | Was a known operator identified? |

No section other than Canonical Operator answers "is this WATCHTOWER?" —
confirmed by the Phase 7 regression audit below.

## Phase 3 — Rewritten wording (before / after)

| State | Before | After |
|---|---|---|
| `WALKBACK_RECOVERED` | "This launch is part of a WATCHTOWER-tracked operation, established by retrospective walkback rather than live detection..." | "This launch belongs to a confirmed operation lineage, established by retrospective walkback rather than live detection..." |
| `PIPELINE_INCONSISTENCY` | "This launch is part of a WATCHTOWER-tracked operation, established by retrospective walkback..." | "This launch belongs to a confirmed operation lineage, established by retrospective walkback..." |
| `WALKBACK_OBSERVED` | "...but the walk did not confirm a WATCHTOWER operation — the treasury lineage was not established." | "...but the available evidence was insufficient to establish confirmed operation lineage." |
| `RECONCILED` | "...A wt_watchtower_launches record exists." | "...A platform launch record exists." |
| Analyst Summary (`WALKBACK_OBSERVED`) | "A funding fragment was observed by walkback, but no WATCHTOWER operation was confirmed." | "A funding relationship was reconstructed by walkback, but confirmed operation lineage was not established." |

Justification for every change: each "before" string named WATCHTOWER as
the implicit subject of a fact (operation-lineage confirmation, or a bare
table's existence) that the underlying data never actually ties to that
specific operator. The classifier (`detection_reconciliation.py`) computes
membership from `wt_walkback_queue.intelligence_outcome`, a generic
confirmation state — it has no operator-identity field at all. Naming
WATCHTOWER in this wording was always an assumption, not a derived fact.

## Phase 4 — Platform vs. operator separation

Backend changes, all string-only (no logic touched):

- `service.py` timeline node `detector`/`rule` strings: `"WATCHTOWER attribution"` → `"Platform attribution"`; `"WATCHTOWER launch attribution"` → `"Platform launch attribution"`. The `kind` field (`"WATCHTOWER_ATTRIBUTION"`) is an internal identifier used for evidence-category grouping, never rendered as text to the analyst — left unchanged to avoid touching classification/dedup logic.
- `service.py` reason strings: `"...confirmed WATCHTOWER treasury"` → `"...confirmed treasury"`; `_attribution_reason()`'s `"matched a known WATCHTOWER treasury/sub-provisioner"` → `"matched a known confirmed treasury/sub-provisioner"`.

These three edits are the only backend changes in this sprint, made solely
because the wording itself asserted an operator identity the underlying
condition (`matched_treasury`, `confirmed` flag) does not actually prove.

## Phase 5 — Canonical Operator sole ownership (verified)

Post-fix `grep` for every remaining `'WATCHTOWER` / `"WATCHTOWER` occurrence
in `templates/discovery.html` and `src/discovery/service.py` confirms:
- Internal `kind` identifiers (`WATCHTOWER_ATTRIBUTION`, `WATCHTOWER_LAUNCH`) — never rendered as visible text.
- `_canonical_identity()`'s own SQL gate (`display_name = 'WATCHTOWER' AND status = 'CONFIRMED'`) — the one legitimate assertion.
- The legacy pre-X20 fallback stream (item #11 above) — structurally scoped to WATCHTOWER by its own query, out of scope for a wording-only fix.

No other Discovery component introduces WATCHTOWER independently.

## Phase 6 — Future-proof terminology

Every rewritten sentence describes a persisted classification value
(`confirmed operation lineage`, `confirmed treasury`) that carries no
operator name. If a future operator (PHANTOM, ORBIT, DELTA, or Unknown) is
identified as canonical instead, none of the rewritten Detection
Provenance, Operation Identity, or Infrastructure Attribution wording
requires a single edit — only `canonicalIdentity()`'s already-correct,
data-driven `operator_name` interpolation changes what name appears, and
only in the one section designed to carry it. Verified directly: passing
`PHANTOM`/`ORBIT`/`DELTA` as `operator_name` into `canonicalIdentity()`
renders that name faithfully with no hardcoded WATCHTOWER leakage (test
`test_canonical_identity_never_hardcodes_watchtower_for_other_operators`).

## Phase 7 — Regression audit (all confirmed via automated tests)

- Detection Provenance never implies operator identity — every classification state (`LIVE_DETECTED`, `RECONCILED`, `WALKBACK_RECOVERED`, `PIPELINE_INCONSISTENCY`, `WALKBACK_OBSERVED`, `WALKBACK_INCONCLUSIVE`) tested to contain no `WATCHTOWER` string.
- Operation Identity never implies canonical operator — both multi- and single-treasury card renderings tested WATCHTOWER-free.
- Infrastructure Attribution never implies operator — all outcome types (`KNOWN_RELAY_REACHED`, `KNOWN_BRIDGE_REACHED`, `KNOWN_CEX_REACHED`, `UNKNOWN_INFRASTRUCTURE`, `LINEAGE_GAP`) tested WATCHTOWER-free.
- Walkback evidence never implies operator identity — the lead-node/endpoint renderer tested WATCHTOWER-free both with no canonical identity and with a genuine infrastructure boundary; the one legitimate exception (canonical identity genuinely resolved) is explicitly tested to confirm it still renders correctly.
- Canonical Operator is the only place WATCHTOWER is asserted, and only when `operator_name` is genuinely populated with that value — confirmed absent when `canonical_identity` is `null`, and confirmed to render other operator names faithfully without WATCHTOWER leakage.

## Regression tests

`tests/test_x25_6_operator_neutral_semantics.py` — 17 tests, all passing,
covering every Phase 7 item above. Additionally updated 5 pre-existing
tests across `test_x25_5_1_membership_gating_fix.py`,
`test_x24_8_attribution_semantics.py`, and `test_x25_2_launch_profile.py`
that had hard-asserted the now-intentionally-removed
"WATCHTOWER-tracked operation" string — updated to assert the new
"confirmed operation lineage" wording plus an explicit `"WATCHTOWER" not in
html` check, preserving each test's original intent (membership-fact vs.
entry-path-fact separation) without the operator-specific language.

Full related regression suite: **137/137 passing.**

## Explicit confirmation: no attribution/operation-identity/launch-profile/detection logic changed

```
git diff --stat -- src/discovery/service.py
```
shows only the three string-literal changes described in Phase 4 (no
control-flow, query, or classification-value changes). `src/ops/
operation_identity.py`, `src/ops/attribution_outcome.py`, and the
`_launch_profile()`/classification logic in `detection_reconciliation.py`
were not touched at all in this sprint — confirmed via `git diff --stat`
showing no changes to those files beyond what X25.5.1 already delivered in
the prior sprint.
