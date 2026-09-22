"""Pure, venue-neutral reduction for versioned operation opening-history profiles."""
from __future__ import annotations

from typing import Iterable, Mapping


ORDER_KEYS = ("slot", "transaction_index", "event_index")


def materialize_opening_history(profile: Mapping, events: Iterable[Mapping]) -> dict:
    """Return the configured ordered non-creator opening events without acquisition."""
    depth = profile.get("opening_event_depth")
    if not isinstance(depth, int) or depth < 1:
        raise ValueError("OPENING_EVENT_DEPTH_INVALID")
    required = {"operation_id", "profile_version", "creator_exclusion_policy", "supported_venues"}
    if required - set(profile):
        raise ValueError("OPENING_HISTORY_PROFILE_INCOMPLETE")
    accepted = []
    for event in events:
        if event.get("qualification_state") != "QUALIFIED" or not event.get("success"):
            continue
        if event.get("creator_status") == "CREATOR" and profile["creator_exclusion_policy"] == "EXCLUDE_CREATOR":
            continue
        if event.get("venue") not in profile["supported_venues"]:
            continue
        if any(not isinstance(event.get(key), int) for key in ORDER_KEYS):
            continue
        accepted.append(dict(event))
    accepted.sort(key=lambda event: tuple(event[key] for key in ORDER_KEYS))
    selected = accepted[:depth]
    return {
        "record_version": "OPERATION_OPENING_HISTORY_V1",
        "operation_id": profile["operation_id"],
        "profile_version": profile["profile_version"],
        "configured_depth": depth,
        "events": [{**event, "ordinal": index} for index, event in enumerate(selected, 1)],
        "qualification_state": "QUALIFIED" if len(selected) == depth else "INSUFFICIENT_EVIDENCE",
        "failure_reasons": [] if len(selected) == depth else ["CONFIGURED_OPENING_EVENT_DEPTH_NOT_REACHED"],
    }
