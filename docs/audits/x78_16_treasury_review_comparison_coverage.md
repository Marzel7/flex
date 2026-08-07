# X78.16 — Treasury Review comparison coverage and analyst triage

Audit date: 7 August 2026

Final verdict: **E — MIXED**

Behaviour comparison before repair: **NOT IMPLEMENTED**

Behaviour comparison after repair: **PARTIAL / EVIDENCE LIMITED**

## Executive finding

Treasury Review did not contain a dimension comparator. `_operation_matches()`
performed one set intersection between candidate wallets and
`operator_entities`; every displayed dimension was then inferred from that
single overlap. It never loaded the observed topology shown immediately above
the comparison, never loaded transaction-derived funding mechanisms, never
loaded Operator matching profiles, and had no compatible behaviour model.

The template independently translated `matched=false` into **No match**, even
when all six states were UNKNOWN. This violated the semantic contract.

The source evidence is mixed: topology and explicit provisioning funding exist
for almost the entire queue; comparable provisioner-reuse behaviour exists for
multi-launch candidates; settlement does not exist in a compatible form.
Accordingly, the justified work is both a wiring repair and a bounded comparator
implementation. Missing evidence remains UNKNOWN.

## Frozen live state

Snapshot captured while the queue was receiving live candidates:

| Measure | Count/value |
|---|---:|
| Pending candidates | 1,963 |
| Newest | `8V37xc…p8gL`, 7 Aug 2026 16:51:27 UTC |
| Oldest | `Ef132N…GtvK`, 6 Jul 2026 17:03:38 UTC |
| Launch arrays | 1,963 |
| Creator arrays | 1,963 |
| Provisioning-wallet arrays | 1,963 |
| Evidence-backed topology | 1,963 |
| Explicit funding mechanisms usable by comparator | 1,795 |
| Comparable multi-launch reuse behaviour | 108 |
| Settlement evidence | 0 |
| Exact confirmed-identity account overlap | 3 |

The legacy scalar behaviour fields (`transfer_pct`, `out_sol`, `recipients`,
`micro_pings`) had at least one value in 1,931 rows, but these fields do not
share a semantic model with Operator Behaviour Intelligence and were therefore
not treated as comparable behaviour.

## Before distribution

| Operation / dimension | MATCH/Exact | PARTIAL | NO_MATCH | UNKNOWN |
|---|---:|---:|---:|---:|
| WATCHTOWER Behaviour | 0 | 0 | 0 | 1,963 |
| WATCHTOWER Funding | 0 | 2 | 0 | 1,961 |
| WATCHTOWER Provisioning | 0 | 0 | 0 | 1,963 |
| WATCHTOWER Settlement | 0 | 0 | 0 | 1,963 |
| WATCHTOWER Topology | 0 | 2 | 0 | 1,961 |
| WATCHTOWER Treasury | 2 | 0 | 0 | 1,961 |
| 3SW2 Behaviour | 0 | 0 | 0 | 1,963 |
| 3SW2 Funding | 1 | 0 | 0 | 1,962 |
| 3SW2 Provisioning | 1 | 0 | 0 | 1,962 |
| 3SW2 Settlement | 0 | 0 | 0 | 1,963 |
| 3SW2 Topology | 0 | 1 | 0 | 1,962 |
| 3SW2 Treasury | 0 | 0 | 0 | 1,963 |

Overall, 1,961 WATCHTOWER and 1,962 3SW2 comparisons had zero evaluated
dimensions but rendered as **No match**.

## UNKNOWN root-cause census

| Dimension | Primary pre-repair cause | Exact failure |
|---|---|---|
| Behaviour | COMPARATOR_NOT_CALLED / COMPARATOR_MISSING | No candidate or reference behaviour was loaded; `wrap_close` plus account overlap was only a placeholder branch. |
| Funding | EVIDENCE_PROJECTION_MISSING | Candidate and Operator mechanisms existed in `wt_provisioning_edges` / `wt_watchtower_launches`, but the comparator used wallet overlap. |
| Provisioning | SCHEMA_MISMATCH | Display used arrays of provisioning wallets while comparison expected those addresses to be canonical Operator entities. |
| Settlement | UNSUPPORTED_COMPARISON | The state was hardcoded UNKNOWN; no compatible settlement input exists. |
| Topology | COMPARATOR_NOT_CALLED | `_observed_topology()` produced a role chain for presentation, but `_operation_matches()` never received or evaluated it. |
| Treasury | CANDIDATE_EVIDENCE_MISSING for identity; correct UNKNOWN otherwise | Exact canonical entity overlap was the only safe positive test. Non-overlap cannot prove contradiction. |

No UNKNOWN was converted to NO_MATCH merely to improve coverage.

## Evidence traces

### CiyEB — seven launches

Review persistence contains seven distinct mints, creators, subproviders and
funding signatures. `wt_provisioning_edges` contains seven signed, explicit
`CiyEB → subprovider` edges, each for 100 SOL and each labelled
`WSOL_WRAP_CLOSE`. No X78 session-root evidence is used.

After repair:

- WATCHTOWER: Behaviour MATCH, Funding MATCH, Provisioning MATCH, Topology
  MATCH, Settlement UNKNOWN, Treasury UNKNOWN → PARTIAL resemblance.
- 3SW2: Behaviour/Funding/Provisioning NO_MATCH, Topology PARTIAL, Settlement
  and Treasury UNKNOWN → limited PARTIAL structural overlap, explicitly 1/4
  evaluated dimensions aligned.

