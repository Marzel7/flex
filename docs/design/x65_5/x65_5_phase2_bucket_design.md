# X65.5 — Phase 2: Define Canonical Operational Bucket

## Design

A new top-level Discovery grouping, **"WATCHTOWER Provisioning,"**
computed as an independent, additive layer over the existing five
dimensions (Behaviour, Creator Identity, Topology, Funding Origin,
Operation Attribution) — not a replacement for any of them, and not
itself a sixth item in the existing drill-down chain. It sits
*above* the drill-down as a parallel, campaign-level lens: "does this
launch's operational fingerprint match the validated WATCHTOWER
provisioning architecture," independent of how far any single
evidentiary dimension happened to resolve for this specific launch.

## What the bucket represents

> "Launches exhibiting the validated WATCHTOWER provisioning
> architecture" — i.e., launches whose creator was funded through a
> single-use provisioning wallet that is itself part of an observable
> multi-recipient SubProv fan-out (Treasury → SubProv → {many
> provisioning wallets} → one becomes Creator), per the operational
> model validated in X65.4.

## Why this must be evidence-of-*behaviour*, not evidence-of-*identity*

The existing dimensions (Creator Identity, Topology, Treasury,
Operation) each ask an identity/lineage question: "who is this
wallet," "where did funding come from," "is this treasury already
known." The canonical bucket asks a **structural/behavioural**
question instead: "does the funding pattern reaching this creator
match the shape this project has already validated as WATCHTOWER's
provisioning fingerprint" (X65.4: fresh creator + observable SubProv
fan-out + single-use provisioning wallet + single creator funded +
wallet not reused). This is deliberately independent of treasury
confirmation status — a launch can match the fingerprint perfectly
while its treasury is brand new and unconfirmed, which is exactly the
population this task's background section flags as currently getting
lost.

## Must not require a confirmed treasury

Per the task's explicit constraint, membership in the bucket is
computed **before and independently of** Treasury Resolution's
`KNOWN_TREASURY`/`UNKNOWN_TREASURY_CANDIDATE`/`UNRESOLVED` outcome.
Treasury confidence becomes a **sub-grouping displayed within** the
bucket (Phase 4), never a gate on entry into it. This mirrors how
Creator Identity, Topology, etc. remain untouched and independently
displayed (Phase 5) — the bucket adds a new lens, it does not change
what any existing lens outputs.

## Relationship to the existing drill-down chain

The bucket is **not** inserted between Behaviour and Creator Identity,
nor does it replace any existing stage. It is presented as an
additional, parallel entry point at the very top of Discovery,
alongside (not instead of) the existing Behaviour Cohort view — a
user can either drill down the existing Behaviour → Creator Identity →
... chain as today, or select "WATCHTOWER Provisioning" to see the
cross-cutting operational-campaign view described in Phases 3-7. Both
views read from, and never mutate, the same underlying per-launch
evidence.
