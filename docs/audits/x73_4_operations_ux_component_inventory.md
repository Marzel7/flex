# X73.4 Operations Intelligence UX Component Inventory

## Decision

The analyst UX has two primary operational pages:

1. `/intelligence/operations` — Investigation workspace owned by Evidence Reconciliation.
2. `/intelligence/operators` — governance registry owned by canonical Operator Identity.

`/intelligence/operator-promotions` is retained as a compatibility URL and redirects to
`/intelligence/operations?focus=review`. Its APIs remain unchanged because they are the
durable, fingerprint-bound decision mechanism used by the Investigation Population profile.

## `/intelligence/operations`

| Component | Purpose | Owner | Duplicate/obsolete | Decision |
|---|---|---|---|---|
| Reconciliation KPI strip | Counts launches by reconciled disposition | Evidence Reconciliation | No | Retain |
| Confirmed Operations | Find and understand confirmed identities | Reconciliation presentation | Representation overlaps Registry, but purpose differs | Retain; link to intelligence records only |
| Investigation Populations | Surface unresolved and promotion-ready bounded populations | Investigation Population + reconciliation package | Replaces Emerging Operator and standalone proposal queues | Retain; merge candidates into this section |
| Review | Surface contradictory evidence | Evidence Reconciliation | Replaces Review Candidate list on promotion page | Retain |
| Infrastructure | Surface shared-service findings without operator inference | Evidence Reconciliation | No | Retain |
| Evidence badges and counts | Rapid triage | Evidence Reconciliation | Detailed evidence exists on profile | Retain compact form |
| Governance actions | Maintain canonical identity | Operator Identity Governance | Wrong owner on this page | Exclude |

## Investigation Population profile

| Component | Purpose | Owner | Duplicate/obsolete | Decision |
|---|---|---|---|---|
| Promotion Readiness | Explain eligibility and blockers | Evidence Reconciliation Package | Replaces Identity % | Retain |
| Supporting/contradictory/missing evidence | Explain disposition | Evidence Semantics | Replaces wallet-overlap-only proposal explanation | Retain |
| Create Candidate / Review / Confirm | Produce canonical identity from a bound proposal | Promotion Service API | Standalone page duplicated the workflow | Move here |
| Current Operator | Resolve resulting canonical identity | Operator Registry | Previously implicit | Add |
| Parent Investigation | Preserve originating population | Investigation Population | Previously implicit | Add |
| Child Operator Identities | Preserve split/promotion lineage | Operator Identity | Previously implicit | Add |
| Related identity clusters | Show hypotheses without governance | Reconciliation presentation | No | Retain and relabel |
| Evidence links | Open package and API evidence | Evidence owner | Previously scattered | Add |
| Identity governance | Expand/merge/split/retire | Operator Registry | Wrong owner | Exclude |

## `/intelligence/operators`

| Component | Purpose | Owner | Duplicate/obsolete | Decision |
|---|---|---|---|---|
| Vertical identity list | Scalable canonical registry | Operator Identity | No | Retain |
| Identity and activity states | Separate attribution from operational activity | Identity Governance | No | Retain |
| Search and lifecycle filters | Find active and historical identities | Operator Registry | No | Retain |
| Identity profile | Govern, expand, review, merge, split, retire/reactivate | Identity Governance | No | Retain |
| Timeline, evidence and history | Permanent provenance | Immutable lifecycle history | No | Retain |
| Promotion queue entry point | Review investigation proposals | Investigation workflow | Duplicate | Remove; link back to Investigation workspace |

## `/intelligence/operator-promotions` obsolete component audit

| Legacy component | Former purpose | Current replacement | Decision |
|---|---|---|---|
| Identity % | Approximate identity strength | Promotion Readiness plus explicit blockers | Retire |
| Review Candidate | Queue weak identity proposals | Reconciled Review and Investigation Populations | Retire |
| Cross-operation wallet overlap | Explain identity proposal | Applicable, provenance-aware evidence package | Merge into profile evidence |
| Operation Alpha / proposal display name | Name a projected proposal | Stable Investigation Population and canonical Operator Identity | Retire |
| Promotion Proposal list | Select proposals | Investigation Population cards and profiles | Retire |
| Proposal fingerprints | Prevent stale decisions | Hidden request binding on profile confirmation | Preserve in API, remove from primary UI |
| Approve | Create canonical Operator Identity | Confirm Operation Identity on eligible population profile | Move |
| Reject / Defer | Record noncanonical proposal decision | Reconciled Review workflow; compatibility API remains | Retire from primary UI |
| Review history | Audit prior decisions | Population evidence history and Operator Identity history | Merge |

## Compatibility

- All `/api/operators/promotions*` endpoints are preserved unchanged.
- `/intelligence/operator-promotions` redirects rather than returning 404.
- `/intelligence/emerging-operators` remains an alias of `/intelligence/operations`.
- Existing Investigation Population, canonical Operator, Discovery, and Walkback URLs remain valid.
- Reconciliation, attribution, evidence, and resolver logic are unchanged.
