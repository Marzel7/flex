"""Pure Watchtower Policy-C checkpoint cadence for historical reconstruction."""
from __future__ import annotations


POLICY_C_CHECKPOINTS = (
    (0, 2 * 3600, 15 * 60),
    (2 * 3600, 6 * 3600, 30 * 60),
    (6 * 3600, 12 * 3600, 60 * 60),
    (12 * 3600, 24 * 3600, 2 * 3600),
)


def policy_c_checkpoint_offsets() -> tuple[int, ...]:
    """Return the 24-hour Policy-C checkpoint offsets, excluding entry discovery."""
    return tuple(
        offset
        for start, end, every in POLICY_C_CHECKPOINTS
        for offset in range(start + every, end + 1, every)
    )
