# X25.3 — Discovery Semantic Clarity & Analyst Summary

Status: Implemented. Presentation/wording refinement only —
`templates/discovery.html` and one additive backend `reason` string in
`src/discovery/service.py`. No detection, attribution, operation-identity,
launch-profile-derivation, or database-schema logic changed. Verified by
diff and by the full regression suite (see bottom).

---

## Phase 1 — Funding Walkback semantics

**Before:** `Funding Walkback · PROVISIONAL` — one heading string concatenating
the section name and the confidence state, so a reader parses "PROVISIONAL"
as if it modified "Funding Walkback" itself (implying the walkback process
was tentative), when it actually describes the **confidence of the
reconstructed relationship**, not the walkback mechanism.

**After:** heading reads `Funding Walkback` alone; the status renders as a
separate chip titled `Relationship Confidence` (`<span class="vi-chip"
title="Relationship Confidence"><b>PROVISIONAL</b></span>`). The section name
and its confidence are now visually and semantically distinct elements.

Location: `templates/discovery.html`, `walkback()`, the card-header line.

## Phase 2 — OBSERVED_ONLY wording

**Before:** `"No matching verified provisioning record."` — true, but leaves
an analyst wondering why a full funding chain can still render below it.

**After:** `"No verified provisioning session was recorded. The funding
chain shown below, if any, was reconstructed retrospectively from chain
history rather than observed live."` — states the absence and immediately
explains the coexistence the sprint asked for.

Location: `src/discovery/service.py`, `DiscoveryService._launch_profile()`,
the `OBSERVED_ONLY` branch's `reason` field. This is the one backend string
change in this sprint — additive only, same field, same shape, no new
column or classification logic.

## Phase 3 — Endpoint semantics

**Finding:** the pre-existing "Endpoint" fallback node was used for two
different situations without distinguishing them:
1. Walkback genuinely reached a named, known infrastructure address
   (`attribution_outcome.terminal_entity` is populated — e.g. Axiom, a CEX,
   a relay).
2. Walkback simply ran out of evidence with no infrastructure identified at
   all (`_stop_reason()` returns things like `"No historical evidence."` or
   `"Confidence too low to continue beyond the recorded evidence."`).

These are not the same fact. Case 1 is a genuine attribution boundary — an
entity the platform identified. Case 2 is an unresolved investigative gap —
no entity was identified. Using "Endpoint" for both erases that distinction
and can make an analyst read a plain evidence gap as if it were a
recognized infrastructure boundary, or vice versa.

