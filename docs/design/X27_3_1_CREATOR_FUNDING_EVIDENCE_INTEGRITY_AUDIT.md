# X27.3.1 — Creator Funding Evidence Integrity Audit

**Investigation only. No code, schema, detection, or attribution changes were made.**

## Success Criterion — Answered

**When is it factually correct for the platform to state "Creator funded by X"?**

Only when **both** of the following independently hold:

1. **Economic capability**: the creator's own wallet (not an ATA, PDA, or
   other derived account) gained an amount of SOL, in the specific
   transaction being cited, that is large enough to plausibly cover the
   cost of a Pump.fun CREATE instruction plus its associated account rent
   (empirically, every genuine sample in this audit cleared several
   hundredths of a SOL; see Phase 4/7).
2. **Single-recipient attribution**: the transaction's SOL outflow from the
   funder is not distributed across multiple unrelated recipients in the
   same instruction set — i.e., the creator's gain must be the funder's
   primary, intentional transfer in that transaction, not one broadcast
   leg among many.

A transfer that fails either test must not be labeled "creator funded by
X" — it may still be recorded as an observed transaction (for provenance),
but must be presented as unconfirmed/insufficient evidence, not as a
funding relationship.

## Phase 1 — Creator funding evidence inventory

Traced every independent write-site capable of producing a "creator funded
by X" claim (research agent + direct reads):

| Write site | File:function | Table.column | Amount floor enforced? |
|---|---|---|---|
| Wrap-close detection | `wrap_close_detector.py::detect_wrap_close/store_candidate` | `wt_wrap_close_candidates`, `wt_ops_v2_creators.funding_amount_sol` | **Yes** — 0.05–7.0 SOL band, self-refund guard |
| Live cascade launch record | `ws_cascade_store.py::record_launch` | `wt_watchtower_launches` (creator_wallet, subprov_funding_sol, wrap_close_sol) | Inherits the 0.05–7 SOL gate from upstream detection |
| Walkback reconstruction | `walkback_worker.py::_find_funder_via_rpc/_extract_amount_sol` | `wt_walkback_queue` (funder_wallet, funder_amount_sol, funding_mechanism) | **None** — any nonzero balance delta qualifies |
| Discovery rendering | `discovery/service.py::_entity()` | (reads `wt_walkback_queue`/`wt_discovered_subprovs` only) | N/A — presentation layer, inherits upstream evidence as-is |
| Treasury-bank hop2 leads | `treasury_bank.py::add_walkback_hop2_lead` | `wt_treasury_review` (out_sol, evidence_sigs) | **None** — any nonzero amount added once dedup passes |
| Treasury funder discovery | `treasury_bank.py::_discover_treasury_funder/_evaluate_funder_candidate` | `wt_confirmed_treasuries`/`wt_treasury_review` promotion | Relative ranking only (top-by-amount among candidates), no absolute floor |
| Subprov distribution mesh | `subprov_distribution.py::backfill_immediate_funder` | `wt_discovered_subprovs.immediate_funder` | **None** — first-seen wins, zero-RPC, no amount comparison at all |
| Independent ancestry walk | `watchtower_attribution.py::score_token/intake_migration` | `migrated_tokens.initial_funder`, `watchtower_token_attribution` | None explicit — ranks by walk amount, no floor |
| Attribution outcome | `attribution_outcome.py::derive_outcome/persist_outcome` | `wt_attribution_outcomes` (evidence JSON) | N/A — **only copies** `queue.get("funder_wallet")` verbatim; does not independently compute a funder |
| Operational Behaviour | `operational_behaviour.py` | (read-only) | N/A — classifies existing state, writes nothing new |

**Conclusion of Phase 1**: exactly one mechanism family (wrap-close
detection, feeding `wt_wrap_close_candidates`/`wt_watchtower_launches`)
enforces an economic-capability floor at write time. Every other
write-site that can produce or propagate a "creator funded by X" claim —
most importantly `walkback_worker.py`, the platform's single largest
source of funder claims by volume — applies **no amount floor whatsoever**.
This is the structural root of the problem X27.3 surfaced.

