"""
services/tap/session.py — Merangkai transport ↔ responder ↔ recorder.

URUTAN OPERASINYA MENGIKAT: rekam dulu, baru balas.

Bila MidLab meng-ACK, alat menganggap datanya sudah tersimpan dan menandai
sampel itu terkirim — ia tidak akan mengirimnya lagi. Kalau proses mati setelah
ACK terkirim tapi sebelum byte-nya tertulis, hasil pasien itu lenyap tanpa jejak.
Jangan membalik urutan _rekam() dan transport.write().
"""

import asyncio
from typing import Callable

from lib.utils import get_logger
from services.tap.detect import (
    build_responder, detect_protocol, is_query, should_hint_baud,
)
from services.tap.recorder import TapRecorder
from services.tap.transport.base import BaseTransport


logger = get_logger("tap_session")


class TapSession:
    """Satu sesi tapping: baca byte, rekam, balas handshake, hitung."""

    def __init__(
        self,
        transport: BaseTransport,
        basis: str,
        recorder: TapRecorder,
        mode: str = "uni",
        on_event: Callable[[dict], None] | None = None,
    ):
        self._transport = transport
        self._basis = basis
        self._recorder = recorder
        self._mode = mode
        self._on_event = on_event

        # Basis AUTO tidak pernah membalas: ia hanya menebak dan melapor.
        # Operator yang memutuskan, responder tidak berganti sendiri.
        self._responder = build_responder("RAW" if basis == "AUTO" else basis)

        self._berhenti = asyncio.Event()
        self.bytes_rx = 0
        self.bytes_tx = 0
        self.message_count = 0
        self.query_count = 0
        self.detected: str | None = None
        self.baud_hint = False

    async def run(self) -> None:
        """Loop sampai transport putus (TCP) atau stop() dipanggil."""
        try:
            while not self._berhenti.is_set():
                data = await self._transport.read()
                if not data:
                    # Arti b"" bergantung transport: TCP = putus (keluar);
                    # serial = alat sedang diam (lanjut, port masih terbuka).
                    if self._transport.is_stream:
                        break
                    continue
                await self._proses(data)
        finally:
            await self._transport.close()

    async def _proses(self, data: bytes) -> None:
        # 1. REKAM DULU — sebelum apa pun dikirim balik.
        self._rekam("rx", data)
        self.bytes_rx += len(data)

        if self._basis == "AUTO" and self.detected is None:
            self.detected = detect_protocol(data)
            if self.detected:
                logger.info(f"[TAP] terdeteksi kemungkinan {self.detected}")

        # 2. Baru hitung balasannya.
        balasan = self._responder.feed(data)

        # 3. Kirim, sambil merekam tiap balasan.
        for b in balasan:
            self._rekam("tx", b, note="auto")
            self.bytes_tx += len(b)
            await self._transport.write(b)

        # 4. Tandai pesan lengkap yang baru muncul.
        pesan = self._responder.messages()
        while self.message_count < len(pesan):
            idx = self.message_count
            self._recorder.mark_message(idx)

            # Mode bidi: tandai query, tapi JANGAN dijawab. Menjawab dengan order
            # sungguhan butuh driver yang justru sedang dibuat; mengarang jawaban
            # bisa membuat alat mencatat error dan mengotori capture.
            if self._mode == "bidi" and is_query(pesan[idx], self._basis):
                self._recorder.mark_query(idx)
                self.query_count += 1
                logger.info(f"[TAP] query terdeteksi di pesan #{idx} — tidak dijawab")

            self.message_count += 1

        self.baud_hint = should_hint_baud(
            self._basis, self.bytes_rx, self.message_count
        )

    async def send_manual(self, data: bytes) -> None:
        """Kirim byte yang diketik operator (dipakai basis RAW)."""
        self._rekam("tx", data, note="manual")
        self.bytes_tx += len(data)
        await self._transport.write(data)

    def _rekam(self, arah: str, data: bytes, note: str = "") -> None:
        self._recorder.write_event(arah, data, note=note)
        if self._on_event is not None:
            row = {"dir": arah, "hex": data.hex()}
            if note:
                row["note"] = note
            self._on_event(row)

    def stop(self) -> None:
        """Minta loop berhenti; run() akan menutup transport."""
        self._berhenti.set()
