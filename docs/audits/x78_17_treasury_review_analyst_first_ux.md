# X78.17 Treasury Review analyst-first UX validation

## Scope and semantic freeze

This milestone changes only `templates/treasury_review.html`. The X78.16
workspace, comparison profiles, evidence selection and governance handlers are
unchanged. Representative live payloads were captured before presentation work
and checked again after it. Overall state, evaluated/matched/partial/
contradicted/unknown dimensions and `recommended_action` were unchanged.

## Before

Every candidate rendered standard topology, two equally prominent six-dimension
comparisons, a recommendation panel, all six governance buttons and detailed
evidence affordances at the same visual level. UNKNOWN dimensions repeated as
full comparison rows. Twenty cards were loaded, but the visible hierarchy read
like an evidence report rather than a decision queue.

## After

Each candidate has three compact bands:

1. Identity, strongest comparison state, updated age, destination and compact
   launch/creator/provisioner metrics.
2. The strongest Operation comparison first, evaluated dimensions only, an
   alignment sentence, muted unevaluated count, and compact secondary Operation
   comparisons. Standard topology is omitted when it merely repeats a topology
   MATCH; divergent or partial topology remains visible.
3. The existing recommendation and exactly one primary action. All other
   governance actions remain under **More actions**. Supporting evidence remains
   collapsed and retains relationship examples, evidence signals and details.

PARTIAL is labelled **Partial resemblance** and never implies common identity.
Zero evaluated dimensions render once as **No comparable evidence yet**.

## Named controls

| Control | Header / primary comparison | Recommendation |
|---|---|---|
| `9gv9v…` | WATCHTOWER MATCH; 3SW2 compact PARTIAL | Link to WATCHTOWER |
| `4231…` | WATCHTOWER MATCH; unevaluated dimensions muted | Link to WATCHTOWER |
| `EM11y…` | 3SW2 MATCH; WATCHTOWER compact PARTIAL | Expand 3SW2 |
| `CiyEB…` | WATCHTOWER PARTIAL RESEMBLANCE; Behaviour, Funding, Provisioning and Topology MATCH; Settlement and Treasury shown as `+2 not evaluated` | Create Investigation |
| zero-evaluated fixture | No comparable evidence yet; no UNKNOWN grid | Existing recommendation retained |

For CiyEB, the unchanged API payload remains four WATCHTOWER matches, two
unknown dimensions, and a separate 3SW2 partial comparison with one partial,
three contradictions and two unknown dimensions.

## Governance regression

All existing actions remain available and continue through the same POST route:

- Approve Treasury
- Link to Existing Operator
- Create Operator Candidate
- Create Investigation
- Needs More Evidence
- Reject Treasury

Analyst and reason prompts remain mandatory. Link still requires a target
`operator_id`. No action is invoked automatically.

## Pagination and performance

- Global order: `ACTIONABLE_FIRST_THEN_NEWEST_WITHIN_GROUP`
- Initial DOM cards: 20
- Increment: 20 through **Load 20 more**
- Live queue during validation: 1,971 candidates
- API payload: 75,207 bytes
- API response: 1.63 seconds
- HTML response: 57,043 bytes in 0.02 seconds

The backend comparison cost and API payload are unchanged. Collapsed DOM
complexity is lower because UNKNOWN rows, standard topology, detailed evidence
and five secondary actions are no longer all visible by default.

## Final visual review

At 1440 px, four candidates are substantially visible in the first 1,400 px.
The strongest match and recommended action are readable from opposite sides of
each card without reading the comparison body. MATCH is green and explicit;
PARTIAL RESEMBLANCE is amber and explicit. Candidate boundaries have increased
inter-card spacing while internal sections use tighter spacing. The responsive
layout stacks comparison and governance below 800 px.
