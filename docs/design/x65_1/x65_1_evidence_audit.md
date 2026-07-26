# X65.1 — Phase 2: Existing Evidence Audit

Read-only audit of every existing table/code path that might already
contain creator/sub-provisioner/treasury relationships for the 19-mint
cohort, performed **before** any new traversal logic was designed —
per the task's explicit instruction to avoid a second relationship
system where existing evidence can be reused.

## Headline finding: the cohort splits cleanly into two groups

| Group | Count | Existing evidence |
|---|---|---|
| **A — already-resolvable via existing tables** | **7 / 19** | Direct funder already known to `wt_active_subprov_sessions` with a `treasury_wallet`, and that treasury is already in `wt_confirmed_treasuries` + linked to an operation in `wt_ops_v2_wallets` |
| **B — no existing evidence anywhere** | **12 / 19** | Zero rows in every funding-lineage table checked; genuinely never observed by any prior indexing pass |

This split is the single most important finding of this phase: **no new
detection logic is needed for Group A** — only a cross-reference join
that connects two already-persisted facts that nothing currently
connects. Group B genuinely has no existing evidence and would require
either new RPC-bounded investigation (out of this phase's read-only
scope, addressed if needed in Phase 3/4) or remains `UNRESOLVED`.

## Evidence source inventory

### `wt_attribution_outcomes` (ops DB) — creator's direct funder, ALREADY PERSISTED for all 19

- **Direction**: creator ← funder (`terminal_entity` column holds the
  wallet that funded the creator).
- **Freshness**: current — these are live rows, `outcome_type='INSUFFICIENT_EVIDENCE'`.
- **Confidence/authority**: authoritative record of "walkback reached
  this point and stopped," not a guess — the existing walkback process
  (per `stop_reason: "Walkback stopped because the persisted evidence
  is insufficient for attribution."`) already did the creator→funder
  hop correctly for all 19 launches; it simply had nothing further to
  connect at the time.
- **Used by Discovery today?** Yes — this is exactly the table
  `funding_topology.py`/`operational_intelligence.py` read to assign
  `topology=UNKNOWN` and `operation_id=None` in the first place.
- **Dormant evidence for these 19?** All 19 already have a usable
  `funder_wallet` (`terminal_entity`) — this is "the wallet that funded
  the creator" (the task's own second required step), already solved,
  zero new detection needed.

### `wt_active_subprov_sessions` (ops DB) — funder's own sub-provisioner status + its treasury, ALREADY PERSISTED for 7/19

- **Direction**: funder (as `subprov_wallet`) ← `treasury_wallet`.
- **Freshness**: all matched rows are `state='EXPIRED'` (session
  lifecycle complete, not stale/wrong — this table's own convention is
  that a session naturally transitions to EXPIRED once its watch window
  closes, per this project's own established memory on session
  lifecycle).
- **Confidence/authority**: authoritative — this table is written by
  live `ws_cascade.py` WS-observed funding events, the same mechanism
  that produces every other subprov/treasury fact this project already
  trusts (per `funding_topology.py`'s own docstring, this exact table
  is one of its three canonical evidence sources).
