"""Pure Watchtower Policy-C checkpoint cadence for historical reconstruction."""
from __future__ import annotations


POLICY_C_CHECKPOINTS = (
    (0, 2 * 3600, 15 * 60),
    (2 * 3600, 6 * 3600, 30 * 60),
    (6 * 3600, 12 * 3600, 60 * 60),
    (12 * 3600, 24 * 3600, 2 * 3600),
)


def policy_c_next_check_at(*, birth_timestamp: int, now: int) -> int | None:
    """Return the next age-anchored Policy-C checkpoint, or ``None`` at 24h.

    The schedule is anchored to immutable canonical birth time rather than a
    worker tick or the last response.  That makes restarts unable to turn one
    missed checkpoint into a burst of immediate provider attempts.
    """
    birth = int(birth_timestamp)
    current = int(now)
    if birth <= 0 or current < birth:
        raise ValueError("INVALID_CANONICAL_BIRTH_TIMESTAMP")
    age = current - birth
    if age >= 24 * 3600:
        return None
    for start, end, every in POLICY_C_CHECKPOINTS:
        if age < end:
            checkpoint = birth + start + (((age - start) // every) + 1) * every
            return checkpoint
    return None


def policy_c_schedule(*, birth_timestamp: int | None, now: int) -> dict[str, int | str | None]:
    """Classify a durable next-check schedule without provider or queue access."""
    if birth_timestamp is None:
        return {"state": "INSUFFICIENT_CANONICAL_BIRTH", "next_check_at": None}
    next_check = policy_c_next_check_at(birth_timestamp=int(birth_timestamp), now=int(now))
    return {
        "state": "NO_FURTHER_CHECK" if next_check is None else "SCHEDULED",
        "next_check_at": next_check,
    }


def policy_c_checkpoint_offsets() -> tuple[int, ...]:
    """Return the 24-hour Policy-C checkpoint offsets, excluding entry discovery."""
    return tuple(
        offset
        for start, end, every in POLICY_C_CHECKPOINTS
        for offset in range(start + every, end + 1, every)
    )