## Phase 2 — Funding transaction audit (measured sample)

Population: 1,505 `wt_walkback_queue` rows with a non-null `funder_wallet`
(793 `PLAIN_XFER`, 712 `WSOL_WRAP_CLOSE`; 1,429 have a populated amount).

**Amount distribution by mechanism** (SOL, `funder_amount_sol`):

| Mechanism | n | min | median | mean | max | exactly 0.0 | <0.001 | <0.01 | ≥0.01 |
|---|---|---|---|---|---|---|---|---|---|
| PLAIN_XFER | 792 | 0.0 | 0.2797 | 6.19 | 116.59 | 103 (13.0%) | 231 (29.2%) | 253 (31.9%) | 539 (68.1%) |
| WSOL_WRAP_CLOSE | 637 | 0.0 | 0.0475 | 2.70 | 170.41 | 3 (0.5%) | 33 (5.2%) | 170 (26.7%) | 467 (73.3%) |

A stratified on-chain sample (n=31: 4 mechanism×tier cells × ~4 samples
each, tiers = zero / <0.01 / 0.01–1 / ≥1 SOL) was fetched via
`getTransaction` and measured for account count, gainer count (accounts
with a positive lamport delta), and the creator's own on-chain gain in
that exact transaction:

| Mechanism | Tier | Accounts | Gainers | Creator gain matches `funder_amount_sol`? |
|---|---|---|---|---|
| PLAIN_XFER | zero (0.0 SOL) | 12 | 10 | Yes (1–3 lamports — the recorded value is accurate, just economically negligible) |
| PLAIN_XFER | <0.01 SOL | 3 | 1 | Yes (1,000 lamports = 0.000001 SOL) |
| PLAIN_XFER | 0.01–1 SOL | 4–22 | 1–8 | Yes, in all 4 samples |
| PLAIN_XFER | ≥1 SOL | 3–12 | 1–7 | Yes, in all 4 samples |
| WSOL_WRAP_CLOSE | zero (0.0 SOL) | 16–37 | 3–4 | **No** — creator received nothing in 3/3 samples (see Phase 5) |
| WSOL_WRAP_CLOSE | <0.01–≥1 SOL | 5–18 | 1–3 | Yes, in 11/12 samples |

## Phase 3 — Fan-out taxonomy

Evidence-supported categories, derived only from the sample above (no
invented categories):

| Category | Signature | Evidence |
|---|---|---|
| **Dust broadcast** | 1 sender, ~10 recipients, 1 transaction, each recipient gains 1–3 lamports | 4/4 PLAIN_XFER "zero"-tier samples: 12 accounts, 10 gainers, 10 identical-shape system-transfer instructions, uniform 1–3 lamport gains across all recipients including the creator |
| **Negligible single transfer** | 1 sender, 1 recipient, 1 instruction, sub-0.00001 SOL | 4/4 PLAIN_XFER "low"-tier samples: 3 accounts, 1 gainer, exactly 1,000 lamports (0.000001 SOL) — a genuine single-target transfer, but of an amount with no real economic capability |
| **Genuine funding transfer** | 1–few sender/recipient legs, gain matches the funder's actual system-transfer amount, amount ≥0.01 SOL | All PLAIN_XFER mid/high-tier samples and all non-zero WSOL_WRAP_CLOSE samples — creator gain matched the recorded `funder_amount_sol` value within rounding, amounts ranged 0.012–43.3 SOL |
| **ATA/account-creation noise** (not a funding transfer at all) | `spl-associated-token-account::createIdempotent`, no SOL delta to the stored creator address | 1/3 WSOL_WRAP_CLOSE "zero"-tier samples — the transaction only creates token accounts for an unrelated wallet; the stored `creator` never appears as a gainer |
| **Mismatched wrap-close destination** | Genuine `closeAccount` instruction exists, but `destination`/`owner` ≠ the `creator` field stored in `wt_walkback_queue` for that row | 1/3 WSOL_WRAP_CLOSE "zero"-tier samples (mint `EgDNr4h127xFkEmaGejVST3MQ3vntqZ9hQ5axWWVpump`): closeAccount destination `uXUZDwVvY3WQFPvHmUsPngQk9qveUdEvHn2Z56a9kXs` vs. stored creator `55pj7DaqF5VZP4JbWHf3Nk2z6ddtq8CDoXxS68EDg5jw` |