- **Used by Discovery today?** `funding_topology.py` reads
  `wt_active_subprov_sessions` keyed by **`subprov_wallet` values
  already known from `wt_provisioning_edges`/`wt_watchtower_launches`**
  — it never independently checks whether a `wt_attribution_outcomes.
  terminal_entity` (this cohort's own funder_wallet) is ITSELF a key in
  `wt_active_subprov_sessions`. This is the missing cross-reference.
- **Dormant evidence for these 19?** **Yes — 7 of 19 funder wallets are
  already a `subprov_wallet` key in this table**, each with a
  `treasury_wallet` already populated:

| Funder wallet (creator's direct funder) | Treasury wallet (already known) | Funding amount | Mechanism |
|---|---|---|---|
| `3KJteRqjBJb5ddR5eZgPZ8uwyWriKuUN5j2ALS97rpU2` | `DchJquEZzM6VqBaxhA9i7r3qAUngPggQJHoTBhwdFEUK` | 630.0 SOL | PLAIN_TRANSFER |
| `7atTgmp9D86zA3f4AfFSFb5XWvDX2doNW4RrbYFqyQJw` | `DchJquEZzM6VqBaxhA9i7r3qAUngPggQJHoTBhwdFEUK` | 680.0 SOL | PLAIN_TRANSFER |
| `82Yzf1hMDyLa1Z8uADcxzMHxmmGedwKj6viUReKfTeKJ` | `9hGcxVHFajR4xMZVUBQBwbxN2J3tt1aXE5KrUB87EZk4` | 650.0 SOL | WSOL_WRAP_CLOSE |
| `DkhL6D3ZEwdDu4RnW4WHJM9ujX2B94UyvxMAL9CCBV4T` | `Dtwi1eLMTLaUuCwbztpFEoepBdzhjhjmLoTyAdsR3p3u` | 1600.0 SOL | PLAIN_TRANSFER |
| `DmoG9vDaYTf8Rd1vb8i6BSKZi5Zuo3ov4FdMmz5aPzSW` | `9hGcxVHFajR4xMZVUBQBwbxN2J3tt1aXE5KrUB87EZk4` | 900.0 SOL | PLAIN_TRANSFER |
| `E33jmbX8TQLDP2m1VUsdfyzQCWZMBXhtB6wzgqXKhe44` | `9hGcxVHFajR4xMZVUBQBwbxN2J3tt1aXE5KrUB87EZk4` | 650.0 SOL | WSOL_WRAP_CLOSE |
| `FLo2pNsAsS4qpZZnPSN2Quf6cEkiej4fJXC3uVrgzU2X` | `DchJquEZzM6VqBaxhA9i7r3qAUngPggQJHoTBhwdFEUK` | 630.0 SOL | PLAIN_TRANSFER |

### `wt_discovered_subprovs` (ops DB) — independent secondary confirmation for 5/7

- **Direction**: subprov (funder) → its own discovery record, including
  `treasury` (matches `wt_active_subprov_sessions`'s value exactly for
  all 5 checked) and `state`.
- **Freshness**: `first_seen`/`last_seen` timestamps around
  2026-07-06/07 for these specific rows.
- **Confidence/authority**: `confidence` field present but low
  (0.28-0.52) and `state='PROVISIONAL_SUBPROV'` for all 5 — this is a
  **discovery-stage** record, not a confirmation. It independently
  corroborates the `treasury` value from `wt_active_subprov_sessions`
  (same wallet, same treasury, cross-referenced from a second table)
  but should not be read as raising confidence on its own — it is
  redundant confirmation of the same underlying fact, not new evidence.
- **Used by Discovery today?** Not currently cross-referenced by
  `funding_topology.py`/`operational_intelligence.py`.
- **Dormant evidence for these 19?** Yes, for the same 5 (of 7) wallets
  that overlap here and in `wt_active_subprov_sessions`.

### `wt_confirmed_treasuries` (ops DB) — the authoritative treasury registry

- **Direction**: treasury wallet → confirmation metadata
  (`method`/`confidence`/`provenance`/`confirmed_at`).
- **Freshness**: `confirmed_at` timestamps range from 2026-06-11
  (`DchJquEZzM6VqBaxhA9i7r3qAUngPggQJHoTBhwdFEUK`, `provenance:
  CONFIRMED_SEED`) to 2026-06-14
  (`9hGcxVHFajR4xMZVUBQBwbxN2J3tt1aXE5KrUB87EZk4`, `provenance:
  CONFIRMED_SUBPROV_TRACE`) to 2026-07-21
  (`Dtwi1eLMTLaUuCwbztpFEoepBdzhjhjmLoTyAdsR3p3u`, `provenance:
  MANUAL_OVERRIDE_X64_DTWI1ELM` — a manual, human-confirmed override
  from earlier project history).
- **Confidence/authority**: **the single most authoritative treasury
  source in the system** — every row here has already passed through a
  confirmation process (automated `3SIGNAL`/`subprov_funder_trace`
  method, or explicit `MANUAL` human override). This is precisely the
  "already-approved treasury review records" / "confirmed treasury
  registry" source Phase 5 asks for.
- **Used by Discovery today?** Yes, in general (this table anchors
  treasury confirmation project-wide), but not currently joined against
  this specific 19-mint cohort's funder wallets.
- **Dormant evidence for these 19?** All 3 treasury wallets that appear
  via Group A's 7 funders (`DchJquEZzM6VqBaxhA9i7r3qAUngPggQJHoTBhwdFEUK`,
  `9hGcxVHFajR4xMZVUBQBwbxN2J3tt1aXE5KrUB87EZk4`,
  `Dtwi1eLMTLaUuCwbztpFEoepBdzhjhjmLoTyAdsR3p3u`) are already present
  and confirmed here — **zero new confirmation activity needed**, only
  a lookup.

### `wt_ops_v2_wallets` (ops DB) — operation attribution for confirmed treasuries

- **Direction**: wallet → `operation_uuid` + `role`.
- **Freshness**: `first_seen`/`last_seen` around 2026-06-11/14 for these
  three treasuries.
- **Confidence/authority**: authoritative — this is the existing,
  already-used operation-attribution join table.
- **Used by Discovery today?** Yes, project-wide, for operation
  attribution generally.
- **Dormant evidence for these 19?** All 3 treasuries are already
  linked to an operation UUID with `role='TREASURY'`:
  `9hGcxVHF...`→`4135d67d-...`, `DchJquEZ...`→`69af7941-...`,
  `Dtwi1eLM...`→`9868e8dd-...`. **Note**: these are three *distinct*
  operation UUIDs despite this project's own persistent memory
  ("Hello program operator linkage") independently identifying all
  three treasury wallets as belonging to the same real-world operator
  (via shared downstream Hello-service payments). This audit does not
  resolve or merge that discrepancy — it is flagged here as a fact for
  Phase 5/9 to surface, not silently corrected (per the task's explicit
  "Do not automatically confirm or reroot treasury identities"
  constraint, which this audit reads as extending to not silently
  merging distinct operation records either).

### Tables checked with ZERO hits for this cohort (confirming Group B has no existing evidence)

| Table | What was checked | Result |
|---|---|---|
| `wt_provisioning_edges` | `to_wallet`/`from_wallet` = any of the 19 creators or 19 funders | 0 rows |
| `wt_candidate_websocket_watches` | `candidate_wallet`/`close_destination` = any of the 19 creators | 0 rows |
| `wt_webhook_hits` | `wallet_address` = any of the 12 Group-B funders | 0 rows |
| `funder_incoming_transfers` (core DB) | `funder_address` = any of the 12 Group-B funders | 0 rows |
| `creator_receivers` (core DB) | `creator_address`/`receiver_address` = any of the 12 Group-B funders | 0 rows |
| `sol_transfers` (core DB) | `destination` = any of the 12 Group-B funders | 0 rows |
| `transfer_index` (core DB) | `source`/`destination` = any of the 12 Group-B funders | 0 rows |
| `wt_create_event_ledger` (ops DB) | `mint` = any of the 19 cohort mints | 0 rows |
| `token_analysis.create_tx_signature` (core DB) | `mint` = any of the 19 cohort mints | all 19 NULL |

**Conclusion for Group B (12 launches)**: these funder wallets have
never been observed by any indexing/detection pass in this system —
not a "checked and found nothing conclusive" state like Group A's
pre-X65.1 `INSUFFICIENT_EVIDENCE` outcome, but a genuine, total absence
of data. Combined with the missing CREATE signature (Phase 1), this
strongly suggests these 12 launches fall into a coverage gap in the
existing detection/indexing pipeline (plausibly: launches too recent
for the funding-lineage indexer to have caught up, or a code path that
skipped indexing) rather than launches the system deliberately examined
and gave up on. Phase 3/4 will determine whether bounded, cached, or
RPC-based investigation can close this gap for any of the 12, or
whether they remain `UNRESOLVED`.

## Summary: what Phase 3/4 needs to build vs. reuse

| Requirement | Reuse existing evidence? | New logic needed |
|---|---|---|
| Creator's direct funder | **Reuse** — `wt_attribution_outcomes.terminal_entity`, already correct for all 19 | None |
| Is the funder a sub-provisioner? | **Reuse for 7/19** — `wt_active_subprov_sessions` presence as `subprov_wallet` is itself the classification signal | For the other 12/19: new evidence must come from a fresh, bounded investigation (Phase 3), since nothing persisted answers this today |
| Sub-provisioner's own upstream funder (treasury candidate) | **Reuse for 7/19** — `wt_active_subprov_sessions.treasury_wallet`, already populated | For the other 12/19: genuinely new work |
| Known-treasury match | **Reuse for 7/19** — `wt_confirmed_treasuries` already has all 3 relevant treasuries confirmed | N/A for the other 12 until/unless a treasury candidate is found |
| Operation attribution | **Reuse for 7/19** — `wt_ops_v2_wallets` already links all 3 treasuries to an operation UUID | N/A for the other 12 |

No second relationship system is needed for Group A — Phase 4/5's job
for those 7 launches is purely a **read-only cross-reference join**
across tables that already exist and already agree with each other.
