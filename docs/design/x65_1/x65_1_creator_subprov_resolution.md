# X65.1 — Phase 3: Creator-to-SubProv Resolution

For each of the 19 launches, resolves the creator's direct provisioning
wallet (the funder identified in Phase 2 via
`wt_attribution_outcomes.terminal_entity`) into one of:
`CONFIRMED_SUBPROV`, `PROBABLE_SUBPROV`, `DIRECT_TREASURY`,
`NON_OPERATIONAL_FUNDER`, `UNRESOLVED`. Prefers transaction-level
evidence (a real `funding_signature`, amount, and timestamp) over
balance heuristics throughout, per the task's explicit instruction.

## Method

For each of the 19 launches:
1. Take the `terminal_entity` (funder wallet) already persisted in
   `wt_attribution_outcomes` (Phase 2 — reused verbatim, not
   re-derived).
2. Check whether that funder wallet is a `subprov_wallet` key in
   `wt_active_subprov_sessions` — if so, pull the FULL row: `treasury_wallet`,
   `funding_signature`, `funding_amount`, `funding_time`,
   `funding_mechanism`, `state`, `open_reason`.
3. Compute `time_from_funding_to_create = create_time - funding_time`
   (a real transaction-level gap, not inferred), to distinguish
   "clearly funded this specific launch" from "funded this wallet at
   some point in its history, unrelated to this CREATE."
4. Cross-check `wt_discovered_subprovs` for independent corroboration
   (same wallet, does the recorded `treasury` value agree?).
5. Where no persisted evidence exists at all (Group B), classify
   `UNRESOLVED` rather than guessing from balance size or any other
   heuristic.

## Results: Group A (7 launches) — all classified `CONFIRMED_SUBPROV`

| Mint | Creator's direct funder | Funding signature | Amount | Funding→CREATE gap | Mechanism | Classification |
|---|---|---|---|---|---|---|
| 2GuvMWJpfNBXdZQZVGEWLV1Dx8qfiLKHHoDDfe4Apump | `7atTgmp9D86zA3f4AfFSFb5XWvDX2doNW4RrbYFqyQJw` | `fhExo3hX2ocdf5SR7yBwdKpZ8cqCNCAkyuwB5eMoajK25apUMn1TNsssSht3piGsnHoTwcsBxi2uFoVKWUtL2jJ` | 680.0 SOL | 380s | PLAIN_TRANSFER | **CONFIRMED_SUBPROV** |
| 2XmV6Jk6ATzKCnVB15cnPHCCF9o4Kn4PXvVFk6Rppump | `FLo2pNsAsS4qpZZnPSN2Quf6cEkiej4fJXC3uVrgzU2X` | `5cyrRqunni8QdmgV...` | 630.0 SOL | 104s | PLAIN_TRANSFER | **CONFIRMED_SUBPROV** |
| 3LZL5cXac86U1ti81V8GEA1qoj3HenLfnJMcQo7opump | `82Yzf1hMDyLa1Z8uADcxzMHxmmGedwKj6viUReKfTeKJ` | `pokoBD8CxcaQCbcq...` | 650.0 SOL | 4,719s (~79 min) | WSOL_WRAP_CLOSE | **CONFIRMED_SUBPROV** |
| 3QFvseNX1Fdkc6SZV4AT2BfSDvMUH4xQDY1H7TbPpump | `E33jmbX8TQLDP2m1VUsdfyzQCWZMBXhtB6wzgqXKhe44` | `54prrH1Z3an666VH...` | 650.0 SOL | 344s | WSOL_WRAP_CLOSE | **CONFIRMED_SUBPROV** |
| GuyE9St1cU54ppHwqD719Q2AHf6AmPha93MEjzv2pump | `DmoG9vDaYTf8Rd1vb8i6BSKZi5Zuo3ov4FdMmz5aPzSW` | `YqHdBaSjdjPkNGMZ...` | 900.0 SOL | 281s | PLAIN_TRANSFER | **CONFIRMED_SUBPROV** |
| HJ1Ry6iJyAqN7jozMTErJHuNA66kpkDkowi7fhCRpump | `DkhL6D3ZEwdDu4RnW4WHJM9ujX2B94UyvxMAL9CCBV4T` | `5cfib2NULZagPVb1...` | 1,600.0 SOL | 122s | PLAIN_TRANSFER | **CONFIRMED_SUBPROV** |
| x8NtU6nnYDn1BwMDGg2oFdBuYBevhJ32kqM97FSpump | `3KJteRqjBJb5ddR5eZgPZ8uwyWriKuUN5j2ALS97rpU2` | `3ku3iqUMrk5DMCsC...` | 630.0 SOL | 298s | PLAIN_TRANSFER | **CONFIRMED_SUBPROV** |

