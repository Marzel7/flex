import json
from pathlib import Path
import shutil

import pytest

from src.evidence.contracts.production_shadow_fixture_runner import (
    BOUND_COHORT_DIGEST, BOUND_PREFLIGHT_DIGEST,
)
from src.evidence.contracts.production_shadow_superseding_preflight import (
    COHORT_DIGEST, PREFLIGHT_DIGEST, SHADOW_OUTPUT,
    SupersedingPreflightError, verify_superseding_preflight,
)


ARTIFACT = Path("docs/audits/psi0b_e3_superseding_preflight")


def test_committed_superseding_artifact_replays_and_runner_is_rebound():
    assert len(verify_superseding_preflight(ARTIFACT)) == 64
    assert BOUND_COHORT_DIGEST == COHORT_DIGEST
    assert BOUND_PREFLIGHT_DIGEST == PREFLIGHT_DIGEST
    assert not SHADOW_OUTPUT.exists()


@pytest.mark.parametrize("mutation", ("missing", "extra", "altered", "authority"))
def test_missing_extra_altered_or_authority_drift_fails(tmp_path, mutation):
    target = tmp_path / "artifact"
    shutil.copytree(ARTIFACT, target)
    if mutation == "missing":
        (target / "cohort.json").unlink()
        reason = "FILE_SET"
    elif mutation == "extra":
        (target / "extra.json").write_text("{}")
        reason = "FILE_SET"
    else:
        path = target / ("preflight.json" if mutation == "altered" else "cohort.json")
        doc = json.loads(path.read_text())
        if mutation == "altered":
            doc["preflight"]["run_id"] = "changed"
        else:
            doc["superseded_identity_replay_verified"] = True
        path.write_text(json.dumps(doc, sort_keys=True, separators=(",", ":")) + "\n")
        reason = "DIGEST|LINEAGE"
    with pytest.raises(SupersedingPreflightError, match=reason):
        verify_superseding_preflight(target)