Per the brief's governing principle, **fan-out itself is not disqualifying**
— the dust-broadcast category is disqualified by the *combination* of
(a) many simultaneous recipients and (b) a per-recipient gain in the
low-single-digit-lamport range, not by recipient count alone. A
"broadcast" transaction moving a meaningful amount to each of several
recipients (not observed in this sample, but structurally distinct from
what was measured) would not automatically fail this rule.

## Phase 4 — Economic capability (measurement only, no thresholds defined yet)

Observed capability tiers in the sample, described only, not yet bounded:

- **Usable balance**: creator gain in the 0.01–43+ SOL range — suffices to
  pay Pump.fun CREATE fees, rent for the bonding-curve/mint accounts, and
  leaves meaningful working balance. Every mid/high-tier sample in both
  mechanisms falls here.
- **Rent-only / bookkeeping amount**: creator gain in the roughly
  0.002–0.01 SOL range (WSOL_WRAP_CLOSE "low" tier) — enough to cover a
  single account's rent-exemption, consistent with legitimate ATA
  provisioning rather than broad "funding," but still a real, targeted,
  single-recipient transfer.
- **Dust**: creator gain of 1–3 lamports, always co-occurring with 9+
  other simultaneous recipients receiving the same order of magnitude —
  no capability to do anything on-chain.
- **Negligible single transfer**: creator gain of ~1,000 lamports
  (0.000001 SOL), a single targeted recipient — some capability exists in
  principle (SOL is SOL) but three orders of magnitude below the observed
  "usable balance" tier, and no corroborating evidence in this sample that
  such a transfer was followed by real provisioning activity.
- **Zero (misattributed)**: no gain to the stored creator address at all —
  not an economic-capability question, a data-integrity one (Phase 3's
  "ATA/account-creation noise" and "mismatched wrap-close destination"
  categories).

## Phase 5 — False-positive audit

Applying "creator gain <0.01 SOL in that specific transaction, OR creator
gain is zero/misattributed" as the disqualifying test (not yet a proposed
production threshold — used here only to size historical impact):

- **PLAIN_XFER**: 253 of 792 rows (31.9%) have `funder_amount_sol < 0.01`;
  every sampled representative of this range was either dust-broadcast or
  a negligible single transfer — no false negatives found in the sample
  (i.e., no <0.01 SOL sample turned out to be genuine funding).
- **WSOL_WRAP_CLOSE**: 170 of 637 rows (26.7%) fall under 0.01 SOL, but the
  sampled evidence shows this mechanism's low tier (0.002–0.01 SOL) is
  **not** dust — it matches genuine, single-recipient, targeted transfers
  (ATA rent-scale provisioning), a materially different shape from
  PLAIN_XFER's low tier. Applying the same raw cutoff to both mechanisms
  would misclassify legitimate WSOL_WRAP_CLOSE evidence as false positive.
- **WSOL_WRAP_CLOSE zero tier** (3 of 637, 0.5%): 100% of the sample (3/3)
  showed either no creator gain or a mismatched destination — this small
  slice is a **genuine misattribution class**, independent of amount
  thresholds; a `0.0`-valued WSOL_WRAP_CLOSE row appears to reliably
  indicate the reconstruction failed to locate the real closeAccount leg,
  not that a real transfer of zero value occurred.

