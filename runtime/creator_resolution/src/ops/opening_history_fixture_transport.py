"""Exact, offline-only routing from qualification adapter requests to fixtures."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Callable, Mapping


VERSION = "OPENING_HISTORY_EXACT_FIXTURE_TRANSPORT_V1"


class FixtureTransportError(RuntimeError):
    pass


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


class ExactQualificationFixtureTransport:
    """A finite request map.  It has no fallback and no network capability."""

    def __init__(self, qualification_manifest: Path, fixture_manifest: Path, *, real_provider_transport: Callable | None = None):
        qualification_raw = qualification_manifest.read_bytes()
        qualification = json.loads(qualification_raw)
        if qualification.get("real_provider_dispatch_allowed") is not False:
            raise FixtureTransportError("QUALIFICATION_MANIFEST_REAL_DISPATCH_FORBIDDEN")
        if real_provider_transport is not None:
            raise FixtureTransportError("QUALIFICATION_MANIFEST_REAL_DISPATCH_FORBIDDEN")
        self.qualification_manifest_sha256 = sha256(qualification_raw).hexdigest()
        self.fixture_manifest_path = fixture_manifest
        self.fixture_manifest_sha256 = sha256(fixture_manifest.read_bytes()).hexdigest()
        frozen = json.loads(fixture_manifest.read_text())
        if frozen.get("real_provider_dispatch_allowed") is not False or frozen.get("historical_evidence") is not False:
            raise FixtureTransportError("QUALIFICATION_MANIFEST_REAL_DISPATCH_FORBIDDEN")
        fixture_rows = {row["mint"]: row for row in frozen["rows"]}
        self.calls: list[dict] = []
        self._routes: dict[tuple[str, object], dict] = {}
        for ordinal, row in enumerate(qualification["rows"], 1):
            derived = fixture_rows.get(row["mint"])
            if not derived or derived["create_signature"] != row["create_signature"]:
                raise FixtureTransportError("QUALIFICATION_FIXTURE_IDENTITY_MISMATCH")
            row_id = derived["row_id"]
            role_paths = derived["fixtures"]
            self._add("getTransaction", row["create_signature"], row_id, "CREATE_getTransaction", fixture_manifest.parent / role_paths["getTransaction_CREATE"])
            self._add("getBlock", derived["create_slot"], row_id, "CREATE_getBlock", fixture_manifest.parent / role_paths["getBlock_CREATE"])
            if "getBlock_CREATE_PLUS_1" in role_paths:
                self._add("getBlock", derived["create_slot"] + 1, row_id, "CREATE_PLUS_1_getBlock", fixture_manifest.parent / role_paths["getBlock_CREATE_PLUS_1"])

    def _add(self, method: str, identity: object, row_id: str, role: str, path: Path) -> None:
        key = (method, identity)
        if key in self._routes or not path.is_file():
            raise FixtureTransportError("QUALIFICATION_FIXTURE_IDENTITY_MISMATCH")
        raw = path.read_bytes()
        self._routes[key] = {"row_id": row_id, "fixture_role": role, "fixture_path": str(path), "fixture_sha256": sha256(raw).hexdigest(), "response": raw}

    def __call__(self, method: str, params: list) -> bytes:
        identity = params[0] if isinstance(params, list) and params else None
        route = self._routes.get((method, identity))
        if route is None:
            raise FixtureTransportError("UNEXPECTED_QUALIFICATION_REQUEST")
        response = route["response"]
        request = {"method": method, "params": params}
        attempt = 1 + sum(call["row_id"] == route["row_id"] and call["provider_method"] == method for call in self.calls)
        self.calls.append({"row_id": route["row_id"], "provider_method": method, "request_identity": identity,
                           "request_identity_digest": sha256(_canonical(request)).hexdigest(), "fixture_role": route["fixture_role"],
                           "fixture_path": route["fixture_path"], "fixture_sha256": route["fixture_sha256"],
                           "exact_response_bytes": len(response), "response_sha256": sha256(response).hexdigest(), "attempt_number": attempt})
        return response
