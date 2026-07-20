# X25.1 — Launch Profile Correction & Track Separation (Design Only)

Status: DESIGN ONLY. Corrects a conclusion in X24.9; does not change any
code, schema, or UI. Supersedes X24.9 §1.1's "reject `EMERGENT`, don't
implement a Launch Profile classification" framing with a narrower, correct
one. X24.9's underlying measurements are unchanged and still cited directly.

---

## 0. The correction, stated precisely

X24.9 measured that "rapid birth→launch" fails to discriminate (97.6% of the
population is rapid) and concluded from that: *don't implement `EMERGENT`
as a classification at all*. That conclusion **conflated two different
claims**:

1. "Rapid birth→launch does not discriminate operations" — **measured,
   true, unchanged**.
2. "Therefore Launch Profile is not a useful classification" — **does not
   follow**, and is rejected here.

A classification can be simultaneously true of nearly the whole population
and still be the right first fact to state about a launch — "this is a
`PROVISIONED` launch" is a structural, verifiable claim independent of how
common it is. The error in X24.9 was treating "doesn't discriminate between
operations" as equivalent to "isn't worth classifying," when Launch Profile
and Operation Identity (X25.0) are answering two different questions and
were never competing for the same job.

## 1. Redesigned Launch Profile (four values, mutually exclusive by
construction)

| Value | Definition | Measured population |
|---|---|---|
| `PROVISIONED` | Has a `wt_watchtower_launches` row with `subprov_wallet` set and `funding_mechanism` in `{WSOL_WRAP_CLOSE, SEEDED_ACCOUNT_CLOSE}` — detected through a verified provisioning mechanism | 43/43 (100%) of the confirmed-launch table |
| `OBSERVED_ONLY` | Recovered retrospectively via walkback/attribution with no matching `wt_watchtower_launches` provisioning row | not present in the confirmed-launch table by definition (that table *is* the provisioned set); applies to the broader walkback-only population tracked in `wt_attribution_outcomes` |

`LINEAGE_REUSE` and `INFRA_REUSE`, as originally proposed in X24.9's
background, are **not values of this enum** — this is the specific fix.
Measured directly: **41 of 43 `PROVISIONED` launches (95%) belong to a
treasury that has already funded another launch** (X24.9 §1, treasury reuse
71% of treasuries / 95% of launches by volume, since the reused treasuries
are also the highest-volume ones). If `LINEAGE_REUSE` were a sibling value
to `PROVISIONED` in the same enum, 95% of launches would need to pick one
and silently lose the other — exactly the kind of conflation X24.8 and
X24.9 both exist to eliminate. The fix: `PROVISIONED`/`OBSERVED_ONLY` stay
as Launch Profile's only two values (mutually exclusive, together exhaustive
of "how was this launch structurally built"), and lineage reuse / infra
reuse move to their own already-existing, already-orthogonal tracks
(Funding Lineage and Infrastructure Classification, X24.9 §1.2/§1.3) and
now additionally to Operation Identity (X25.0), rather than being folded
back into Launch Profile as competing labels.

**Facts, not classifications** — rendered alongside `PROVISIONED`, never as
alternative values:
- Birth→launch timing (e.g. "2s") — descriptive; 97.6% rapid, per X24.9.
- Funding mechanism (`WSOL_WRAP_CLOSE` / `SEEDED_ACCOUNT_CLOSE`).
- Treasury launch count at time of this launch (e.g. "treasury launch #15")
  — this is the presentation-layer surfacing of the Funding Lineage count
  X24.9 §1.2 already recommended exposing as an integer, not a boolean.
- Creator freshness — always true by construction in this population
  (X24.9), stated as a fact, never as a discovered signal.

Example rendering, matching the format proposed in this correction:

```
Launch Profile: PROVISIONED
  ✓ Fresh creator
  ✓ Birth → Launch: 2s
  ✓ Mechanism: WSOL_WRAP_CLOSE
  ✓ Treasury launch #15
```

## 2. Two tracks, not one — confirmed as the correct architecture

- **X24.x (Launch Taxonomy)**: what kind of launch is this, structurally
  and evidentially? Axes: Launch Profile (§1, this doc), Funding Lineage,
  Infrastructure Classification, Attribution Outcome, Operator Identity,
  Detection Provenance (X24.9 §1, X24.8).
- **X25.x (Operation Identity)**: which campaign owns this launch? The
  treasury-funding-mesh model from X25.0 §1, computed independently of any
  single launch's own classification.

**One launch belongs to exactly one Operation; one Operation contains many
launches.** This is a strict one-to-many relationship, not a peer axis —
Operation Identity should be attached to a launch as a reference (an
`operation_id`, per X25.0 §7's proposed object shape), the same way
Attribution Outcome and Detection Provenance are already attached as
independent facts about the launch, not values competing inside Launch
Profile's own enum.

Both example card layouts in the brief are valid simultaneously:

```
Launch Profile: PROVISIONED
Funding Lineage: Treasury Mesh #2 (reused)
Detection: PROGRAM_LOGS
Attribution: KNOWN_RELAY_REACHED
Operator: WATCHTOWER
```

```
Launch Profile: PROVISIONED
Operation: Mesh #1
Detection: ACTIVE_CATCHUP
Operator: Unknown
```

Note both examples use `PROVISIONED` — under the corrected model this is
expected and correct, since 100% of the confirmed population is
`PROVISIONED` today; what differs between the two launches is everything on
the *other* tracks, exactly as designed.

## 3. What changes in X24.9 and X25.0 as a result

- **X24.9 §1.1's verdict on `CREATOR_REUSE`** (reject as an axis, fold into
  Launch Profile as a fact) **still stands** — that reasoning did not depend
  on the EMERGENT question and remains correct on its own measured grounds
  (0% creator reuse, by construction of the input filter).
- **X24.9 §1.1/§5's verdict "do not implement `EMERGENT` at all"** is
  **superseded** by this document: implement Launch Profile as
  `PROVISIONED`/`OBSERVED_ONLY`, with rapid-timing and freshness demoted to
  descriptive facts rather than omitted entirely.
- **X24.9 §1.3's Infrastructure Classification and X25.0's Operation
  Identity are unaffected** — both were already designed as separate,
  orthogonal tracks from Launch Profile, and this correction only clarifies
  that Launch Profile itself must not re-absorb the reuse concepts those
  tracks already own.

## 4. Explicitly out of scope (confirmed unchanged)

No files under `src/`, `templates/`, or any DB schema were modified to
produce this document. All measurements cited are re-used from X24.9's and
X25.0's own already-run queries, plus one new confirmatory query (§1,
41/43 co-occurrence check) — no new destructive or write operations were
run against `database/wt_ops_v2.db`.
