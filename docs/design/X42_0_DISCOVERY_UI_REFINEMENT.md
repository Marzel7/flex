# X42.0 — Discovery UI Refinement (Canonical Intelligence Presentation)

UI/UX refinement only. No backend architecture, schema, attribution logic, or scoring
changes. Follows [X39.0](X39_0_CANONICAL_ENTITY_RECONCILIATION_AUDIT.md)–
[X41.0](X41_0_SHADOW_MODE_IMPLEMENTATION.md); the frozen architecture is unmodified.
All changes are in `templates/discovery.html`.

## What was changed

1. **"Known Operations" → "Confirmed WATCHTOWER Operations"**, with the requested
   description text, everywhere the string appeared (panel title, loading state, error
   state).
2. **Operator → WATCHTOWER → Operations → Treasuries → Subproviders → Provisioning
   Wallets → Creators → Launches hierarchy strip** — a new `operatorHierarchyStrip()`
   presentation-only helper, rendered under the Operations panel title. No new data
   fetch; purely a static labeled chain.
3. **"Matching Launches" → "Shared Characteristics"**, and each existing outcome group
   (Known Operation, CEX Reached, Repeat Creator, Unknown Infrastructure, Lineage Gap,
   etc. — all already computed server-side by `group_mints_by_outcome()`) now shows a
   one-line "why grouped" explanation (`DW_GROUP_WHY`, client-side text only, keyed on
   the existing group labels).
4. **Operation cards now show an ACTIVE/QUIET/DORMANT status chip**, derived purely from
   `op.last_activity` (already returned by `/api/ops-v2/known-operations`) — Active <24h,
   Quiet 1–7d, Dormant >7d. No new backend field.
5. **Supporting Intelligence panel retitled** "Funding Topology → Behaviour → Mechanism"
   → "Topology → Behaviour → Infrastructure", matching the canonical Topology/Behaviour/
   Infrastructure/Evidence-Gaps conceptual grouping from the spec. The "Funding
   Mechanisms" tree-level heading is now "Infrastructure" (same data, same click-through
   mechanics — funding mechanism IS an infrastructure observation).

## What was explicitly scoped out, and why

Sections 4/5 of the request asked for new filterable Behaviour categories (Rapid
Migration <5m, Migration 5–15m, Creator Recycling, Provisioning Burst) as first-class,
clickable dimensions in the topology tree. Investigating the backend
(`src/ops/operational_behaviour_tags.py`) found only 3 behaviour tags currently computed
(Rapid Birth→Migration, Burst Launcher, Repeat Creator) — its own docstring states
migration-timing and creator-recycling categories are "explicitly left as future work,
not stubbed with an invented threshold." Making these real, filterable categories would
require new backend aggregation/classification logic, which conflicts directly with the
task's stated constraint ("No backend architecture changes... No attribution logic
changes"). Per user direction, this was resolved as: **UI-only for this pass** —

- The existing 3 real behaviour tags are unchanged and still fully functional.
- Four **disabled, non-clickable placeholder rows** ("Rapid Migration (<5m)",
  "Migration (5–15m)", "Creator Recycling", "Provisioning Burst", each labeled "Planned")
  were added to the Behaviour tree level so the UI layout communicates the intended
  future taxonomy without fabricating classification. `bindTopoLevelClicks()` was updated
  to skip `.dw-topo-row-disabled` rows so they cannot be clicked or mistaken for live
  filters.
- **Recommendation, not implemented here**: a future X43.0 (Behaviour Taxonomy Expansion)
  should intentionally extend `operational_behaviour_tags.py` to compute these categories
  as real, backend-driven classifications before promoting these placeholders to live
  filters.

Section 6 (Unknown Infrastructure as an investigation category, sub-clustered by shared
funding wallet/exchange/timing) is largely already served by the existing
`emergingCandidate()` rendering (identity-class grouping for unknown clusters) and the
new "why grouped" text on the Unknown Infrastructure outcome group — sub-clustering by
exchange/collector specifically would again require new backend aggregation and was not
added.

## Verification

- Full-file brace balance check (695/695) and Jinja2 template parse — both clean.
- Flask test-client smoke test against the real `/discovery` route: HTTP 200, all new
  text strings (`Confirmed WATCHTOWER Operations`, `Shared Characteristics`,
  `Topology → Behaviour → Infrastructure`, hierarchy strip, placeholder labels) present
  in the rendered output.
- **Full browser verification**: launched an isolated Flask instance (port 5099, not the
  live production instance on 5002) with the discovery page route plus the
  `operation_dashboard_routes` and `operator_routes` blueprints mounted (the landing
  page's `Promise.all` needs all of them; two were missing from an initial minimal mount,
  causing "Discovery intelligence is temporarily unavailable" — traced directly via
  per-endpoint `curl`/urllib checks, then fixed by mounting the correct blueprint). Real
  production data rendered correctly: 4 operations shown with correct treasury/
  subprovider/creator/launch counts, correct QUIET/DORMANT status badges matching their
  actual `last_activity` age, the full hierarchy strip, and the retitled Topology panel.
  Screenshots captured and inspected directly (not just asserted).
- `node -e` unit-check of the exact placeholder-row template-string logic, confirming
  correct HTML escaping and class names.
- Existing test suite: `test_x26_5_1_attribution_health_window_integrity.py` (2 failures,
  confirmed pre-existing and unrelated via the same `git stash` verification method used
  in X41.0) and `test_discovery_workspace.py` (5/5 pass) — no new regressions.

## Answer to the stated success criterion

The Discovery page now visibly communicates the Operator → Operation → Treasury →
Subprovider → Provisioning Wallet → Creator → Launch hierarchy, names itself as Confirmed
WATCHTOWER Operations rather than an ambiguous "known" list, explains why launches are
grouped together instead of presenting a flat list, and distinguishes Active/Quiet/
Dormant operations at a glance — all without any change to detection, attribution, or
scoring logic. The one area intentionally left incomplete (expanded Behaviour taxonomy)
is visibly signposted as planned rather than silently omitted or faked, with a clear
recommendation for the backend work that would be needed to complete it.
