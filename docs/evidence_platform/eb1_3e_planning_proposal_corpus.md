# EB1.3E Planning-Proposal Corpus

EB1.3E accepts only `(EB1.3D manifest, EB1.3C verified projection)` pairs that pass exact EB1.3D replay. It groups immutable proposal entries by their originating stage and authority lane, preserving the full proposal plus its source-manifest identity. Duplicate manifests, collisions, unverified inputs, and cross-lane substitution fail closed.

Each lane exposes proposal, distinct-requirement, distinct-review, scope, alternative, and assumption counts together with immutable entry and lane digests. The corpus exposes only coverage counts, source-manifest lineage, lane records, and exact replay identity; it calculates no rates, preferences, rankings, profiles, or decisions.

Every entry remains `NON_EXECUTABLE_PLANNING_PROPOSAL`, with planning and execution authority false. Corpus assembly does not review, approve, select, merge authority lanes, acquire, fulfill, access, deploy, configure, or activate anything. Qualification uses frozen and ephemeral fixtures only.