**Why `CONFIRMED_SUBPROV`, not `PROBABLE_SUBPROV`**: each of these 7
funder wallets has a complete `wt_active_subprov_sessions` record with
a real, valid-format Solana signature (verified: 87-88 character
alphanumeric, matching this project's own established
`valid_signature()` convention), a plausible pre-CREATE funding-to-CREATE
gap (104s to ~79 minutes — all consistent with a genuine provisioning
window, not a coincidental unrelated transfer), and a populated
`treasury_wallet`. This is the same durable, WS-observed evidence this
project already treats as authoritative for subprov classification
everywhere else (`funding_topology.py`'s own docstring names this exact
table as one of its three canonical sources) — reusing it here, not
inventing a new threshold.

Independent corroboration via `wt_discovered_subprovs` (5 of the 7 also
present there) confirms the same `treasury` value in every case — no
contradictory evidence found anywhere.

## Results: Group B (12 launches) — classified `UNRESOLVED`

| Funder wallet | Evidence checked | Result |
|---|---|---|
| `FyWwg3aYJn268Jxv6niKwBEptr1s48XtJJG96R5HVSG2` | `wt_active_subprov_sessions`, `wt_discovered_subprovs`, `wt_webhook_hits`, `funder_incoming_transfers`, `creator_receivers`, `sol_transfers`, `transfer_index`, `wt_provisioning_edges` | Zero rows in every table |
| `EkGqFEGfv7BsQ2qGArL7cqD9jhtsM9gR6p7SPEzKTJKw` | Same | Zero rows |
| `4j33GX1Z3yvgF2Sx6kQmT63jZscWyuHKTs5s5SzXRSK7` | Same | Zero rows |
| `4BJhnZqa5k8PjLKBDmrbfGLnabKJdY6LStQjoDwZ4i6g` | Same | Zero rows |
| `EdqpE1jBonFk9QCzQc3dkM1JC512ZXfmHtVvwbc7CVrW` | Same | Zero rows |
| `DCyQJVfAL37WtcwWAmLNeTatRG553WyfDNytQok41tko` | Same | Zero rows |
| `9WVUzBkmUrpoEnJNH5THtmDKeF4vkUNbUpZKHTeU85v4` | Same | Zero rows |
| `9o5198YMonexLu5aiATgFgqPwgQccsEGoQoTPjXeni7J` | Same | Zero rows |
| `AjphaVN9MgirLh4LrwShMy6TdK7frAcWwfh2MzUHaCni` | Same | Zero rows |
| `1JFLdVdAto6btM28odDRC8XQzqd4yAHmmviFiSmQnVe` | Same | Zero rows |
| `HXMUxU94Zs2hGHW6r4odBiCTMxkzjV7YGJHAMYdTPFRY` | Same | Zero rows |
| `ApgLKt2k1knBhUh8kcEN7HStaznb7E7tPTtbJghbNZdg` | Same | Zero rows |

**Why `UNRESOLVED`, not `NON_OPERATIONAL_FUNDER`**: `NON_OPERATIONAL_FUNDER`
would be an affirmative claim — "we checked and this wallet is not a
sub-provisioner" — but for these 12, there is no evidence of any kind
to check against. Classifying them `NON_OPERATIONAL_FUNDER` would
overstate the negative; `UNRESOLVED` is the honest classification when
the required evidence (a `wt_active_subprov_sessions` record, or any
prior indexing at all) is simply absent, per this project's own
governing classification principle used throughout this whole system
("absence of a match is not evidence of absence of behaviour").

## Why no `DIRECT_TREASURY` or `PROBABLE_SUBPROV` cases in this cohort

- **`DIRECT_TREASURY`** would require the creator's direct funder to
  itself already be a confirmed treasury (i.e., no intermediate
  sub-provisioner hop at all). None of the 19 funder wallets are
  themselves present in `wt_confirmed_treasuries` — checked directly,
  zero matches. This makes sense structurally: `QUICK_BIRTH_MIGRATION`
  launches (this cohort's defining behaviour) tend to follow the same
  provisioning pattern documented throughout this project's history
  (treasury → subprov → creator), and a treasury funding a creator with
  zero intermediate hop would be an unusual, more directly detectable
  pattern that this project's existing evidence sources would likely
  have already surfaced under a different topology (`LINEAR`, per
  `funding_topology.py`'s own `treasury_direct_no_subprov` derivation
  path) rather than `UNKNOWN`.
- **`PROBABLE_SUBPROV`** (partial/lower-confidence evidence, e.g. a
  transfer exists but no formal session record, or a session exists
  but with contradictory/ambiguous treasury data) was not needed for
  this specific 19-launch cohort — every funder wallet checked either
  had a complete, internally-consistent `wt_active_subprov_sessions`
  record (Group A) or had literally zero persisted evidence (Group B).
  The category remains available for future cohorts where partial
  evidence exists.

## Summary

| Classification | Count | Launches |
|---|---|---|
| CONFIRMED_SUBPROV | 7 | 2GuvMWJp..., 2XmV6Jk6..., 3LZL5cXa..., 3QFvseNX..., GuyE9St1..., HJ1Ry6iJ..., x8NtU6nn... |
| PROBABLE_SUBPROV | 0 | — |
| DIRECT_TREASURY | 0 | — |
| NON_OPERATIONAL_FUNDER | 0 | — |
| UNRESOLVED | 12 | all remaining |
