# EP1.0 — Evidence Platform Foundation

EP1.0 provides isolated infrastructure only. It has no production producers,
RPC acquisition, normalization, primitives, detectors, projections, Operations,
or governance integration.

## Components

- `EvidenceConfig`: feature flags and isolated paths; every flag defaults OFF.
- `ArtifactStore`: SHA-256-addressed, deterministic gzip artifacts with digest
  verification and non-destructive retention hooks.
- `EvidenceIntakeQueue`: bounded filesystem spool with pending, processing,
  retry and dead-letter states.
- `EvidenceWriter`: the sole Evidence database writer, protected by an exclusive
  process lock and capable of bounded batch commits.
- `EvidenceDatabase`: append-only envelopes, provenance, artifact references and
  writer receipts. Triggers reject update and deletion.
- Evidence health and metrics blueprint: opt-in and not registered with the
  production Flask application in EP1.0.

## Default-off configuration

```text
EVIDENCE_PLATFORM_ENABLED=0
EVIDENCE_WRITER_ENABLED=0
EVIDENCE_QUEUE_ENABLED=0
EVIDENCE_ARTIFACT_STORE_ENABLED=0
EVIDENCE_HEALTH_ENABLED=0
```

Optional isolated paths and limits:

```text
EVIDENCE_DATABASE_PATH=database/evidence_platform/evidence.db
EVIDENCE_QUEUE_PATH=database/evidence_platform/intake
EVIDENCE_ARTIFACT_PATH=database/evidence_platform/artifacts
EVIDENCE_QUEUE_MAX_MESSAGES=10000
EVIDENCE_QUEUE_MAX_BYTES=268435456
EVIDENCE_WRITER_BATCH_SIZE=100
EVIDENCE_WRITER_POLL_SECONDS=1.0
EVIDENCE_MAX_ATTEMPTS=5
```

No component is initialized merely by importing `src.evidence`.

## Synthetic validation

EP1.0 accepts only explicit synthetic/manual messages through
`EvidencePlatform.synthetic_message`. No production caller imports or invokes
that method.

The writer can be started manually only after every required flag is enabled:

```bash
python -m src.evidence.writer
```

The health surface is a separate, optional process and is not registered with
the production application:

```bash
python -m src.evidence.health
```

It exposes `/api/evidence/health` and `/api/evidence/metrics` on the isolated
health port only when the platform and health flags are enabled.

## Transaction boundary

The writer transaction contains only:

1. append envelope;
2. append provenance;
3. append artifact reference;
4. append idempotency receipt;
5. commit.

Artifact writes, digest verification, queue claims, retries and health checks
occur outside the database transaction. No RPC or interpretation is present.

## Crash recovery

- Queue messages are atomically renamed from `pending` to `processing`.
- Cold start returns stranded `processing` and `retry` messages to `pending`.
- A commit-before-ack crash is safe: `writer_receipts` and immutable envelope
  keys make the second delivery a duplicate rather than another append.
- Repeated failures enter `dead_letter` after the configured attempt limit.

## Rollback

Stop the Evidence writer and set every Evidence flag to `0`. No production
cleanup, migration, schema reversal, or data rewrite is required. Existing
production databases and queues are never opened by the Evidence package.

## Deferred work

Shared acquisition, RPC mirroring, normalization, replay, primitives,
Operation Contracts, unknown discovery, consumer migration and governance are
explicitly deferred to later Evidence Platform milestones.
