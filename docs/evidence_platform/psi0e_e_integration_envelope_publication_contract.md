# PSI0E-E Integration-Envelope Publication Contract

PSI0E-E qualifies a fixture-only atomic publisher for the descriptive integration envelope produced by PSI0E-A. It does not publish the real PSI0E-C envelope and grants no consumer, integration, deployment, Evidence-mode, production-activation, or EB2 authority.

The publisher accepts canonical injected bytes only. It binds the PSI0E-D closure, PSI0E-C input and envelope identities, PSI0E-A contract and qualification, and PSI0E-B closure. Validation requires the exact envelope schema and source identities, `default_off=true`, `consumer_enabled=false`, production-derived descriptive-envelope provenance, `ABSENT_NOT_NEGATIVE`, consistent per-surface accounting, and false policy, ranking, integration, deployment, and activation flags.

Successful fixture publication creates exactly:

- `envelope.json`
- `contract.json`
- `hashes.json`

The files are canonical and deterministically ordered. Each staging file and the staging directory are fsynced, publication uses one atomic rename into a caller-supplied absent path, the parent directory is fsynced, and the completed bundle is replay-verified. Existing output is never overwritten. Validation, write, fsync, rename, and post-publication replay failures remove staging or renamed output and do not retry.

Qualification covers canonical success, deterministic replay, input-order independence, malformed and noncanonical input, schema/source/provenance/default-state/accounting/reason/authority drift, output reuse, write/fsync/rename faults, replay tamper, and no-partial-publication cleanup. All fixtures are synthetic and ephemeral.

The real PSI0D, PSI0C, and PSI0B bundles were not opened. The real PSI0E-C envelope was not reconstructed or published.
