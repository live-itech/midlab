"""Per-instrument bidirectional raw byte logger to tcp_<id>.comm.log.

Format: {timestamp}.{ms} [tcp_<id>] {direction} {decoded}
Where decoded maps control chars to <TAG> and non-printable bytes to \\xNN.
"""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from typing import Dict

from lib import timeutil

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


class CommLogger:
    _cache: Dict[int, "CommLogger"] = {}

    def __init__(self, instrument_id: int):
        self.instrument_id = instrument_id
        self._logger = logging.getLogger(f"midlab.comm.tcp_{instrument_id}")
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False
        self._warned = False
        if not self._logger.handlers:
            try:
                os.makedirs(LOG_DIR, exist_ok=True)
                handler = RotatingFileHandler(
                    os.path.join(LOG_DIR, f"tcp_{instrument_id}.comm.log"),
                    maxBytes=ROTATE_BYTES,
                    backupCount=ROTATE_BACKUPS,
                )
                formatter = logging.Formatter(
                    "%(asctime)s.%(msecs)03d [tcp_" + str(instrument_id) + "] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S",
                )
                # Samakan dengan get_logger: jam lokal lab, supaya lalu lintas
                # byte di *.comm.log bisa dikorelasikan dengan log service.
                formatter.converter = timeutil.logging_converter
                handler.setFormatter(formatter)
                self._logger.addHandler(handler)
            except Exception as exc:
                logging.getLogger("web_console").warning(
                    "CommLogger init failed for instrument %s: %s", instrument_id, exc
                )

    @classmethod
    def for_instrument(cls, instrument_id: int) -> "CommLogger":
        if instrument_id not in cls._cache:
            cls._cache[instrument_id] = cls(instrument_id)
        return cls._cache[instrument_id]

    def rx(self, data: bytes) -> None:
        if not data:
            return
        try:
            self._logger.info("← RX %s", _decode_for_log(data))
        except Exception as exc:
            if not self._warned:
                self._warned = True
                logging.getLogger("web_console").warning(
                    "CommLogger rx failed (instrument %s): %s", self.instrument_id, exc
                )

    def tx(self, data: bytes) -> None:
        if not data:
            return
        try:
            self._logger.info("→ TX %s", _decode_for_log(data))
        except Exception as exc:
            if not self._warned:
                self._warned = True
                logging.getLogger("web_console").warning(
                    "CommLogger tx failed (instrument %s): %s", self.instrument_id, exc
                )
