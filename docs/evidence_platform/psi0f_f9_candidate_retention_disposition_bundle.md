# PSI0F-F9 candidate-retention and disposition bundle

PSI0F-F9 is a pure fixture-only canonical retention bundle for all caller-injected
inputs required to reconstruct the qualified PSI0F-F5 immutable logical source.
It performs no file, database, network, service, configuration, or clock access.

The F8 architecture named evaluation, candidate, disposition, accounting, hash,
and contract documents. F9 corrects that design before implementation: a lossless
F5 replay also requires the explicitly ordered operation cohort, normalized EB0.4C
runtime projections, and closed vocabulary. The exact bundle is therefore:

- `contract.json`
- `cohort.json`
- `evaluations.json`
- `runtime.json`
- `candidates.json`
- `dispositions.json`
- `vocabulary.json`
- `accounting.json`
- `hashes.json`

Every document is canonical JSON bytes. The hash manifest binds every other file
and supplies one deterministic bundle digest. Replay requires the exact file set,
canonical bytes, matching hashes, the bound F9 contract, and exact accounting.
It then invokes the unchanged F5 materializer in memory and must reconstruct the
same F5 source digest. Collection order is normalized, while the explicit member
order inside each disposition remains identity-bearing.

Candidate lifecycle remains descriptive and cannot imply nomination. Every
disposition still explicitly says `PROPOSED` or `SUPPORTED`; the unchanged F5
evidence gates and all false authority fields remain authoritative. F9 neither
retains real records nor writes a bundle, performs a real-source materialization,
invokes EB0.4H, publishes a surface, identifies an operator, or authorizes any
production action.
