# PSI0F-F1 deterministic EB0.4H rematerialization

PSI0F-F1 is an isolated fixture-only boundary for producing an EB0.4H bundle
from an explicitly supplied canonical logical source. It does not weaken or
modify EB0.4G, EB0.4H, PSI0F-D, or PSI0F-B.

The logical source binds the ordered platform-operation cohort, exact EB0.4C
normalized runtime evidence, explicit `PROPOSED` or `SUPPORTED` candidate
membership, a closed role and descriptor vocabulary, component hashes,
accounting, lineage, provenance, one source digest, and false authority. Group
membership remains supplied evidence; it is never inferred from topology or
behavioural similarity.

After canonical validation, the boundary creates one isolated ephemeral SQLite
fixture with the exact EB0.4F three-table schema. The unchanged EB0.4G extractor
opens it read-only with `query_only`, deadlines, and ceilings. The unchanged
EB0.4H writer publishes into staging. Files and the staging directory are
fsynced, then the exact five-file bundle is renamed to a new absent output and
replay-verified. Validation, construction, extraction, write, fsync, rename, or
replay failure removes staging and any renamed output without retry.

The returned result retains only the output path and immutable source,
extraction, bundle, file, and contract identities. Real EB0.4 materialization,
PSI0F adapter application, operational-surface publication, operator identity,
ranking, policy, integration, deployment, Evidence modes, activation, and EB2
remain separately gated.
