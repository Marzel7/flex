"""Operation-neutral blockchain acquisition interfaces."""

from .transaction import (
    AcquisitionContext,
    AcquisitionMetadata,
    AcquisitionResponse,
    SharedTransactionAcquisition,
    acquisition_scope,
)
from .factory import build_transaction_acquisition

__all__ = [
    "AcquisitionContext",
    "AcquisitionMetadata",
    "AcquisitionResponse",
    "SharedTransactionAcquisition",
    "acquisition_scope",
    "build_transaction_acquisition",
]
