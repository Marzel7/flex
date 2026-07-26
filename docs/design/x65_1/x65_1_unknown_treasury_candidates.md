# X65.1 — Phase 6: Unknown-Treasury Candidate Classification

## Result for this cohort: zero `UNKNOWN_TREASURY_CANDIDATE` cases

This phase's classification applies when an upstream wallet is found
but is **not** already confirmed in `wt_confirmed_treasuries`. For this
specific 19-launch cohort:

- **Group A (7 launches)**: every upstream candidate found was already
  a `KNOWN_TREASURY` (Phase 5) — no unconfirmed candidate exists in
  this group.
- **Group B (12 launches)**: no upstream candidate wallet was found at
  all (Phase 3/4) — there is nothing to classify as
  `UNKNOWN_TREASURY_CANDIDATE` either, since that classification
  requires an actual candidate wallet to exist and simply lack
  confirmation. Group B proceeds to `UNRESOLVED` (Phase 7), not this
  category.

This is not a gap in the analysis — it is the honest outcome of this
specific 19-launch sample. The `UNKNOWN_TREASURY_CANDIDATE`
classification, its required capture fields (candidate wallet,
evidence path, creators/sub-providers funded, inbound/outbound SOL,
largest transfer, active date range, fanout behaviour, links to known
infrastructure, reasons unconfirmed), and its explicit
non-auto-promotion rules (no promotion from one transfer, wallet size
alone, matching amount tails, timing proximity, shared RPC observation,
or generic ATA rent patterns) remain fully specified and available for
any future cohort where a genuine unconfirmed candidate is found — none
was found here.

## Why this outcome is plausible, not suspicious

All three treasury wallets reached via this cohort's 7 `CONFIRMED_SUBPROV`
launches were **already** confirmed in `wt_confirmed_treasuries` well
before this task began (2026-06-11, 2026-06-14, and 2026-07-21
respectively — the most recent of the three predates this task's
execution). Given this project's history of active treasury discovery
and confirmation work (documented extensively in this project's own
operating memory — dozens of prior sessions dedicated to exactly this
kind of treasury-resolution work), it is entirely plausible that the
small number of `QUICK_BIRTH_MIGRATION`/`FRESH_CREATOR`/`UNKNOWN`-topology
launches that DO have a resolvable sub-provisioner hop resolve to
treasuries this project has already found and confirmed through other,
earlier investigation — the gap being closed by this task is the
**missing cross-reference join** (Phase 2's finding), not a gap in
treasury-discovery coverage itself.

## What would trigger this classification in a future cohort

For completeness, and to document the design even though it wasn't
exercised: an upstream wallet would be classified
`UNKNOWN_TREASURY_CANDIDATE` (never auto-promoted to `KNOWN_TREASURY`)
when it has real, multi-launch, treasury-scale funding behavior (per
Phase 4's `wt_active_subprov_sessions`-style evidence: funds multiple
distinct sub-provisioners across a real date range, moves non-trivial
SOL volume) but does **not** yet appear in `wt_confirmed_treasuries`.
Per the task's explicit prohibition, none of the following would ever
be sufficient alone to promote such a candidate: a single transfer,
wallet size alone, matching amount tails, timing proximity, shared RPC
observation, or generic ATA-rent patterns. Promotion remains a
human-in-the-loop decision via whatever existing confirmation process
already populates `wt_confirmed_treasuries` (the same `3SIGNAL`/
`subprov_funder_trace`/manual-override methods already seen in Phase 5)
— this task does not add, bypass, or shortcut that process.
