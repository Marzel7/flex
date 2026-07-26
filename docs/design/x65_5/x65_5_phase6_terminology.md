# X65.5 — Phase 6: Operational Topology Naming

## Current terminology and why it under-communicates the validated model

`src/ops/funding_topology.py`'s current labels
(`TOPOLOGY_LABELS`, line 43-49):

| Internal constant | Current display label |
|---|---|
| `MULTI_LEVEL_FAN_OUT` | "Multi-Level Fan-Out" |
| `MESH` | "Mesh" |
| `FAN_OUT` | "Fan-Out" |
| `LINEAR` | "Linear" |
| `UNKNOWN` | "Unknown" |

These labels are generic graph-theory terms — they describe a shape,
not the specific, now-validated operational mechanism (X65.4) that
produces that shape in this platform's actual data: a Treasury funding
a SubProv, which performs an observable multi-recipient wrap-close
fan-out to single-use provisioning wallets, one of which becomes a
fresh creator. An analyst seeing "Multi-Level Fan-Out" has no way to
know, from the label alone, that this specific term corresponds to a
well-evidenced, characteristic WATCHTOWER pattern rather than an
arbitrary graph shape that happened to appear in the data.

## Recommended terminology

| Current label | Recommended replacement | Rationale |
|---|---|---|
| "Fan-Out" | **"SubProv Fan-Out"** | Names the specific mechanism (a SubProv wallet fanning out), not just the shape — directly ties the label to the validated architecture from X65.4 |
| "Multi-Level Fan-Out" | **"WATCHTOWER Provisioning Fan-Out"** (or, if a shorter form is needed, "Multi-Tier SubProv Fan-Out") | This classification specifically represents a subprov that is itself a recorded child of another subprov (X65.4 Phase 1) — i.e., a multi-tier WATCHTOWER provisioning chain, not a generic "the graph has more than 2 levels" observation |
| "Linear" | **Retain as "Linear"** | This label is accurate and mechanism-neutral as-is — a single, direct subprov→creator relationship with no observed fan-out is genuinely well-described by "Linear"; renaming it risks implying a false positive claim of WATCHTOWER-ness for launches that may not be WATCHTOWER at all |
| "Mesh" | **Retain as "Mesh"** | Per X65.4/X29.1, this classification's underlying rule (treasury also structurally a subprov elsewhere) currently matches zero launches in the live corpus and is explicitly flagged as needing a richer data source before it can be considered a validated pattern — renaming it to something WATCHTOWER-specific would overstate confidence in a rule that isn't yet proven; "Mesh" remains an honest, provisional graph-shape label until that separate investigation resolves it |
| "Unknown" | **Retain as "Unknown"** | Already accurate and appropriately neutral |

## Why not rename every label to a WATCHTOWER-specific term

Only "Fan-Out" and "Multi-Level Fan-Out" are renamed, because only
those two classifications are the ones X65.4 specifically validated as
corresponding to the real, observed WATCHTOWER provisioning mechanism.
"Linear," "Mesh," and "Unknown" describe the *absence* of that pattern,
a *different, unvalidated* pattern, or *insufficient evidence*,
respectively — applying WATCHTOWER-specific terminology to any of
those would misrepresent what the platform has actually validated,
directly contradicting this task's own stated goal ("The goal is not
to simplify or weaken classification").

## Relationship to the new canonical bucket

This renaming is independent of, and does not substitute for, the
canonical WATCHTOWER Provisioning bucket (Phase 2). The bucket's
membership test (Phase 3) deliberately does **not** depend on the
Topology field's value at all (to avoid inheriting X65.4's known
Topology-classifier gap) — so renaming "Fan-Out" to "SubProv Fan-Out"
improves the existing Topology dimension's own clarity and honesty
about what it represents, but does not by itself fix the
underclassification problem X65.4 identified (that gap is a separate,
not-yet-implemented fix, tracked in X65.4 Phase 7). Both changes are
complementary: better terminology for the existing dimension, plus a
new, independent bucket that doesn't rely on that dimension's current
limitations.
