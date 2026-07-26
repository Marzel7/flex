# X65.11 — Phase 3: Measure SubProvider Fan-Out

12 distinct sub-providers underlie the 19-launch cohort. For each, all
four requested measures are reported using the tables already
established as authoritative in prior investigations
(`wt_provisioning_edges` for distinct-creators-funded,
`wt_candidate_websocket_watches` for distinct-candidate/provisioning-wallet
counts, `wt_active_subprov_sessions` for session/funding-event count).

## Per-subprovider measurements

| SubProvider | Distinct provisioning wallets observed | Distinct candidate wallets observed | Creators funded (all-time, `wt_provisioning_edges`) | Launches produced (this 24h cohort) | Sessions recorded (all-time) |
|---|---|---|---|---|---|
| 5tzFkiKscXHK… | 0 | 0 | **68** | 5 | 53 |
| BmFdpraQhkiD… | 0 | 0 | **33** | 3 | 2 |
| Dv34prGm2BT7… | 0 | 0 | **18** | 2 | 10 |
| iGdFcQoyR2Mw… | 0 | 0 | **10** | 1 | 12 |
| 8mowmVCEewZ9… | 0 | 0 | **8** | 1 | 1 |
| 2EpHmj6CLGQJ… | 0 | 0 | 1 | 1 | 1 |
| 62meUYzzLJAL… | 0 | 0 | 1 | 1 | 1 |
| 8DWH19uhVTaz… | 0 | 0 | 1 | 1 | 2 |
| CnS6ZtLCnT5y… | 0 | 0 | 1 | 1 | 1 |
| 52XHKRHELcqz… | 0 | 0 | 0 | 1 | 1 |
| 5SNDBEZLHtQX… | 0 | 0 | 0 | 1 | 1 |
| Co2Q6mEkB7iG… | 0 | 0 | 0 | 1 | 1 |

## Distinct provisioning wallets / candidate wallets: zero coverage for this cohort

**Every one of the 12 sub-providers has zero rows in
`wt_candidate_websocket_watches`** — confirmed directly, not assumed
(`SELECT COUNT(DISTINCT candidate_wallet)` returns 0 for all 12). This
is the same evidence-coverage boundary already established in
X65.4/X65.8: `wt_candidate_websocket_watches` is populated exclusively
by the live cascade's real-time wrap-close detector
(`_handle_subprov_tx()`), and this specific 24-hour cohort's launches
were all resolved via the **walkback** path
(`walkback_class="FULL_WALKBACK"` for all 19, confirmed in Phase 2),
not the live cascade — so this evidence source, while real and
reliable where it exists, simply has no data for this particular
population.

## Creators-funded (all-time, `wt_provisioning_edges`): does exhibit fan-out for 5 of 12 sub-providers

5 of the 12 sub-providers show `>1` distinct creator funded across all
recorded history: **5tzFkiKscXHK…** (68 creators), **BmFdpraQhkiD…**
(33), **Dv34prGm2BT7…** (18), **iGdFcQoyR2Mw…** (10), and
**8mowmVCEewZ9…** (8). These 5 sub-providers **do exhibit operational
fan-out** by this measure — the same evidence source Topology's
existing `_subprov_sibling_counts()` function already reads.

The remaining 7 sub-providers show either exactly 1 creator (4
sub-providers: `2EpHmj6CLGQJ…`, `62meUYzzLJAL…`, `8DWH19uhVTaz…`,
`CnS6ZtLCnT5y…`) or 0 recorded creators at all (3 sub-providers:
`52XHKRHELcqz…`, `5SNDBEZLHtQX…`, `Co2Q6mEkB7iG…`) — for these, no
fan-out is evidenced by this source either.

## Launches produced (this 24h window)

3 sub-providers produced more than 1 launch within this specific
24-hour window: `5tzFkiKscXHK…` (5 launches), `BmFdpraQhkiD…` (3),
`Dv34prGm2BT7…` (2). These 3 are the same 3 sub-providers among the
top 5 all-time-fan-out sub-providers above (excluding `iGdFcQoyR2Mw…`
and `8mowmVCEewZ9…`, which each produced only 1 launch in this specific
24h window despite having real historical fan-out) — consistent with,
not contradicting, their broader observed reuse.

## Does each SubProvider exhibit operational fan-out?

| SubProvider | Fan-out determination |
|---|---|
| 5tzFkiKscXHK… | **Yes** — 68 creators funded all-time, 5 launches in this window alone |
| BmFdpraQhkiD… | **Yes** — 33 creators, 3 launches in this window |
| Dv34prGm2BT7… | **Yes** — 18 creators, 2 launches in this window |
| iGdFcQoyR2Mw… | **Yes** — 10 creators all-time (only 1 launch in this specific window, but the sub-provider's own historical fan-out is real and independently evidenced) |
| 8mowmVCEewZ9… | **Yes** — 8 creators all-time (1 launch in this window) |
| 2EpHmj6CLGQJ… | **No** — exactly 1 creator recorded |
| 62meUYzzLJAL… | **No** — exactly 1 creator recorded |
| 8DWH19uhVTaz… | **No** — exactly 1 creator recorded |
| CnS6ZtLCnT5y… | **No** — exactly 1 creator recorded |
| 52XHKRHELcqz… | **Insufficient evidence** — 0 recorded creator edges at all |
| 5SNDBEZLHtQX… | **Insufficient evidence** — 0 recorded creator edges at all |
| Co2Q6mEkB7iG… | **Insufficient evidence** — 0 recorded creator edges at all |

**5 of 12 (42%)** sub-providers in this cohort show direct, measured
fan-out evidence. **4 of 12 (33%)** show single-creator evidence only
(consistent with either genuine linear operation or simply not yet
having funded a second creator at time of measurement — this
investigation does not speculate which). **3 of 12 (25%)** have no
creator-edge evidence at all, and — per the confirmed zero coverage in
`wt_candidate_websocket_watches` above — no alternative fan-out
evidence source either, for this specific cohort.
