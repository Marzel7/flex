"""Isolated Evidence Platform infrastructure (EP1.0).

This package has no production acquisition, detector, projection, governance,
or database dependencies. All components are disabled by default.
"""

from .config import EvidenceConfig
from .service import EvidencePlatform

__all__ = ["EvidenceConfig", "EvidencePlatform"]
