# X65.4 — Phase 3: Replay Confirmed WATCHTOWER Launches

Read-only replay of all 43 confirmed WATCHTOWER launches
(`wt_watchtower_launches`), retrieving every SubProv outbound wrap-close
destination recorded in `wt_candidate_websocket_watches` (the table
identified in Phase 1 as already capturing this evidence, unused by
the topology classifier).

## Method

For each confirmed launch, its `subprov_wallet` is looked up against
`wt_candidate_websocket_watches.subprov_wallet` — this table
accumulates every distinct wrap-close destination (`candidate_wallet`)
a subprov has ever produced, with `detected_at` timestamps, persisted
independently of whether that candidate went on to become a confirmed
creator. This is a direct read of already-observed on-chain wrap-close
events (via the live `_handle_subprov_tx()` detector, Phase 1), not an
inference.

## Full replay table

| Launch | SubProv | Outbound Wallets (recorded) | Creator Included | Fan-Out Observed |
|---|---|---|---|---|
| JyJWcxa8xP... | qXkSCeBgP2... | 0 | No/Unrecorded | NO_DATA |
| AB7XXeQAvN... | 8aBvMmrHDS... | 0 | No/Unrecorded | NO_DATA |
| 3gbBrgtwyx... | BG2JAUnCfK... | 0 | No/Unrecorded | NO_DATA |
| Bn9kT53VKy... | 92smSgLayD... | 1 | Yes | NO (single recipient observed) |
| sP79aMCqfZ... | 6VN6342pFq... | 2 | Yes | YES |
| 2PZAgPXXAU... | DuKr6aB5G9... | 5 | Yes | YES |
| 5iPoWhLAzo... | G5JRESGwRo... | 13 | Yes | YES |
| 3SkdUCkXKX... | GNK7SgYsYb... | 37 | Yes | YES |
| 2vBvPiCpsb... | 3ibQskyAaw... | 50 | Yes | YES |
| GQEEL98udp... | CqPi7QXcTg... | 45 | Yes | YES |
| 6YqsppC6qj... | B1oX1pfaY9... | 0 | No/Unrecorded | NO_DATA |
| 9x4NHggD8U... | FUynWoZkcT... | 179 | Yes | YES |
| 9YXYH9A8b2... | 464wCztQ7h... | 57 | Yes | YES |
| CQJzHVvpn3... | EYNp8EyTJS... | 9 | Yes | YES |
| 7DZuY9tjXs... | D7G1EqBmyP... | 35 | Yes | YES |
| 6hDxh9uXFw... | FqT762KExZ... | 15 | Yes | YES |
| F2fcE5sjDu... | Cxnxj3GY15... | 9 | Yes | YES |
| 4MnczXgbDt... | 6icF3iEZ9U... | 26 | Yes | YES |
| 5UQNY2hk4f... | EjLDusrnNA... | 11 | Yes | YES |
| 6YZm2PVLBo... | 5UQ3xUkjEb... | 255 | Yes | YES |
| CPtvQTf8bX... | FWzPYZ1ACb... | 60 | Yes | YES |
| 7YnzMgUvUj... | 7JyZomL65J... | 19 | Yes | YES |
| AshPvt8cws... | ETk1zp9PCy... | 39 | Yes | YES |
| AyafwyhUhZ... | DhtTjp5Kqe... | 300 | Yes | YES |
| EN3kJPf6bv... | GShjLKmT6Q... | 75 | Yes | YES |
| 3fc6tLVPx6... | CatvBkJKLs... | 106 | Yes | YES |
| F7NmdG9JAh... | FaJqMSy9iF... | 218 | Yes | YES |
| EZozuXuPez... | C352d3HuGP... | 167 | Yes | YES |
| 6SXTLNED1i... | 6gjV3DXLPr... | 61 | Yes | YES |
| AvLiJBdtb4... | HWMd928pVx... | 239 | Yes | YES |
| 7pncD23yVt... | FhMsKVZv1P... | 272 | Yes | YES |
| F612mB7c9p... | 2EHGiKb9HT... | 4 | Yes | YES |
| HHmh4bSYBX... | 69ruAQ6U79... | 310 | Yes | YES |
| EeujXJZkoy... | 2sojeUxW3E... | 34 | Yes | YES |
| 3xFT4J96Vz... | 5BjZr8pXgw... | 70 | Yes | YES |
| 753AMCTdvo... | 2pujHeofFz... | 15 | Yes | YES |
| Ct2VDLuBan... | 23aRnFmTZ3... | 86 | Yes | YES |
| C4TFLdu1f2... | 4SBRxk8vcn... | 481 | Yes | YES |
| EQ6qQsweDh... | 9e2HETPeiT... | 14 | Yes | YES |
| AwXtJ4QsZw... | HA71615XkB... | 2 | Yes | YES |
| FN7GB2Mf4p... | 5jUDw8xRXq... | 7 | Yes | YES |
| 4SLVH8rtur... | EH9ymijvhY... | 54 | Yes | YES |
| EGB4sv9ddN... | ANenEukvmp... | 25 | Yes | YES |

## Result: genuine operational fan-out existed for the large majority of this cohort

- **38 of 43 (88.4%)** confirmed launches show `Fan-Out Observed = YES`
  — the subprov produced **more than one** distinct wrap-close
  destination, with the confirmed creator being exactly one of them.
  Fan-out breadth ranges widely: from 2 recipients (`sP79aMCqfZ...`) up
  to 481 (`C4TFLdu1f2...`).
- **1 of 43 (2.3%)** (`Bn9kT53VKy...`) shows exactly 1 recorded
  recipient — genuinely linear as recorded, no fan-out evidence either
  way in this data source.
