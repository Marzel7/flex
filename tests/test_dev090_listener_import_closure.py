"""Real listener import closure; no stubs or operational listener startup."""
import importlib
import threading
import socket
import pytest


def test_actual_listener_import_closure(tmp_path,monkeypatch):
    for key,name in [('PREMIGRATION_LOG_PATH','premigration.txt'),('RPC_METRICS_DB','rpc-metrics.db'),('DB_PATH','fixture-core.db'),('MIGRATION_LOG_PATH','migration.txt')]:
        monkeypatch.setenv(key,str(tmp_path/name))
    # Existing shared-writer reaper is part of DEV090's already-qualified
    # database dependency, not a worker introduced by this module. Establish
    # that baseline before checking for additional listener import workers.
    from src.utils import db_locking
    started=[]
    def forbid(*a,**k):
        started.append('background/provider attempted')
        raise AssertionError('listener import must not acquire or start workers')
    monkeypatch.setattr(threading.Thread,'start',forbid)
    monkeypatch.setattr(socket.socket,'connect',forbid)
    listener=importlib.import_module('src.core.pumpfun_curve_listener')
    from src.ops.acquisition_availability import AcquisitionAvailability
    assert listener.AcquisitionAvailability is AcquisitionAvailability
    assert not started
    assert callable(listener.PumpFunCurveListener._insert_bonding_curve_token)
    assert all(p.stat().st_size<1048576 for p in tmp_path.rglob('*') if p.is_file())
