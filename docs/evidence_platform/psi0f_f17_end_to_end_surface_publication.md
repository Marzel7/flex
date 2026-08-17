# PSI0F-F17 end-to-end known-behaviour surface publication

PSI0F-F17 implements one atomic fixture-only application from an isolated F13
retained-input store to a canonical PSI0F known-behaviour surface.

The application exports the retained inputs through F13's bounded URI read-only,
query-only path; reconstructs and replay-verifies the F9 bundle and F5 immutable
source; invokes the unchanged F1 rematerializer once; and verifies the resulting
EB0.4H bundle. It then adapts caller-injected immutable PSI0E bundle bytes and the
new EB0.4H bytes through the unchanged PSI0F-D adapter, producing the unchanged
PSI0F-B descriptive surface without a cross-layer join.

Both outputs are published together under one new root:

- `eb0_4h/` contains the five canonical EB0.4H files.
- `surface/` contains `run.json`, `surface.json`, and `hashes.json`.

The run manifest binds the F13 contract and retention identities, F9 bundle, F5
source, F1 contract, EB0.4H bundle, immutable PSI0E bundle, PSI0F adapter and
surface contracts, final surface digest, and false authority. Publication stages
the entire root, replay-verifies it, fsyncs it, renames only if the destination is
absent, fsyncs the parent, and performs a full post-publication replay. The
surface and EB0.4H run identities must agree.

Qualification uses a temporary synthetic F13 store and temporary output. It also
replays once against the already-retained qualified immutable PSI0E bundle whose
fixed digest is `88c7de3156a4dc07b3c3b2461b4e1e37e85d5bd06d217d904472e4f4bc6f4d9c`.
No production database, provider, service, configuration, deployment, activation,
operator identity, ranking, policy decision, or cross-layer entity linkage is
used or authorized.
