"""Deterministic, closed registries used by the generic Operation runtime."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Generic, Mapping, Optional, TypeVar

from ..contracts import canonical_json_bytes
from .formalization import BehaviourModuleProtocol, DetectorProtocol, TopologyModuleProtocol


T = TypeVar("T")


@dataclass(frozen=True)
class RegistryEntry(Generic[T]):
    item_id: str
    version: str
    implementation: T
    digest: str


class ImplementationRegistry(Generic[T]):
    """Explicit registration only; no imports or plugin discovery from contracts."""

    def __init__(self, *, id_attribute: str, version_attribute: str) -> None:
        self.id_attribute = id_attribute
        self.version_attribute = version_attribute
        self._entries: dict[tuple[str, str], RegistryEntry[T]] = {}

    def register(self, implementation: T) -> str:
        item_id = str(getattr(implementation, self.id_attribute))
        version = str(getattr(implementation, self.version_attribute))
        key = item_id, version
        identity = hashlib.sha256(canonical_json_bytes([item_id, version])).hexdigest()
        existing = self._entries.get(key)
        if existing is not None:
            if existing.implementation is not implementation:
                raise ValueError(f"registry collision: {item_id}@{version}")
            return existing.digest
        self._entries[key] = RegistryEntry(item_id, version, implementation, identity)
        return identity

    def resolve(self, item_id: str, version: str) -> T:
        try:
            return self._entries[(item_id, version)].implementation
        except KeyError as exc:
            raise LookupError(f"unregistered implementation: {item_id}@{version}") from exc

    def versions(self, item_id: str) -> tuple[str, ...]:
        return tuple(sorted(version for entry_id, version in self._entries if entry_id == item_id))

    def entries(self) -> tuple[RegistryEntry[T], ...]:
        return tuple(self._entries[key] for key in sorted(self._entries))


class PresentationRegistry:
    def __init__(self) -> None:
        self._schemas: dict[str, tuple[str, Mapping[str, Any]]] = {}

    def register(self, version: str, schema: Mapping[str, Any]) -> str:
        payload = canonical_json_bytes(dict(schema))
        digest = hashlib.sha256(payload).hexdigest()
        existing = self._schemas.get(version)
        if existing and existing[0] != digest:
            raise ValueError(f"presentation registry collision: {version}")
        self._schemas.setdefault(version, (digest, dict(schema)))
        return digest

    def resolve(self, version: str) -> Mapping[str, Any]:
        try:
            return dict(self._schemas[version][1])
        except KeyError as exc:
            raise LookupError(f"unregistered presentation schema: {version}") from exc

    def versions(self) -> tuple[str, ...]:
        return tuple(sorted(self._schemas))


class TopologyVersionRegistry:
    """Version-only registry matching the frozen TopologyModuleProtocol."""

    def __init__(self) -> None:
        self._implementations: dict[str, TopologyModuleProtocol] = {}

    def register(self, implementation: TopologyModuleProtocol) -> str:
        version = str(implementation.topology_version)
        existing = self._implementations.get(version)
        if existing is not None and existing is not implementation:
            raise ValueError(f"topology version collision: {version}")
        self._implementations.setdefault(version, implementation)
        return hashlib.sha256(canonical_json_bytes(["topology", version])).hexdigest()

    def resolve(self, version: str) -> TopologyModuleProtocol:
        try:
            return self._implementations[version]
        except KeyError as exc:
            raise LookupError(f"unregistered topology version: {version}") from exc

    def versions(self) -> tuple[str, ...]:
        return tuple(sorted(self._implementations))


class RuntimeRegistries:
    def __init__(self) -> None:
        self.behaviours: ImplementationRegistry[BehaviourModuleProtocol] = ImplementationRegistry(
            id_attribute="module_id", version_attribute="module_version"
        )
        self.detectors: ImplementationRegistry[DetectorProtocol] = ImplementationRegistry(
            id_attribute="detector_id", version_attribute="detector_version"
        )
        self.topologies = TopologyVersionRegistry()
        self.presentations = PresentationRegistry()

    def dependency_versions(self) -> tuple[Mapping[str, tuple[str, ...]], Mapping[str, tuple[str, ...]]]:
        behaviours = {entry.item_id: self.behaviours.versions(entry.item_id)
                      for entry in self.behaviours.entries()}
        detectors = {entry.item_id: self.detectors.versions(entry.item_id)
                     for entry in self.detectors.entries()}
        return behaviours, detectors
