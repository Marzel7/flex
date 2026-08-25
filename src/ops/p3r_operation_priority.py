"""Small pure helpers for P3R operation-priority evidence scoring."""

def atomic_recurrence_points(value: str | None) -> int:
    """Return the recurrence component of the fixed 25-point atomic dimension."""
    if value in {"STRONGLY_RECURRENT", "ATOMIC_STRONGLY_RECURRENT"}:
        return 10
    if value == "OBSERVED_NOT_STRONGLY_RECURRENT":
        return 5
    return 0
