# X64.2 — Phase 10: Falsification

For each apparent pattern surfaced in Phases 1-9, the alternative,
non-treasury explanation is evaluated and — where the data supports it —
accepted instead.

## 1. The 4-launch, 9-minute density spike (14:45:26–14:54:23)

**Candidate hypothesis**: coordinated provisioning wave from a single
operator.

**Falsification test**: do the 4 launches share ANY evidence beyond
timing? Checked: creators (4 distinct), disposable wallets (4 distinct),
funding amounts (4 distinct: 0.112139, 0.022086, 0.008144, 0.088967 SOL —
no relationship), funding mechanism (all `WSOL_WRAP_CLOSE` — but that is
true of the entire 18-row set by construction of the query, not
distinguishing to this sub-window).

**Verdict: REJECTED as coordinated.** Nothing links these 4 launches
except proximity in time. Given pump.fun's overall launch volume, a
9-minute window containing 4 unrelated `WSOL_WRAP_CLOSE`-funded creators
is not distinguishable from ordinary platform density without an
independent baseline rate (not computable from this data — flagged as a
gap, not assumed away). Treated as coincidental clustering, not a
provisioning wave.

## 2. Rapid (1-2 second) migration across 17/18 tokens

**Candidate hypothesis**: shared operational tooling / a single
treasury's launch automation.

**Falsification test**: is 1-2 second create→migrate a distinguishing
signature of THIS cohort, or a known general property of
`WSOL_WRAP_CLOSE`-funded pump.fun launches broadly?

**Verdict: REJECTED as cohort-specific.** Per established prior findings
in this project (see memory: "Staged vs instant reframe" — 81% of
confirmed WATCHTOWER launches show INSTANT/uncatchable migration timing,
median ~1s), rapid migration is the *general* behavioural signature of
the WSOL_WRAP_CLOSE handoff mechanism across the WATCHTOWER dataset as a
whole, not something unique to these 18 launches. Its presence here is
consistent with "these are 18 more instances of the same broad mechanism
class," which is already the selection criterion — it adds no
*additional* evidence of a shared treasury beyond what the query already
selected for.

## 3. `B1cJJMstShf…`'s two launches, two different disposable wallets

**Candidate hypothesis**: operator rotating disposable infrastructure
per-launch as a deliberate evasion tactic.

**Falsification test**: is there evidence the wallets were coordinated
(shared upstream, shared creation pattern) rather than simply two
independently-obtained disposable wallets?

**Verdict: INSUFFICIENT EVIDENCE to accept OR reject** — genuinely
undetermined, not falsified. No hop2/upstream data exists for either
wallet (`wt_walkback_edge_candidates` is empty for both), so there is no
stored basis to confirm or rule out a shared origin. This is reported as
an open question requiring RPC to resolve (see "Where RPC would be
required," below) — not asserted as evidence of evasion.

## 4. `Dbvr7ktCbxq…` funding two mints for the same creator (0.001994 SOL,
   0.003994 for the sibling wallet — off by 0.002 SOL)

**Candidate hypothesis**: a deliberate, tooling-generated amount pattern
(e.g., a script incrementing a base amount).

**Falsification test**: is a 0.002 SOL difference between two DIFFERENT
wallets' amounts (not even the same wallet — `Dbvr7ktCbxq…` at 0.001994
and `GxyGhyQKv…` at 0.003994) more likely a deliberate pattern or
coincidental at this sample size (n=2)?

**Verdict: REJECTED as a fingerprint.** Two data points cannot establish
a tooling pattern; a coincidental ~0.002 SOL gap between two independent
small transfers (both under 0.004 SOL, a range where ATA-rent-adjacent
residual amounts are common — pump.fun WSOL wrap/close flows routinely
leave sub-0.01 SOL residuals from rent reclamation) is fully consistent
with ATA rent effects or simple randomness, not a distinguishing
signature. Not accepted as evidence.

## 5. "18 launches in a 24h/40h window is itself suspicious"

**Candidate hypothesis**: the sheer count implies a single active
operator.

**Falsification test**: does the volume alone, without any shared
identity/amount/wallet evidence, indicate common origin?

**Verdict: REJECTED.** 18 independent, `WSOL_WRAP_CLOSE`-funded,
rapid-migrating launches over 40 hours, each from a distinct creator using
a distinct disposable wallet, is equally consistent with 17-18
*independent* operators or creators all using the same publicly-known
WATCHTOWER handoff mechanism (which, per this project's own prior
findings, is a broadly reused pattern across many confirmed and suspected
WATCHTOWER operations, not something exclusive to one treasury). Volume
alone, absent any linking evidence, is not treated as clustering evidence.

## 6. The `HTog7L8R…` outlier (funding 12 days before token creation)

**Candidate hypothesis A**: this represents a long-dormant treasury
wallet activated after a multi-day delay — itself interesting treasury
behaviour.

**Candidate hypothesis B**: this is a walkback mis-selection — the
`_find_with_evidence(before_signature=create_sig, ...)` search picked the
closest-*preceding*-in-slot-order `WSOL_WRAP_CLOSE`-shaped transaction
within its bounded signature window, which may not be the creator's
actual immediate funding transaction if the true funding tx fell outside
the fetched page window (`SIG_PAGE_COUNT`/`TX_FETCH_LIMIT` bounds) or was
filtered for another reason.

**Falsification test**: does a 12-day gap match the "quick birth"
pattern this dataset is otherwise selected around? No — every other row
in the 18-set has a funding-to-CREATE gap of well under a day (most,
hours; several, under an hour). A 12-day gap is a ~280x outlier versus
the next-largest gap in this set.

**Verdict: Hypothesis B accepted as the more likely explanation, though
NOT independently confirmed without RPC.** This row is excluded from all
clustering/family analysis in this audit as a probable weak-match
artifact rather than genuine treasury-dormancy evidence, consistent with
this audit's evidence-preservation principle (mechanism evidence is kept
and reported, not silently discarded, but also not overinterpreted).

## Where RPC would genuinely be required (not performed in this audit)

Per the task's constraint, no RPC was issued. The following specific,
narrow questions could not be resolved from stored evidence and would
require it:

1. **Upstream convergence (Phase 4)** — determining whether any of the 18
   disposable wallets share a common unresolved upstream funder requires
   a hop2 walk (`getSignaturesForAddress` + `getTransaction` against each
   of the 18 disposable wallets) — zero such data currently exists in
   `wt_walkback_edge_candidates` for any of them. This is the single
   highest-value follow-up RPC investigation this audit identifies: it is
   the one question genuinely capable of turning "Low confidence, no
   shared evidence" into a positive treasury finding, and it is currently
   completely unanswered, not merely weakly answered.
2. **`B1cJJMstShf…`'s two disposable wallets' own funding origin** — same
   hop2 gap, specific to this one creator's two wallets, would confirm or
   reject whether they trace to a common source.
3. **`HTog7L8R…`'s true immediate funding transaction** — a fresh
   `getSignaturesForAddress` walk on the creator wallet
   `FpJ1LUmGzcqpbduH1p4WfTMm72enuZYeV1NS1Jg8TG6f` around its actual
   `created_at` (2026-07-20T14:22:13) would confirm whether a closer,
   correctly-matched funding transaction exists that the original walk
   missed, or whether the 12-day-old transaction genuinely is the
   creator's only funding event (which would then need separate
   explanation).

None of these were performed. They are named explicitly, per the task's
instruction, as the specific points where additional RPC is required and
why — not executed in this pass.
