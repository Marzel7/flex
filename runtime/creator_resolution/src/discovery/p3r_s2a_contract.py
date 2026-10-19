"""Provider-free contract guards for OPS-DISCOVERY-P3R-S2A."""
from __future__ import annotations

import hashlib
import json
from typing import Iterable, Mapping

FORBIDDEN_DETECTOR_FIELDS = frozenset({
    "watchtower", "3sw2", "three_sw2", "canonical_operation_id",
    "canonical_operation_membership", "human_operation_label",
    "evaluation_control_class",
})


def canonical_digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def detector_safe_projection(record: Mapping[str, object]) -> dict[str, object]:
    leaked = FORBIDDEN_DETECTOR_FIELDS.intersection(record)
    if leaked:
        raise ValueError(f"canonical evaluation fields forbidden in detector input: {sorted(leaked)}")
    return dict(record)


def deterministic_order(records: Iterable[Mapping[str, object]]) -> tuple[dict[str, object], ...]:
    """Order without control labels; S2B supplies only pre-frozen neutral keys."""
    return tuple(sorted((dict(row) for row in records), key=lambda row: (str(row["source_rank"]), str(row["mint"]))))


def validate_design(*, watchtower: int, three_sw2: int, contrast: int,
                    migration_cap: int, topology_cap: int, behaviour_cap: int) -> dict[str, int]:
    if min(watchtower, three_sw2, contrast, migration_cap, topology_cap, behaviour_cap) < 0:
        raise ValueError("design counts must be non-negative")
    total = watchtower + three_sw2 + contrast
    residual_maximum = migration_cap + topology_cap + behaviour_cap
    if total != 24:
        raise ValueError("S2A frozen design requires exactly 24 cohort slots")
    if (migration_cap, topology_cap, behaviour_cap) != (24, 12, 8):
        raise ValueError("S2A frozen residual ceiling is 24/12/8")
    if residual_maximum > 44:
        raise ValueError("residual request budget exceeded")
    return {"cohort_size": total, "residual_request_maximum": residual_maximum}


def source_rank(*, direct_funding: bool, upstream_funding: bool, ep3_topology: bool,
                timing: bool, migration_actor: bool, behaviour: bool) -> tuple[int, int, int]:
    """Neutral, pre-selection evidence-completeness rank for S2B only."""
    independent = int(ep3_topology) + int(timing) + int(migration_actor) + int(behaviour)
    return (-independent, -int(direct_funding or upstream_funding), -int(upstream_funding))


def contrast_match_ladder(*, migration_week: int | None, funding_state: int,
                          fanout_band: int | None) -> tuple[tuple[object, ...], ...]:
    """Frozen deterministic matching fallbacks; no candidate-specific tuning."""
    week = migration_week if migration_week is not None else -1
    band = fanout_band if fanout_band is not None else -1
    return ((week, funding_state, band), (week, funding_state), (funding_state,), ())
