# PSI0D-D immutable-assessment summary adapter

PSI0D-D fixture-qualifies a provenance-preserving adapter from an injected canonical three-file PSI0C assessment representation to the pure PSI0D-B descriptive projection core. Its adapter contract digest is `48e4480d741793c78dda8413ce8fa233c849d3781538bc32c0283a436f00bd7d`.

The adapter replay-verifies the exact file set, canonical JSON bytes, file hashes, assessment bundle and assessment identities, PSI0C-B contract, lineage, production-derived immutable provenance, and false authority flags. It derives only permitted aggregate counts and reason codes. Source mints, addresses, signatures, payloads, unmatched keys, conflict assertions, and other field values are not retained or emitted.

The existing PSI0D-B fixture entry point remains fail-closed for production-derived provenance. Both paths share only a private pure projection core. The adapter accepts injected bytes and performs no filesystem, database, network, service, or configuration I/O. It grants no policy, ranking, integration, deployment, or activation authority.

Qualification uses frozen synthetic bundle representations only. The real PSI0C assessment and PSI0B bundle remain unopened. Any application to the real immutable assessment, local consumer output, integration, deployment, Evidence Mirror or Cohort Mode activation, production activation, or EB2 work requires separate authorization.