### Other representative candidates

| Candidate | Launches | WATCHTOWER | 3SW2 |
|---|---:|---|---|
| `37xt1…` | 5 | Behaviour/Funding/Provisioning/Topology MATCH | Behaviour/Funding/Provisioning NO_MATCH; Topology PARTIAL |
| `HFbe94…` | 4 | Funding/Provisioning/Topology MATCH; Behaviour NO_MATCH | Behaviour/Funding MATCH; Topology PARTIAL; Provisioning NO_MATCH |
| `77wic…` | 3 | Funding/Provisioning/Topology MATCH; Behaviour NO_MATCH | Funding MATCH; Topology PARTIAL; Behaviour/Provisioning NO_MATCH |
| `EM11y…` | 1 | structural PARTIAL | explicit 3SW2 client overlap; strongest MATCH triage control |

At no point does similarity link, approve, expand, reject, or reclassify a
candidate.

## Confirmed Operation reference profiles

### WATCHTOWER

- Canonical reference: 69 TREASURY entities.
- Operational launch ledger: 176 launches, 176 creators, 156 provisioners.
- Funding mechanisms: WSOL_WRAP_CLOSE, SEEDED_ACCOUNT_CLOSE and PLAIN_XFER.
- Declared topology: Treasury → Subprovider → Creator → Launch.
- Comparable behaviour: rotating provisioners (156/176).
- Settlement: no compatible Treasury Review comparison source.

### 3SW2

- Canonical reference: one CLIENT entity with 13 evidence observations.
- Explicit provisioning edges: 13 launches/creators from the persistent client.
- Funding mechanism: PLAIN_XFER.
- Declared identity model: persistent client/controller reuse, not WATCHTOWER's
  treasury/subprovider identity.
- Comparable behaviour: persistent provisioner (1/13).
- Settlement: no compatible comparison source.

Treasury Review now consumes the same declared Operation matching profiles used
by Discovery, plus the persisted transaction edges used by operational profiles.
It does not consume unsafe historical sessions.

## Comparator semantics

Each result now exposes:

- `evaluated_dimensions`
- `matched_dimensions`
- `partial_dimensions`
- `contradicted_dimensions`
- `unknown_dimensions`

Overall semantics:

- zero evaluated → NOT_EVALUATED;
- explicit canonical account overlap plus alignment → MATCH;
- evidence alignment without identity overlap → PARTIAL;
- evaluated evidence with no alignment → NO_MATCH.

`matched` retains its narrow account-overlap meaning for legacy governance
recommendation code. Structural similarity cannot silently become governance.

## After distribution

| Operation / dimension | MATCH | PARTIAL | NO_MATCH | UNKNOWN |
|---|---:|---:|---:|---:|
| WATCHTOWER Behaviour | 84 | 0 | 24 | 1,855 |
| WATCHTOWER Funding | 1,795 | 0 | 0 | 168 |
| WATCHTOWER Provisioning | 1,963 | 0 | 0 | 0 |
| WATCHTOWER Settlement | 0 | 0 | 0 | 1,963 |
| WATCHTOWER Topology | 1,963 | 0 | 0 | 0 |
| WATCHTOWER Treasury | 2 | 0 | 0 | 1,961 |
| 3SW2 Behaviour | 7 | 0 | 101 | 1,855 |
| 3SW2 Funding | 1,190 | 0 | 605 | 168 |
| 3SW2 Provisioning | 0 | 0 | 1,963 | 0 |
| 3SW2 Settlement | 0 | 0 | 0 | 1,963 |
| 3SW2 Topology | 0 | 1,963 | 0 | 0 |
| 3SW2 Treasury | 0 | 1 | 0 | 1,962 |

Overall operation results:

- WATCHTOWER: 2 MATCH, 1,961 PARTIAL.
- 3SW2: 1 MATCH, 1,962 PARTIAL.

PARTIAL explicitly means evaluated resemblance, not common identity.

## Queue and performance

Actionable-first groups after repair:

| Group | Count |
|---|---:|
| Confirmed Operation comparison found | 3 |
| Partial Operation comparison found | 1,797 |
| Multiple evaluated dimensions align | 163 |
| Evaluated, no alignment | 0 |
| No comparable evidence | 0 |

The first three rows are the explicit-overlap controls `9gv9v…`, `4231K…`,
and `EM11y…`. Within every group ordering is newest evidence first with treasury
address as the deterministic final tie-breaker.

Before: 100 cards, 380,340-byte API payload, approximately 1.51 seconds, and
100 initial DOM cards. After: 20 cards per page with incremental “Load 20 more.”
Full-queue comparison and sort measured approximately 0.99 seconds; only the
requested page is serialized and rendered.

## Post-X78 integrity and governance safety

- Funding comparison reads signed `wt_provisioning_edges` and the WATCHTOWER
  launch ledger, never historical `treasury_wallet` session labels.
- No positive-delta, co-occurrence, temporal allocation, or inherited-root
  evidence is admitted.
- Settlement remains UNKNOWN.
- No canonical Operator, review status, attribution, reconciliation, resolver,
  or governance record is mutated by comparison.
- Existing governance POST actions remain explicit human actions with analyst
  and reason metadata.

The defect was not primarily visual. Existing trustworthy evidence was
disconnected, the comparator was incomplete, and the UI then misrepresented
non-evaluation as NO MATCH.
