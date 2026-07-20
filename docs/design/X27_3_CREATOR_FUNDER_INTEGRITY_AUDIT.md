# X27.3 — Creator Funder Integrity Audit

**Investigation only. No code, schema, detection, or attribution changes were made.**

Subject wallet: `GF7YB1jGktkRQNnXU5YCuVRCQdoHcctkUw1q5bgbLLXc` ("GF7Y")
Reference mint: `HwacTUkcf5d4oipWUtdxkLbamJEJJUZMSGnBVxi9pump`
Reference creator: `FFAU89PNxFsK2Sh9zY9tEaqPEMgZswM4T8ryAttJTGzR`

## Verdict (Success Criterion B)

**The displayed relationship is factually misleading and is proven incorrect
by both persisted evidence and independent on-chain verification.** GF7Y did
not "fund" the creator in any operationally meaningful sense. The precise
defect is identified in the Root Cause section below.

## Phase 1 — Funding trace (exact path Discovery took)

For mint `HwacTUkcf5d4oipWUtdxkLbamJEJJUZMSGnBVxi9pump`, `DiscoveryService._entity()`
(`src/discovery/service.py`) built the rendered chain from exactly one
source row, with no wrap-close or launch-ingestion record available:

1. **Table consulted**: `wt_walkback_queue` (ops DB), `WHERE mint = ?`.
   Row:
   ```
   creator=FFAU89PNxFsK2Sh9zY9tEaqPEMgZswM4T8ryAttJTGzR
   subprov=GF7YB1jGktkRQNnXU5YCuVRCQdoHcctkUw1q5bgbLLXc
   treasury=NULL
   walkback_class=FULL_WALKBACK
   attribution_source=unknown
   intelligence_outcome=LINEAGE_GAP
   funding_mechanism=PLAIN_XFER
   funder_wallet=GF7YB1jGktkRQNnXU5YCuVRCQdoHcctkUw1q5bgbLLXc
   funder_amount_sol=0.0
   funder_sig=nGQyaKyig19X19U1VAQnd1PUfgG3L8Z8xDtGhKxTt72dBC8Fa3UnBgEWBH7Mtf1aUg6HFbTp1ZynECZ4hPngx6A
   funder_block_time=1784205546
   ```
2. Because `walk.get("creator")` was present with no `launch`/`migration`/
   `lifecycle` row, `_entity()` took the **X26.7 walkback-only branch**
   (service.py:410-432) and emitted a `CREATOR_IDENTIFIED` node sourced
   entirely from this one row.
3. **Second table consulted**: `wt_discovered_subprovs` (ops DB),
   `WHERE subprov = ?` (service.py:434). Row:
   ```
   subprov=GF7YB1jGktkRQNnXU5YCuVRCQdoHcctkUw1q5bgbLLXc
   creator_count=2
   treasury=NULL, treasury_known=0
   confidence=0.4
   state=dismissed
   wrap_close_count=0
   discovery_source=WALKBACK_RECURRING_FUNDER
   funding_mechanism=PLAIN_XFER
   ```
4. **Gate evaluated** (service.py:442): `sp_rejected = state.upper().startswith("REJECTED")`.
   `"DISMISSED".startswith("REJECTED")` is `False`, so `sp_rejected=False`
   and the `SUBPROVISIONER_RESOLVED` node was rendered as if this were a
   live, unrejected candidate — this is the exact point identified in Phase 7.
5. No SQL beyond simple `WHERE`-keyed single-row lookups was involved; no
   RPC, no scoring, no walkback re-execution — Discovery only composed
   already-persisted rows.

## Phase 2 — On-chain verification of the funding transaction

RPC-verified directly (`getTransaction`, `nGQyaKyig19X19U1VAQnd1PUfgG3L8Z8xDtGhKxTt72dBC8Fa3UnBgEWBH7Mtf1aUg6HFbTp1ZynECZ4hPngx6A`,
slot 433274221, blockTime 1784205546, `meta.err = null`):

| Field | Value |
|---|---|
| Signer / sender | `GF7YB1jGktkRQNnXU5YCuVRCQdoHcctkUw1q5bgbLLXc` |
| Instruction count | 10 `system.transfer` instructions, single transaction |
| Recipients | 10 distinct addresses, including `FFAU89PN...` |
| Amount to `FFAU89PN...` | **1 lamport (0.000000001 SOL)** |
| Amounts to other 9 recipients | 1–3 lamports each |

The transaction **exists and is genuine** — GF7Y did send 1 lamport to
the creator address in this exact signature. But this is a **10-way, single
-transaction lamport-dust fan-out**, not a funding transfer: every
recipient received 1–3 lamports, an amount with no operational value (it
cannot pay rent, fees, or seed an account). This matches the textbook
signature of a wallet-dusting broadcast, independent of and consistent
with the platform's own prior classification.

**No intermediate wallet was incorrectly promoted** — the signature and
sender/recipient pairing are exactly as recorded. The defect is not in
which wallet was identified; it is in treating a 1-lamport dust receipt as
"creator funding" at all.

