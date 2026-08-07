# X78.13 — Transaction-First Historical Lineage Reconstruction

## Verdict

The additive transaction-first substrate is implemented and frozen independently of Operator identity and historical session roots. It proves that useful operational topology can be reconstructed from explicit transactions, but the currently retrievable historical corpus is far too incomplete to reassess canonical identity safely.

- Canonical recommendation: **D — Evidence coverage remains insufficient for canonical reassessment.**
- Historical-session recommendation: **QUARANTINE FROM LINEAGE.** Retain rows as historical context only; never let `treasury_wallet` manufacture a Tier-1 edge.
- Discovery recommendation: transaction-first populations should eventually become Discovery's primary structural input, after controlled historical acquisition raises coverage. Do not switch Discovery yet.
- Production impact: none. Canonical attribution, sessions, Walkback, Discovery, Registry, reconciliation, resolver and governance were not modified.

## 1. Launch census and acquisition budget

The reconstruction starts with every `token_analysis` launch, ordered by mint. It does not filter or prioritise by WATCHTOWER, 3SW2, an Investigation Population, Treasury Review, session root, or known treasury.

| Census item | Launches |
|---|---:|
| Historical launches | 1,596,895 |
| With creator | 1,562,163 |
| With creation signature | 1,539,324 |
| With creation timestamp after transaction enrichment | 1,567,926 |
| With source platform | 1,593,760 |
| With persisted walkback evidence | 3,849 |
| Without persisted walkback evidence | 1,593,046 |
| Eligible and queued for additional RPC | 1,529,992 |

Before retrieval, the minimum estimate was 3,059,984 RPC calls, 25.1 GB cache growth at 8 KB per transaction, and 34.0 hours at 25 calls/second. An uncontrolled multi-million-call loop would violate the milestone's RPC-safety requirement. The run therefore exhausted persisted evidence/cache first, wrote a deterministic acquisition queue for every eligible remainder, and executed no new RPC calls. Missing coverage is classified as unavailable, never contradicted.

## 2. Evidence model

The separate database is `database/transaction_first_lineage.db`. Its additive tables are prefixed `tf_` and cover runs, launch facts, transaction cache, directional edges, context observations, paths, populations, acquisition queue, canonical overlays and historical-session comparisons.

Evidence hierarchy:

1. Tier 1: explicit transaction direction (`sender → recipient`) with signature and block time.
2. Tier 2: independently curated identity/governance, applied only after graph freeze.
3. Tier 3: co-occurrence and behavioural context, persisted separately and unable to create edges.
4. Tier 4: inherited/session metadata, comparison-only and unable to create edges.

Supported explicit edge semantics are direct SOL transfer, account-creation funding, seeded-account closure, SPL transfer, controlled WSOL close to a distinct recipient, and other parsed explicit transfers. Positive balance deltas, self-close proceeds, trading gains, account co-occurrence and shared-pool presence do not create arrows.

Every persisted edge records sender, recipient, signature, block time, amount, asset, relationship/mechanism, program, launch and creator context, evidence source and RPC verification. Adjacent selected hops also record incoming/outgoing amount, time gap and amount difference. Amount similarity never creates an edge.

## 3. Frozen graph and coverage

| Result | Count |
|---|---:|
| Cached transactions replayed | 21,994 |
| Cache misses | 31 |
| Candidate relationship rows inspected | 18,271 |
| Verified launches | 3,560 |
| Launch contexts reconstructed | 3,849 |
| Explicit edges persisted | 14,062 |
| Context observations persisted separately | 22,006 |
| Exact one-hop paths | 1,776 |
| Exact two-hop paths | 395 |
| Exact multi-hop paths | 813 |
| No surviving path in represented corpus | 865 |
| Ecosystem launches with unavailable/pending evidence | 1,593,335 |
| Transaction-first populations | 891 |

All alleged ancestry is strictly chronological. Cycles and a parent edge occurring at or after its descendant edge are rejected. A root means only the oldest explicit source on the longest valid path; it is not a confirmed treasury or Operator.

## 4. Naturally emerging operational populations

Largest repeated structures in the currently available corpus include:

| Root | Launches | Creators | Direct children | Max depth | Interpretation |
|---|---:|---:|---:|---:|---|
| `5tzFkiK…` | 56 | 56 | 36 | 6 | Transactional root; infrastructure label is post-freeze context |
| `QVtWcAX…` | 41 | 41 | 41 | 8 | Clean-room repeated topology |
| `3hJX3p8…` | 36 | 36 | 36 | 5 | Rediscovered control |
| `4GFSMkZ…` | 20 | 20 | 20 | 8 | Rediscovered control |
| `ASTyf…` | 9 | 9 | 9 | 4 | Repeated topology |
| `FLipg…` | 8 | 8 | 8 | 5 | Repeated topology |

Other controls remain present through governed membership comparison: FJYr has 32/39 complete transaction paths, 7wPW 16/16, and Aksm 8/11. Their longest-path roots can fragment when an older explicit source is available, which is correct: identity labels do not truncate the graph at a familiar wallet.

Top clean-room structures with no current governed-object launch membership include `QVtWcAX…` (41 launches), `5F1seMK…` (6), `21MRDUo…` (5), `9rGRpP3…` (5), `38HGfTm…` (4), and `9dUXDdC…` (4). These are candidates for later investigation, not automatic Operations.

