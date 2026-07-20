# X29.10 — Operational Fan-Out Semantics Audit

Investigation only, per the brief. No code changed. Builds on X29.9's persisted-evidence enumeration for `ANenEukvmpYsyP52LgDsZN6kj3n7igjbJDTCtj4xCAXq`; no new RPC or DB writes performed.

## Category enumeration for ANen

**Category A — Provisioned wallets** (`wt_subprov_evidence`, wallets that received operational funding from ANen regardless of outcome): **26** wallets, each with `wrap_close_sig`, `amount_sol`, `observed_at` (range: 1784048376–1784048663, i.e. HTR9U7 is the *last* of the 26, not a separate event).

**Category B — Observed candidates** (`wt_candidate_websocket_watches`, promoted to live WS observation): **25** wallets —
- `state=EXPIRED_SIBLING, close_reason=sibling_idle`: 24
- `state=FIRED_CREATE, close_reason=create`: 1

**Category C — Confirmed creators** (`wt_watchtower_launches`, produced `FIRED_CREATE` and an authoritative launch row): **1** wallet (HTR9U7, `create_time=1784048633`).

## Subset relationship

Verified directly (every Category B/C wallet address checked against Category A): **strict nesting, no exceptions** —

```
Provisioned (A, 26)  ⊇  Candidates (B, 25)  ⊇  Confirmed creators (C, 1)
```

