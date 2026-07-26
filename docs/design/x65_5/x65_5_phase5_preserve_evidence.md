# X65.5 — Phase 5: Preserve Existing Evidence

## Principle

The canonical bucket is an **aggregation layer above** the existing
five dimensions, never a replacement for any of them. Every field
Discovery currently shows for a launch continues to be shown,
unchanged, when that launch is viewed inside the WATCHTOWER
Provisioning bucket.

## Per-launch row, inside the bucket — required fields (unchanged from today)

| Field | Source (unchanged) |
|---|---|
| Creator Identity | `src/ops/creator_identity.py` (X64.8/X64.9) |
| Operational Topology | `src/ops/funding_topology.py` (X29.1, terminology reviewed in Phase 6 but the underlying classifier and its evidence are unchanged) |
| Treasury | `src/ops/treasury_resolution.py` (X65.1) — both the resolution status AND the underlying evidence path (subprov, wrap signature, etc.) |
| Funding Origin | Same source as today (X65.1 Phase 1: equivalent to Topology for the relevant population; unchanged) |
| Operation Attribution | `wt_ops_v2_wallets.operation_uuid`, joined via confirmed treasury (X65.1) — unchanged, including the `__UNASSIGNED__` case |
| Confidence | Both the existing per-dimension confidence values (e.g. Treasury Resolution's `0.95` confirmed-treasury score) AND the new bucket-level Operational Confidence tier (Phase 3: High/Medium/Baseline) are shown side by side, not merged into one number |

## Explicit non-goals

- The bucket does not recompute, override, or hide any existing
  dimension's value. A launch that is `Topology=UNKNOWN` today remains
  `Topology=UNKNOWN` when viewed inside the WATCHTOWER Provisioning
  bucket — the bucket's own membership test (Phase 3) is independent of
  the Topology field precisely so that Topology's own, separately
  known limitations (X65.4) are never silently "fixed" by hiding them
  inside a new aggregate label.
- The bucket does not change Operation Attribution's `__UNASSIGNED__`
  semantics — a launch can be a full, high-confidence member of
  WATCHTOWER Provisioning while still correctly showing
  `__UNASSIGNED__` for Operation, if its treasury has not been
  confirmed and linked to an operation UUID. This is the same
  discipline X65.1 already established (never force-assign an
  operation without a confirmed treasury) — the new bucket does not
  weaken it.
- No existing API response field is removed. The bucket is additive:
  a new `watchtower_provisioning` (or similarly named) field/object is
  proposed to be added to each launch record and to the aggregate
  response, alongside all currently-returned fields — not implemented
  in this task, per its "no implementation" constraint, but explicitly
  scoped this way so a future implementation cannot reasonably
  interpret this design as license to drop or restructure existing
  fields.

## Why this matters operationally

Per this project's own recurring lesson (X65.0's explicit task
constraint, X65.1's explicit constraint, X64.9's over-permissive-cleanup
correction) — every prior investigation in this arc has been careful
to add new classification without weakening or silently replacing
existing, already-validated evidence. This phase makes that same
discipline explicit for X65.5: the canonical bucket succeeds only if
an analyst who already trusts today's Creator Identity/Topology/
Treasury/Operation fields can continue to rely on them exactly as
before, with the new bucket adding a campaign-level view on top rather
than asking them to trust a new, opaque aggregate instead.
