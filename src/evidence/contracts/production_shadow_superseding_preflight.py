"""PSI0B-E3 canonical superseding cohort/preflight artifact."""

from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import shutil
from typing import Mapping

from .production_shadow_run_preflight import (
    build_immutable_cohort_artifact,
    build_production_shadow_run_preflight,
)


SCHEMA_VERSION = "psi0b-e3.v1"
UPSTREAM_BUNDLE_DIGEST = "2c07d41b9c243f8f0c8ca52e0c54c0b184f28a1ed855e98de2a975f936a688e5"
SELECTED_PROJECTION_DIGEST = "fd538d454cd10c7f7cd5ce5fa6fe251d2781efc42614ec10cff800a9348952ff"
COHORT_ID = "eb0.1p-selected-5000-v1"
COHORT_DIGEST = "c6069972dddc58fd95cbc3de231e2c4640afbb9dbc0b73475e70964929970722"
RUN_ID = "psi0b-shadow-20260814-02"
FACT_FAMILY = "LaunchFact"
SHADOW_OUTPUT = Path("/Users/kevinkeaveney/Dev/claude/flex/docs/audits/psi0b_runs/psi0b-shadow-20260814-02")
OUTPUT_FINGERPRINT = "55ee7ad8c26322c55b828f21c7ab80085688c46ac5e3129bc0d25b88b9c19ff3"
PREFLIGHT_DIGEST = "b2cfd09743c4ba21f7a61a816d8eff8b43e43a1b6e482c4ef9a7f2bd982dcca1"
SUPERSEDED_COHORT_DIGEST = "8f0a54838574e2e82f95030a5981a8b21b13629d493635719a0d23f5013bfbe3"
SUPERSEDED_PREFLIGHT_DIGEST = "35aa4d8e2f1519a60e6c3418a800476952e88bb1e44a4529c0ca7b9a90c960a0"
FILES = {"cohort.json", "preflight.json", "hashes.json"}


class SupersedingPreflightError(RuntimeError):
    """Named fail-closed PSI0B-E3 artifact violation."""


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"


def _sha(value: bytes) -> str:
    return sha256(value).hexdigest()


def _verify_upstream_bundle(bundle: Path) -> tuple[str, ...]:
    bundle = Path(bundle)
    if {row.name for row in bundle.iterdir()} != {"run.json", "aggregate.json", "corpora.json", "hashes.json"}:
        raise SupersedingPreflightError("PSI0B_E3_UPSTREAM_FILE_SET_MISMATCH")
    hashes = json.loads((bundle / "hashes.json").read_text())
    actual = {name: _sha((bundle / name).read_bytes()) for name in ("aggregate.json", "corpora.json", "run.json")}
    if hashes.get("files") != actual or hashes.get("bundle_digest") != _sha(_canonical(actual)):
        raise SupersedingPreflightError("PSI0B_E3_UPSTREAM_REPLAY_MISMATCH")
    if hashes["bundle_digest"] != UPSTREAM_BUNDLE_DIGEST:
        raise SupersedingPreflightError("PSI0B_E3_UPSTREAM_IDENTITY_DRIFT")
    corpora = json.loads((bundle / "corpora.json").read_text())
    mints = tuple(corpora.get("selected_mints", ()))
    if len(mints) != 5_000 or len(set(mints)) != 5_000:
        raise SupersedingPreflightError("PSI0B_E3_MEMBERSHIP_COUNT_DRIFT")
    return mints


