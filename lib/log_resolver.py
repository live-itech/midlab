"""Map a web console service id to its log file path.

Centralized so the resolver can be unit-tested without importing FastAPI.

Convention:
- ``tcp_<id>__comm`` → ``<LOG_DIR>/tcp_<id>.comm.log``  (raw comm trace)
- anything else      → ``<LOG_DIR>/<service>.log``      (regular service log)
"""
from __future__ import annotations

import os

LOG_DIR = "/var/log/midlab"


def resolve_log_path(service: str, log_dir: str = LOG_DIR) -> str:
    if service.endswith("__comm"):
        base = service[: -len("__comm")]
        return os.path.join(log_dir, f"{base}.comm.log")
    return os.path.join(log_dir, f"{service}.log")
