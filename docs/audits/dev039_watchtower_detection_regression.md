# DEV039 — October 8 Watchtower regression audit

Verdict: PARTIAL — no post–October 8 detection regression proven. A legacy
cached-label confirmation weakness is already present in the frozen source.
Full five-stage positive-control parity and exact loaded-source identity remain
unproven. No implementation, replay, rollback or deployment was performed.

## Authority and isolation

- Audit branch: `codex/dev039-watchtower-regression-audit`.
- Frozen reference: `064021bd7e3aef0dec2e55d067693d94b8cf1922`.
- The freeze is a documentation commit, not one production executable SHA.
- Frozen manifests: `watchtower_operational_baseline_20261008.v1.json` and
  `watchtower_operational_baseline_postfreeze_20261008.v1.json`.
- October 8 22:23:25Z observation: API `7e9c2db073a73edab349c078fa2759ef1ec879c1`;
  listener ancestor `bf1bdfc59b4acfed0338a493189d91d212bda50f` plus dirty source
  reproduced in `4c1549718d3b711912dacdfb8c990dc5767267c5`; cascade
  `26da06daba23608bd64d5b86cde1f92b24d3a98d`; bridge
  `a1b7cf2cef38e2baa9dc2480dc42ab5828b7a68d`; monitor worker
  `1c8022b53d10db168f90afabbed08cc646007912`; funding worker
  `6806d2b2268bd5240f52be184bfbde1642070f76`.
- That manifest does not establish an October 8 walkback or scheduler loaded SHA.
- Current observed listener PID22938 has the same ancestor and exact on-disk
  listener SHA256 `3aa1ecb12699cd43831902179d8945eb12597b11ec3950321df3217be9ad95cc`
  recorded October 8. This is not an in-memory byte attestation.
- Current walkback PID73413: `/Users/kevinkeaveney/Production/flex-walkback-dev002`,
  HEAD `afbe9e58b11eb2e4d616246da123ce31ab45700c` plus modified tracked and
  untracked admission components. No single clean executable revision can be claimed.
- Current scheduler PID66022: `77492d22497e31a5984a234e2bb1f5cd2fc10613`;
  its scheduler file has no diff against the frozen reference. Cascade HEAD
  remains `26da06daba23608bd64d5b86cde1f92b24d3a98d` (one untracked guard).
- Listener/walkback/scheduler effective OPS binding is the canonical
  `/Users/kevinkeaveney/Dev/claude/flex/database/wt_ops_v2.db`; core binding is
  `/Users/kevinkeaveney/Dev/claude/flex/database/flex_complete_database.db`.
- Walkback effective canonical predicate mode is `shadow`; the frozen integration
  already defaults to shadow and makes legacy decisions authoritative.

## Relevant contract and comparison

Accepted topology is Treasury → SubProvider → Funding Account → Creator → Launch.
An address label or common relay is not a substitute for an independently retained
role/edge. WSOL creator funding and qualified provisioning evidence are required
by the membership gate. Funding amounts alone do not prove identity or repeatability.

| Component | October 8 versus current evidence | Impact |
|---|---|---|
| Listener | Exact on-disk listener hash matches recorded deployment | No listener-source regression demonstrated |
| Rejected SubProvider gate | `_is_known_subprov` AST identical; excludes REJECTED states | F7p3's rejected registry row must not qualify here |
| Ledger cache | `_cached_watchtower_treasury` AST identical; takes latest non-null treasury label without route/mechanism qualification | Cached label can satisfy separate confirmation branch |
| Confirmation branch | Both sources use `known_subprov OR cached_treasury`, then prefer the discovered-subprov row's treasury | Rejected row can be reused when cache is nonempty; not proof of upstream control |
| Treasury test / handoff mechanism gate | `_is_known_treasury` and `_is_watchtower_like_handoff` AST identical | No demonstrated change in these gates |
| Canonical predicate/adapters/integration | No file diff against frozen source; runtime is shadow | Predicate is not an enforced replacement for legacy confirmation |
| Membership gate | Old strict session/queue treasury equality; current role-normalized validation, explicit rejection telemetry, membership/outbox seam | Material source change, but both reject these PLAIN_XFER routes; no demonstrated causal false-positive regression |
| Funding decoder | Current deep_walkback differs (79 lines in diff); no replay performed | No decoder causality or positive-control parity claimed |
| Cascade | Current older pinned source differs in schema initialization and armed-path handling from aggregate freeze | Source-composition difference, not evidence of a post-freeze deployment regression |
| Scheduler | Relevant scheduler source unchanged against freeze | No scheduler-source regression demonstrated |

Current on-disk walkback SHA256:
`a45ccc0db0a34269b8811975fef1b9297a27acad745074a3674a11f8f771e5b9`.
Registry-promotion SHA256:
`cd5508da7d20b76a0770eaa44aaa4d694d715e3612c7426a4f850d9b20d28370`.
Deep-walkback SHA256:
`9913a0db3e056a301b88e60308b00657e513c1fd8c14c7f3f21b0079739a468f`.

