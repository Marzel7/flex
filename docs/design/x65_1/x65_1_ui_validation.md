# X65.1 — Phase 8: Discovery UI Integration

## Changes made

### Backend: new API endpoint

**`GET /api/ops-v2/treasury-resolution?mints=<comma-separated>`**
(`src/core/operation_dashboard_routes.py`) — a new, read-only route
wrapping `src/ops/treasury_resolution.py`'s `resolve_treasury_for_cohort()`.
Bounded to at most 200 mints per request (matching this project's
existing per-request bounding convention elsewhere, e.g.
`launches.slice(0,200)` in the launch-results renderer). Returns
`{"ok": true, "resolutions": {mint: {treasury_resolution: {...}}}}`.

Live-tested against the running process (restarted, pid 64992):
```
GET /api/ops-v2/treasury-resolution?mints=EDNvjVDjKVfR...,4WfoYERYFw3A...,c5Zye8yFd1AG...
-> {"ok": true, "resolutions": {...}}  (confirmed correct UNRESOLVED/KNOWN_TREASURY
    payloads matching Phase 3-5's manual analysis exactly)
```

### Frontend: Treasury Resolution panel

**`templates/discovery.html`** — added:
- A new mount point (`dw-x65-1-treasury-resolution-mount`), positioned
  immediately after the existing Funding Origin mount
  (`dw-topo-infra-mount`) in `operationalIntelligencePanel()`'s section
  order.
- `renderTreasuryResolution()`: fires only when
  `TOPO_SELECTION.funding === 'UNKNOWN'` (the exact terminal state this
  task exists to resolve past) and the current Funding Origin cohort
  (`x60FundingRows()`) is non-empty. Fetches only the mints not already
  cached (`X65_1_TREASURY_CACHE`), bounded to 200 per request, via the
  new API endpoint above.
- `renderTreasuryResolutionTable()`: renders summary cards (one per
  `KNOWN_TREASURY`/`UNKNOWN_TREASURY_CANDIDATE`/`NO_SUBPROV`/`UNRESOLVED`
  status actually present) plus a full per-launch table with columns
  Mint / Creator / Sub-Provisioner / Treasury-or-Candidate / Status /
  Operation / Confidence / an expandable evidence `<details>` per row.
- Wired into the existing render pipeline: `renderX58Mounts()` now
  calls `renderTreasuryResolution()` alongside the other section
  renderers, so it updates automatically whenever the cohort selection
  changes.
- New CSS block matching this file's existing visual conventions
  (`.dw-x65-1-*` classes, reusing `--ip-cyan`/`--ip-dim`/`--ip-sec` CSS
  variables already used throughout the page).

## Suggested presentation from the task vs. what was implemented

The task suggested a nested breadcrumb structure under Funding Origin
(`Known Treasury → WATCHTOWER Treasury / Other Confirmed Treasury`,
`Unknown Treasury Candidate`, `Direct Treasury Funding`, `No
Sub-Provider`, `Unresolved`). This implementation instead surfaces the
same information as a **flat, always-visible summary + table** directly
below Funding Origin, for two reasons:
1. **Every result is shown regardless of status** (per the task's own
   explicit "Do not hide unresolved launches" requirement) — a nested
   breadcrumb tree structure would naturally invite treating
   `UNRESOLVED`/`UNKNOWN_TREASURY_CANDIDATE` as dead-end leaves to
   collapse or hide, which risks violating that requirement. A flat
   table with all four statuses always rendered side-by-side makes
   "nothing is hidden" a structural property, not a behavior that
   depends on remembering to expand every branch.
2. This cohort (19 launches, and likely similarly small for any
   Funding-Origin-UNKNOWN cohort in a typical window) does not need a
   deep interactive breadcrumb hierarchy to stay navigable — a flat
   table already shows every required column (creator, sub-provider,
   treasury-or-candidate, hop path implicit in the evidence, status,
   operation, confidence, expandable evidence) at a glance.

The distinction between "WATCHTOWER Treasury" and "Other Confirmed
Treasury" (the task's suggested sub-split under Known Treasury) is
preserved via the `Operation` column plus each row's evidence
(`operation_id`, cross-referenceable against this project's own
canonical `WATCHTOWER_OPERATOR_ID`) — not as a separate visual
sub-group, since none of this cohort's 7 `KNOWN_TREASURY` results are
WATCHTOWER-attributed (Phase 5), making a dedicated WATCHTOWER
sub-branch empty and not worth the added UI complexity for this
specific cohort. The underlying data model (`operation_id` on every
`treasury_resolution` object) fully supports adding that visual split
later if a future cohort actually contains WATCHTOWER-attributed
results.

## Operation Attribution behavior

Per the task's explicit requirement:
- A `KNOWN_TREASURY` result's `operation_id` is surfaced directly in
  the new table's Operation column — this reflects the **existing**,
  already-confirmed operation assignment (Phase 5's `wt_ops_v2_wallets`
  lookup), never a new or auto-generated one.
- `UNKNOWN_TREASURY_CANDIDATE` rows always render with `operation_id:
  null` → displayed as `—` in the Operation column, never resolved to
  an operation — consistent with `TOPO_SELECTION.operation` remaining
  unaffected by this panel (this panel is purely additive information
  alongside the existing Operation Attribution stage, it does not
  short-circuit or bypass that stage's own selection logic).
- `UNRESOLVED` rows are fully visible in the table (not hidden), each
  carrying its own `reason` string, viewable via the expandable
  evidence `<details>` per row.

## Verification performed

- **API**: live-tested directly against the running process — correct
  `ok:true` response shape, correct per-mint resolution payloads
  matching the manually-derived Phases 3-5 results exactly.
- **JS syntax**: extracted the page's single `<script>` block, replaced
  Jinja template expressions (`{{ ... }}`) with a neutral placeholder to
  produce valid standalone JS, and ran `node --check` — clean, no
  syntax errors introduced.
- **Function hoisting**: confirmed `_short()` and `x58Card()` (both
  reused by the new code) are standard `function` declarations
  (hoisted), so their physical position in the file relative to the new
  code does not matter.
- **Page load**: `GET /discovery` returns HTTP 200 after the change
  (confirmed the template still renders without a server-side error).
- **Not performed**: a live, visual, in-browser click-through of the
  new panel — no browser-automation tooling (Playwright/chromium-cli)
  was available in this environment. This is stated explicitly per
  this project's own standing instruction to never claim a UI change
  works visually without actually driving it in a browser; the checks
  above (API correctness, JS syntax validity, template renders without
  error) are the maximum verification achievable without that tooling,
  and are reported as exactly that — not as a substitute for a visual
  confirmation.
