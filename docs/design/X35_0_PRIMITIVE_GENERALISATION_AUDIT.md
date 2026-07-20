# X35.0 — Primitive Generalisation Audit

Investigation only. No code changes. Follows [X33.0](X33_0_CANONICAL_MOTIF_DISCOVERY.md)
and [X34.0](X34_0_PRIMITIVE_SUFFICIENCY_AUDIT.md), whose frozen two-primitive library
(Primitive A — WSOL seed-and-close identity handoff, `closeAccount.destination` = next
operational wallet; Primitive B — bimodal bulk-vs-dust capital allocation) is the fixed
foundation being tested here. All numbers from live SQL against `database/wt_ops_v2.db`,
run 2026-07-20.

**Objective**: test whether Primitives A/B are operation-independent, not find a new
operation. No attribution attempted below — classification of behaviour only.

## Phase 1 — Group unattributed walkback-recovered launches

Population: `wt_walkback_queue` rows with a non-null `treasury` that is NOT in
`wt_confirmed_treasuries` (i.e. not already tagged as a confirmed WATCHTOWER treasury root).

- **266 rows** meet this criterion, grouped into distinct treasury wallets, none of which
  are WATCHTOWER-confirmed.
- Grouped by `funding_mechanism` × `walkback_class`:

| funding_mechanism | walkback_class | count |
|---|---|---|
| (none recovered) | LINK_ONLY | 139 |
| WSOL_WRAP_CLOSE | PARTIAL_TREASURY | 95 |
| PLAIN_XFER | PARTIAL_TREASURY | 32 |

`LINK_ONLY` rows (139/266, 52%) have no funding_mechanism recovered at all — these are
lineage-linked but evidentially thin (a mint linked to a wallet without a walked-back
funding edge), so Phase 2 classification below applies only to the 127 rows that carry an
actual mechanism.

Largest individual unattributed treasuries by launch count: `GhoS9xAiarhv…` (30, all
LINK_ONLY), `2oosgwS9FQLc…` (19, WSOL_WRAP_CLOSE/PARTIAL_TREASURY), `EwgmaSPqcD9N…` (17,
LINK_ONLY), `hZPbb8fV418c…` (12, PLAIN_XFER/PARTIAL_TREASURY), `HGEZr9KAPEm4…` (11,
WSOL_WRAP_CLOSE/PARTIAL_TREASURY).

## Phase 2 — Primitive Classification (behaviour only, no attribution)

| Group (by treasury) | Primitive A present? | Primitive B present? | Basis |
|---|---|---|---|
| WSOL_WRAP_CLOSE / PARTIAL_TREASURY (95 rows, 15+ distinct treasuries) | **YES** | Partial — see Phase 3 | `funding_mechanism='WSOL_WRAP_CLOSE'` is definitionally the wrap→close instruction shape confirmed in X34.0; `funder_amount_sol` avg 0.044 SOL, range 0–0.64 SOL |
| PLAIN_XFER / PARTIAL_TREASURY (32 rows, ~8 distinct treasuries) | **NO** | Ambiguous | No close-account handoff instruction; a direct transfer. `funder_amount_sol` avg 2.14 SOL, range 0.0009–39.5 SOL — no clean bulk/dust bimodal split at this sample size (see Phase 3) |
| LINK_ONLY (139 rows, many distinct treasuries) | **UNKNOWN** | **UNKNOWN** | No funding_mechanism recovered — cannot classify without further RPC walkback; genuinely "neither observed" rather than "neither present" |

Cross-referencing these same non-confirmed treasuries against `wt_provisioning_edges`
(the richer structural table used in X33.0) as `from_wallet`:

- 8 of the non-confirmed treasuries DO appear as `TREASURY_TO_SUBPROV` funders in the edge
  table, confirming Primitive B's transfer *shape* recurs outside the confirmed set —
  but amounts are mostly near-zero (0.0–9.28 SOL avg per group, mostly clustering at the
  dust end, not the 84–2000+ SOL bulk-provisioning scale seen in confirmed WATCHTOWER
  treasuries). Only one group (`EiMJefb6bdJu…`, avg 9.28 SOL) approaches a
  provisioning-scale (rather than dust-scale) transfer, and even that is well below
  confirmed WATCHTOWER's 270 SOL average bulk capitalization.

