from src.ops.durable_execution_evidence import PhaseEvidenceStore


def test_recovery_survives_termination_after_hot_retire(tmp_path):
    store = PhaseEvidenceStore(tmp_path, "run-2")
    store.emit("SELECTED", manifest_path="/tmp/manifest.json", selected=500)
    store.emit("PUBLISHED", cold_destination="/tmp/delta.sqlite", copied=500)
    store.emit("HOT_RETIRE_BEGIN", hot_before=1000)
    store.emit("HOT_RETIRE_COMMIT", retired=500, elapsed_seconds=0.4)

    recovered = PhaseEvidenceStore(tmp_path, "run-2").recovery()
    assert recovered["retirement_committed"] is True
    assert recovered["complete"] is False
    assert recovered["selected_manifest"] == "/tmp/manifest.json"
    assert recovered["cold_destination"] == "/tmp/delta.sqlite"


def test_pre_delete_partial_execution_never_claims_retirement(tmp_path):
    store = PhaseEvidenceStore(tmp_path, "run-3")
    store.emit("SELECTED", manifest_path="/tmp/manifest.json", selected=500)
    store.emit("PUBLISHED", cold_destination="/tmp/delta.sqlite", copied=500)

    recovered = store.recovery()
    assert recovered["retirement_committed"] is False
    assert recovered["complete"] is False


def test_complete_marker_is_distinct_from_committed_delete(tmp_path):
    store = PhaseEvidenceStore(tmp_path, "run-4")
    store.emit("HOT_RETIRE_COMMIT", retired=500)
    store.emit("COMPLETE", parity="PASS")

    recovered = store.recovery()
    assert recovered["retirement_committed"] is True
    assert recovered["complete"] is True
