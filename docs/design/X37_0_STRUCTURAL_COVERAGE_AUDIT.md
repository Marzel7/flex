# X37.0 — Structural Coverage Audit

Investigation only. No code changes, no schema proposals. Follows
[X33.0](X33_0_CANONICAL_MOTIF_DISCOVERY.md)–[X36.0](X36_0_OPERATION_FINGERPRINT_DISCOVERY.md).
Code-lineage findings below come from a dedicated read-only code trace (Explore agent,
citing file:line); coverage numbers are from live SQL against `database/wt_ops_v2.db`,
run 2026-07-20.

## Correction to X36.0, discovered during this pass

X36.0 reported "6 of 58 confirmed treasuries appear in `wt_provisioning_edges`." That
claim was actually based on presence in `wt_watchtower_launches.treasury_wallet`, a
**different** table, not `wt_provisioning_edges`. Re-checked directly here:

```sql
SELECT COUNT(*) FROM wt_confirmed_treasuries ct
JOIN wt_provisioning_edges pe ON pe.from_wallet = ct.treasury
```
returns **0**. **Zero of 58 confirmed treasuries appear as a `from_wallet` in
`wt_provisioning_edges`, not 6.** The true figure is worse than X36.0 stated. This is
corrected in Phase 5 below and should be treated as superseding the X36.0 number.

## Phase 1 — Schema Relationship (from code trace)

| Table | Written by | Trigger | Evidence level | Complete or windowed? | Append-only or revised? |
|---|---|---|---|---|---|
| `wt_provisioning_edges` | `_upsert_edge()`, `src/ops/provisioning_edges.py:106-147`, called only from `src/core/walkback_worker.py:552-553` | Scheduled worker draining `wt_walkback_queue` | Structural funding-edge ledger, deliberately excludes confidence/promotion state (module docstring, provisioning_edges.py:1-18) | **Windowed** — only records from module deployment onward (X21B); no backfill of pre-existing edges | Append-preserving: `first_observed_by_flex` never overwritten; `last_observed_by_flex`/amount/mechanism updated on re-observation |
| `wt_confirmed_treasuries` | 4 separate paths: `promote_to_confirmed()` (manual), `auto_confirm_from_launch_chain()` (automated, called from `operation_scheduler.py:646`), `/subprov-link` set action (manual, `operation_dashboard_routes.py:3232`), approve-candidate endpoint (manual, `:3865`) | Mixed: 2 manual dashboard actions, 1 automated behavioral trigger | Identity-confirmation registry, not a structural ledger | Intended as **current authoritative state**, not a historical record | Mostly first-write-wins; the approve-candidate path allows `DO UPDATE`; `revert_auto_promotion()` performs a hard `DELETE` (seed treasuries protected) |
| `wt_subprov_evidence` | `promote_to_subprov()`, `src/core/ws_cascade_store.py:999-1029`, called from `src/core/ws_cascade.py` (3 call sites) | Real-time WS cascade daemon only | Raw wrap-close observation record | **Windowed** — only from cascade-daemon deployment onward | Strictly append-only/immutable by design (docstring: "ALWAYS preserved... never suppressed") |
| `wt_watchtower_launches` | `record_launch()`, `ws_cascade_store.py:2190-2219` (real-time), plus `watchtower_backfill.py:140-149` (bounded backfill, `WT_BACKFILL_DAYS` default 2 days) | Real-time WS cascade + a narrow rolling backfill job | Live cascade's own detection record | **Windowed, not historical** — live detection starts at cascade deployment; backfill only covers ~2 days | Append-only (`INSERT OR IGNORE`), one narrow UPDATE limited to a provenance tag field |
| `wt_walkback_queue` | `enqueue_migration()`, `src/core/walkback_queue.py:303-346`, called from `watchtower_attribution.py:147` (migration intake) | Real-time per-migration event (Layer 1 of attribution pipeline) | Work queue + retained history of processed items | Queue-plus-history hybrid, not a pure ledger | Rows are mutated in place through status lifecycle (`pending→running→complete/failed`); `force=True` performs `DELETE`+re-insert |

### Evidence-flow diagram (as actually wired, not as it "should" be)

