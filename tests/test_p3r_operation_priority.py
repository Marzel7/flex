from src.ops.p3r_operation_priority import atomic_recurrence_points

def test_strong_atomic_aliases_receive_strong_points():
    assert atomic_recurrence_points('STRONGLY_RECURRENT') == 10
    assert atomic_recurrence_points('ATOMIC_STRONGLY_RECURRENT') == 10

def test_weaker_or_missing_atomic_states_do_not_receive_strong_points():
    assert atomic_recurrence_points('OBSERVED_NOT_STRONGLY_RECURRENT') == 5
    assert atomic_recurrence_points(None) == 0
    assert atomic_recurrence_points('NOT_OBSERVED') == 0
