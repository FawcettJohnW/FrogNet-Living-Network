#!/opt/frognet_semantic/venv/bin/python3
import os, sys, time, threading, uuid

_DEBUG = os.environ.get("FROGNET_DEBUG", "0") == "1"
_PID = os.getpid()
_LOCK = threading.RLock()

def _ts():
    return time.strftime("%Y-%m-%d %H:%M:%S")

def trace(msg: str):
    if not _DEBUG:
        return
    with _LOCK:
        sys.stderr.write(f"[{_ts()}] [PID {_PID}] {msg}\n")
        sys.stderr.flush()

def new_trace_id() -> str:
    return uuid.uuid4().hex[:8]