## Phase 3 — Quantify Where Primitives Occur

### Primitive A (WSOL_WRAP_CLOSE) outside confirmed WATCHTOWER
- Occurrence: 95 rows across roughly 15 distinct non-confirmed treasuries.
- Amount: avg 0.044 SOL, max 0.64 SOL — **an order of magnitude smaller** than confirmed
  WATCHTOWER's SUBPROV_TO_CREATOR wrap-close edges (avg 17.5 SOL per X33.0's
  `wt_subprov_evidence` figures). This is consistent with the mechanism being reused, but
  at rent-only/near-dust scale rather than capital-carrying scale — i.e. the instruction
  *shape* recurs, but not always paired with a real funding transfer.
- Graph depth: 1 hop (subprov→creator only; no confirmed further chaining observed in
  this population within this pass).
- Reuse: same handful of treasuries recur (2oosgwS9FQLc… = 19 occurrences, HGEZr9KAPEm4…
  = 11) — concentrated, not evenly spread, mirroring the concentration pattern already
  seen in confirmed WATCHTOWER (X33.0 Motif 8).

### Primitive B (bulk vs dust) outside confirmed WATCHTOWER
- PLAIN_XFER group: avg 2.14 SOL, but **no clean bimodal split** — the confirmed
  WATCHTOWER split (270 SOL bulk vs 0.0006 SOL dust, a ~450,000x gap) is not reproduced
  here at n=32; instead a smoother, low/mid-range distribution (0.0009–39.5 SOL) with no
  obvious two-cluster structure.
- Edge-table cross-reference: mostly near-zero average amounts per treasury (dust-scale),
  one exception at 9.28 SOL avg — still short of bulk-provisioning scale.
- **Conclusion: Primitive B's mechanism (capital movement from a persistent wallet) recurs,
  but its defining bimodal-amount signature does NOT clearly reproduce at this sample size
  outside confirmed WATCHTOWER.** This may be a small-sample artifact (32–95 rows vs
  hundreds in confirmed data) rather than proof the primitive doesn't generalize — flagged
  as inconclusive, not negative.

## Phase 4 — Where Primitives Don't Occur

The 139 LINK_ONLY rows are the clearest "primitive not observed" population — but this is
an **evidentiary gap, not a behavioural finding**: `walkback_class=LINK_ONLY` means the
walkback pipeline recovered a lineage link (mint→wallet) without a full funding-edge replay,
per the walkback design ([[walkback-queue-design]]: LINK_ONLY/SKIP/PARTIAL/FULL_WALKBACK
are RPC-effort tiers, not behavioural categories). These rows cannot be scored as "neither
primitive" with confidence — they simply were not walked back far enough to tell. Promoting
them to "candidate new operation" or "candidate new primitive" would be premature; the
correct next step is running these specific mints through a fuller (FULL_WALKBACK-tier)
pass, not treating the absence of data as a finding.

No group in this pass showed a funding mechanism that was neither Primitive A's
close-handoff shape nor Primitive B's transfer shape (e.g., no atypical program
interactions, no third instruction pattern) — within the 127 rows that did carry a
recovered mechanism, every one decomposed into A or B's mechanics. The catalogue of
"differences" is therefore about **scale and clustering strength**, not about a
structurally different mechanism:
- Amounts are smaller and less bimodal than confirmed WATCHTOWER.
- Multi-tier chaining (subprov funding subprov) was not observed in this population within
  this pass (not queried exhaustively — flagged as a follow-up, not a negative finding).

## Deliverable — Per-Operation-Candidate Primitive Classification

Framed as requested (candidate groupings, not attributions):

**Group 1 — `2oosgwS9FQLc2TYDkBroNiTfTMzKkUZGsbTNAhkFzvFN` (19 launches, WSOL_WRAP_CLOSE)**
- Primitive A: YES (19 wrap-close occurrences)
- Primitive B: Weak/dust-scale only (2 TREASURY_TO_SUBPROV edges, avg 0.002 SOL)
- Verdict: **Primitive A confirmed at low capital scale; Primitive B present but not at
  provisioning scale.**

