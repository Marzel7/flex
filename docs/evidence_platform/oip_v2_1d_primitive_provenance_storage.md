# OIP v2.1D — Primitive Provenance Amplification & Storage Efficiency

## Storage Verdict

**E — MIXED**

## Acquisition Verdict

**READY_FOR_NEXT_1000**

The next acquisition remains a staged 1,000-attempt batch. This report does not authorize 5,000 calls and does not implement a production storage migration.

## Exact Cause

The corpus contains **3,495,337 unique Evidence→Primitive links** across **10 Primitive families**. `BEHAVIOURAL_TIMING` contributes **3,137,655 links (89.77%)**. Its largest Evidence source is `AccountParticipationFact → BEHAVIOURAL_TIMING` at **2,737,103 links**.

The complete v2.1C increment of **968,304 links** is accounted for. New `BEHAVIOURAL_TIMING` Primitives generated **937,858 links (96.86%)**; `AccountParticipationFact → BEHAVIOURAL_TIMING` alone generated **851,471**. These new cohort-level Primitives legitimately reference historical Evidence, which is why 270 new transactions produce far more provenance links than their new Evidence facts alone.

Every link is semantically unique; the composite primary key proves **zero exact duplicates**. There are **zero logical facts with multiple Evidence versions** in this corpus, so version coexistence is not the amplification driver. Deterministic replay inserts zero Primitive rows and zero provenance links on pass two.

## Physical Decomposition

The full **601,097,366-byte** v2.1C increment is explained with **zero residual**:

| Component | Incremental bytes |
|---|---:|
| Provenance link table | 142,045,184 |
| Provenance composite PK index | 159,211,520 |
| Artifacts, reports, telemetry | 216,585,366 |
| Evidence table, indexes, provenance | 67,948,544 |
| Primitive rows and index | 14,536,704 |
| Other database pages | 770,048 |

The current link representation costs **305.61 bytes/link** because both 64-character TEXT identities are stored in the table and repeated in its composite B-tree index.

## Shadow Prototype

The isolated compact-key prototype preserves all **3,495,337 relationships** with digest `e1fc7564747f4188afa5d71be5c4d21264947baa3e53b631250ca239b8381a53`. It reduced the complete link subsystem from **1,068,224,512 bytes** to **108,281,856 bytes**, saving **89.86%**. A 100-Primitive lookup sample improved from **73.49 ms** to **11.45 ms**.

The prototype preserves external content identities through a compatibility view and immutable triggers. It is evidence for a separate migration design, not production code.

## Scaling

At the current representation, the next 1,000 attempts project to **2.23 GB** (1.67–2.78 GB). Applying the measured compact ratio projects **1.22 GB** (0.92–1.53 GB), a **45.04%** reduction in total incremental storage.

## Invariants

Zero RPC, zero production interaction, and zero new coverage. The canonical Evidence, Primitive, Runtime, Discovery, motif, relationship, identity, governance, and schema implementations were not changed. Primitive replay remains deterministic.
