"""Run DEV004 credential qualification without runtime startup or credentials.

Invoke from env -i. Network and database entry points are denied before pytest
collection. Actual source price functions use synthetic transports and objects.
"""
import json
from pathlib import Path
import socket
import sqlite3
import sys
import types
import threading
import urllib.request

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))


def forbidden(*args,**kwargs):
    raise AssertionError('LIVE_NETWORK_OR_DATABASE_ACCESS_FORBIDDEN')


socket.socket.connect=forbidden
socket.create_connection=forbidden
urllib.request.urlopen=forbidden
sqlite3.connect=forbidden
stub=types.ModuleType('src.utils.db_locking');stub.db_connect=forbidden;stub.DB_WRITE_LOCK=threading.RLock()
sys.modules['src.utils.db_locking']=stub
# No project DB/reaper bootstrap. Only the existing price consumer needs this stub.
import requests
requests.sessions.Session.request=forbidden
import pytest


class Results:
    def __init__(self):self.passed=self.failed=self.skipped=0
    def pytest_runtest_logreport(self,report):
        if report.when=='call':
            self.passed+=int(report.passed);self.failed+=int(report.failed);self.skipped+=int(report.skipped)
        elif report.failed:self.failed+=1


results=Results()
status=pytest.main(['-c','/dev/null','--noconftest','-p','no:cacheprovider','-q','--tb=short',
 str(ROOT/'tests/dev004/test_birdeye_authority.py'),str(ROOT/'tests/test_x78_10_price_service_singleton.py'),str(ROOT/'tests/test_x78_13_price_service_schema_lease.py')],plugins=[results])
data={'passed':results.passed,'failed':results.failed,'skipped':results.skipped,'exit_code':int(status),'network':'DENIED','database_access':'DENIED','credential_values':'SYNTHETIC_ONLY; env -i and mocked dotenv','production_writes':0,'provider_calls':0,'service_actions':0,'runtime_deployment':'NOT_AUTHORIZED_NOT_ATTEMPTED'}
(ROOT/'docs/dev/lightweight_dev004_credentials/qualification.json').write_text(json.dumps(data,indent=2)+'\n')
raise SystemExit(status)
