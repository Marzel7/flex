# PSI0B-E3 Superseding Preflight

PSI0B-E3 explicitly supersedes the unreplayable PSI0B-B cohort and preflight identities. The old digests remain historical references and are marked `superseded_identity_replay_verified=false`; they are never presented as verified inputs.

The replacement binds the exact 5,000 `selected_mints` in retained `corpora.json` array order from EB0.1P bundle `2c07d41…`, cohort ID `eb0.1p-selected-5000-v1`, source identity `fd538d45…`, fact family `LaunchFact`, run `psi0b-shadow-20260814-02`, and the still-absent isolated `-02` output path. Canonical `cohort.json`, `preflight.json`, and `hashes.json` are committed under `docs/audits/psi0b_e3_superseding_preflight`.

Replay verifies canonical bytes, exact file set and hashes, 5,000 unique ordered members, nested cohort equality, output fingerprint, supersession flags and the new cohort/preflight digests. PSI0B-C and PSI0B-D accept only the new identities. The five SELECT templates, C16 boundaries, health gates, ceilings, abort semantics and non-integration/non-activation authority are unchanged.

This milestone uses local frozen artifacts only. It does not materialize a production execution authorization, observe health, open a database, execute a query or create the `-02` shadow output.