## Phase 3 — Walkback reconstruction audit

The recorded path has exactly one hop:

```
GF7Y (funder_wallet, PLAIN_XFER, 1 lamport)
  ↓
FFAU89PN... (creator)
```

`walkback_class=FULL_WALKBACK` for this row, but `attribution_source=unknown`
and `intelligence_outcome=LINEAGE_GAP` — meaning the platform's own
attribution classifier (`src/ops/attribution_outcome.py`) evaluated this
chain and explicitly **did not** confirm it as a genuine treasury→creator
lineage; it stopped at `LINEAGE_GAP` (walkback ran out of usable evidence
past this point). GF7Y became the displayed "sub-provisioner" purely
because it was the last wallet to send the creator address *any* lamports
before CREATE — the walkback's job is to find the immediate funder, and it
correctly found GF7Y as that funder. The reconstruction is technically
accurate (GF7Y did send those lamports last); the misrepresentation is in
Discovery's presentation layer treating "last funder found" as
"legitimate sub-provisioner," rather than surfacing the `LINEAGE_GAP`
outcome and the `dismissed` state as disqualifying context.

## Phase 4 — Dusting evidence audit

The platform has **three dedicated dust-tracking tables**:
`wt_dust_markers`, `wt_dust_observations`, `wt_dust_recipient_lifecycle`
(all in `wt_ops_v2.db`). Queried directly for GF7Y as both dust-sender and
dust-recipient:

| Table | Rows for GF7Y |
|---|---|
| `wt_dust_markers` | 0 |
| `wt_dust_observations` (as `dust_wallet`) | 0 |
| `wt_dust_observations` (as `recipient_wallet`) | 0 |
| `wt_dust_recipient_lifecycle` | 0 |

**GF7Y has never been recorded in any of the platform's own persisted
dust-classification tables.** The "known spam dusting account" premise
in this sprint's background is not backed by any surviving platform
record — it may be prior analyst/external knowledge, a classification
that was made and never persisted, or a classification of a different,
similar-looking wallet. This audit cannot confirm the historical
classification from the database; it can only confirm that the on-chain
transaction (Phase 2) is independently consistent with dust-broadcast
behavior, and that this specific pattern was never captured by the
platform's dust pipeline for this wallet.