```
migration event ──▶ enqueue_migration() ──▶ wt_walkback_queue (pending)
                                                    │
                                    walkback_worker.py (scheduled loop)
                                                    │
                                    ┌───────────────┴────────────────┐
                                    ▼                                ▼
                     capture_provisioning_relationship()      status → complete
                                    │
                                    ▼
                          wt_provisioning_edges


real-time wrap-close event (ws_cascade.py) ──▶ promote_to_subprov() ──▶ wt_subprov_evidence
                                            └─▶ record_launch()       ──▶ wt_watchtower_launches
                                                                             │
                                                            watchtower_backfill.py (±2 days)


operation_scheduler.py (LAUNCH_CHAIN detection) ──▶ auto_confirm_from_launch_chain()
dashboard human actions ──▶ promote_to_confirmed() / subprov-link / approve-candidate
                                            └─▶ wt_confirmed_treasuries
                                            (no write path INTO wt_provisioning_edges
                                             or wt_watchtower_launches from here)
```

**The critical structural fact**: `wt_confirmed_treasuries` and `wt_provisioning_edges` are
fed by **two entirely separate pipelines with no cross-write in either direction**. A
treasury confirmed via `LAUNCH_CHAIN` detection never gets a corresponding row in
`wt_provisioning_edges` unless the *same mint* independently also passes through
`enqueue_migration()` → the walkback worker. This is confirmed as a deliberate
architectural choice from the `provisioning_edges.py` docstring (it explicitly says it does
NOT capture confidence/promotion state — i.e., it was designed to stay decoupled from the
confirmation registry), not an accidental omission of the join itself. Whether the
*consequence* of that decoupling (zero overlap in practice) is intended is a separate
question addressed in Phase 2.

## Phase 2 — Explaining the Missing Treasuries (52, revised to 58 given the corrected figure)

Since the corrected coverage figure is 0/58 (not 6/58), Phase 2 is reframed: explain why
**all 58** confirmed treasuries lack `wt_provisioning_edges` rows, using `confirmed_at`
timestamps and `method` as the evidence trail (no speculation beyond what these fields
support):

| method | count | confirmed_at range | Explanation category |
|---|---|---|---|
| LAUNCH_CHAIN | 37 | 2026-06-17 to 2026-06-30 | **Pipeline separation** — the LAUNCH_CHAIN detector (`operation_scheduler.py`) observed a full chain in-memory/at-confirmation-time but writes only to `wt_confirmed_treasuries`; it never calls `capture_provisioning_relationship()`. The underlying chain evidence existed at confirmation time but was not persisted into the edge ledger. |
| 3SIGNAL / 3SIGNAL+ORIGINAL | 6 | 2026-06-11 (single batch timestamp) | **Different evidentiary method entirely** — these are confirmed via a signal-based method that doesn't touch the walkback/edge pipeline at all; no structural edge capture was ever expected for this method. |
| REVIEW_PROMOTED | 4 | 2026-06-11 to 2026-06-13 | **Manual promotion** — a human "✓ promote" action confirms identity directly; this path does not run `capture_provisioning_relationship()` as part of promotion. |
| subprov_funder_trace | 7 | 2026-06-15 to 2026-07-02 | **Different evidentiary method** — confirmed via an RPC-verified subprov-link action in the dashboard, a separate manual-trace pipeline from the walkback worker. |
| human_review_recovery_safe | 2 | 2026-06-22 to 2026-07-05 | **Manual approve-candidate action** — same reasoning as REVIEW_PROMOTED, a distinct approval endpoint. |

None of the 58 fall into "outside retention window," "pipeline failure," "filtered
intentionally," or "unknown" — every treasury's absence from `wt_provisioning_edges`
is explained by **which confirmation method was used**, and every method except
LAUNCH_CHAIN was never expected to populate the edge table by design. LAUNCH_CHAIN is the
one exception worth flagging distinctly: it is the only method where a full structural
chain genuinely existed at confirmation time and simply wasn't captured into
`wt_provisioning_edges` — a real, evidence-backed **persistence gap**, not an
"expected separation," for 37 of the 58 (64%).

## Phase 3 — Coverage Matrix

| Evidence type | Table | Count with evidence / 58 | % |
|---|---|---|---|
| Provisioning edges (structural funding graph) | `wt_provisioning_edges` | 0 | 0% |
| Launch evidence (live cascade detection) | `wt_watchtower_launches` | 6 | 10.3% |
| Subprov evidence (via known subprov from launches) | `wt_subprov_evidence` | 4 | 6.9% |
| Walkback evidence (queue/lineage record) | `wt_walkback_queue` | 9 | 15.5% |
| **Complete lineage** (launch AND walkback both present) | — | 5 | 8.6% |

No confirmed treasury has all four evidence types simultaneously in this pass (the
"complete lineage" figure above checks only launch+walkback, the two broadest;
adding subprov-evidence and provisioning-edges would only lower it further, since
provisioning-edges is 0%).

## Phase 4 — Historical Completeness / Recoverability

