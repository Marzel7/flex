# EP0.1 compatibility freeze

This directory contains the permanent compatibility contract and generated
production characterisation fixtures for Evidence Platform Phase 0A.

Generate a baseline:

```bash
python scripts/ep0_1_generate_compatibility.py --out compatibility/ep0_1
```

The source databases are always opened with SQLite `mode=ro` and
`PRAGMA query_only=ON`. The generator does not import the Flask application,
run schema setup, start workers, call RPC, or write to a source database.

For a formal release capture, pause checkpoint-producing maintenance or point
the generator at immutable filesystem snapshots. It fails if either database
size or modification time changes during its capture.

## Reproducibility

Run twice against the same immutable snapshots and compare:

```bash
python scripts/ep0_1_generate_compatibility.py --main-db snapshot/main.db --ops-db snapshot/ops.db --out /tmp/ep-a
python scripts/ep0_1_generate_compatibility.py --main-db snapshot/main.db --ops-db snapshot/ops.db --out /tmp/ep-b
diff -ru /tmp/ep-a /tmp/ep-b
```

Add `--quick-check` to the formal release capture after cloning. It is opt-in
because SQLite integrity checking scans the complete multi-gigabyte databases
and is not required to establish fixture determinism.

An empty diff is the determinism gate. The tool never writes a generated clock
to a fixture. `snapshot_timestamp` is supplied explicitly or derived from the
stable database mtimes.

The persisted intelligence-snapshot directory and serializer metrics file are
also inputs. Copy them beside a formal database snapshot and provide
`--projection-dir` and `--serializer-metrics` so the complete capture is fixed.

## Captured contracts

- canonical Operators and entities;
- identity lifecycle and governance history;
- treasury review state and actions;
- walkback outcomes, directional edges and queues;
- creator-funding queues and persisted funding records;
- WATCHTOWER persisted projections;
- Discovery persisted activity;
- Operations Registry read-model inputs;
- API read models with volatile response clocks removed;
- UI route, template and projection hashes;
- database, schema, fixture and consumer digests;
- worker and queue health.

Performance is captured as an observed baseline, not an optimisation target.
Runtime-only CPU and memory samples belong in the reviewed release report and
must not be inserted into deterministic golden fixtures.

## Compatibility rule

Every later Evidence Platform phase must compare its output to
`golden_behaviour_contract.json`. Differences listed as forbidden fail the
phase unless a later, explicitly approved migration changes the contract.
