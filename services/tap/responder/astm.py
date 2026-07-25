"""
services/tap/responder/astm.py — Responder handshake ASTM E1381.

Alur: ENQ → ACK, tiap frame → ACK, EOT menutup sesi. Satu sesi (ENQ..EOT) =
satu pesan untuk keperluan export.

Checksum frame TIDAK diverifikasi: tujuan tapping adalah membuat alat mau
mengirim, bukan memvalidasi. Frame rusak tetap di-ACK dan tetap terekam apa
adanya — justru itu yang ingin dilihat operator saat mendiagnosis.
"""

from protocols.astm.constants import ENQ, ACK, EOT, STX, ETX, ETB
from services.tap.responder.base import BaseResponder


ENQ_B = bytes([ENQ])
ACK_B = bytes([ACK])
EOT_B = bytes([EOT])
STX_B = bytes([STX])


class AstmResponder(BaseResponder):
    """Balas ENQ dan tiap frame dengan ACK; kumpulkan sesi jadi pesan."""

    NAME = "ASTM"

    def __init__(self):
        self._buf = bytearray()          # byte yang belum membentuk frame utuh
        self._sesi = bytearray()         # isi frame sesi berjalan
        self._pesan: list[bytes] = []

    def feed(self, data: bytes) -> list[bytes]:
        balasan: list[bytes] = []
        self._buf.extend(data)

        while self._buf:
            b = self._buf[0]

            if b == ENQ:
                self._buf.pop(0)
                self._sesi.clear()
                balasan.append(ACK_B)
                continue

            if b == EOT:
                self._buf.pop(0)
                if self._sesi:
                    self._pesan.append(bytes(self._sesi))
                    self._sesi.clear()
                continue

            if b == STX:
                frame = self._ambil_frame()
                if frame is None:
                    break                # frame belum lengkap, tunggu chunk berikutnya
                self._sesi.extend(frame)
                balasan.append(ACK_B)
                continue

            # Byte di luar framing (mis. sisa CR/LF) — buang, jangan menggantung.
            self._buf.pop(0)

        return balasan

    def _ambil_frame(self) -> bytes | None:
        """
        Ambil satu frame STX..ETX/ETB + checksum + CRLF dari buffer.

        Returns None bila frame belum lengkap (biarkan di buffer).
        """
        for i in range(1, len(self._buf)):
            if self._buf[i] in (ETX, ETB):
                # ETX/ETB + 2 char checksum + CR + LF
                akhir = i + 4
                if len(self._buf) < akhir:
                    return None
                frame = bytes(self._buf[:akhir])
                del self._buf[:akhir]
                return frame
        return None

    def messages(self) -> list[bytes]:
        return list(self._pesan)