**Group 2 — `HGEZr9KAPEm4zSfBCNVpf9zpeW1bU8rfiEi8xUePnCh9` (11 launches, WSOL_WRAP_CLOSE)**
- Primitive A: YES
- Primitive B: not found as a `TREASURY_TO_SUBPROV` edge in this pass (not in the edge
  table's `from_wallet` set) — flagged as not-yet-checked rather than absent.
- Verdict: **Primitive A confirmed; Primitive B inconclusive (data not queried further).**

**Group 3 — `hZPbb8fV418cRdFRzyLDumct9SgEsEqeLEoos9JF8DP` (12 launches, PLAIN_XFER)**
- Primitive A: NO (no close-account handoff mechanism recorded)
- Primitive B: Ambiguous (transfer amounts present, avg unreported at group level in this
  pass — would need a per-treasury breakdown, not done here)
- Verdict: **Neither primitive cleanly confirmed** — candidate for either a genuinely
  different (non-WATCHTOWER-shaped) coordinated pattern, or simply organic/background
  activity that happens to share a mint with a walked-back treasury. Cannot distinguish
  the two without further investigation. **INVESTIGATE.**

**Group 4 — `GhoS9xAiarhvPAubc6AkGSKf3ePq3mvFHAdk9PTpnQf4` (30 launches, LINK_ONLY)**
- Primitive A: UNKNOWN (no mechanism recovered)
- Primitive B: UNKNOWN
- Verdict: **No known primitives determinable from current data — INVESTIGATE** (requires
  a FULL_WALKBACK-tier pass on this specific treasury before any classification is possible;
  this is the single largest unattributed cluster in the dataset by launch count and is the
  strongest candidate for follow-up).

## Confidence Assessment

- **Primitive A generalises structurally** (HIGH confidence) — the wrap→close handoff
  instruction shape recurs outside confirmed WATCHTOWER treasuries, confirming it is not
  an artifact specific to the confirmed address set. Its capital scale in this
  non-confirmed population is much smaller, which is expected if it's being reused by
  smaller/different operators rather than being mis-detected.
- **Primitive B's transfer mechanic generalises** (MEDIUM confidence) but its **bimodal
  amount signature does not clearly reproduce** at current sample sizes outside confirmed
  WATCHTOWER (MEDIUM-LOW confidence on the amplitude/clustering claim specifically,
  LOW-N caveat).
- **No third primitive was surfaced.** Every recovered mechanism in the unattributed
  population decomposed into A or B; the unresolved 139 LINK_ONLY rows are an evidentiary
  gap, not evidence of a new mechanism.

## Recommendation

The framework generalises on the identity-transfer axis (Primitive A) with high confidence
and on the capital-allocation axis (Primitive B) with moderate confidence — this supports
treating the two-primitive library as a genuine cross-operation detection foundation, not
a WATCHTOWER-specific artifact. Two concrete follow-ups, in order of expected yield:

1. Run FULL_WALKBACK on `GhoS9xAiarhvPAubc6AkGSKf3ePq3mvFHAdk9PTpnQf4` (30 LINK_ONLY
   launches, the largest unresolved cluster) to convert it from UNKNOWN to a scored group.
2. Investigate Group 3 (`hZPbb8fV418c…`, PLAIN_XFER-only, 12 launches) specifically to
   determine whether it represents a genuinely different coordination pattern (candidate
   new primitive) or is simply background/organic activity incidentally sharing a walked-
   back mint — the spec's Phase 4 "does it occur naturally?" question, which this pass
   could not answer for this specific group with available data.

## Answer to the stated success criterion

The primitives are **operation-independent on the mechanism-shape axis** (both recur
outside the confirmed WATCHTOWER treasury set) but **not yet independence-confirmed on the
amplitude axis** (Primitive B's defining bimodal split is weaker/unconfirmed at current
non-WATCHTOWER sample sizes). No behaviour in the tested population required a genuinely
new primitive — the two-primitive library holds as a detection foundation, with one
specific unresolved cluster (Group 3) flagged for targeted follow-up rather than treated
as evidence against the model.
