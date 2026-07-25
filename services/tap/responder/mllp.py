"""
services/tap/responder/mllp.py — Responder handshake HL7 di atas MLLP.

Envelope <VT>pesan<FS><CR> agnostik terhadap isi. Satu-satunya parsing yang
dibutuhkan: MSA-2 wajib memantulkan MSH-10 — pecah MSH per '|', ambil field
ke-10. Itu saja; tidak ada pengetahuan tentang OBX, PID, atau alatnya.

Pesan tanpa MSH tidak di-ACK: ACK palsu membuat alat mengira datanya sudah
tersimpan, padahal MidLab tidak bisa mengidentifikasinya.
"""

from datetime import datetime

from protocols.hl7.constants import MLLP_START_BYTE, MLLP_TRAILER
from services.tap.responder.base import BaseResponder


class MllpResponder(BaseResponder):
    """Balas tiap pesan MLLP dengan ACK yang memantulkan MSH-10."""

    NAME = "HL7"

    def __init__(self, timestamp: str | None = None):
        # timestamp: override untuk unit test agar hasilnya deterministik.
        self._buf = bytearray()
        self._pesan: list[bytes] = []
        self._timestamp = timestamp

    def feed(self, data: bytes) -> list[bytes]:
        balasan: list[bytes] = []
        self._buf.extend(data)

        while True:
            akhir = self._buf.find(MLLP_TRAILER)
            if akhir == -1:
                break
            pesan = bytes(self._buf[: akhir + len(MLLP_TRAILER)])
            del self._buf[: akhir + len(MLLP_TRAILER)]

            self._pesan.append(pesan)
            ack = self._bangun_ack(pesan)
            if ack:
                balasan.append(ack)

        return balasan

    def _bangun_ack(self, pesan: bytes) -> bytes | None:
        teks = pesan.strip(MLLP_START_BYTE).rstrip(MLLP_TRAILER).decode(
            "utf-8", errors="replace"
        )
        msh = next((s for s in teks.split("\r") if s.startswith("MSH")), None)
        if msh is None:
            return None

        f = msh.split("|")
        control_id = f[9] if len(f) > 9 else ""
        stempel = self._timestamp or datetime.now().strftime("%Y%m%d%H%M%S")

        badan = (
            f"MSH|^~\\&|MidLab|TAP|||{stempel}||ACK|{control_id}|P|2.3.1\r"
            f"MSA|AA|{control_id}\r"
        )
        return MLLP_START_BYTE + badan.encode("utf-8") + MLLP_TRAILER

    def messages(self) -> list[bytes]:
        return list(self._pesan)
