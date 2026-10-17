"""
protocols/boditech_afias1/builder.py — Builder HL7 Boditech AFIAS-1

Satu-satunya pesan LIS → alat: ACK^R24^ACK atas OUL^R24. Identitas alat
(MSH-5/6) dan processing ID (MSH-11) dipantulkan dari pesan alat.
"""

from datetime import datetime

from lib.utils import get_logger
from protocols.boditech_afias1.constants import (
    MLLP_START_BYTE, MLLP_TRAILER,
    FIELD_SEPARATOR, ENCODING_CHARACTERS, SEGMENT_TERMINATOR, MESSAGE_ENCODING,
    HL7_VERSION, PROC_PRODUCTION, ACK_AA,
    EVENT_ACK_R24, ACK_SENDING_APP,
)


logger = get_logger("boditech_afias1_builder")


def _now_timestamp() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S")


class AfiasBuilder:
    """Builder pesan HL7 AFIAS-1 (arah LIS → alat)."""

    def __init__(self, now=None):
        """
        Args:
            now: callable pengganti jam sistem (`YYYYMMDDHHMMSS`) — unit test.
        """
        self._now = now or _now_timestamp

    def _wrap_mllp(self, segments: list) -> bytes:
        body = "".join(f"{seg}{SEGMENT_TERMINATOR}" for seg in segments)
        return MLLP_START_BYTE + body.encode(MESSAGE_ENCODING, errors="replace") + MLLP_TRAILER

    def build_ack_r24(self, context: dict) -> bytes:
        """
        ACK^R24^ACK atas OUL^R24.

        Args:
            context: hasil AfiasParser.parse_msh() pesan alat
        """
        context = context or {}
        control_id = context.get("control_id", "")

        msh = FIELD_SEPARATOR.join([
            "MSH",
            ENCODING_CHARACTERS,                          # MSH-2
            ACK_SENDING_APP,                              # MSH-3
            "",                                           # MSH-4
            context.get("model", ""),                     # MSH-5
            context.get("serial", ""),                    # MSH-6
            self._now(),                                  # MSH-7
            "",                                           # MSH-8
            EVENT_ACK_R24,                                # MSH-9
            control_id,                                   # MSH-10
            context.get("processing_id") or PROC_PRODUCTION,  # MSH-11
            HL7_VERSION,                                  # MSH-12
        ])
        msa = FIELD_SEPARATOR.join(["MSA", ACK_AA, control_id])

        logger.info(f"ACK^R24 dibangun untuk control_id={control_id or '-'}")
        return self._wrap_mllp([msh, msa])