**Downstream impact** (X27.3's GF7Y case is one confirmed instance of the
PLAIN_XFER dust-broadcast false positive): every row that reaches
`Discovery`'s walkback-only rendering branch
(`src/discovery/service.py:410-432`, the exact code path exercised in
X27.3) inherits whichever `funder_wallet`/`funder_amount_sol` value is
stored, with **no** amount-based gate anywhere in that render path. Since
`intelligence_outcome=LINEAGE_GAP` was true for all 149 GF7Y rows (X27.3
Phase 6) and is common generally for PLAIN_XFER-sourced rows lacking a
confirmed treasury, the Attribution Outcome layer already flags these as
unconfirmed — but Discovery's presentation still renders a
`SUBPROVISIONER_RESOLVED`/`CREATOR_IDENTIFIED` node with IDENTITY-level
visual weight regardless of the underlying amount, so the *analyst-facing*
impact is real even though the *classification* layer is comparatively
more conservative.

## Phase 6 — Counter-example audit (guard against over-correction)

Explicitly searched for legitimate low-value or multi-recipient patterns,
per the brief's warning not to treat fan-out or low value as
automatically invalid:

- **WSOL_WRAP_CLOSE "low" tier (0.002–0.01 SOL)**: single recipient, single
  targeted transfer, consistent with genuine ATA-rent provisioning — a
  legitimate operational pattern that must **not** be swept into a
  "dust" bucket merely because its absolute SOL value is small. This is
  the clearest counter-example found: a naive "amount < 0.01 SOL = reject"
  rule would incorrectly discard ~170 genuine WSOL_WRAP_CLOSE
  observations.
- **PLAIN_XFER mid-tier multi-gainer sample** (0.029704 SOL, 13 accounts,
  8 gainers): this transaction has multiple gainers (like the dust
  samples) but the creator's own gain (29,703,590 lamports ≈ 0.0297 SOL)
  is a real, usable amount, not a uniform micro-broadcast — recipient
  count alone did not correlate with illegitimacy here. This confirms the
  brief's caution: fan-out shape is not itself disqualifying; the
  per-recipient economic magnitude is what distinguishes dust from a
  legitimate multi-output operational transaction (e.g. a treasury paying
  out several sub-provisioners in one batch).
- No sample in this audit showed a case where a *legitimately large*
  transfer was structured as a many-recipient uniform micro-broadcast —
  the two properties (many simultaneous recipients + uniform sub-10-lamport
  amounts) co-occurred in every dust sample and never separately.

## Phase 7 — Canonical evidence rule (proposed definition, not implemented)

**"Creator funded by X" may only be stated when, for a single cited
transaction:**

1. The creator's own wallet address (not a derived/associated account) is
   a positive-delta account in that transaction's balance changes.
