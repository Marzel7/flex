"""Registry-only adapter executor; acquisition capability is supplied by runtime."""
from __future__ import annotations

from collections.abc import Callable, Mapping


class AdapterUnavailable(RuntimeError):
    pass


class AdapterRegistryExecutor:
    """Dispatch an already-registered adapter without pricing or provider policy."""
    def __init__(self, registry: Mapping[str, tuple[str, str]], acquire: Mapping[str, Callable]):
        self._registry = dict(registry)
        self._acquire = dict(acquire)

    def __call__(self, _adapter: str, event: Mapping[str, object]) -> Mapping[str, object]:
        operation = str(event.get("operation_id") or "")
        registered = self._registry.get(operation)
        if registered is None:
            raise AdapterUnavailable("ADAPTER_UNAVAILABLE")
        adapter, _version = registered
        capability = self._acquire.get(adapter)
        if capability is None:
            raise AdapterUnavailable("ADAPTER_CAPABILITY_UNBOUND")
        return dict(capability(dict(event)))
