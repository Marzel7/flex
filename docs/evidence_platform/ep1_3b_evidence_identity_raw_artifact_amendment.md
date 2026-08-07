# EP1.3B — Evidence Identity and Raw Artifact Contract Amendment

Status: **IMPLEMENTED**

EP1.3B resolves the three semantic conflicts recorded by EP1.3A. It amends
identity and raw retention only. It does not normalize facts, create primitives,
run detectors, expose Evidence to production consumers, or change authority.

## Two-level identity

`logical_fact_id` identifies the provider- and parser-independent occurrence:

```text
SHA-256(canonical JSON array[
  fact_family, chain, network, natural_key
])
```

`evidence_id` identifies one immutable normalized observation of that logical
fact:

```text
SHA-256(canonical JSON array[
  fact_family,
  fact_schema_version,
  logical_fact_id,
  parser_id,
  parser_version,
  normalized_payload_digest,
  raw_artifact_digest
])
```

Consequences:

- equivalent provider observations share a logical identity but remain distinct
  immutable Evidence observations;
- conflicting provider observations coexist and are never collapsed;
- parser versions coexist without overwriting earlier observations;
- replaying the same artifact with the same parser is idempotent;
- provider agreement is a later projection, never an Evidence-layer mutation.

## Canonical serialization

Identity inputs use UTF-8 JSON with sorted object keys and compact separators.
Arrays preserve order, strings are not Unicode-normalized, null is retained,
and integers use their JSON decimal representation. Floats, non-string object
keys, bytes, sets, NaN and infinities are rejected. A final newline is part of
the canonical byte encoding.

## RawArtifact representations

New EP1.2 acquisitions retain the exact provider response body already consumed
by the HTTP client. Its SHA-256 digest addresses the unmodified bytes before
artifact-store gzip compression. Metadata records media type, storage
compression, provider, sanitized endpoint, request digest, HTTP status,
acquisition time, byte length and representation.

Three states are explicit:

- `EXACT_PROVIDER_ARTIFACT`: exact provider response bytes are retained;
- `CANONICALIZED_RESPONSE_REPRESENTATION`: only the historical/parsed response
  representation is retained;
- `RAW_BYTES_UNAVAILABLE`: no replayable body is available and no bytes are
  fabricated.

Pre-EP1.3B EP1.2 artifacts are not rewritten. Envelopes created before the
representation field existed are treated as canonicalized legacy artifacts.

## EP1.2 acquisition compatibility

EP1.1 reads the same HTTP response body once, then parses the client's cached
body through the existing JSON/text path. The asynchronous mirror receives a
copy of those bytes. It performs no RPC, provider retry, pagination, timeout,
cache operation, detector work or production write. Mirror failure remains
non-authoritative and cannot interrupt creator funding.

## Failure and replay contract

Exact bytes are base64 encoded only inside the bounded handoff/spool object and
decoded before content-addressed persistence. Queue replay therefore reproduces
the identical artifact digest without another provider call. Parsed-only inputs
fall back to the prior canonical response representation with an explicit
marker; they never claim exact replay fidelity.

## Implementation readiness

The permanent identity and raw-artifact ambiguities are resolved. EP1.3 may now
formalize and implement the frozen fact-family schemas against these contracts.
EP1.3B itself deliberately stops before fact normalization.
