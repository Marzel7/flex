# X64.4 — Phase 5/6: Historical Evidence & Age Assessment

## Phase 5 — Evidence of prior existence

Searched for: historical creator links, historical attribution,
historical campaigns, treasury review, previous confirmations, archived
lineage, prior promotion — all read-only, both databases.

- **`wt_confirmed_treasuries` for `4231KLYi…` itself**: **no row** — it
  has never been confirmed as a treasury in this system.
- **`wt_treasury_review` for `4231KLYi…`**: **no row** — it has never
  even been queued for treasury review.
- **`wt_discovered_subprovs` for `4231KLYi…` as the `subprov` column**:
  **no row** — it is not independently catalogued as a discovered
  subprov either (it only appears as `first_creator` on two OTHER
  wallets' rows, per `treasury_downstream.md`).
- **No campaign table** (`wt_campaigns`, live DB) references `4231KLYi…`
  — checked, zero rows.
- **No prior promotion event** exists anywhere.

**However — and this is the central finding of this audit — the
question "did THIS WALLET exist before its first observation" is the
wrong question to be asking in isolation.** The correct question, given
what Phase 4 established, is: **did the INFRASTRUCTURE THAT FUNDS
`4231KLYi…` predate `4231KLYi…`'s own first appearance?** And the answer
to that is unambiguous:

- `9hGcxVHFajR4xMZVUBQBwbxN2J3tt1aXE5KrUB87EZk4` (`4231KLYi…`'s window-1
  upstream funder) was confirmed as a treasury on **2026-06-22T16:08:59**
  (epoch `1782144539`) — **24 days before** `4231KLYi…`'s first recorded
  activity (2026-07-16T11:51:55).
- `DchJquEZzM6VqBaxhA9i7r3qAUngPggQJHoTBhwdFEUK` (`4231KLYi…`'s window-2
  upstream funder) was confirmed **2026-06-11T07:47:49** (epoch
  `1781164069`) — **35 days before** `4231KLYi…`'s first recorded
  activity, and this specific wallet address is independently referenced
  in this project's own prior session memory as part of a previously
  identified, recurring Hello-payment-linked operator cluster — i.e. it
  is not merely "confirmed in the DB," it is a wallet this investigation
  has already characterized as long-lived, reused infrastructure in
  earlier, separate work.

**Explicit statement, per the task's Phase 5 instruction**: historical
evidence that `4231KLYi…` itself (as a distinct address) operated before
2026-07-16T11:51:55 **does not exist** in either database — that
specific claim cannot be supported. But historical evidence that the
*treasury infrastructure funding it* substantially predates it **does
exist**, and is strong (two independently-confirmed treasuries, 24 and
35 days prior, one with independent cross-session corroboration).

## Phase 6 — Age assessment

**Category: `4231KLYi…` itself — newly introduced (as a distinct
address); the infrastructure funding it — long-lived.**

This is not a contradiction — it is the expected shape of a **disposable
downstream subprov spun up by pre-existing, long-lived treasury
infrastructure**, exactly the X62 primitive this entire audit chain
(X62→X64→X64.2→X64.3) has been characterizing: treasuries are reused;
their disposable downstream wallets are not. Supporting timestamps:

| Wallet | Role | First confirmed/observed | Age relative to `4231KLYi…`'s first activity |
|---|---|---|---|
| `DchJquEZzM…` | Treasury (upstream) | 2026-06-11T07:47:49 | −35 days (predates) |
| `9hGcxVHF…` | Treasury (upstream) | 2026-06-22T16:08:59 | −24 days (predates) |
| `4231KLYi…` | Subprov (this audit's subject) | 2026-07-16T11:51:55 | 0 (baseline) |
| `4231KLYi…` reactivation | Same subprov, second window | 2026-07-19T11:37:42 | +3 days |
| `82Yzf1hM…`, `56m5gW58…` | Downstream of `4231KLYi…` | 2026-07-16T13:06:47–13:36:58 | same day, minutes after |

**`4231KLYi…` itself: "recently activated."** Its entire observed
lifespan in this system spans 2026-07-16T11:51:55 through
2026-07-19T11:37:42 — under 3 days, with the bulk of activity
concentrated in a 2h15m window on the first day, plus one isolated
reactivation 3 days later. This is consistent with — though not proof
of — a disposable/short-lived operational role, not a long-lived treasury
identity in its own right.

**The treasuries funding it: "long-lived" relative to `4231KLYi…`**, on
the specific, narrow evidence that both were confirmed 24+ days earlier.
This audit makes no claim about how much further back either treasury's
OWN history extends — that would require a separate genesis audit on
`9hGcxVHF…`/`DchJqu…` themselves, not performed here (out of scope; flagged
as a gap, not investigated).

## Direct answer to the audit's central question

**"New treasury with recent operational history" vs. "older treasury
whose earlier history simply was not recovered" — neither framing fits,
because `4231KLYi…` is not itself functioning as a treasury root in this
system's own recorded data.** It is a **subprov**, funded by two
separately-confirmed, pre-existing treasuries. The correct
characterization is: **`4231KLYi…` is a newly-observed, short-lived
downstream wallet operating under long-lived, already-known treasury
infrastructure** — not a new treasury, and not a treasury whose lineage
merely wasn't recovered (its lineage IS recovered, and it points to two
already-confirmed treasuries).
