# X69.4 Additive Reconciliation API Metadata

## Result

Operation attribution responses may now include a presentation-safe,
versioned `reconciliation` object. Registry attribution remains authoritative;
the object is omitted whenever the immutable population/package/result cannot
be generated.

The shared projection reaches only existing operation-attribution consumers:

```text
OperationAttributionService
        ├── Discovery entity detail
        ├── Token detail
        ├── Operational Intelligence
        └── Registry-backed search detail
              + optional reconciliation
```

No normal UI consumes the object. No Registry, Discovery, confirmation,
lifecycle, search matching, or operation-assignment rule changed.

## Schema

Schema version: `operation-reconciliation-v1`

```json
{
  "reconciliation": {
    "schema_version": "operation-reconciliation-v1",
    "population_revision_id": "ipr:…",
    "reconciliation_package_id": "erp:…",
    "disposition": "UNRESOLVED",
    "reasoning_summary": "…",
    "supporting_evidence_count": 0,
    "contradictory_evidence_count": 0,
    "missing_evidence_count": 0,
    "dependency_groups": [],
    "deterministic_result_id": "dr:…",
    "legacy_shadow_agreement": false,
    "expected_difference": true
  }
}
```

Full evidence, provenance, exact reasoning chains, replay data, and developer
diagnostic models are intentionally excluded.

## Compatibility validation

For every live Registry family, the enriched assignment was copied, its
`reconciliation` child removed, and the result compared with the legacy
assignment. Every legacy field and value was identical. Unknown assignments
and package-generation failures omit `reconciliation` entirely.

Response metadata is copied at the API boundary. A caller cannot mutate the
cached immutable result used by another response. Metadata shares the existing
Registry refresh lifetime and is cleared by the existing attribution-cache
invalidation path.

| Control | Existing attribution | Reconciliation | Agreement | Expected difference |
|---|---|---|---:|---:|
| WATCHTOWER | unchanged | CONFIRMED_OPERATION | true | false |
| B48k / Dv34 | unchanged | UNRESOLVED | false | true |
| C7Ha | unchanged | REVIEW | false | true |

Random and unknown entities retain their existing attribution. Reconciliation
is present only when the family has a corresponding immutable population and a
valid evidence package.

## Regression and performance validation

- 51 focused X69, Registry, confirmation, operation-profile, Operational
  Intelligence, and consumer-migration tests passed.
- 18 focused metadata/diagnostics/consumer tests passed independently.
- Cold metadata-enabled resolution on the validation host: 6.396 seconds.
- Warm cached resolution: 0.001322 seconds.
- Registry-backed search with cached reconciliation metadata: 6.915 seconds
  (dominated by its existing family composition).
- Existing consumers deserialize dictionaries as before and ignore the unknown
  optional child.
- No production consumer reads or branches on reconciliation metadata.
- No database or schema writes were introduced.