2. The creator's gain in that transaction is large enough to plausibly
   fund real on-chain activity — this audit measured a clear empirical
   gap between the dust/negligible tiers (1 lamport – 0.000001 SOL) and
   the smallest genuine tier observed (0.002 SOL, WSOL_WRAP_CLOSE ATA
   rent) — a gap of roughly three orders of magnitude with no observed
   evidence in between. The exact cutoff is a Phase-9/implementation
   decision, not fixed here, but should sit inside that empirical gap
   rather than at an arbitrary round number, and should very likely differ
   by mechanism (WSOL_WRAP_CLOSE's legitimate floor is measurably lower
   than PLAIN_XFER's) rather than use one shared constant.
3. The transaction is not a uniform micro-broadcast to many simultaneous
   recipients — i.e., disqualify on the *combination* of (a) several other
   accounts gaining a comparable order of magnitude in the same
   transaction and (b) the creator's own gain falling in the negligible
   range, never on recipient count or transaction shape alone.
4. For `WSOL_WRAP_CLOSE` specifically, the `closeAccount` instruction's
   `destination`/`owner` must match the stored creator address exactly —
   a transaction lacking any qualifying transfer to that address (or
   showing only unrelated ATA-creation instructions) is not evidence of
   funding at all and should be treated as a reconstruction failure, not
   a zero-value funding event.

This definition does **not** rely solely on transfer amount (rule 3 exists
because amount alone would misclassify a genuine multi-output batch), does
**not** rely solely on recipient count (rule 2/Phase 6 exists because a
low absolute amount can still be genuine), and requires address-level
correctness (rule 1/4) as a precondition before amount is even evaluated.

## Phase 8 — Historical impact assessment (no repairs performed)

- **PLAIN_XFER rows likely misclassified as funding**: up to 253 of 792
  (31.9%) fall below the empirically-observed genuine-transfer floor;
  the 103 rows at exactly `0.0` SOL (13.0%) are the highest-confidence
  false positives, directly matching the confirmed GF7Y dust-broadcast
  shape.
- **WSOL_WRAP_CLOSE rows likely misclassified**: only the 3 of 637 (0.5%)
  `0.0`-valued rows show the misattribution pattern; the remaining 26.2%
  of sub-0.01-SOL rows are very likely genuine (Phase 6) and should not be
  counted as false positives under this proposed rule.
- **Affected Discovery pages**: any token/creator/sub-provisioner page
  whose only supporting record is a `wt_walkback_queue` row with a
  qualifying-as-false-positive amount — the exact rendering path already
  traced in X27.3 (`discovery/service.py:410-432`, `434-459`) applies
  uniformly to all such rows, so this is not isolated to GF7Y.
- **Affected Operational Behaviour pages**: `operational_behaviour.py`
  reads the same `wt_discovered_subprovs`/`wt_walkback_queue` rows and
  would inherit the same false positives wherever it surfaces funding
  mechanism/amount facts, though this audit did not separately re-verify
  its specific rendering paths.
- **Affected attribution summaries**: `wt_attribution_outcomes` embeds
  `funder_wallet` verbatim into its evidence JSON without independently
  validating amount (Phase 1) — every `LINEAGE_GAP`/`KNOWN_MULTI_TOKEN_CREATOR`
  outcome whose source row falls in the false-positive range carries this
  same unverified funder claim forward into its evidence, even though the
  outcome_type itself (`LINEAGE_GAP`) already signals low confidence.

No counts beyond the ranges above were computed with certainty for
downstream tables (e.g. exact row counts in `wt_attribution_outcomes` or
`operational_behaviour` outputs affected) — doing so would require
re-running those services against the flagged mint set, which is out of
scope for an investigation-only sprint.

## Phase 9 — Recommendation (not implemented)

Recommend that creator funding require **all** of:

1. **Economically meaningful transfer**, measured against a
   mechanism-specific floor derived from the empirical gap identified in
   Phase 7 (not a single shared constant across `PLAIN_XFER` and
   `WSOL_WRAP_CLOSE`).
2. **Single-recipient (or economically-dominant-recipient) attribution**
   within the cited transaction — reject when the creator's gain is one
   of several comparable-magnitude simultaneous gains, per Phase 3/6.
3. **Address-exactness** for `WSOL_WRAP_CLOSE`: the `closeAccount`
   destination must equal the stored creator address; a transaction
   without this exact match is a reconstruction failure to be flagged
   separately, not a valid zero-value observation.
4. Do **not** additionally require multiple independent observations as a
   blanket rule — this audit found single, well-formed, unambiguous
   genuine transfers (Phase 2 mid/high tiers) that would be needlessly
   held back by a repetition requirement; corroborating evidence should
   be an *upgrade* to confidence, not a precondition for the base claim.

This recommendation is offered for a future, separately-scoped
implementation sprint; per this sprint's explicit constraints, no code,
schema, or threshold was changed here.
