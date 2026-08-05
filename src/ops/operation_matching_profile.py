"""X75.0 Discovery <-> Operations Convergence — per-Operation matching profiles.

Different confirmed Operations are defined by different evidence. WATCHTOWER
is defined by Treasury -> Subprovider -> Provisioning -> Creator -> Launch
(a CONFIRMED_TREASURY_CONTROL-class population). A hypothetical Operation
defined by creator reuse alone would instead key off CREATOR_REUSE_CONTROL.
This module makes that difference EXPLICIT, DATA-DRIVEN CONFIGURATION rather
than hardcoded per-operator branches scattered across the codebase (the
pattern found and flagged in prior WATCHTOWER-only hardcoding: eg.
_canonical_identity() in discovery/service.py, _canonical_families() in
emerging_operator_service.py, and TreasuryExpansionResolver — none of those
are touched by this module; it is additive).

A "matching profile" is a small, explicit declaration of which entity_type
values (from operator_entities) and which evidence_type values (from the
existing src/ops/disposition_resolver.py control/population vocabulary --
reused, not duplicated) an Operation considers its OWN defining evidence.
Profiles are looked up by operator_id; an operator with no declared profile
falls back to a generic default (population + control evidence in general,
matching disposition_resolver.py's own generic behaviour) rather than being
silently assumed to look like WATCHTOWER.

This module is read-only / pure-function: it never writes to the database,
never mutates attribution, reconciliation, or registry state. It only
answers "given this Investigation Population's evidence, which known
Operations does it most resemble, and why."
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.ops.watchtower_alignment import WATCHTOWER_OPERATOR_ID


@dataclass(frozen=True, slots=True)
class OperationMatchingProfile:
    operator_id: str
    display_name: str
    defining_entity_types: frozenset[str]
    defining_control_types: frozenset[str]
    defining_population_types: frozenset[str]
    description: str


# Declared profiles. Adding a new Operation's profile here is the ONLY step
# needed to make Discovery match against it -- no other module needs a new
# per-operator branch. WATCHTOWER's profile is declared the same way every
# other operation's would be; nothing here is more privileged than anything
# else at the code level, only in the *data* each profile declares.
_KNOWN_PROFILES: dict[str, OperationMatchingProfile] = {
    WATCHTOWER_OPERATOR_ID: OperationMatchingProfile(
        operator_id=WATCHTOWER_OPERATOR_ID,
        display_name="WATCHTOWER",
        defining_entity_types=frozenset({"TREASURY", "SUB_PROVISIONER"}),
        defining_control_types=frozenset({"CONFIRMED_TREASURY_CONTROL", "RPC_CONFIRMED_LINEAGE"}),
        defining_population_types=frozenset({"TREASURY_RELATIONSHIPS", "PROVISIONING_LINEAGE", "PROVISIONING_SESSIONS"}),
        description="Treasury -> Subprovider -> Provisioning -> Creator -> Launch, "
                     "confirmed via wrap-close/seeded-account funding mechanism.",
    ),
    "64527dc2-8073-50c0-8bd7-7ef49e62d875": OperationMatchingProfile(
        operator_id="64527dc2-8073-50c0-8bd7-7ef49e62d875",
        display_name="3SW2",
        defining_entity_types=frozenset({"CLIENT"}),
        defining_control_types=frozenset({"PERSISTENT_CONTROLLER_REUSE", "RETURN_TO_CONTROLLER"}),
        defining_population_types=frozenset(),
        description="Client-relationship reuse, independent of WATCHTOWER's treasury/"
                     "subprovider topology.",
    ),
}

# Generic fallback for a confirmed operator with no declared profile --
# matches disposition_resolver.py's own generic control+population logic,
# so "no profile declared" degrades to "the same evidence-worthiness test
# everything already passes through," never a WATCHTOWER assumption.
_GENERIC_PROFILE_TEMPLATE = {
    "defining_entity_types": frozenset(),  # empty = no entity_type constraint
    "defining_control_types": frozenset({
        "RETURN_TO_CONTROLLER", "SETTLEMENT_CONVERGENCE", "RPC_CONFIRMED_LINEAGE",
        "PERSISTENT_CONTROLLER_REUSE", "CONFIRMED_TREASURY_CONTROL", "CREATOR_REUSE_CONTROL",
    }),
    "defining_population_types": frozenset({
        "TREASURY_RELATIONSHIPS", "PROVISIONING_LINEAGE", "PROVISIONING_SESSIONS",
    }),
}


def get_profile(operator_id: str, display_name: str | None = None) -> OperationMatchingProfile:
    """Return the declared profile for operator_id, or a generic fallback
    profile (never a WATCHTOWER-shaped assumption) if none is declared."""
    declared = _KNOWN_PROFILES.get(operator_id)
    if declared:
        return declared
    return OperationMatchingProfile(
        operator_id=operator_id,
        display_name=display_name or operator_id,
        description="No matching profile declared; using the generic evidence-worthiness "
                     "criteria (any control + population evidence), not a WATCHTOWER-shaped assumption.",
        **_GENERIC_PROFILE_TEMPLATE,
    )


def all_declared_profiles() -> list[OperationMatchingProfile]:
    return list(_KNOWN_PROFILES.values())


@dataclass(frozen=True, slots=True)
class MatchResult:
    operator_id: str
    display_name: str
    score: float                      # 0..1, fraction of the profile's defining evidence matched
    matched_entity_types: frozenset[str]
    matched_control_types: frozenset[str]
    matched_population_types: frozenset[str]
    reason: str


def score_population_against_profile(
    profile: OperationMatchingProfile,
    *,
    entity_types: frozenset[str],
    control_types: frozenset[str],
    population_types: frozenset[str],
) -> MatchResult:
    """Score how well a population's observed evidence matches ONE profile's
    defining evidence. Pure function, no I/O. Score is the fraction of the
    profile's declared defining-evidence groups (entity/control/population)
    that have at least one match, averaged across the groups the profile
    actually declares (an empty declared set for a group is excluded from
    the denominator, not treated as a failed match)."""
    matched_entities = entity_types & profile.defining_entity_types
    matched_controls = control_types & profile.defining_control_types
    matched_population = population_types & profile.defining_population_types

    # Each group's contribution is the FRACTION of that group's declared
    # defining types that were actually matched, not a binary any-overlap
    # flag -- a profile declaring {TREASURY, SUB_PROVISIONER} as its
    # defining entity types must not be satisfied by a population that only
    # has a TREASURY (e.g. B48k, which shares WATCHTOWER's treasury but has
    # no subprovisioner/wrap-close chain of its own -- shared infrastructure
    # is not the same as matching the full defining structure).
    groups = []
    if profile.defining_entity_types:
        groups.append(len(matched_entities) / len(profile.defining_entity_types))
    if profile.defining_control_types:
        groups.append(len(matched_controls) / len(profile.defining_control_types))
    if profile.defining_population_types:
        groups.append(len(matched_population) / len(profile.defining_population_types))
    score = (sum(groups) / len(groups)) if groups else 0.0

    reasons = []
    if matched_entities:
        reasons.append(f"entity types {sorted(matched_entities)}")
    if matched_controls:
        reasons.append(f"control evidence {sorted(matched_controls)}")
    if matched_population:
        reasons.append(f"population evidence {sorted(matched_population)}")
    reason = f"Matches {profile.display_name} via " + "; ".join(reasons) if reasons else \
        f"No defining evidence for {profile.display_name} found in this population."

    return MatchResult(
        operator_id=profile.operator_id,
        display_name=profile.display_name,
        score=round(score, 3),
        matched_entity_types=matched_entities,
        matched_control_types=matched_controls,
        matched_population_types=matched_population,
        reason=reason,
    )


def match_population_against_all_known_operations(
    conn,
    *,
    entity_types: frozenset[str],
    control_types: frozenset[str],
    population_types: frozenset[str],
    min_score: float = 0.34,
) -> list[MatchResult]:
    """Compare one Investigation Population's observed evidence against
    EVERY confirmed Operation's own matching profile (declared or generic
    fallback), returning matches above min_score, best first. conn is a
    read-only connection to database/wt_ops_v2.db."""
    rows = conn.execute(
        "SELECT operator_id, display_name FROM operators WHERE status='CONFIRMED'"
    ).fetchall()
    results = []
    for row in rows:
        operator_id = row["operator_id"] if hasattr(row, "keys") else row[0]
        display_name = row["display_name"] if hasattr(row, "keys") else row[1]
        profile = get_profile(operator_id, display_name)
        match = score_population_against_profile(
            profile,
            entity_types=entity_types,
            control_types=control_types,
            population_types=population_types,
        )
        if match.score >= min_score:
            results.append(match)
    results.sort(key=lambda m: m.score, reverse=True)
    return results
