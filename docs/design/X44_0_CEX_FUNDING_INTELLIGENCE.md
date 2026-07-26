# X44.0 — CEX Funding Intelligence Expansion (Read-Only Funding Origin Analysis)

Follows [X43.0](X43_0_BEHAVIOUR_CLASSIFICATION_EXPANSION.md). Strictly a read-only
intelligence enhancement built entirely from already-persisted funding evidence — no
architecture, schema, attribution, scoring, confidence, Operator, or Operation changes.

**Revised after initial feedback (X44.1)**: the first pass built a separate
origin-grouped summary panel prepended above the flat launch list. Actual ask was
simpler and more useful: attach the CEX exchange/origin/path info **directly to each
token address** in the existing list, not a disconnected summary block. The backend
module and data are unchanged; only the presentation layer was reworked.

## What was implemented

### `src/ops/cex_funding_intelligence.py` — new module
`build_cex_funding_intelligence()` groups `wt_attribution_outcomes` rows with
`outcome_type='KNOWN_CEX_REACHED'` by withdrawal origin (`terminal_entity`), reading
already-persisted fields from `evidence_json`:
- `evidence_json.boundary.name` — the **already-identified** exchange name (e.g. "Binance",
  "KuCoin"), sourced upstream from `src/utils/infra_mapping.py`'s `CEX_ACCOUNTS` registry
  (confirmed live: 56 known CEX accounts spanning 30 distinct exchange names). **Never
  inferred** — a row with no `boundary.name` is labelled `"Unknown CEX"`, never guessed
  from the address.
- `evidence_json.creator`, `.treasuries`, `.subprovisioners` — the observed downstream
  funding-path hops. Renders only hops actually present in the evidence, never an invented
  intermediate hop.
- `wt_ops_v2_wallets` — cross-referenced (read-only) to count how many existing
  Operations already contain the withdrawal-origin wallet. Checked directly against real
  data: **0 of 502 real CEX-reached launches currently link to any Operation** (an honest
  fact, reported as-is).

Also computes, as origin-level aggregates (still available via `result["origins"]` for
any future use, but no longer the primary UI surface):
- **`multi_cex_creators`** — creators whose CEX-reached launches touch more than one
  distinct withdrawal origin (exact wallet match only; 1 real case in production).
- **`shared_infrastructure`** — the same subprov/treasury hop reused across ≥2 distinct
  origins, plus a "Cross-Exchange Hop" case: a subprov hop that is itself another origin's
  own withdrawal address. **Found live in production**: KuCoin's own withdrawal wallet
  (`BmFdpraQhkiDQE6SnfG5omcA1VwzqfXrwtNYBwWTymy6`) appears as an intermediate subprov hop
  on one Binance-attributed launch.

### `result["mints"]` — the field the actual UI need is built on
A dict keyed by mint address, one entry per `wt_attribution_outcomes` row (mint is that
table's primary key, so this is a 1:1 carry of already-computed per-row fields, not a new
query): `{mint, exchange, origin, origin_short, creator, creator_short, treasuries,
subprovisioners, completed_at}`. This is what lets the UI answer "what CEX info does
*this specific token* have" with one dict lookup, instead of only getting an
origin-grouped summary that has to be cross-referenced back to individual tokens.

### A real bug found and fixed during this pass
The raw `evidence_json` data itself has a quirk: many rows duplicate `terminal_entity`
inside their own `subprovisioners` list (the origin wallet listing itself as its own hop).
Fixed by excluding `sp == origin` before building the path or the shared-infrastructure
aggregation — verified by comparing output before/after the fix.

### `src/core/operation_dashboard_routes.py` — new route
`GET /api/ops-v2/cex-funding-intelligence?window=<24h|7d|30d|all>` — thin wrapper calling
the module above, reusing the existing `discovery_window.py` window-parsing convention.
Read-only, zero writes.

### `templates/discovery.html` — inline per-token badge (not a separate panel)
`cexRowDetail(mint)` looks up the mint directly in a client-side cache
(`CEX_MINT_CACHE`, populated once per render from `/api/ops-v2/cex-funding-intelligence`)
and returns a small badge (`<span class="dw-cex-badge">Binance</span>`) plus a compact
funding-path string (`Binance → 5tzF...uAi9 → ...`), rendered **inline next to the
existing token-address link** via a new `launchRow(mint)` helper — replacing the plain
`<a>...</a>` construction previously used in both the flat-list and grouped-list code
paths in `renderGroupedLaunches()`. Fetched alongside (via `Promise.all`, not after) the
existing launch-list fetch in `updateLaunchTableFilter()`, so the badge is present on
first render, not a second re-render pass. No new filtering model, no change to which
launches are shown or how topology/behaviour/mechanism selections work — only the
existing row's markup gained an inline detail.

The earlier `renderCexFundingIntelligence()`/`renderCexOrigin()` origin-summary-panel
functions and their `.dw-cex-panel`/`.dw-cex-origin`/etc. CSS were removed entirely.

## Validation

- **No schema changes, no new tables, no writes**: unchanged from the original pass —
  confirmed via `PRAGMA query_only=ON` and the `test_no_writes_occur` regression test.
- **No attribution/treasury/Operation/confidence changes**: unchanged — this module has
  no import path into any attribution-decision code.
- **16 regression tests** (`tests/test_x44_0_cex_funding_intelligence.py`, 2 added for the
  `mints` field): confirms per-mint entries are keyed by the mint's own address (not
  collapsed into origin-level records), and that two different mints sharing one origin
  each retain their own creator/path data. All 16 pass.
- **Visual verification**: extracted the exact `esc`/`abbr`/`href`/`cexRowDetail`/
  `_short`/`launchRow` functions plus their CSS into a standalone HTML page, fed it 8 real
  mint records from the live database, and screenshotted the result — confirmed each
  token address renders with its own inline exchange badge (Binance, Bidget, KuCoin,
  Coinbase, OKX) and compact funding-path text directly beside it, exactly as requested.
- **Broader regression suite**: `cex`/`discovery`-keyword tests show only the same 2
  pre-existing failures already confirmed unrelated in X41.0/X42.0 (stale HTML-content
  string assertions unrelated to this feature); 114 passed.

## Explicit constraints honored

No Operations or Operators were merged. No confidence value was raised or created. No
exchange was classified heuristically — every label traces to `evidence_json.boundary.name`.
No withdrawal cluster was invented. No ownership was inferred.

## Answer to the stated success criterion

Each token/mint in the Discovery launch list now shows its CEX exchange and withdrawal
origin directly, inline, with a compact funding-path summary — answering "which exchange
funded this specific launch" at the point where an analyst is already looking, rather than
via a separate summary block that has to be manually cross-referenced back to individual
tokens.
