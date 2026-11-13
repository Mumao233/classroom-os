"""No secrets: locate any reduced-token CPython import/startup stall."""
import sys,time
started=time.monotonic()
def mark(name):print('{"phase":"'+name+'","elapsed":'+str(round(time.monotonic()-started,3))+'}',flush=True)
mark('python_started')
import hashlib
mark('hashlib_loaded')
import importlib.util
mark('importlib_loaded')
from pathlib import Path
mark('pathlib_loaded')
sys.path.insert(0,str(Path(__file__).resolve().parent))
import common
mark('common_loaded')
common.runtime_dependencies()
mark('runtime_dependencies_pinned_readonly')
import cryptography,rfc8785
mark('crypto_loaded')