## Three October 14 negative controls

- `Ei6z97gFFVqfoMu4oswbSViBuBSh4fJDrd2ZMpSMpump`
- `CjtwB9XL59Y8sgApovHGrryzUHYxTE33fZnSLLsKpump`
- `GzDuFXiViWWN6ELoDRGH5QV6HFfjrmy69oxhmdiGpump`

Prior identity-specific retained audit established direct PLAIN_XFER from
`F7p3dFrjRTbtRp8FRF6qHLomXbKRBzpvBLjtQcfcgmNe`, null session treasury,
WATCHTOWER_CONFIRMED queue labels, and durable FUNDING_MECHANISM_MISMATCH rejection.
No canonical membership or separate Funding Account was established.

Frozen infra_mapping identifies F7p3 as Relay.link Solver, category relay.
Frozen infra_mapping labels `69SNcRC8NqjHBSXEcugCN5oFKRQoKmddmWzZYc3tqtxk`
as Active Trading/suspicious, not affirmative Watchtower treasury qualification.
The canonical DB nevertheless has an inherited CONFIRMED_SEED treasury label;
it must not be promoted into transaction-backed accepted authority.

Fresh bounded ledger reads found pre–October 8 PLAIN_XFER records associating the
same F7p3/69SN pair: `JEFc63CmYWfM3HgxDv5Q3Z9Q59vbr387JgVCjmnopump`,
`FEF4DTeH8YAsYGzYmEgoWKhyeU5WFk9YzdWw64V2pump`, and
`3V94P3hjY1XKnYJnCjPrSSLzBiY8DD1UHnSKsYqqpump`.
This establishes that the retained label contamination predates the requested
boundary, not a reconstructed October 8 database snapshot.

Static conclusion: with the retained nonempty cache and rejected SubProvider row,
the frozen shortcut can produce the same incorrect legacy confirmation. Its
separate membership gate rejects plain transfers. This is not an executed replay
or proof of the exact branch taken by the historical process. The cache shortcut
was introduced by `26e528b0ae1fa5ed465c4fe9059a259d903fef29` (August 25), not a
proven post–October 8 regression commit.

## Positive controls and missing detections: evidence limits

Bounded reads selected two preserved control-fixture identities:
`AwXtJ4QsZwHwD84C4WtJfhAAiF9FBFisMwry1LSLpump` and
`EQ6qQsweDhsdYqbYMv4J2dTZwEMvnETQH9rQHr9fpump`.
They retain distinct provisioning wallets/Funding Accounts
`7FqyKpX3Qdtx3xaH8sVtU5Z7e7m5oP9DNq6P93iNVD6R` and
`CVmmf13hBGwUfPwp3EvGy6k1hB6fSQXvZkwsmxTK1UKa`, respectively, with
PERSISTED_SIGNATURE_DECODE WSOL_WRAP_CLOSE evidence. Their queue mechanism is
null, no provisioning session was found, and neither currently has membership.
They are incomplete five-stage controls; no classification-equivalence PASS.

Latest observed membership remains
`E8bCunY5FYCtpmCeL1oCWgKLeFxA9z4oMNBe9MLVpump`, assigned
2026-10-10T12:27:40Z. It has WSOL queue/session funding but queue/session treasury
addresses differ and no separate provisioning-wallet row was found. It is the
latest recorded assignment, NOT a newly certified complete five-stage genuine
control. Do not erase its existing acceptance or infer a new failure from that gap.

An indexed query of the ten latest workflow candidates discovered after that
assignment returned none. This is not an exhaustive census of all queue, ledger
or detection paths and does not prove no genuine launches occurred. No newer
fully qualified missed candidate was established. Missing genuine detection,
attribution or admission regression remains INSUFFICIENT_EVIDENCE.

## Decision and safety

No post-freeze regression commit or first affected deployment is proven. The three
negative controls demonstrate legacy label/qualified-membership disagreement;
their membership gate prevented canonical contamination. This does not certify
the entire membership population uncontaminated.

Minimum compatible future correction to qualify separately: prevent excluded
infrastructure or unqualified ledger labels from bypassing route qualification
in the existing cache confirmation branch. Do not loosen admission, blanket-revert
production, rewrite history, or infer a missing funding hop. Full five-stage
controls and exact loaded-source/deployment evidence are prerequisites to claiming
regression parity. No change is authorized by this report.

Verification: static AST/source comparison, retained manifests, process cwd and
whitelisted non-secret environment, compact read-only SQLite queries with 1–2s
progress deadlines. No tests invoking acquisition or writers; no replay, per final
user instruction. Provider calls0; production changes0; DB/queue writes0; restarts0.
DEV037/research evidence not modified. Only this compact audit document and the
handoff are produced; no raw payload retention, database copies or unbounded growth.
New document design bound 50KB, zero unbounded storage paths.

RUNTIME_DEPLOYMENT_REQUIRED=false — read-only audit/documentation only.
NEXT_ACTION: Stop. Present pre-existing cache-trust defect and remaining evidence
gaps; require separate authorization for any correction or replay.