**Decision: renamed, conditionally, not universally.** Per the brief's own
instruction ("no implementation unless a clearly better semantic model
exists"), a single blanket rename (e.g. always "Infrastructure Boundary")
would have been wrong for case 2 — it would fabricate the implication of a
recognized boundary where none exists. Instead:
- Case 1 (`attribution_outcome.terminal_entity` present, no canonical
  operator): renders as **"Infrastructure Boundary"**, naming the actual
  terminal entity address as the node's identity, consistent with how every
  other entity node in the chain is rendered.
- Case 2 (no terminal entity, no canonical operator): renders as
  **"Walkback Stopped"**, an honest process label for an unresolved gap —
  it does not imply an entity was found.
- The canonical-operator case (pre-existing, unchanged) continues to render
  "Canonical operator reached."

Three distinct terminal states, three distinct labels, none overloaded.

Location: `templates/discovery.html`, `walkback()`'s `endpoint` branch.

## Phase 4 — Relationship chain audit

**Reviewed:** `visualChain()` (the "Recorded relationship chain" panel).

**Finding:** node labels already render the entity's real
`terminal_entity_type` value directly from the backend (e.g. `"AUTOMATION"`,
`"CEX"`, `"BRIDGE"` — see `src/ops/attribution_outcome.py`'s
`entity_type: category.upper()`), not a hardcoded generic string. There is
no separate "Automation" conclusion label anywhere in the template — the
concern the brief raised (a conclusion masquerading as an entity name) does
not exist in the current code; the node already names the entity type
itself, which is exactly the objectively-correct model the brief asks to
check for.

**Decision: unchanged, justified.** No change made — renaming an
already-correct entity-type label to a different word would not improve
clarity, and the brief explicitly says "do not change unless the
distinction is objectively clearer."

## Phase 5 — Analyst Summary

Implemented as `analystSummary(d)` in `templates/discovery.html`, rendered
as a `Launch Summary` card immediately after the Identity header (before
every other section, in both the full-timeline and empty-timeline render
paths).

Every sentence maps to exactly one existing, independently-derived field:

| Sentence | Source field |
|---|---|
| "Verified provisioned launch." / "Funding was reconstructed retrospectively..." | `d.launch_profile.classification` |
| "Live detected." / "Detected via reconciliation..." / "Recovered retrospectively by walkback..." | `d.detection_reconciliation.classification` |
| "Attribution reached a confirmed canonical operator." / "Attribution terminated at X infrastructure." / "Attribution outcome: X." | `d.attribution_outcome.outcome_type` / `.terminal_entity_type` |
| "Canonical operator: X." / "No canonical operator identified." | `d.canonical_identity.operator_name` |

**No Operation Identity sentence is included.** X25.0's Operation Identity
model is a design document only — no `operation_id`/`operation` field exists
in the API response today. Including a fabricated "Funding lineage belongs
to Treasury Mesh #2" sentence (as in the brief's own example) would violate
the sprint's explicit "do not invent anything new" constraint, since no such
field currently exists to synthesize from. This is a known, intentional gap:
once X25.0 is implemented (a separate, not-yet-scheduled sprint), the
Operation Identity sentence should be added to `analystSummary()` following
the same one-field-per-sentence pattern.

## Phase 6 — Terminology audit

| Term | Category | Verdict |
|---|---|---|
| **Endpoint** | Was ambiguous (entity vs. process) | **Fixed in Phase 3** — split into "Infrastructure Boundary" (entity) and "Walkback Stopped" (process) |
| **Automation** | Entity type | Not overloaded — already a real `terminal_entity_type` value rendered as-is (Phase 4) |
| **Identity** | Used consistently as a *conclusion* label ("Identity · Level 1", "Canonical Operator" card's "Identity" sub-cell) — both usages mean "who/what this resolved to," not conflicting meanings | No change |
| **Attribution** | Used consistently as a *conclusion* label (Attribution Outcome, Attribution chain) — all usages refer to the walkback's terminal conclusion or its evidentiary steps, not detection or operator identity (confirmed independent in X24.8) | No change |
| **Infrastructure** | Entity-type label (Infrastructure Boundary, infrastructure category names) — consistent, not conflated with Operator (X24.8 already confirmed no leakage) | No change |
| **Walkback** | Now used consistently as a *process* name (Funding Walkback, "Walkback Stopped") after Phase 3's fix; previously "Endpoint" broke this by hiding the process/entity distinction inside one node | Fixed by Phase 3 |
| **Confidence** | Now split explicitly: "Relationship Confidence" (Phase 1, describes a specific walkback hop/edge) vs. per-node "Confidence: X" (describes that specific entity resolution) vs. `attribution_outcome.confidence` (describes the terminal classification) — these were already different fields before this sprint, Phase 1 just made the walkback-level one nameable instead of an unlabeled badge | Labeled in Phase 1 |

Only two renames were made (Phase 1's badge label, Phase 3's endpoint split)
— every other reviewed term already had one consistent meaning throughout
Discovery, confirmed by direct code inspection rather than assumed.

## Phase 7 — Consistency audit

| Section | Question it now answers | Confirmed non-overlapping with |
|---|---|---|
| Launch Profile (X25.2) | How was this launch structurally established? | Funding Lineage, Operation Identity, Attribution Outcome — X25.1/X25.2 confirmed these are separate tracks; unchanged here |
| Funding Walkback | What funding relationships were reconstructed, and with what confidence? | Detection Provenance (how discovered) and Attribution Outcome (where terminated) — the walkback card shows the *path*; the terminal node type (Phase 3) now correctly distinguishes "reached a boundary" from "ran out of evidence," but still doesn't claim detection timing or operator identity |
| Detection Provenance | How did WATCHTOWER learn about this launch? | Confirmed independent of Attribution Outcome by direct measurement in X24.9 (0 mint-level collisions, both detection sources co-occur freely with every outcome type) |
| Attribution Outcome | Where did attribution terminate? | Never mentions WATCHTOWER, operator identity, or detection timing (X24.8 audit); Phase 3 additionally ensures the walkback's own terminal node correctly reflects this same outcome rather than a generic "Endpoint" |
| Operation Identity | Which operation owns this launch? | Not yet implemented in code (X25.0 is design-only) — correctly absent from the Analyst Summary rather than fabricated |
| Canonical Operator | Was a known operator identified? | Gated strictly on `operator_name` (X24.8); Analyst Summary explicitly renders "No canonical operator identified" when absent, never silence that could be misread as "not checked" |

All six questions in the sprint's success criteria have exactly one section
answering them, and the Analyst Summary synthesizes only the four that
currently have backing fields (Launch Profile, Detection Provenance,
Attribution Outcome, Canonical Operator) without fabricating the remaining
two (Funding Lineage detail beyond what's shown in the walkback chain itself,
and Operation Identity, which doesn't exist as a field yet).

---

## Explicit confirmation: no backend inference changed

The only backend change in this entire sprint is the `OBSERVED_ONLY` reason
string in `DiscoveryService._launch_profile()` (Phase 2) — same field name,
same shape (`{classification, reason, facts}`), same trigger condition
(`subprov_wallet`/`funding_mechanism` check unchanged). No detection logic,
attribution logic, operation-identity logic, launch-profile classification
rule, funding-lineage logic, or database schema was touched. Confirmed via:
```
git diff --stat -- src/ | grep -v test
```
showing only `src/discovery/service.py` with a single-string change inside
an existing function, no new queries, no new tables, no changed SQL.

## Regression tests

See `tests/test_x25_3_semantic_clarity.py` — proves: the walkback heading no
longer contains `Funding Walkback · <status>` as one string; the confidence
chip renders with the `Relationship Confidence` title; the `OBSERVED_ONLY`
reason mentions retrospective reconstruction; the endpoint node renders
"Infrastructure Boundary" only when a terminal entity exists and "Walkback
Stopped" otherwise, never the removed generic "Endpoint" string; the
Analyst Summary renders only sentences backed by present fields and omits
absent ones; and the full existing X24.1/X24.8/X25.2 regression suites still
pass unmodified except where they asserted the old literal strings this
sprint intentionally changed (Phase 1's heading, Phase 3's endpoint label).

## Before / after (text description, no screenshot capability in this environment)

**Before**, a `WALKBACK_RECOVERED`, no-canonical-operator, `KNOWN_RELAY_REACHED`
launch rendered:
```
Funding Walkback · CONFIRMED
[Launch] → [Creator] → [Sub-provisioner] → [Endpoint: "CONFIRMED" / <stop_reason>]
```
with no summary card, and `OBSERVED_ONLY` reading only "No matching
verified provisioning record."

**After**, the same launch renders:
```
Launch Summary
  Funding was reconstructed retrospectively; no verified provisioning
  session was recorded.
  Recovered retrospectively by walkback, not live detection.
  Attribution terminated at automation infrastructure.
  No canonical operator identified.

Funding Walkback  [Relationship Confidence: CONFIRMED]
[Launch] → [Creator] → [Sub-provisioner] → [Infrastructure Boundary: <address>]
```
