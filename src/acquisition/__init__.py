"""Operation-neutral blockchain acquisition interfaces."""

from .transaction import (
    AcquisitionContext,
    AcquisitionMetadata,
    AcquisitionResponse,
    SharedTransactionAcquisition,
    acquisition_scope,
)

__all__ = [
    "AcquisitionContext",
    "AcquisitionMetadata",
    "AcquisitionResponse",
    "SharedTransactionAcquisition",
    "acquisition_scope",
]