## 5. Canonical overlay — applied after freeze

### WATCHTOWER

| Comparison class | Launches |
|---|---:|
| Independently rediscovered, same canonical root | 0 |
| Independently rediscovered, different root | 1 |
| Partially rediscovered | 20 |
| Evidence unavailable | 155 |
| Transactionally contradicted | 0 |
| Total | 176 |

This is not a disproof of WATCHTOWER. Only 21 launches currently have any surviving upstream path and just one has a complete path; 155 lack adequate evidence. The single complete path reaches a different oldest explicit root.

All 15 canonical treasury families were compared. DchJ has 18 partial and 40 unavailable; Dtwi has one different-root and 16 unavailable; 69SN has two partial and five unavailable. Every other family is entirely unavailable in this corpus. None has a same-root complete reconstruction. No demotion is warranted at this coverage.

### 3SW2

Of 13 governed launches, 12 are partially reconstructed and one is evidence-unavailable. Four partial paths currently terminate at `3SW2zqu…`; no complete path independently establishes the governed Operation root. This is partial corroboration, not a clean-room canonical reproduction.

### Investigations and review

| Object | Governed launches | Complete | Partial | Unavailable |
|---|---:|---:|---:|---:|
| AaZk / B48k | 97 | 0 | 72 | 25 |
| FJYr | 39 | 32 | 0 | 7 |
| 7wPW | 16 | 16 | 0 | 0 |
| Aksm | 11 | 8 | 0 | 3 |
| 8QCV | 8 | 0 | 0 | 8 |
| C7Ha | 9 | 0 | 5 | 4 |
| 29dj | 2 | 1 | 1 | 0 |
| Hjf3 | 2 | 1 | 1 | 0 |
| 8dWc | 2 | 1 | 1 | 0 |

### Infrastructure

| Object | Governed launches | Complete | Partial | Unavailable |
|---|---:|---:|---:|---:|
| 5tzF | 204 | 121 | 17 | 66 |
| BmFd | 77 | 45 | 0 | 32 |
| A77H | 41 | 27 | 1 | 13 |
| iGdF | 28 | 17 | 2 | 9 |
| 8mow | 17 | 9 | 0 | 8 |

These comparisons measure transaction-path availability for governed launch membership. They do not assign, confirm or remove infrastructure identity.

## 6. Named controls

### 69SN

`69SNcRC…` does **not** naturally emerge as a repeated transaction-first root or population from the available launch-started corpus. It has zero transaction-first population launches, creators, direct children or complete launch paths. Its historical WATCHTOWER overlay is two partial launches and five unavailable launches.

The known positive chain remains transaction-valid as an independent parser control:

`69SN → 9St6 → 8CEy → Bvv4 → 5tzF`

Each of its four arrows was found in its own signed transaction. That proves those four transfers, not other 69SN ancestry and not a 69SN-to-launch population. The chain was never used to seed discovery.

### CiyEB

`CiyEB6HX…` does not emerge in the currently persisted walkback corpus. Its historical launch evidence remains queued/unavailable; it is not contradicted. A later controlled acquisition run must start from its launches and independently recover explicit provisioning paths before it can become a transaction-first population.

### Negative controls

WSOL self-close, trading net gain, passive swap gain, reverse transfer and mere co-signing produced no false claimed edge. The known 69SN chain produced all four expected explicit edges.

## 7. Historical sessions

Post-freeze comparison of `wt_active_subprov_sessions` found:

| Class | Sessions |
|---|---:|
| Correct direct relationship | 28 |
| Incorrect inherited ancestry | 7 |
| Unverifiable historical ancestry | 216,112 |

No session row was changed. The overwhelming unverifiable share and confirmed inherited errors justify **QUARANTINE FROM LINEAGE**: sessions may remain context but must not manufacture Tier-1 ancestry.

## 8. Determinism, performance and safety

Two complete post-fix shadow rebuilds produced the same value digest:

`8fa832a2d3111a8baf23781df2a3d0a812b556cc0f03e58dba5edeb093b0810b`

Both produced 14,062 edges, 3,849 path rows and 891 populations, with no duplicate primary keys. The final run took 52.395 seconds, including 4.324 seconds graph construction and 46.136 seconds measured database work/overlays. Peak deterministic batch size was 2,000. RPC calls were zero and cache hits were 21,994.

Regression controls prove changing an Operator label, canonical identity input, Treasury Review decision, or stored session root cannot change the graph digest. Canonical and Registry data are reachable only through post-freeze overlay functions.

## 9. What actually exists

The available transaction corpus proves 14,062 explicit edges and 813 multi-hop launch paths. It naturally produces repeated structures led by 5tzF, QVtW, 3hJX and 4GFS, plus hundreds of smaller populations. It does not naturally produce 69SN or CiyEB as launch populations in current coverage. Only one WATCHTOWER launch is completely reconstructed and it reaches a different root; 20 are partial and 155 remain unavailable.

Therefore X78.13 establishes the safe reconstruction substrate and the acquisition backlog, but it does not establish a substantially complete historical graph. A later bounded acquisition milestone should process `tf_acquisition_queue` with explicit call/storage budgets, checkpointed caching and resume support. Only after coverage materially improves should governance reassess canonical Operators.
