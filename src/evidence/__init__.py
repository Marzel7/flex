"""Isolated Evidence Platform infrastructure (EP1.0).

This package has no production acquisition, detector, projection, governance,
or database dependencies. All components are disabled by default.
"""

from typing import TYPE_CHECKING, Any

from .config import EvidenceConfig

if TYPE_CHECKING:
    from .service import EvidencePlatform

__all__ = ["EvidenceConfig", "EvidencePlatform"]


def __getattr__(name: str) -> Any:
    if name == "EvidencePlatform":
        from .service import EvidencePlatform

        return EvidencePlatform
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted((*globals(), *__all__))
