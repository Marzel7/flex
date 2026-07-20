# X29.6 — Discovery Architecture Audit

Investigation only, per the brief. No code changed.

## Part 1 — Functional Audit: why the confirmed creator is not discoverable

**Traced directly against the live databases and live API, not speculated.**

Test subject: creator `HTR9U7dkk1eEwmyFyzCzERdy3vr8CM6T8hW5FY1s24gt`, mint `EGB4sv9ddNhWeUhnsAvpqP8xaEps4cx5bc956LPcpump`.

| Question | Traced answer |
|---|---|
| Is the launch present in the database? | **Yes.** `wt_watchtower_launches`: `creator_wallet=HTR9U7...`, `subprov_wallet=ANenEukv...`, `treasury_wallet=9hGcxVHF...`, `create_time=1784048633` (2026-07-14T17:03:53Z), `state=FIRED_CREATE`. |
| Is it present in the investigation endpoint? | **Yes, at `window=all`.** `GET /api/ops-v2/investigation-pipeline?window=all&mint=EGB4...` returns `assignment.bucket=KNOWN_OPERATION`, `reason="Investigation complete. Attribution reached a confirmed canonical operator."` |
| Is it returned by Discovery API queries? | **No, at the default window.** `GET /api/ops-v2/investigation-pipeline?window=24h` (no mint filter — the query Discovery's landing table actually issues) returns **0 total mints**, full stop. Not "0 matching this creator" — the entire 24h window is currently empty. |
| Is it filtered out? | No dedicated filter excludes it — `KNOWN_OPERATION`/`CANONICAL_OPERATOR_REACHED` launches are not suppressed by any bucket or outcome-type filter. |
| Is it outside the selected time window? | **Yes — this is the root cause.** `create_time=1784048633` is 2026-07-14T17:03:53Z; today is 2026-07-19, ~08:51 UTC — **4.66 days old**. `investigation_pipeline.py`'s `since = now - window_seconds` with the default `window_seconds=86400` (24h) excludes it by construction: `create_time >= since` evaluates `False`. |
| Is the WATCHTOWER identity attached correctly? | **Yes.** `outcome_type=CANONICAL_OPERATOR_REACHED` in `wt_attribution_outcomes`, `assignment.bucket=KNOWN_OPERATION` at the API layer. Attribution is correct and complete; it is simply not being asked for. |
| Is the UI hiding it? | Not by a deliberate hide rule — but functionally yes, because **`discovery.html` hardcodes `window=24h` on every launch-browsing request** (`investigation-pipeline?window=24h` for the bucket table, `operational-intelligence?window=24h` for the topology/behaviour/mechanism hierarchy) with **no window selector exposed anywhere in the UI** (verified: zero `<select>`/dropdown/toggle controlling window in `discovery.html`). The only non-24h path is the single-mint lookup (`window=all&mint=...`), which requires already knowing the exact mint. |
| Is Discovery only showing a subset of launches? | **Yes — a rolling 24-hour subset, permanently.** Anything older simply falls out of view with no way to widen the range from the UI. |

**Exact root cause:** Discovery's landing/browsing views are hardcoded to a 24-hour lookback with no user-facing control to change it. The launch is fully detected, fully attributed, and correctly classified — it is invisible purely because it is older than 24 hours and Discovery has no mechanism to look further back except by already knowing the exact mint address (a chicken-and-egg requirement that defeats the purpose of *discovery*).

A secondary, compounding symptom was also observed and is worth noting but is not the root cause: `operational-intelligence?window=24h` returned `total_launches: 652` with `cache_state: refreshing`, `cache_age_seconds: 1125` — an `SWRCache`-served snapshot generated ~19 minutes prior, from whatever activity fell in *that* prior 24h window. This means the topology/behaviour/mechanism hierarchy view can show a stale, already-rolled-off set of launches for several minutes after the true window has moved on, independent of the hardcoded-window problem above.

The one working path that exists today: the search box (`/api/discovery/search?q=<address>`) is not window-limited and does find the creator by exact address (verified: returns `{"type": "creator", "label": "Creator", "id": "HTR9..."}`). But this only helps an analyst who already has the exact address in hand — it cannot help someone trying to *discover* an operation they don't yet know exists, which is Discovery's stated purpose.

## Part 2 — Semantic Audit

### What Discovery is actually optimised for today

Reviewing `discovery.html`, its routes, `investigation_pipeline.py`, and `operational_intelligence.py` together: Discovery is built as a **rolling classification-status dashboard for the last 24 hours**, not an investigation tool for a specific operation. Its primary interactions are:

- A bucket table (`window=24h`, optionally `group_by=creator/outcome`) — "how many launches got which outcome_type in the last day."
- A topology → behaviour → mechanism drill-down (`window=24h`) — "how many of today's launches look like Fan-Out, are Rapid-Birth, use Wrap-Close."
- A single-mint deep-dive (`window=all&mint=X`) — the *only* place `window=all` is used, and only once you already have a specific mint.

None of these are framed around "show me what operation this creator belongs to" as a starting point. The search box gets closest, but it's a flat lookup-by-address utility bolted onto the side, not the primary navigation model, and it returns one address at a time — it cannot answer "show me all launches under this subprovider" or "show me every creator this treasury has funded," because there is no role-based aggregate view to search *into*.

### Where classifications outrank operational identity

Confirmed directly in `discovery.html`'s DOM assembly order (`render(d)`): the per-mint deep-dive page concatenates sections in this order — `identityHeader` → `summaryCard` → `infra` (Operational Attribution) → `fundingBoundary` → `walletQuality` → `leads` → `flow`/`wb` (walkback) → `provenance` → `operationCard` (`operation_identity`) → `operatorIdentity` (`canonical_identity`) → `opBehaviour` → `creatorAct` → `attribution` (timeline) → `evidence` → `lineage` → `raw`. Role-bearing identity (`operationCard`/`operatorIdentity`) sits roughly two-thirds of the way down a single long page, after four other classification/evidence sections. And the landing page above the per-mint view is the topology/behaviour/mechanism hierarchy itself, titled exactly "Funding Topology → Behaviour → Mechanism" — an analyst's very first click, before opening any specific launch, is already into a classification browser, not an operation browser.

### Per-dimension audit: role, relationship, behaviour, or evidence?

| Dimension | Describes | Primary or supporting? |
|---|---|---|
| **Operational Attribution** (`outcome_type`/bucket) | A conclusion about identity — "this belongs to a known operator / is unresolved / etc." | Should be primary — it's the closest thing to an answer to "what operation is this," but it's currently expressed as a flat bucket label, not tied to a role chain. |
| **Funding Boundary** (X29.3) | Evidence — what external funding source was observed and how completely searched. | Supporting evidence, correctly scoped as such (X29.3's own design already treats it this way, rendered "directly beneath" attribution, never replacing it). |
| **Funding Topology** (X29.1) | Currently: a per-launch scalar. Per X29.5's finding: actually a per-subprovider/treasury *relationship* fact (out-degree, depth). | Currently primary (top of the landing page); should be supporting metadata attached to whichever role-node it actually describes (per X29.5). |
| **Operational Behaviour** (X29.1) | Behaviour — timing/pattern signal (Rapid Birth, Burst Launch) on the creator/launch. | Correctly modeled as additive, secondary evidence in the existing three-axis design — this one is not conflated. |
| **Funding Mechanism** (X29.1) | Evidence — how the funding transfer was technically performed (wrap-close, plain transfer). | Correctly supporting/evidentiary, same as Behaviour. |
| **Wallet Quality** (X29.4) | Environmental annotation — spam/dust signal on a wallet, explicitly orthogonal to identity. | Correctly modeled as supporting-only and explicitly never influencing identity (X29.4's own design goal, achieved). |

Four of six dimensions (Boundary, Behaviour, Mechanism, Wallet Quality) are already correctly scoped as supporting evidence. The two that are *not* correctly scoped are exactly the two the brief flags: **Operational Attribution is treated as the whole answer when it's really the top of a role chain**, and **Funding Topology is applied to the wrong unit** (X29.5's finding, reconfirmed here).

### Missing concept: operational role

Discovery has no first-class, queryable, role-typed entity. `operation_identity.py` has a `role` field, but it means `ROOT`/`MEMBER` of an operation *cluster* — it is not the Creator/Provisioning-Wallet/Subprovider/Treasury role vocabulary the brief (and X29.5) describe. `wt_provisioning_edges` stores `edge_type ∈ {TREASURY_TO_SUBPROV, SUBPROV_TO_CREATOR}`, which implicitly encodes role via the edge's endpoints, but nothing derives or surfaces "wallet X's role is Subprovider" as a queryable, searchable fact today. This is the same gap X29.5 identified from the topology angle; this audit confirms it independently from the discovery/search angle — there is no way to ask Discovery "show me every subprovider" or "show me every provisioning wallet feeding creator Y," because subprovider/provisioning-wallet is not a modeled entity type anywhere the UI or its APIs can query.

### Searchability, by target type

| Target | Findable today? | Why / why not |
|---|---|---|
| Confirmed WATCHTOWER launch (exact address known) | Yes | `/api/discovery/search` is not window-limited. |
| Confirmed WATCHTOWER launch (address unknown, browsing) | **No**, if >24h old | Landing views are hardcoded `window=24h` with no way to widen. |
| Unknown/new operation | **No** | Nothing surfaces "here is a cluster of launches sharing a subprovider you haven't seen labeled before" — the only aggregation is by `outcome_type` bucket or by topology label, neither of which is keyed to a specific unfamiliar wallet. |
| New subprovider | **No** | Subprovider is not a modeled/searchable entity; it only appears as a column value inside a launch row. |
| Provisioning wallet | **No** | Same as above — not a modeled entity at all in the current schema exposed to Discovery. |
| Creator | Partially | Findable by exact address via search, or via the 24h bucket table if recent; not findable by "show me all creators under subprovider X." |
| Treasury | Partially | Same constraint as creator. |

## Recommendation (description only, no code)

**Fix the functional bug first, independent of any redesign:** Discovery's landing views must not hardcode `window=24h` with no escape hatch. At minimum, a window control (24h / 7d / 30d / all) needs to exist in the UI, because right now a fully-attributed, correctly-classified, confirmed WATCHTOWER launch is completely invisible through normal browsing the moment it turns a day old — the system's own confidence in its answer (`KNOWN_OPERATION`, reached via `CANONICAL_OPERATOR_REACHED`) is strictly higher than the visibility Discovery gives it.

**Then, reorganize around role, per X29.5's conclusion, generalized beyond WATCHTOWER:** the primary organizing concept Discovery is missing is an explicit, queryable **Role** axis (Creator / Provisioning Wallet / Subprovider / Treasury), populated from the same edge data (`wt_provisioning_edges`) that already exists. Discovery's entry point should let an analyst search or browse *by role* — "show me subprovider X," "show me every creator under treasury Y" — with the existing six intelligence dimensions attached as supporting metadata on whichever role-node they actually describe, exactly as the current design already does correctly for Behaviour/Mechanism/Wallet Quality/Boundary. Topology terms become descriptions of a Subprovider/Treasury node's edge set rather than a label on the launch; Operational Attribution becomes the conclusion drawn at the top of the resolved role chain rather than a flat, disconnected bucket. This generalizes cleanly to any future, currently-unknown operation, because the four roles and the edges between them are the only structural claim being made — no operation-specific vocabulary is required to represent a new, differently-shaped funding chain.
