# EB1.3C Verified-Lineage Adapters

EB1.3C closes the lineage ambiguity identified by EB1.3B. It verifies canonical EB1.1H bundle bytes and hashes, reconstructs and replay-verifies the EB1.1D manifest and EB1.1E corpus, and treats their projection, manifest, and corpus digests as authoritative. Every EB1.2A disposition must match those digests and the exact EB1.1 requirement identity, authority lane, stage, and cohort/window scope.

Only after that verification does the adapter construct EB1.3A records. Candidate evidence classes, planning assumptions, proposal sequence, reason, rationale, and supersession remain explicit caller inputs; callers cannot supply or override lineage, review identity, authority, or scope. The latest disposition must be `READY_FOR_SEPARATE_PLANNING`.

The adapter inherits EB1.3A's non-executable content restrictions and both false authority grants. It does not derive, select, rank, review, approve, acquire, fulfill, access, deploy, configure, or activate anything. Qualification is limited to frozen and ephemeral fixtures and makes no production compatibility claim.