| Missing-evidence group | Count | Recoverability | Why |
|---|---|---|---|
| LAUNCH_CHAIN treasuries missing provisioning_edges | 37 | **Partially recoverable** | The underlying on-chain transaction history (wrap-close sigs, funding txs) still exists on Solana; a targeted re-walk via `enqueue_migration()`/the walkback worker for these specific mints could populate `wt_provisioning_edges` retroactively. Not "fully recoverable" without knowing this pass did not verify RPC availability for all 37 mints individually. |
| 3SIGNAL / REVIEW_PROMOTED / subprov_funder_trace / human_review_recovery_safe treasuries missing provisioning_edges (21 total) | 21 | **Partially recoverable, lower priority** | Same on-chain data in principle still exists; but these methods were never designed to populate the edge table, so "recovering" them means running an entirely separate walkback pass on wallets that were confirmed through non-structural evidence — feasible but not a gap-fill, a new investigation. |
| Treasuries missing from `wt_watchtower_launches` (52/58) confirmed before cascade deployment or outside its ~2-day backfill window | subset of the above | **Permanently unrecoverable via this table specifically** — the live cascade only detects in real time; a treasury whose launches predate cascade deployment has no way to retroactively populate `wt_watchtower_launches` beyond the 2-day backfill window. This does not mean the underlying launch is unrecoverable in general (RPC/walkback could still reconstruct it into a different table), only that *this specific table* cannot be backfilled further. |

No missing treasury falls into "permanently unrecoverable in every sense" — in every
case, the underlying on-chain evidence should still exist (Solana history is not pruned at
the RPC layer for the timeframes involved here, per the RPC investigation discipline
already established in project memory); the limitation is which **internal tables** can or
cannot be retroactively populated, not whether the ground-truth chain data still exists.

## Phase 5 — Bias Assessment for X33.0–X36.0

**X33.0 (Canonical Motif Discovery)**: Built primarily from `wt_provisioning_edges`,
`wt_watchtower_launches`, `wt_capital_reloads`, `wt_vanity_families` — i.e., the
structural/live-cascade tables, not `wt_confirmed_treasuries`. **Largely unaffected** by
the coverage gap found here, because it never claimed to represent *all confirmed
WATCHTOWER treasuries* — it measured motifs within whatever the edge/launch tables
contained. However, X33.0's Motif 8 ("treasury reuse concentration," using
`wt_watchtower_launches.treasury_wallet`) implicitly treated its 5-6-treasury sample as
representative of confirmed-WATCHTOWER treasury behavior broadly. Given that this is
only 6/58 (10%) of all confirmed treasuries, **Motif 8's magnitude claims (e.g. "top 2
treasuries = 74% of attributed launches") should be read as describing the small subset of
treasuries visible to the live cascade, not confirmed WATCHTOWER as a whole.** Estimated
bias: **moderate** — the direction of the finding (treasury reuse is concentrated) is
likely still true, but the specific percentage is only representative of a 10% slice.

**X34.0 (Primitive Sufficiency Audit)**: The SEEDED_ACCOUNT_CLOSE decode used
`wt_watchtower_launches` signatures and confirmed via direct on-chain RPC decode — this
conclusion is **unaffected** by the coverage gap, since it was validated against raw
transaction data, not against table completeness. Primitive A/B's existence claims stand
regardless of which treasuries happen to have edge-table rows.

**X35.0 (Primitive Generalisation Audit)**: Explicitly used `wt_walkback_queue` treasuries
NOT in `wt_confirmed_treasuries` — this pass's population was deliberately outside the
confirmed set, so it is **unaffected** by the confirmed-treasury coverage gap found here.
However, X35.0's comparison baseline ("confirmed WATCHTOWER" capital-scale figures, e.g.
270 SOL avg bulk capitalization) was itself drawn from the same narrow 6-treasury
launch-evidence slice X33.0 used — so X35.0's **similarity comparisons inherit the same
moderate bias**: the "WATCHTOWER baseline" it compared against was never a full
58-treasury baseline, only the visible 10%.

**X36.0 (Operation Fingerprint Discovery)**: **Most affected.** X36.0's central
finding was built directly on the (corrected) coverage gap and used it appropriately as a
caveat — but the specific number it reported (6/58) was wrong; the true number is 0/58.
This changes X36.0's framing from "a small overlap exists and is usable as a
structural sample" to "there is no overlap at all between the identity-confirmation
registry and the structural edge table." X36.0's Phase 1 "confirmed treasuries with
structural fingerprints (n=6)" table was actually built from `wt_watchtower_launches`
only, not `wt_provisioning_edges` — the table itself remains factually accurate (its
numbers came from real launches data), but its **labeling was imprecise** in a way that
this audit's Phase 1 correction resolves. The magnitude of X36.0's similarity-score
estimates (25-35% for Cluster 4) does not change, since those were never claiming to be
representative of all 58 treasuries — but the framing of "6/58 have structural coverage"
should be revised to "0/58 have provisioning-edge coverage; 6/58 (10.3%) have
launch-table coverage; 9/58 (15.5%) have walkback coverage; 5/58 (8.6%) have both launch
and walkback."

