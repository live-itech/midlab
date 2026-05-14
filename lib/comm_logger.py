"""Per-instrument bidirectional raw byte logger to tcp_<id>.comm.log.

Format: {timestamp}.{ms} [tcp_<id>] {direction} {decoded}
Where decoded maps control chars to <TAG> and non-printable bytes to \\xNN.
"""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from typing import Dict

LOG_DIR = "/var/log/midlab"
ROTATE_BYTES = 50 * 1024 * 1024
ROTATE_BACKUPS = 5

_CTRL = {
    0x02: "<STX>", 0x03: "<ETX>", 0x04: "<EOT>", 0x05: "<ENQ>",
    0x06: "<ACK>", 0x15: "<NAK>", 0x17: "<ETB>",
    0x0D: "<CR>", 0x0A: "<LF>",
}


def _decode_for_log(data: bytes) -> str:
    out = []
    for b in data:
        if b in _CTRL:
            out.append(_CTRL[b])
        elif 0x20 <= b <= 0x7E:
            out.append(chr(b))
        else:
            out.append(f"\\x{b:02x}")
    return "".join(out)
