# X65.2 — Phase 4: Population Analysis

Groups the 12 unresolved launches by earliest failure point (Phase 3).

## Groups

| Group | Count | Percentage |
|---|---|---|
| Missing CREATE (Program CREATE observed = NO) | 0 | 0% |
| **Missing CREATE Ledger** (Program CREATE + Birth persisted both YES, CREATE ledger = NO) | **12** | **100%** |
| Missing Funding (CREATE ledger YES, Funding captured = NO) | 0 | 0% |
| Missing Walkback (Funding captured YES, Walkback queued = NO) | 0 | 0% |
| Missing Treasury Link (Walkback/SubProv YES, Treasury linked = NO) | 0 | 0% |
| Missing Topology (Treasury linked YES, Topology derived = NO) | 0 | 0% |
| Other | 0 | 0% |

## Single group: Missing CREATE Ledger (100%, 12/12)

**Count**: 12. **Percentage**: 100% of the unresolved cohort.

**Representative launches**: all 12 are equally representative, since
this is a single, uniform population with no internal variation in
earliest-failure stage — `CmoCuZ9J2YT1QHv28p3QRphhZot6Sdbu6P6Aw4Vmpump`
and `9Mn2t7yX2TmSSMEsQqDnFvcmNAGVCPhjevXpKfqgpump` are used below as
the two most-documented representative cases (one ordinary case, one
with the additional partial-recovery evidence).

**Supporting evidence**:
- `token_analysis.create_tx_signature` is `NULL` for all 12, while
  `pf_ws_creator`/`earliest_tx_creator` are correctly populated for all
  12 — proving the CREATE event was observed but its durable signature
  was never retained.
- `wt_create_event_ledger` has zero rows for all 12.
- 10 of 12 show a direct `[PUMPPORTAL] 🟢 Birth]` log line confirming
  the birth event was received and processed by the listener at the
  time; the remaining 2 (`B3Fq8SqBtsxsWw...`, `71TKvknpvwRcjd...`)
  show the same downstream symptom but their birth-time log evidence
  has since rotated out of the 4 retained log files.
- The proximate write-path mechanism (from the prior investigation
  pass, still valid and unchanged by this pass): a migration-time
  creator re-extraction function
  (`_update_token_entry_with_creator()`,
  `pumpfun_curve_listener.py:7933`/`:7963`) performs an unconditional
  `UPDATE ... SET create_tx_signature=?` with no `COALESCE`, which
  overwrites an already-correct birth-time signature with `NULL`
  whenever its own independent migration-time RPC re-validation
  doesn't reconfirm the transaction.
- A second, independent contributing factor surfaced in this pass'
  Phase 1: the `watchtower_listener` process crash-loops chronically
  across the entire 2026-07-15→07-21 window (3,224 restarts total,
  median gap ~6.3 minutes, 43.7% of gaps under 5 minutes). **8 of the
  12 launches occurred within ±30 minutes of a listener restart** (5
  within ±5 minutes), meaning in-memory state the birth/migration
  handlers depend on (`_portal_vsol`, cached creator lookups) may have
  been reset mid-sequence for a meaningful fraction of this cohort,
  independent of and additive to the clobber mechanism.

**Why one single group explains the whole 12-launch cohort**: no
launch in this cohort shows a different earliest-failure pattern —
there is no launch that fails at Funding, Walkback, SubProv, or
Treasury-Link as its *first* gap. This is a strong signal that a
single, well-defined mechanism (the CREATE-ledger/signature
persistence gap) is fully responsible for this cohort's population,
rather than the cohort being an assortment of unrelated failure modes.

## Empty groups (explicitly confirmed absent, not merely unchecked)

- **Missing CREATE**: would require `pf_ws_creator` also being unset —
  checked and false for all 12 (Phase 1/2).
- **Missing Funding** (as the *earliest* gap): would require CREATE
  ledger to be `YES` with Funding captured `NO` — CREATE ledger is
  `NO` for all 12, so Funding captured's own `NO` status is not this
  cohort's earliest gap, even though it is also `NO` in absolute terms.
- **Missing Walkback**: Walkback is `YES` (complete) for all 12 — not
  a failure point for any launch in this cohort.
- **Missing Treasury Link / Missing Topology**: both downstream of
  SubProv identified, which is `NO` for all 12, but SubProv's `NO`
  status is itself downstream of the earlier CREATE-ledger gap in
  pipeline order, so it is never the *earliest* recorded failure here.
- **Other**: no launch showed evidence inconsistent with the single
  Missing-CREATE-Ledger pattern.