def build_superseding_documents(bundle: Path) -> Mapping[str, object]:
    mints = _verify_upstream_bundle(bundle)
    cohort = build_immutable_cohort_artifact(
        cohort_id=COHORT_ID, mints=mints,
        source_artifact_digest=SELECTED_PROJECTION_DIGEST,
    )
    preflight = build_production_shadow_run_preflight(
        run_id=RUN_ID, cohort=cohort, fact_family=FACT_FAMILY,
        output_directory=SHADOW_OUTPUT,
    )
    if cohort.cohort_digest != COHORT_DIGEST:
        raise SupersedingPreflightError("PSI0B_E3_COHORT_DIGEST_DRIFT")
    if preflight.preflight_digest != PREFLIGHT_DIGEST or preflight.output_directory_fingerprint != OUTPUT_FINGERPRINT:
        raise SupersedingPreflightError("PSI0B_E3_PREFLIGHT_DIGEST_DRIFT")
    cohort_doc = {
        "schema_version": SCHEMA_VERSION,
        "supersedes": SUPERSEDED_COHORT_DIGEST,
        "superseded_identity_replay_verified": False,
        "member_ordering": "EXACT_UPSTREAM_SELECTED_MINTS_ARRAY_ORDER",
        "upstream_bundle_digest": UPSTREAM_BUNDLE_DIGEST,
        "selected_projection_digest": SELECTED_PROJECTION_DIGEST,
        "cohort": asdict(cohort),
    }
    preflight_doc = {
        "schema_version": SCHEMA_VERSION,
        "supersedes": SUPERSEDED_PREFLIGHT_DIGEST,
        "superseded_identity_replay_verified": False,
        "preflight": asdict(preflight),
    }
    return {"cohort.json": cohort_doc, "preflight.json": preflight_doc}


def materialize_superseding_preflight(bundle: Path, output: Path) -> str:
    output = Path(output)
    if output.exists():
        raise SupersedingPreflightError("PSI0B_E3_OUTPUT_NOT_NEW")
    documents = build_superseding_documents(bundle)
    payloads = {name: _canonical(value) for name, value in documents.items()}
    digests = {name: _sha(value) for name, value in payloads.items()}
    bundle_digest = _sha(_canonical(digests))
    hashes = _canonical({"schema_version": SCHEMA_VERSION, "files": digests, "bundle_digest": bundle_digest})
    staging = output.with_name(f".{output.name}.tmp")
    if staging.exists():
        raise SupersedingPreflightError("PSI0B_E3_STAGING_EXISTS")
    try:
        staging.mkdir(parents=True)
        for name, payload in payloads.items():
            with (staging / name).open("xb") as handle:
                handle.write(payload)
        with (staging / "hashes.json").open("xb") as handle:
            handle.write(hashes)
        staging.replace(output)
    except Exception as exc:
        if staging.exists():
            shutil.rmtree(staging)
        raise SupersedingPreflightError("PSI0B_E3_ATOMIC_WRITE_FAILED") from exc
    verify_superseding_preflight(output)
    return bundle_digest


def verify_superseding_preflight(output: Path) -> str:
    output = Path(output)
    if not output.is_dir() or {row.name for row in output.iterdir()} != FILES:
        raise SupersedingPreflightError("PSI0B_E3_FILE_SET_MISMATCH")
    try:
        cohort = json.loads((output / "cohort.json").read_text())
        preflight = json.loads((output / "preflight.json").read_text())
        hashes = json.loads((output / "hashes.json").read_text())
    except Exception as exc:
        raise SupersedingPreflightError("PSI0B_E3_INVALID_JSON") from exc
    documents = {"cohort.json": cohort, "preflight.json": preflight}
    if any((output / name).read_bytes() != _canonical(doc) for name, doc in documents.items()):
        raise SupersedingPreflightError("PSI0B_E3_NONCANONICAL_JSON")
    actual = {name: _sha((output / name).read_bytes()) for name in documents}
    bundle_digest = _sha(_canonical(actual))
    if hashes != {"schema_version": SCHEMA_VERSION, "files": actual, "bundle_digest": bundle_digest}:
        raise SupersedingPreflightError("PSI0B_E3_DIGEST_MISMATCH")
    c = cohort.get("cohort", {})
    p = preflight.get("preflight", {})
    if (cohort.get("supersedes") != SUPERSEDED_COHORT_DIGEST or
            preflight.get("supersedes") != SUPERSEDED_PREFLIGHT_DIGEST or
            cohort.get("superseded_identity_replay_verified") is not False or
            preflight.get("superseded_identity_replay_verified") is not False or
            c.get("cohort_digest") != COHORT_DIGEST or
            p.get("preflight_digest") != PREFLIGHT_DIGEST or
            p.get("cohort") != c or len(c.get("mints", ())) != 5_000 or
            len(set(c.get("mints", ()))) != 5_000 or
            p.get("output_directory_fingerprint") != OUTPUT_FINGERPRINT):
        raise SupersedingPreflightError("PSI0B_E3_LINEAGE_OR_MEMBERSHIP_DRIFT")
    return bundle_digest