**Recommendation flag**: if a "known dust marker" registry entry exists for
GF7Y elsewhere (spreadsheet, prior report, a different environment's DB),
it was not found in this repository's live database and there is currently
no code path that would prevent this exact wallet from being promoted to
sub-provisioner again — the dust pipeline and the walkback/subprov pipeline
do not currently cross-reference each other at all.

## Phase 5 — Wallet behaviour breakdown

All 149 `wt_walkback_queue` rows referencing GF7Y (as `funder_wallet` or
`subprov`) were audited:

| Category | Count | Basis |
|---|---|---|
| PLAIN_XFER "funding" transfers (0.0 SOL rounded) | 103 of 103 populated | `funder_amount_sol` distribution: **100% are exactly `0.0`** |
| Wrap-close observations | 0 | `wt_discovered_subprovs.wrap_close_count = 0` |
| Confirmed treasury funding | 0 | `treasury_known = 0`, `treasury = NULL` |
| Dust observations (platform-recorded) | 0 | Phase 4 |

There is no observed category of GF7Y activity in this database other
than the same near-zero-lamport `PLAIN_XFER` pattern, repeated 103+ times
against different creator addresses. No "normal transfer" or genuine
"infrastructure funding" activity was found. Based on persisted evidence,
**GF7Y does not appear to perform multiple genuine operational roles** —
every observed transaction is consistent with a single behavior: a
mass, near-zero-lamport, single-transaction fan-out (dusting), which the
walkback pipeline's "find the most recent funder" logic then
misclassifies as sub-provisioning whenever no larger legitimate funder
exists in the lookback window.

## Phase 6 — Historical creator-funding analysis

| Metric | Value |
|---|---|
| Distinct creators "funded" | 103 (via `funder_wallet`), 101 (via `subprov`) |
| Launches produced | 149 |
| Funding mechanism | 100% `PLAIN_XFER` |
| `walkback_class` | 101 `FULL_WALKBACK`, 46 `PARTIAL_TREASURY` |
| `intelligence_outcome` | **100% `LINEAGE_GAP`** — every single one |
| Repeat observations | `first_seen=1783891697` → `last_seen=1784237952` (~4 days), `wt_discovered_subprovs.state=dismissed` (already reviewed once and dismissed) |

Creator funding is **recurring and dominant in volume** (149 launches, the
largest source of Unknown-Infrastructure attribution for this wallet) but
**uniformly unconfirmed** — every single one of the 149 rows terminated at
`LINEAGE_GAP`, the platform's own "insufficient evidence to confirm a real
lineage" outcome, and the one `wt_discovered_subprovs` row summarizing this
wallet was already human-dismissed. There is no subset of "isolated" vs.
"dominant" genuine funding to separate — the entire 149-launch population
shares the identical unconfirmed, dismissed-then-still-rendered pattern.

## Phase 7 — Integrity check

**Can a wallet simultaneously satisfy "known dust marker" AND "creator
funder"? No — not as currently modeled and rendered.**

The precise defect: `src/discovery/service.py` line 442 gates the
`SUBPROVISIONER_RESOLVED` rendering on:
```python
sp_rejected = bool(sp) and str(sp.get("state") or "").upper().startswith("REJECTED")
```
This check (introduced in X26.6.1 to suppress rendering for
`REJECTED_INFRASTRUCTURE`/similar states) does not recognize `state='dismissed'`
— a different literal string produced by the human-dismiss action at
`operation_dashboard_routes.py:3189` (`action='dismiss'` →
`UPDATE wt_discovered_subprovs SET state='dismissed'`). Both states mean
the same thing operationally (an analyst or the platform concluded this is
not a genuine sub-provisioner), but only one of the two literal spellings
is checked. This is a **presentation-layer defect**, not a detection,
attribution, or walkback defect:

- Walkback correctly recorded GF7Y as the last wallet to send any lamports
  before CREATE (Phase 2/3) — this part of the reconstruction is accurate.
- Attribution correctly refused to confirm the lineage (`LINEAGE_GAP` on
  100% of rows) — attribution is not overclaiming.
- `wt_discovered_subprovs` correctly recorded the dismissal
  (`state='dismissed'`) — the underlying data is not stale or wrong.
- **Discovery's rendering gate is the sole point of failure**: it fails to
  honor the `dismissed` state and displays a `SUBPROVISIONER_RESOLVED`
  "IDENTITY"-category node as if the candidate were still live, giving an
  unconfirmed, dismissed, dust-pattern wallet the visual weight of a
  resolved identity.

## Phase 8 — Cross-platform consistency audit

| Page/section | GF7Y description | Consistent? |
|---|---|---|
| Discovery (`SUBPROVISIONER_RESOLVED` node) | Rendered as a live sub-provisioner (state gate miss, Phase 7) | **No** — should reflect `dismissed` |
| `wt_discovered_subprovs` (backing data) | `state='dismissed'` | Correct, but not surfaced |
| `wt_unknown_infrastructure_registry` | `terminal_entity_type='INFRASTRUCTURE'`, `eligible=1`, evidence_json embeds `"state":"dismissed"` verbatim | Internally **self-contradictory**: eligible=1 despite its own embedded evidence recording dismissal |
| `wt_attribution_outcomes` | 149 rows, `terminal_entity=GF7Y` (not independently re-checked per-row in this audit, but consistent with `LINEAGE_GAP` volume above) | Not evaluated for internal consistency in this pass |
| Dust registries (`wt_dust_*`) | No rows at all for GF7Y | N/A — this wallet was simply never processed by the dust pipeline |

No page currently asserts "GF7Y is a confirmed dust marker" (since no such
record exists to assert), so there is no direct textual contradiction
between "dust" and "sub-provisioner" labels on any single page today.
The real inconsistency is narrower but still real: **the Unknown
Infrastructure registry's own persisted evidence blob already states
`"state":"dismissed"` while the same row is flagged `eligible=1`** — an
internal contradiction within a single table, independent of the Discovery
rendering bug.

## Root Cause

Two independent, compounding defects, both in **presentation/eligibility
logic, not detection/attribution**:

1. `src/discovery/service.py:442` — the `sp_rejected` gate checks only for
   states starting with `"REJECTED"`, missing the `"dismissed"` state
   produced by the manual dismiss action, so a human-dismissed candidate
   is rendered as a resolved identity.
2. `wt_unknown_infrastructure_registry.eligible` is not re-evaluated
   against the current `wt_discovered_subprovs.state` at write time — the
   row was marked `eligible=1` and its own `evidence_json` snapshot
   already contains `"state":"dismissed"`, meaning the eligibility flag
   was never revisited after the dismissal.

Neither defect involves the walkback reconstruction itself (which
correctly identified GF7Y as the literal last funder) nor the attribution
classifier (which correctly refused to confirm the lineage). The dust
classification premise in the brief could not be corroborated from any
persisted platform table, though the on-chain transaction shape (1
transaction, 10 recipients, 1-3 lamports each) is independently consistent
with dust-broadcast behavior.

## Recommendation

(Not implemented in this sprint per the "investigation only" scope.)

- Extend the Phase 7 gate to treat `dismissed` (and any other
  analyst-rejection terminology already in use, e.g. via a shared
  constant) equivalently to `REJECTED*` when deciding whether to render
  `SUBPROVISIONER_RESOLVED`.
- Re-evaluate `wt_unknown_infrastructure_registry.eligible` whenever the
  backing `wt_discovered_subprovs.state` changes, rather than leaving a
  stale `eligible=1` alongside evidence that already records dismissal.
- Consider whether a `PLAIN_XFER` transfer below a minimum lamport
  threshold (e.g. sub-rent-exempt amounts) should be excluded from
  sub-provisioner candidacy entirely, given 100% of this wallet's 103
  "funding" transfers rounded to exactly `0.0` SOL.
