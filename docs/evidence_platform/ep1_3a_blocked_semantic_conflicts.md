# EP1.3A blocker — frozen contract semantic conflicts

Status: **RESOLVED BY EP1.3B**

EP1.3B approved the recommended resolution below: separate provider-independent
logical fact identity from artifact/parser-scoped immutable observation
identity, and retain exact response bytes for future EP1.2 acquisitions without
additional RPC. See `ep1_3b_evidence_identity_raw_artifact_amendment.md`.

The full X78.26 audit output was recovered from the retained local Codex
session dated 2026-08-07. It supplies the twelve fact-family definitions, but
formalization uncovered three contradictions that cannot be resolved as mere
serialization mechanics.

## 1. Evidence identity conflicts with provider provenance aggregation

The approved identity formula includes `raw_artifact_digest`:

```text
SHA-256(
  fact_family
  + fact_schema_version
  + chain/network
  + natural_key
  + normalized_payload_digest
  + raw_artifact_digest
)
```

The same contract also requires:

```text
same chain occurrence + same normalized fact
    = one logical normalized fact
      + multiple provenance observations
```

Two providers can return semantically identical content in different raw
response artifacts. Their raw artifact digests differ, so the approved formula
necessarily creates two Evidence IDs. The current `EvidenceRecord` has no
separate `logical_fact_id` through which those records could be aggregated.

Resolving this requires one semantic choice:

- remove `raw_artifact_digest` from logical fact identity and attach multiple
  artifact/provenance observations; or
- retain artifact-scoped Evidence IDs and add a separate deterministic logical
  fact identity.

EP1.3A does not authorize choosing between them.

## 2. Evidence identity conflicts with parser-version coexistence

EP1.3A requires parser-version coexistence and states that different parser
versions create new observations. The approved Evidence ID formula contains
neither `parser_id` nor `parser_version`.

For the same artifact, natural key and normalized payload, two parser versions
therefore generate the same Evidence ID even though coexistence requires
distinct observations. Adding parser identity to the hash, natural key or
another observation layer changes semantic identity and is not an encoding
mechanic.

## 3. EP1.2 artifacts do not satisfy the frozen RawArtifact contract

X78.26 and EP1.3A require exact provider response bytes to be retained for
replay. EP1.2 currently stores a canonical JSON serialization of the parsed
`AcquisitionResponse`:

```text
status + parsed data/text + selected headers
```

EP1.1 parses the provider body with `response.json()` before the mirror sees it.
Original byte ordering, whitespace, numeric spelling and other wire-level
representation are no longer available. EP1.3A prohibits modifying EP1.2 mirror
behavior, so it cannot truthfully formalize existing EP1.2 artifacts as frozen
`RawArtifact` records.

Resolving this requires either:

- a scoped EP1.2 amendment that captures exact response bytes during the
  already-existing acquisition, with zero additional RPC; or
- an explicit amendment redefining RawArtifact replay from exact-response bytes
  to canonicalized parsed-response bytes.

The second option weakens the approved X78.26 replay boundary.

## Impact

Until these decisions are approved, machine-checkable `EvidenceRecord` and
fact-family contracts cannot satisfy all required EP1.3A tests simultaneously:

- deterministic Evidence ID;
- one logical fact with multiple provider provenance observations;
- conflicting provider observations coexisting;
- parser-version coexistence;
- exact-response replay.

Implementing any apparent resolution would silently redesign permanent
Evidence identity.

## Required unblock

Approve explicit amendments for:

1. artifact-scoped versus logical-fact identity;
2. parser-version observation identity;
3. exact raw-byte capture in EP1.2.

Recommended direction for review:

- retain artifact-scoped immutable observations;
- add a separate deterministic `logical_fact_id` excluding provider artifact
  and parser version;
- include parser identity in observation identity;
- amend EP1.2 to retain exact response bytes without additional acquisition.

This recommendation is not implemented and is not treated as approved.

No contract code, normalization, schema, database write, production change,
commit or push was performed.
