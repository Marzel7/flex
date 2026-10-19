"""Operation Contract v1 loading with no dynamic code execution."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .formalization import ContractRegistryModel, validate_contract


class OperationContractLoader:
    def __init__(self, registry: ContractRegistryModel) -> None:
        self.registry = registry

    def load_mapping(self, value: Mapping[str, Any]) -> Mapping[str, Any]:
        contract = validate_contract(value)
        self.registry.register(contract)
        return contract

    def load_bytes(self, payload: bytes) -> Mapping[str, Any]:
        value = json.loads(payload.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Operation Contract document must be a JSON object")
        return self.load_mapping(value)

    def load_path(self, path: Path) -> Mapping[str, Any]:
        return self.load_bytes(Path(path).read_bytes())
