# X65.5 — Phase 4: Support Unknown / New Treasuries

## Design principle

Treasury resolution status is displayed as a **sub-grouping within**
the WATCHTOWER Provisioning bucket, never as a gate on membership.
A launch enters the bucket purely on the Phase 3 mandatory criteria
(fresh creator + observable wrap-close provisioning), independent of
whether its treasury is confirmed, new, or entirely unresolved.

## Treasury confidence tiers (within the bucket)

| Tier | Definition | Source |
|---|---|---|
| **Confirmed Treasury** | The resolved upstream wallet already exists in `wt_confirmed_treasuries` | `treasury_resolution.py`'s `KNOWN_TREASURY` status (X65.1) |
| **Probable Treasury** | A subprov session with a real, direct funding relationship exists (`wt_active_subprov_sessions`), but the upstream treasury wallet itself is not yet in `wt_confirmed_treasuries` | `treasury_resolution.py`'s `SUBPROV_PROBABLE`/session-evidenced-but-unconfirmed classification |
| **New Treasury** | A treasury wallet is observed funding a subprov for the first time in this platform's own history (first-seen within a recent window, e.g. no `wt_active_subprov_sessions` row involving this treasury predates the current observation) | Derived from `wt_active_subprov_sessions.funding_time` — first-observed timestamp for this specific treasury wallet |
| **Unknown Treasury** | No subprov/treasury evidence resolves at all — the launch matches the provisioning fingerprint (Phase 3 mandatory criteria) but its upstream funder is entirely unresolved | `treasury_resolution.py`'s `UNRESOLVED`/`NO_SUBPROV` status |

"New Treasury" and "Unknown Treasury" are **distinct**: New Treasury
means a real, evidenced treasury wallet was found and this is the
first time this platform has seen it act as a treasury; Unknown
Treasury means no treasury-level evidence was found at all (the
walkback stopped at the creator's direct funder with no further
lineage, per X65.1/X65.2's `UNRESOLVED` findings). Collapsing these
into one label would lose real, existing distinguishing evidence — so
they remain separate tiers.

## Why launches must still surface together regardless of tier

The task's core motivation — launches from unseen/newly-discovered
treasuries exhibiting the identical operational fingerprint should not
be hidden behind treasury resolution — is honored directly by Phase 3's
"treasury lineage is optional, confidence-increasing, never gating"
rule. A `New Treasury` or `Unknown Treasury` launch is not a
lesser-quality member of the bucket in any structural sense — it is
the *same* bucket, with a lower-confidence treasury tag displayed
alongside it, exactly mirroring how the existing Operation Attribution
stage already correctly leaves a launch `__UNASSIGNED__` without
excluding it from Discovery entirely (X65.1's own precedent).

## Presentation: distinguishing treasury confidence without fragmenting the bucket

Recommended approach — a **single bucket with an internal breakdown**,
not four separate top-level buckets:

```
WATCHTOWER Provisioning (58)
  ├── Confirmed Treasury   (31)
  ├── Probable Treasury    (—)   [shown only if nonzero]
  ├── New Treasury         (12)
  └── Unknown Treasury     (15)
```

Selecting the top-level "WATCHTOWER Provisioning" bucket shows all 58
launches together, with each row's own Treasury tag visible inline
(consistent with Phase 5's "preserve every field" requirement).
Selecting a treasury-tier sub-row (e.g. "New Treasury") filters to just
that 12-launch subset — the same interaction pattern Discovery
already uses for its existing dimensions (a click narrows the
population without leaving the page or losing context), applied here
to a treasury-confidence sub-axis instead of a full drill-down stage.
This keeps the operational campaign visually unified (one bucket, one
count, one card) while still letting an analyst immediately see and
filter by treasury confidence when they want to.

## Most intuitive presentation, justified

A **single bucket with a segmented sub-count bar** (as above) is
recommended over alternatives considered:
- *Four separate top-level buckets* (Confirmed/Probable/New/Unknown
  WATCHTOWER) — rejected: this is exactly the fragmentation this task
  exists to eliminate, just moved one level down.
- *A single bucket with no visible treasury breakdown at all* —
  rejected: loses real, already-available treasury-confidence
  information the task explicitly wants preserved and surfaced (Phase
  5's "Do not replace or remove current evidence dimensions").
- *A single bucket with an inline color/badge per launch row only, no
  aggregate sub-counts* — considered viable, but a segmented count bar
  additionally gives an at-a-glance campaign-health summary (e.g. "12
  of 58 launches in this campaign trace to a treasury we've never seen
  before" is itself a meaningful, immediately-visible signal) that a
  per-row badge alone would require scrolling/counting to reconstruct.
