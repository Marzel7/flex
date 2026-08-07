# X78.18 Treasury Review canonical-identity exclusion audit

## Verdict

**E — MULTIPLE CAUSES:** two stale mutable review rows, promotion paths that
bypassed review resolution, a pending query with no canonical exclusion, and a
recommendation function that interpreted exact overlap without checking whether
the candidate itself was already governed.

The repair is a dynamic actionable-state projection plus a write-path safety
gate. Historical rows are not mutated and no history is fabricated.

## Named canonical verification

| Address | Authoritative treasury | Operator entity | Identity asset | Classification |
|---|---|---|---|---|
| `4231KLYipwRTmFdQ6ZBa1H4Jf3EpfF62Gzg6DWHWvhPZ` | `manual_override`, `MANUAL_OVERRIDE_X64_HOP2_EVIDENCE`, confirmed 2026-07-21 11:15:24 UTC | WATCHTOWER `TREASURY`, added 11:18:40 | active WATCHTOWER `TREASURY`, X76.1 projection | CONFIRMED_TREASURY |
| `9gv9vLtP1WxM7hgMbg4XsV6XLBNniKt8eFbbDqNcPDN1` | `manual_override`, `MANUAL_OVERRIDE_X65_USER_REQUEST`, confirmed 2026-07-24 15:32:42 UTC | WATCHTOWER `TREASURY`, added 15:41:46 | active WATCHTOWER `TREASURY`, X76.1 projection | CONFIRMED_TREASURY |

Neither address has a direct `attribution_evidence` or
`wt_attribution_outcomes` row. Their canonical status is nevertheless explicit
in the authoritative confirmed-treasury registry and both canonical identity
projections; it is not inferred from X78.16 comparison output.

`EM11ygA9txKF36WCUitFFkkcJ1muvSebgVqRPZojb1z3` is **not canonical**. Its
candidate address is absent from canonical membership. The 3SW2 MATCH is caused
by a downstream client overlap (`3SW2zqu…`), so EM11 remains actionable.

## Review rows and lifecycle timelines

The review table uses treasury address as its primary key; it has no separate
row ID or updated-at column.

### 4231

- Candidate created by `walkback_hop2`: 2026-07-21 11:11:58 UTC.
- Status remains `PENDING_REVIEW`; reviewer/reviewed-at are null.
- Manually confirmed 3 minutes 26 seconds later.
- Projected into WATCHTOWER `operator_entities` 3 minutes 16 seconds later.
- Projected into the identity asset/event ledger by X76.1 on 2026-08-05.
- Review actions: none. The X76.1 event records deterministic projection, not
  the original analyst decision.

### 9gv9v

- Candidate created by `walkback_hop2`: 2026-07-24 12:20:21 UTC; last walkback
  evidence 15:05:11.
- Status remains `PENDING_REVIEW`; reviewer/reviewed-at are null.
- Manually confirmed at 15:32:42.
- Projected into WATCHTOWER `operator_entities` at 15:41:46.
- Projected into the identity asset/event ledger by X76.1 on 2026-08-05.
- Review actions: none.

Both rows predate canonical promotion. No immutable review action contradicts
the mutable state; instead, the historical override path did not produce one.
Inventing an action or analyst reason now would be incorrect.

## Query and recommendation root cause

`list_review_workspace()` selected only by
`wt_treasury_review.status = 'PENDING_REVIEW'`. It did not exclude confirmed
treasuries, direct Operator entities, or active identity assets.

`_operation_matches()` correctly reported direct account overlap and Treasury
MATCH. `_governance_recommendation()` then treated every explicit match as a new
link/expansion decision. It had no canonical governance-state input. Thus a
state fact was misread as an unresolved action.

The X78.16 comparator remains unchanged. Comparison and governance state are
now explicitly separate:

- comparison: MATCH / PARTIAL / NO_MATCH / NOT_EVALUATED;
- governance: ACTIONABLE / ALREADY_CANONICAL.

## Full pending-queue census

Before repair, 1,971 physical `PENDING_REVIEW` rows existed. Exactly two
candidate addresses were already canonical:

| Operation | Confirmed treasury | Direct entity | Active asset | Total excluded |
|---|---:|---:|---:|---:|
| WATCHTOWER | 2 | 2 | 2 | 2 distinct addresses |
| 3SW2 | 0 | 0 | 0 | 0 |
| Other Operations | 0 | 0 | 0 | 0 |

There were no pending candidates whose own address was a confirmed client or
other direct canonical role. Downstream overlaps are not counted or excluded.

## Promotion-path audit

- `treasury_bank.promote_to_confirmed()` is coherent: it writes the confirmed
  treasury, changes the review row to `CONFIRMED`, aligns identity, and appends
  immutable review/attribution evidence.
- The legacy `treasury-promote` route calls that coherent path.
- The recovery-safe `treasury-approve` route directly writes the registry but
  explicitly changes the review row to `APPROVED` and writes audit history.
- Subprovider funder-link confirmation directly inserts a confirmed treasury
  and aligns identity without resolving a pre-existing review row.
- `auto_confirm_from_launch_chain()` directly confirms and aligns identity
  without resolving a pre-existing review row.
- Historical manual overrides/backfills can directly seed confirmed membership
  and reconcile identity without touching review state; this produced the two
  named cases.
- Identity governance expansion and reconciliation can project a direct entity
  or asset independently of the review lifecycle.

Dynamic exclusion is therefore safer and more complete than rewriting only the
two known rows or patching one promotion path. It covers every canonical source
without deleting or rewriting evidence.

## Repair

`_canonical_membership()` checks the candidate's own address, in order, against:

1. direct non-rejected `operator_entities` membership;
2. active `operator_identity_assets` membership;
3. authoritative `wt_confirmed_treasuries` membership (WATCHTOWER).

Ordinary `PENDING_REVIEW` projection excludes such rows. Explicit future states
such as `IDENTITY_CONFLICT`, `REOPENED`, or `GOVERNANCE_REVIEW` are not silently
created and are not affected by the exact pending-only exclusion.

Direct detail remains available and renders `ALREADY_CANONICAL` with
`Known WATCHTOWER Treasury` and no recommended action. `perform_action()` also
returns `ALREADY_CANONICAL` (HTTP 409) before any mutation, preventing duplicate
approve/link/candidate decisions.

## Before and after

- Physical pending review rows retained: **1,971**.
- Actionable Pending Treasury Review before: **1,971**.
- Actionable Pending Treasury Review after: **1,969**.
- Already-canonical rows excluded: **2**, both WATCHTOWER.
- Rows reopened for contradiction: **0**.
- Historical rows deleted or rewritten: **0**.

The live listener continued ingesting during final validation, moving the
physical/actionable counts to 1,972/1,970; the exclusion delta remained exactly
two. Warm response samples were 0.93–1.72 seconds, within the X78.17 range.

The first actionable row is now EM11/3SW2 expansion evidence, followed by
genuine partial-resemblance investigations. The first 20 contain no
`ALREADY_CANONICAL` item; pagination remains global actionable-first with 20
rows per page.

## Registry, comparison, and governance regression

- Registry truth and Pending Treasury Review no longer contradict each other.
- EM11 remains because downstream canonical overlap is not direct candidate
  membership.
- Unresolved and partial-resemblance candidates remain.
- Rejected rows remain excluded by their existing status.
- No automatic link, approval, demotion, attribution, reconciliation, or
  resolver change was introduced.
- X78.16 comparison states and evidence remain unchanged.
- Historical review and identity event records remain intact.