- **3 of 43 (7.0%)** show `0` recorded outbound wallets in
  `wt_candidate_websocket_watches` — these launches' subprovs have no
  candidate-watch history captured for them at all (most likely because
  they occurred before this specific table/instrumentation was
  deployed, or their session expired/was superseded before a watch
  could be opened — this data source's absence does not by itself mean
  no fan-out occurred, only that this table has no record of it;
  flagged `NO_DATA` rather than assumed Linear).

## Caveat: this is the platform's own already-recorded observation, not a fresh on-chain query

Per this project's standing RPC-investigation discipline, no new RPC
calls were made in this phase — every number above comes from
`wt_candidate_websocket_watches`, itself populated by the live
`_handle_subprov_tx()` detector's own wrap-close decode (Phase 1). This
means the true on-chain fan-out could be equal to or greater than what
is shown here (a subprov's wrap-close event could in principle produce
destinations this specific detection pass didn't capture, e.g. due to
a missed WS message or a session that had already expired before a
later destination's transaction landed) — but it cannot be less, since
every row here is a directly-observed, already-persisted fact, not an
estimate.

## Phase 3A — Validate Provisioning Wallet Behaviour (the wrap wallet itself)

The task separately asked to validate the **provisioning wallet**
between SubProv and Creator — this is the `wrap_wallet` field in
`wt_candidate_websocket_watches` (the single-use ephemeral wallet that
receives SOL from the subprov, wraps it to WSOL, and closes the WSOL
ATA to the creator — the mechanism this project's own memory calls the
"WATCHTOWER wrap-close pattern").

### Method

For each of the 43 confirmed launches, the `wrap_wallet` associated
with the confirmed creator's wrap-close was looked up, then checked
against the same table for: (a) how many distinct candidate wallets
that `wrap_wallet` has ever been recorded funding (should be exactly
1, if it is genuinely single-use), and (b) how many distinct subprovs
have ever used that same `wrap_wallet` (should be exactly 1, if it is
not reused across different provisioning cycles).

### Results

| Launch | Provisioner (wrap_wallet) | Distinct Candidates Funded | Distinct SubProvs Used By | Creator Funded | Reused |
|---|---|---|---|---|---|
| Bn9kT53VKy... | C9TRPMM2BH... | 1 | 1 | Yes | No |
| sP79aMCqfZ... | AqmGm7HwBY... | 1 | 1 | Yes | No |
| 2PZAgPXXAU... | 6WBjrDHqB5... | 1 | 1 | Yes | No |
| 5iPoWhLAzo... | CQKmWs6EdB... | 1 | 1 | Yes | No |
| 3SkdUCkXKX... | 6YYwkYtspP... | 1 | 1 | Yes | No |
| 2vBvPiCpsb... | 7Nkp1ctVEw... | 1 | 1 | Yes | No |
| GQEEL98udp... | 3N99QoPZpV... | 1 | 1 | Yes | No |
| CQJzHVvpn3... | EZJJf8t1id... | 1 | 1 | Yes | No |
| F2fcE5sjDu... | De8k9qKMEr... | 1 | 1 | Yes | No |
| 5UQNY2hk4f... | F4cX2UVifa... | 1 | 1 | Yes | No |
| 6YZm2PVLBo... | 73SSARaRZQ... | 1 | 1 | Yes | No |
| AshPvt8cws... | FG6W2SuAWM... | 1 | 1 | Yes | No |
| AyafwyhUhZ... | 6YmkWVoR1c... | 1 | 1 | Yes | No |
| 6SXTLNED1i... | 4LY6dKo2eD... | 1 | 1 | Yes | No |
| F612mB7c9p... | AU1UPnq7oh... | 1 | 1 | Yes | No |
| 3xFT4J96Vz... | 3rDKtkkwNQ... | 1 | 1 | Yes | No |
| 753AMCTdvo... | B9dubhLDYH... | 1 | 1 | Yes | No |
| Ct2VDLuBan... | k21uxic63r... | 1 | 1 | Yes | No |
| EQ6qQsweDh... | CVmmf13hBG... | 1 | 1 | Yes | No |
| AwXtJ4QsZw... | 7FqyKpX3Qd... | 1 | 1 | Yes | No |
| EGB4sv9ddN... | HZB2FdTaY9... | 1 | 1 | Yes | No |

(21 of 43 launches had a `wrap_wallet` recorded in
`wt_candidate_websocket_watches`; the other 22 either predate this
field's population or fall in the same `NO_DATA` set noted above for
the subprov-level fan-out check.)

### Result: the invariant holds for every case checked, with a caveat

**For all 21 launches where a wrap wallet was recorded, it funded
exactly 1 candidate and was used by exactly 1 subprov — no reuse and
no multi-recipient funding was found anywhere in this platform's own
database.** This is consistent with, and does not contradict, the
task's hypothesized invariant (fresh, single-use provisioning wallet;
single outbound transaction to the creator only; not reused).

**Caveat — not independently RPC-verified on-chain**: per this
project's standing RPC-investigation discipline (never use the
default/env RPC key for investigative work; only a user-supplied
temporary key), no fresh `getSignaturesForAddress` calls were made
against any wrap wallet in this phase. The "exactly one outbound
transaction" and "not reused" claims are confirmed **within this
platform's own already-persisted observations** (which only records
wrap-close destinations the live detector actually saw) — they are not
confirmed against the wrap wallet's complete on-chain transaction
history, which could in principle contain additional transactions this
platform never observed (e.g., if a wrap wallet were reused after this
platform's observation window, or used in a transaction type the
wrap-close decoder doesn't recognize). No exception to the invariant
was found in any locally-available evidence.