**Conclusions that should be revised**: X36.0's specific "6 of 58" statement (Data-source
note and Phase 1) is factually corrected here to "0 of 58 in provisioning_edges; 6 of 58 in
launches." No other numeric conclusion in X33–X36 requires revision — the qualitative
takeaways (primitives generalize on shape but not amplitude; no third primitive; low
similarity scores) all survive because none of them depended on the specific 6-vs-0
distinction.

## Phase 6 — Runtime Readiness

| Capability | Readiness | Justification |
|---|---|---|
| Primitive detection (A/B mechanism classification) | **READY** | Validated directly via on-chain decode (X34.0) and reproduced at scale across multiple independent tables (`wt_subprov_evidence` n=79,974, `wt_provisioning_edges` n=1,022) — does not depend on `wt_confirmed_treasuries` coverage at all. |
| Operation clustering (grouping wallets into candidate operations) | **NOT READY** | Requires the structural edge graph (`wt_provisioning_edges`) to be linked to confirmed identity (`wt_confirmed_treasuries`), and that link is currently 0% populated. Clustering today would only ever see the ~10-16% of confirmed treasuries visible via launches/walkback, silently excluding the other 84-90%. |
| Behavioural fingerprinting | **PARTIALLY READY** | X36.0 showed fingerprint dimensions (fan-out breadth, Primitive-A rate) are computable and meaningful in isolation, but any fingerprint claiming to characterize "confirmed WATCHTOWER" is actually only characterizing the small visible slice — usable for methodology validation, not yet for confident operation-level claims. |
| Unknown-operation discovery | **NOT READY** | This depends on comparing new candidate treasuries against a reliable WATCHTOWER baseline fingerprint; since that baseline itself only reflects 10-16% of confirmed treasuries, any similarity score computed against it inherits an unknown (and likely non-trivial) sampling bias. |
| Runtime scoring (real-time confidence assignment) | **NOT READY** | Same reasoning as clustering — a runtime scorer trained or calibrated against the current partial overlap would systematically miss or misjudge the 84-90% of confirmed-treasury behavior that never appears in the structural tables it would score against. |

## Ranked List of Structural Gaps by Impact

1. **Zero overlap between `wt_confirmed_treasuries` and `wt_provisioning_edges`** (highest
   impact) — blocks any identity-linked structural analysis; affects clustering, unknown-
   operation discovery, and runtime scoring directly.
2. **`wt_watchtower_launches` coverage is only 10.3% of confirmed treasuries**, driven by
   its windowed nature (real-time cascade + narrow 2-day backfill) — this is the practical
   ceiling on how much of confirmed-WATCHTOWER behavior is even observable in the tables
   used by X33–X36.
3. **LAUNCH_CHAIN treasuries (37/58, 64% of all confirmed treasuries) have evidence that
   existed at confirmation time but was never persisted into the structural tables** — the
   single largest, most concretely recoverable gap, since the chain was actually observed
   once, just not written to `wt_provisioning_edges`.
4. **No single table currently provides "complete lineage"** (launch + walkback + subprov +
   edges together) for more than 8.6% of confirmed treasuries — this caps the precision of
   any fingerprint or clustering claim that wants to reason about a treasury's full
   lifecycle rather than one observed facet of it.

## Answer to the stated success criterion

**No — the current data model does not provide sufficiently complete structural coverage
to support reliable operation-level analysis, and completeness breaks down specifically at
the identity-to-structure join.** Primitive-level detection (X34.0) is solid and
independent of this gap. But every capability that requires linking a *confirmed identity*
to its *structural funding graph* — clustering, fingerprint-based operation discovery,
runtime scoring — is currently operating on 0% direct edge-table overlap and at best an
8.6% complete-lineage sample via indirect tables. This is best characterized as an
**architectural separation between two independently-evolved pipelines (identity
confirmation vs. structural capture) compounded by one concrete persistence gap**
(LAUNCH_CHAIN's 37 treasuries, whose chain evidence existed but was never written to the
edge ledger) — not a fundamental flaw in the primitive model itself, which remains sound.
