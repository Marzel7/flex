"""Deterministic, fail-closed admission for bounded Evidence mirror cohorts."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


MAX_PHASE_ONE_MINTS = 50
_MINT = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


class CohortManifestError(ValueError):
    pass


def _canonical(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


@dataclass(frozen=True)
class CohortManifest:
    cohort_id: str
    schema_version: int
    created_at: str
    purpose: str
    maximum_mints: int
    mints: tuple[str, ...]
    manifest_hash: str

    @classmethod
    def load(cls, path: Path) -> "CohortManifest":
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CohortManifestError(f"manifest unavailable: {type(exc).__name__}") from exc
        if not isinstance(raw, dict):
            raise CohortManifestError("manifest must be an object")
        required = {"cohort_id", "schema_version", "created_at", "purpose", "maximum_mints", "mints"}
        if set(raw) != required:
            raise CohortManifestError("manifest fields do not match schema v1")
        if raw["schema_version"] != 1 or not isinstance(raw["cohort_id"], str) or not raw["cohort_id"].strip():
            raise CohortManifestError("invalid cohort identity/schema")
        if not isinstance(raw["created_at"], str) or not isinstance(raw["purpose"], str):
            raise CohortManifestError("invalid manifest metadata")
        maximum = raw["maximum_mints"]
        mints = raw["mints"]
        if not isinstance(maximum, int) or isinstance(maximum, bool) or not 0 <= maximum <= MAX_PHASE_ONE_MINTS:
            raise CohortManifestError("maximum_mints must be an integer from 0 to 50")
        if not isinstance(mints, list) or not all(isinstance(mint, str) and _MINT.fullmatch(mint) for mint in mints):
            raise CohortManifestError("manifest contains an invalid mint")
        if len(set(mints)) != len(mints):
            raise CohortManifestError("manifest contains duplicate mints")
        if len(mints) > maximum:
            raise CohortManifestError("manifest mint count exceeds maximum_mints")
        canonical = {**raw, "mints": sorted(mints)}
        return cls(raw["cohort_id"], 1, raw["created_at"], raw["purpose"], maximum,
                   tuple(canonical["mints"]), hashlib.sha256(_canonical(canonical)).hexdigest())


@dataclass(frozen=True)
class CohortDecision:
    state: str
    mint: str | None


class EvidenceMirrorCohortGate:
    """An immutable in-memory manifest; file edits never change live membership."""
    def __init__(self, manifest: CohortManifest) -> None:
        self.manifest = manifest
        self._mints = frozenset(manifest.mints)

    def decide(self, mint: str | None) -> CohortDecision:
        if not mint:
            return CohortDecision("REJECTED_MISSING_COHORT_IDENTITY", None)
        return CohortDecision("ACCEPTED_COHORT" if mint in self._mints else "EXCLUDED_NOT_IN_COHORT", mint)