The one wallet in A not in B (`2josE3T5...`, X29.9's "orphan") is the only non-trivial set difference; every member of B is a member of A, and the sole member of C is a member of both A and B. This is a clean, well-behaved funnel — not a "something more complex" case the brief allowed for.

## What `fan_out`/`creator_count`/`historical_launches` actually measure today

Traced directly in `src/ops/operational_lineage.py` and `src/ops/operations_summary.py`:

| Metric | Code | Reads from | Actually measures |
|---|---|---|---|
| `fan_out_count` (lineage, per-subprovider node) | `_fan_out_count()`, `operational_lineage.py:130` | `wt_provisioning_edges` (`SUBPROV_TO_CREATOR`) ∪ `wt_watchtower_launches.creator_wallet` | **Category C only** — confirmed creators. For ANen this returns 1. |
| `historical_launches` (lineage, per-subprovider node) | `_historical_launch_count()`, `operational_lineage.py:153` | `COUNT(*) FROM wt_watchtower_launches WHERE subprov_wallet=?` | **Category C only** — confirmed launches. For ANen this returns 1 (identical to `fan_out_count` for this wallet, since ANen has exactly one confirmed creator so far). |
| `creator_count` (operations summary, per-operation) | `_creators_for_treasuries()`, `operations_summary.py:66` | `DISTINCT creator_wallet FROM wt_watchtower_launches WHERE treasury_wallet IN (...)` | **Category C only** — confirmed creators, aggregated across the whole operation's treasuries. |
| "Launch count" (`recent_launch_count`, operations summary) | `operation["launch_count"]`, sourced from `operation_identity.py`'s `build_operations()` | `wt_watchtower_launches`, grouped per treasury | **Category C only** — confirmed launches, same source as the above, at operation scope. |

**None of the four currently-exposed metrics measure Category A (provisioned) or Category B (candidate) at all.** Every one of them is a different aggregation scope (per-subprovider-node vs. per-operation) over the *same* Category-C-only source tables. There is no code path anywhere in `operational_lineage.py` or `operations_summary.py` that reads `wt_subprov_evidence` or `wt_candidate_websocket_watches`.

## Where Discovery conflates the concepts

Exactly one conflation was found, and it is significant: `templates/discovery.html:933` renders `fan_out_count` under the label **"Fan-out"** with no qualifier. "Fan-out" as a plain English term (and per the brief's own framing throughout X29.5–X29.9) naturally reads as *operational* fan-out — how many wallets did this subprovider actually reach — which is Category A's job (26 for ANen). What the UI actually displays is Category C's count (1). This is not merely an underestimate; it inverts the expected magnitude relationship an investigator would assume ("fan-out" sounds like it should be the *largest* of the three numbers, but the code silently serves the *smallest*).

`historical_launches` (same node, right below "Fan-out" in the UI) is honestly labeled and honestly Category C — no conflation there, since "launches" unambiguously implies confirmed creation events.

`operations_summary.py`'s `creator_count`, labeled plainly "Creator" in the Operations panel (`discovery.html:1249`), is also honestly labeled — "Creator" implies a confirmed creator wallet, which is exactly what it measures.

**So the conflation is narrow and specific**: only the word "Fan-out" attached to `fan_out_count` is doing work its underlying data doesn't support. Every other current label matches its underlying metric's actual scope.

## Which interpretation is true for "Fan-out = 1"

Per the brief's four possible readings — "one funded wallet / one candidate / one creator / one launch" — the true answer is: **one confirmed creator, which also happens to be one confirmed launch** (Category C, and for ANen specifically, C's single member is also the only wallet in C, so "creator" and "launch" collapse to the same count here). It is emphatically **not** "one funded wallet" (there were 26) and **not** "one candidate" (there were 25). An investigator reading "Fan-out = 1" would reasonably conclude ANen is a low-activity or one-off subprovider — the opposite of X29.8/X29.9's finding that ANen funded at least 26 wallets and was placed under live candidate watch for 25 of them within a five-minute window.

## Should provisioned-but-never-launched wallets count as operational intelligence?

Yes, under the platform's own existing philosophy, already expressed elsewhere in the codebase. Two pieces of precedent support this, found directly in the schema and prior memory:

1. **`sibling_idle`/`EXPIRED_SIBLING` is itself a recorded, first-class classification outcome**, not a deletion or an absence of evidence (X29.9's finding — the row persists with its `close_reason` populated). The platform already treats "this wallet was funded and observed, but excluded from launch attribution by the sibling-suppression rule" as a fact worth keeping, not discarding.
2. **The existing buy-swarm philosophy** (memory: `buy-swarm-vs-creator` — "wrap-close funds BOTH creators AND fan-out buy-swarms; discriminator: subprov funding MANY wallets at SAME instant = buy-swarm... reject/don't arm") already establishes that a subprovider's *total* funding fan-out — including swarms and siblings that never become creators — is meaningful evidence of the subprovider's *behaviour*, even when none of those specific wallets individually becomes an attributable launch. The whole reason the sibling-suppression rule exists is to correctly interpret operational funding patterns, which presupposes that the funding pattern itself (Category A) is the thing being reasoned about, with launch-confirmation (Category C) as a downstream filter, not the definition of the activity.

Given that, provisioned wallets that never launch are not noise to be hidden — they are direct evidence of *how* a subprovider operates (burst size, sibling-suppression rate, dust-spray vs. genuine provisioning), which is exactly the kind of signal X29.5's audit found missing from Discovery's presentation.

## Can Discovery identify an operation using only confirmed launches?

No, not reliably, per X29.7.1/X29.8/X29.9's own findings restated in this vocabulary: using Category C alone, ANen presents as a subprovider with exactly 1 confirmed creator — indistinguishable from a one-off, incidental funder. The operational reality (26 provisioned wallets, 25 placed under live candidate observation within a five-minute burst) is invisible unless Category A and/or B are surfaced. Confirmed launches (C) answer "what did this subprovider's activity ultimately produce," which is a real and useful question, but it is not the same question as "how does this subprovider actually operate" — the latter requires A and B as visible, separate layers, not folded into or replaced by C.

## Formal definitions (deliverable)

- **Operational fan-out** (Category A): the count of distinct wallets that received operational funding from a subprovider, via any recorded funding-evidence table (`wt_subprov_evidence`, extendable to other mechanism-specific evidence tables), regardless of whether that wallet was ever watched as a candidate or ever produced a launch. For ANen: **26**.
- **Candidate fan-out** (Category B): the count of distinct wallets promoted from operational funding into live WS candidate observation (`wt_candidate_websocket_watches`), together with their terminal `state`/`close_reason`. For ANen: **25** (24 `EXPIRED_SIBLING`/`sibling_idle`, 1 `FIRED_CREATE`/`create`).
- **Confirmed creator count**: the count of distinct wallets among the candidates that reached `state=FIRED_CREATE` and produced an authoritative row in `wt_watchtower_launches`. For ANen: **1**.
- **Launch count**: the count of authoritative launch rows in `wt_watchtower_launches` attributable to the subprovider (or, at operation scope, to the operation's treasuries). Distinct in principle from confirmed creator count only if a single creator wallet could produce more than one launch row (not observed for ANen; the two numbers coincide here but are not definitionally identical — a repeat-creator scenario would make them diverge).

**Relationship between the four**: Operational fan-out ⊇ Candidate fan-out ⊇ Confirmed creator count, and Confirmed creator count and Launch count are equal-or-related-by-repeat-creator-multiplicity (launch count ≥ confirmed creator count in general, though not observed to diverge in this dataset).

## Recommendation

The operational graph should represent **multiple layers of operational evidence, not confirmed creators alone** — but this is a semantics/labeling recommendation only, per the brief's explicit "do not propose implementation." Concretely: Discovery's presentation should be capable of distinguishing "Provisioned wallets: 26," "Candidate wallets: 25," "Confirmed creators: 1," and "Confirmed launches: 1" as four separately-labeled facts, rather than compressing them into one word ("Fan-out") that currently silently means only the last of the four. This is consistent with, not a departure from, the existing three-stage pipeline (`wt_subprov_evidence` → `wt_candidate_websocket_watches` → `wt_watchtower_launches`) X29.9 already found to be operating correctly and intentionally — the recommendation is to make that existing three-stage structure visible in Discovery's vocabulary, not to build a new one.
