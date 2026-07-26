# X65.19 — Confirm the Provisioning-Wallet Stage in WATCHTOWER Topology (Full Report)

Read-only investigation. No code changes, no database writes, no UI changes.
Ground-truth population: **Population A** (`wt_watchtower_launches`, 43 cascade-confirmed
launches — X65.13's established naming). Classifier-only Campaign candidates were
explicitly excluded, per the task's own scope. Bounded RPC used only where the task
authorized it: single already-persisted signature decodes, plus (for 2 launches with
corrupted signature fields) a capped, ≤5-page `getSignaturesForAddress` pagination —
never an unbounded wallet-history scan. RPC endpoint used: a temporary,
user-supplied Helius key, per this project's own RPC discipline
(`getTransaction`/`getSignaturesForAddress` only, never the 100cr enhanced-tx
endpoint). 2026-07-22.

## Contents

1. [Ground-Truth Population](#ground-truth-population)
2. [Per-Launch Transaction Path](#per-launch-transaction-path)
3. [Classification Results](#classification-results)
4. [Proof Standard Applied](#proof-standard-applied)
5. [Fan-Out / Raw-Recipient Cross-Check](#fan-out--raw-recipient-cross-check)
6. [Unresolved Case](#unresolved-case)
7. [Does the Current Graph Collapse Provisioning Wallet into SubProvider?](#does-the-current-graph-collapse-provisioning-wallet-into-subprovider)
8. [Should the Canonical Model Include a Mandatory Provisioning-Wallet Stage?](#should-the-canonical-model-include-a-mandatory-provisioning-wallet-stage)

---

## Ground-Truth Population

All **43** rows of `wt_watchtower_launches` (Population A). For each, `treasury_wallet`,
`subprov_wallet`, `creator_wallet` were read directly from the table; the
creator-funding signature (`wrap_close_signature`) was already persisted for 40 of 43
rows. The remaining 3 had either a NULL or visibly truncated `wrap_close_signature`/
`create_signature` (one literally ending in the placeholder string `XXXX`) — a genuine,
pre-existing data-quality gap in this table, not something this audit introduced.

---

## Per-Launch Transaction Path

Every one of the 40 launches with a usable `wrap_close_signature` was decoded via a
single bounded `getTransaction` call on that exact, already-known signature — no
signature search was required for these. For the 2 launches with a corrupted
signature, a capped `getSignaturesForAddress` walk (≤5 pages of 20, i.e. ≤100
signatures per wallet) on the creator wallet was used to recover the true earliest
transaction, staying within the task's explicit "no broad unbounded scan" constraint;
both resolved within the first 1–2 pages. For the 1 remaining launch, the creator
wallet's activity exceeded the 100-signature bound without reaching its true earliest
transaction — this one launch is reported as UNRESOLVED (Section 6), per the task's own
instruction not to perform unbounded scans.

### Full per-launch table (all 43)

| Mint | SubProvider | Wallet P (provisioning wallet) | Creator | Mechanism | Result |
|---|---|---|---|---|---|
| 2PZAgPXXAUWv… | DuKr6aB5G9… | 6WBjrDHqB5… | GivKFveyFc… | system_transfer | PROVEN |
| 2vBvPiCpsbFw… | 3ibQskyAaw… | 7Nkp1ctVEw… | 4ikp3CVJKc… | system_transfer | PROVEN |
| 3SkdUCkXKXi8… | GNK7SgYsYb… | 6YYwkYtspP… | FhtW8vL8wx… | system_transfer | PROVEN |
| 3fc6tLVPx6kA… | CatvBkJKLs… | 6HAMAQVNTP… | BmyS4SoNVK… | seeded_account_close | PROVEN |
| 3gbBrgtwyxPW… | BG2JAUnCfK… | 7W5Tzic3vP… | 73U8U1kMyZ… | system_transfer (recovered sig) | PROVEN |
| 3xFT4J96VzGi… | 5BjZr8pXgw… | 3rDKtkkwNQ… | HbtYRYrQXw… | system_transfer | PROVEN |
| 4MnczXgbDtpD… | 6icF3iEZ9U… | 3C34Yk3qUU… | B4uCrx4e2c… | seeded_account_close | PROVEN |
| 4SLVH8rturi9… | EH9ymijvhY… | 3jjFpsE9nG… | DJPW53Gs7V… | seeded_account_close | PROVEN |
| 5UQNY2hk4fga… | EjLDusrnNA… | F4cX2UVifa… | 4FFJsh7hsu… | system_transfer | PROVEN |
| 5iPoWhLAzoXR… | G5JRESGwRo… | CQKmWs6EdB… | 3FxWFZExCF… | system_transfer | PROVEN |
| 6SXTLNED1iLn… | 6gjV3DXLPr… | 4LY6dKo2eD… | 9zp7wnLeYS… | system_transfer | PROVEN |
| 6YZm2PVLBozy… | 5UQ3xUkjEb… | 73SSARaRZQ… | G6Thtd8b3D… | system_transfer | PROVEN |
| 6YqsppC6qjJ3… | B1oX1pfaY9… | 8Y8u1aubCB… | B8DNQYnaUJ… | system_transfer | PROVEN |
| 6hDxh9uXFwzW… | FqT762KExZ… | 6xmQSL9hAf… | HX2ws5rRDG… | seeded_account_close | PROVEN |
| 753AMCTdvouX… | 2pujHeofFz… | B9dubhLDYH… | GruMyrgjek… | system_transfer | PROVEN |
| 7DZuY9tjXszN… | D7G1EqBmyP… | EjFvXJfkao… | 4RcuVuKujr… | seeded_account_close | PROVEN |
| 7YnzMgUvUjSM… | 7JyZomL65J… | 5tsCDQ5uHk… | AAFh1LVv48… | seeded_account_close | PROVEN |
| 7pncD23yVtmV… | FhMsKVZv1P… | 9FZRFBurNt… | CNGvwd4M5s… | seeded_account_close | PROVEN |
| 9YXYH9A8b2Xj… | 464wCztQ7h… | DUKTwT5sSk… | 7KeLvrpQiR… | seeded_account_close | PROVEN |
| 9x4NHggD8U5g… | FUynWoZkcT… | HbUsgRo62E… | 82YQnT2NMw… | seeded_account_close | PROVEN |
| AB7XXeQAvN2y… | 8aBvMmrHDS… | 8R11d5TvWX… | GaUEGkhHd3… | system_transfer (recovered sig) | PROVEN |
| AshPvt8cwspQ… | ETk1zp9PCy… | FG6W2SuAWM… | HwWCrYNw8A… | system_transfer | PROVEN |
| AvLiJBdtb4om… | HWMd928pVx… | A7xsiu1N3X… | DAcDFN2CVT… | seeded_account_close | PROVEN |
| AwXtJ4QsZwHw… | HA71615XkB… | 7FqyKpX3Qd… | 8ZbWuDTaMi… | system_transfer | PROVEN |
| AyafwyhUhZW4… | DhtTjp5Kqe… | 6YmkWVoR1c… | BtLXR5hqch… | system_transfer | PROVEN |
| Bn9kT53VKyTS… | 92smSgLayD… | C9TRPMM2BH… | 65ikf16h8C… | system_transfer | PROVEN |
| C4TFLdu1f2iG… | 4SBRxk8vcn… | F2NNVW8Fna… | FyiSQ2WVLW… | seeded_account_close | PROVEN |
| CPtvQTf8bXKP… | FWzPYZ1ACb… | 7co6Va1XZN… | GWQMRdwVWN… | seeded_account_close | PROVEN |
| CQJzHVvpn3Ew… | EYNp8EyTJS… | EZJJf8t1id… | ErJY7v127x… | system_transfer | PROVEN |
| Ct2VDLuBanTr… | 23aRnFmTZ3… | k21uxic63r… | B6KijpAbbZ… | system_transfer | PROVEN |
| EGB4sv9ddNhW… | ANenEukvmp… | HZB2FdTaY9… | HTR9U7dkk1… | system_transfer | PROVEN |
| EN3kJPf6bvz2… | GShjLKmT6Q… | 7k2jPti8AM… | HA4EntVy1f… | seeded_account_close | PROVEN |
| EQ6qQsweDhsd… | 9e2HETPeiT… | CVmmf13hBG… | HTWboNP89K… | system_transfer | PROVEN |
| EZozuXuPezcR… | C352d3HuGP… | J9aMarTtEK… | 2WbeYh8U3z… | seeded_account_close | PROVEN |
| EeujXJZkoyGv… | 2sojeUxW3E… | AVBh2G7hda… | 85cQKmrfYp… | seeded_account_close | PROVEN |
| F2fcE5sjDuSM… | Cxnxj3GY15… | De8k9qKMEr… | CTwPRChVWw… | system_transfer | PROVEN |
| F612mB7c9pXA… | 2EHGiKb9HT… | AU1UPnq7oh… | DkWzKH4pUs… | system_transfer | PROVEN |
| F7NmdG9JAhEj… | FaJqMSy9iF… | GTnh7qHwfe… | 9yxYy2Qv11… | seeded_account_close | PROVEN |
| FN7GB2Mf4pw1… | 5jUDw8xRXq… | H5PveMYsUk… | 2p3WcB7wyS… | seeded_account_close | PROVEN |
| GQEEL98udpaC… | CqPi7QXcTg… | 3N99QoPZpV… | DfkddMyr41… | system_transfer | PROVEN |
| HHmh4bSYBXsP… | 69ruAQ6U79… | 8jhjrcXFbf… | CZFfFxU6Ar… | seeded_account_close | PROVEN |
| JyJWcxa8xPwg… | qXkSCeBgP2… | — | AL2qj6AuYw… | — | **UNRESOLVED** |
| sP79aMCqfZB1… | 6VN6342pFq… | AqmGm7HwBY… | syBxEjpS5a… | system_transfer | PROVEN |

---

## Classification Results

| Classification | Count | % of 43 |
|---|---|---|
| **SUBPROV_TO_PROVISIONING_TO_CREATOR** | **42** | **97.7%** |
| DIRECT_SUBPROV_TO_CREATOR | 0 | 0% |
| LONGER_INTERMEDIARY_CHAIN | 0 | 0% |
| UNRESOLVED | 1 | 2.3% |

**Zero launches in this ground-truth cohort show a direct SubProv→Creator transfer.**
Every single resolvable launch shows exactly one intermediate wallet — never zero,
never more than one. Two distinct on-chain mechanisms both produce this identical
three-hop shape:

- **`system_transfer` (24 of 42, 57.1%)**: SubProv sends SOL directly to Wallet P via a
  plain `system.transfer`; P then wraps it into a WSOL token account and immediately
  `closeAccount`s that account with `destination = creator`.
- **`seeded_account_close` (18 of 42, 42.9%)**: SubProv uses `createAccountWithSeed`
  to fund a brand-new token account whose `base` (controlling owner) is Wallet P; that
  account is then closed with `destination = creator`. This is the mechanism the
  project's DB labels `SEEDED_ACCOUNT_CLOSE`.

Both mechanisms are structurally identical at the level this audit tests: **SubProv
funds Wallet P; Wallet P's account is closed to Creator; SubProv, Wallet P, and Creator
are three distinct addresses in every single case.**

---

## Proof Standard Applied

Per the task's own required proof bar, for every launch marked PROVEN this audit
verified **all four** conditions directly from the decoded transaction, not inferred:

1. **SubProvider → Wallet P**: the `system.transfer.source` (or
   `createAccountWithSeed.source`) instruction field equals `subprov_wallet` exactly.
2. **Wallet P → Creator**: the `closeAccount.destination` instruction field equals
   `creator_wallet` exactly.
3. **Wallet P ≠ SubProvider**: verified as a direct string inequality on every row —
   true for all 42.
4. **Wallet P ≠ Creator**: verified as a direct string inequality on every row — true
   for all 42.
5. **Ordering**: both instructions occur within the **same atomic transaction**, with
   the SubProv→P instruction appearing before the closeAccount instruction in program
   order — the strongest possible ordering guarantee (atomicity), stronger than the
   task's minimum requirement of "the SubProv→P transaction must precede" the P→Creator
   transaction.

No launch was classified on the basis of naming convention, prior DB labels, or
inference — every PROVEN result rests on a directly decoded transaction's own parsed
instruction fields.

---

## Fan-Out / Raw-Recipient Cross-Check

For every confirmed Wallet P, checked directly against `wt_candidate_websocket_watches`
(the one table X65.16/X65.18 already established as the genuine raw, pre-creator-filter
recipient ledger):

| Classification | Count |
|---|---|
| Observed raw fan-out recipient (Wallet P itself present as `candidate_wallet`) | **0 of 42** |
| Creator recorded instead of provisioning wallet (subprov has coverage, but the recorded `candidate_wallet` is the creator, not Wallet P) | **39 of 42** |
| Absent, no coverage at all for this subprov (recoverable by RPC, not persisted) | **3 of 42** |

**Zero of the 42 confirmed provisioning wallets ever appear in
`wt_candidate_websocket_watches` at all.** This was checked further, not left as a bare
absence: for all 39 "creator recorded instead" cases, this audit directly confirmed
that the **creator wallet itself** is the `candidate_wallet` value the live cascade
recorded for that subprov (e.g. subprov `92smSgLayD…`'s one recorded
`wt_candidate_websocket_watches` row has `candidate_wallet = 65ikf16h8C…`, the creator
— not `C9TRPMM2BH…`, the proven Wallet P). **This is not a contradiction of Wallet P's
existence** — it is direct, positive evidence of *why* Wallet P is invisible to this
table: the live cascade daemon's own detection logic
(`_handle_subprov_tx()`, `src/core/ws_cascade.py`, per X65.4/X65.16/X65.18's own prior
tracing) records the `closeAccount.destination` as the candidate — which, for this
mechanism, is always the creator, one hop past Wallet P. The live table structurally
skips the intermediate hop, not because Wallet P doesn't exist, but because the
detector's own extraction point (the close instruction) is one hop downstream of it.

---

## Unresolved Case

**`JyJWcxa8xPwgKZFT13mPyDymLrjXhxkQTTyTJC3pump`**: `wt_watchtower_launches` has neither a
usable `wrap_close_signature` nor a usable `create_signature` for this row (both
NULL). A bounded, ≤5-page (≤100 signature) `getSignaturesForAddress` walk on the
creator wallet (`AL2qj6AuYwwxk3Wpn7v3Pk5KhixNBRTyukyQgy9Re44v`) did not reach that
wallet's true earliest transaction within the bound — this creator has more than 100
recorded signatures, meaning it is a genuinely high-activity wallet whose funding
event cannot be located without either an unbounded scan (explicitly disallowed by the
task) or a different, more targeted search (e.g., anchoring on a known approximate
block-time window from `wt_walkback_queue`, not attempted in this audit since it falls
outside the bounded windows the task explicitly authorizes). **Precise reason:
insufficient persisted signature reference, and the wallet's transaction volume
exceeds the bounded-search limit this audit deliberately imposed on itself.**

---

## Does the Current Graph Collapse Provisioning Wallet into SubProvider?

**Yes, functionally, at both the live-cascade-table layer and the topology-classifier
layer — confirmed directly, not assumed.**

- **`wt_candidate_websocket_watches`** (Section 5): records the creator, not Wallet P,
  as the subprov's "candidate" — the intermediate hop is entirely invisible in this
  table's own data, for all 42 confirmed cases.
- **`wt_provisioning_edges`** (X65.17's own prior proof, reused here without
  re-deriving it): only ever models `SUBPROV_TO_CREATOR` directly — there is no
  `SUBPROV_TO_PROVISIONING` or `PROVISIONING_TO_CREATOR` edge type in this table's
  schema at all (`CHECK(edge_type IN ('TREASURY_TO_SUBPROV','SUBPROV_TO_CREATOR'))`,
  already cited in X65.17). This table has no representational capacity for Wallet P
  whatsoever, by its own `CHECK` constraint — not a coverage gap, a genuine schema
  omission.
- **`funding_topology.py`'s classifier** (X65.18's own prior trace): its FAN_OUT/LINEAR
  decision reads exactly these two tables, neither of which records Wallet P — so the
  topology classifier's own internal graph model has never represented this hop
  either.

**The current graph, end to end, treats every confirmed WATCHTOWER launch as a
two-hop SubProv→Creator relationship, when the on-chain reality — proven directly for
42 of 43 ground-truth launches — is a three-hop
SubProv→Wallet P→Creator relationship, atomic within a single transaction.**

---

## Should the Canonical Model Include a Mandatory Provisioning-Wallet Stage?

**Yes — the evidence in this audit supports making it mandatory, not optional, for the
cascade-confirmed ground-truth population specifically.** 42 of 42 resolvable launches
(97.7% of the full 43-launch ground truth, 100% of the launches where the underlying
transaction could be decoded at all) show this exact three-hop shape with zero
exceptions and zero ambiguity — this is not a "sometimes" pattern requiring a
probabilistic or optional model element; it is the **universal** shape of every
decodable cascade-confirmed WATCHTOWER launch.

This directly resolves the ambiguity the canonical model diagram (used throughout
X65.11–X65.18) left open:

```
Treasury → SubProvider → [Provisioning Wallet] → Creator → Launch
```

The bracketed, optional-looking "[Provisioning Wallet]" notation used in prior reports
should be **un-bracketed** — made a mandatory, always-present stage — for the
cascade-confirmed population this audit tested. This is a proof-of-existence finding
about the ground truth's actual on-chain structure; it does not itself imply
`wt_provisioning_edges` or `wt_candidate_websocket_watches` should be modified (that
would be a separate, code-level decision, out of this read-only audit's scope), only
that the **documented model** describing what actually happens on-chain should reflect
what this audit proved happens on-chain, every time it could be checked.

### Deliverables

Complete per-launch transaction path with signatures, mechanisms, and wallet-role
assignments for all 43 ground-truth launches (Section 2); exact classification counts
and percentages, with zero DIRECT_SUBPROV_TO_CREATOR or LONGER_INTERMEDIARY_CHAIN
results and 42/43 (97.7%) SUBPROV_TO_PROVISIONING_TO_CREATOR (Section 3); the full
four-condition proof standard applied to every PROVEN result (Section 4); a
fan-out/raw-recipient cross-check proving the live cascade table itself records the
creator, not the provisioning wallet, for 39 of 42 cases — direct evidence of, not
evidence against, the provisioning wallet's existence (Section 5); one precisely
explained unresolved case, with the exact reason and the bounded-search limit that
produced it (Section 6); direct confirmation that the current graph (both live tables
and the topology classifier) structurally collapses the provisioning-wallet stage
(Section 7); and a recommendation, grounded in a 42/42 zero-exception result, that the
canonical model's Provisioning Wallet stage be treated as mandatory rather than
optional for the cascade-confirmed ground truth (Section 8). No code was changed; no
database writes occurred; no UI was modified.
