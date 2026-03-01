"""
Shared RPC Metrics - Ensures all components use the same recorder instance.

This module provides a shared, singleton recorder instance that's accessed
by both the RPC Metrics API and the listener/extractor code.
"""

from rpc_metrics_recorder import (
    RPCMetricsRecorder,
    record_request,
    record_stream_bytes,
    get_recorder,
    initialize_recorder,
)

# Re-export everything from the recorder module
__all__ = [
    'RPCMetricsRecorder',
    'record_request',
    'record_stream_bytes',
    'get_recorder',
    'initialize_recorder',
]
