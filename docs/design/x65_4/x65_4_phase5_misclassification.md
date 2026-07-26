# X65.4 — Phase 5: Quantify Misclassification

Population-level check across the full live Discovery cohort (7-day
window, 4,194 launches with a topology assignment), cross-referencing
each classified launch's subprov wallet against
`wt_candidate_websocket_watches` (the platform's own already-recorded
wrap-close destination history — the same evidence source used in
Phases 3/4).

## Population baseline

| Topology | Count | % of total |
|---|---|---|
| `MULTI_LEVEL_FAN_OUT` | 233 | 5.6% |
| `MESH` | 0 | 0.0% |
| `FAN_OUT` | 462 | 11.0% |
| `LINEAR` | 780 | 18.6% |
| `UNKNOWN` | 2,718 | 64.8% |
| **Total** | **4,194** | **100%** |

## Linear launches with verified SubProv fan-out

| Result | Count | % of LINEAR population |
|---|---|---|
| No candidate-watch data available (cannot verify either way) | 777 | 99.6% |
| Verified genuinely single-recipient (`n=1`) | 1 | 0.1% |
| **Verified real fan-out (`n>1`) despite a `LINEAR` label** | **2** | **0.3%** |

Only 2 of 780 `LINEAR`-classified launches in the current 7-day
population have any directly-verifiable contradiction via
`wt_candidate_websocket_watches` — a small absolute number, but this
reflects data availability, not classifier correctness: **99.6% of the
`LINEAR` population has zero candidate-watch data to check against at
all**, because most Discovery launches (walkback-resolved, not
live-cascade-confirmed) never populate that table in the first place
(Phase 4's core finding). The true rate of `LINEAR` mislabeling among
launches that *do* have real underlying subprov fan-out cannot be
established from this data source for the 99.6% with no coverage — it
can only be bounded from below at 0.3% (2 confirmed cases) and is
almost certainly higher, per the 43-confirmed-launch cohort's much
starker 88.4% mismatch rate (Phase 3/4), which had far better
candidate-watch coverage precisely because those launches came through
the live cascade.

## Multi-Level launches with no fan-out

Checked all 233 `MULTI_LEVEL_FAN_OUT` launches: this classification
uses entirely different evidence (walkback-selected hop chains and
`SUBPROV_SESSION_OPENED_WS` sub-subprov lineage, per Phase 1) which is
internally self-consistent by construction (a launch is only assigned
this label when a specific already-selected multi-hop chain or
sub-subprov relationship is directly recorded) — no case was found
where a `MULTI_LEVEL_FAN_OUT` label lacked its own supporting evidence.
This dimension of misclassification (a false-positive Multi-Level
label) is not evidenced in the current population; the finding in this
investigation is specifically about **false-negative** Fan-Out
detection (real fan-out mislabeled `LINEAR`/`UNKNOWN`), not
false-positive Multi-Level assignment.

## Unknown launches where topology could now be determined

| Result | Count | % of UNKNOWN population |
|---|---|---|
| Have a resolvable subprov wallet at all | 63 | 2.3% |
| **Now determinable via `wt_candidate_websocket_watches`** | **0** | **0.0%** |

None of the 63 `UNKNOWN` launches with a resolvable subprov wallet have
any `wt_candidate_websocket_watches` history — consistent with the
finding above: these are walkback-resolved launches that never passed
through the live cascade's real-time wrap-close detector, so this
particular evidence source cannot resolve them either. This does not
mean their true topology is unknowable in principle — only that this
specific, already-persisted data source (the one shown in Phases 3/4
to correctly reveal fan-out for cascade-confirmed launches) has no
coverage for this subset of the `UNKNOWN` population.

## Reconciling the population-level numbers with the 43-launch confirmed-cohort numbers

The population-level check (above) finds a much smaller number of
directly-provable mismatches (2 `LINEAR`, 0 `UNKNOWN` resolvable) than
the 43-launch confirmed-WATCHTOWER replay (Phase 3/4: 21 of 21 scored
launches misclassified, 88.4% fan-out rate among all 43). This is not
a contradiction — it reflects a **coverage gap in the verification
data itself**, not a difference in the underlying defect. The 43
confirmed launches were specifically selected because they passed
through the live cascade (`wt_watchtower_launches`), which is the only
pathway that populates `wt_candidate_websocket_watches`. The broader
4,194-launch Discovery population is dominated (survivorship: 2,718 of
4,194, 64.8%) by walkback-resolved launches that never touch the live
cascade at all, so the verification evidence this phase relies on
simply doesn't exist for most of them. **The true population-wide
misclassification rate is very likely closer to the 43-launch cohort's
88.4% than to the 0.3%/0.0% figures above** — those lower figures are
an artifact of where independent verification evidence happens to
exist, not evidence that most of the population is correctly
classified. This distinction is treated as a load-bearing finding for
Phase 6/7, not glossed over.
