"""
services/tcp_socket/receiver.py — ResultReceiver

Komponen TCPSocketService yang menerima data hasil pemeriksaan dari alat lab.
Menangani buffer management, deteksi batas message (ASTM frame / HL7 MLLP),
parsing via protocol module, dan penyimpanan ke tbl_result.

Alur: raw bytes → buffer → detect boundary → parse → save DB
"""

import asyncio
from datetime import datetime, timezone

from lib.db import save_result
from lib.utils import get_logger
from lib.comm_logger import CommLogger


# Konstanta boundary detection
ASTM_ENQ = 0x05
ASTM_EOT = 0x04
ASTM_STX = 0x02
ASTM_ETX = 0x03
ASTM_ETB = 0x17
ASTM_ACK = 0x06

MLLP_START = 0x0B
MLLP_END = 0x1C


class ResultReceiver:
    """
    Menerima dan memproses data hasil pemeriksaan dari alat lab.

    Menangani:
    - Buffer management untuk partial data dari TCP socket
    - Deteksi batas message berdasarkan protocol (ASTM / HL7)
    - Parsing data via protocol module
    - Penyimpanan hasil ke tbl_result dengan status=pending

    Digunakan di semua mode operasi (unidirectional maupun bidirectional).
    """

    def __init__(self, instrument_config, protocol_module, socket_lock=None):
        """
        Args:
            instrument_config: InstrumentConfig dari config.py
            protocol_module: Instance protocol module (ASTMModule/HL7Module)
            socket_lock: asyncio.Lock bersama (untuk bidirectional mode)
        """
        self._config = instrument_config
        self._protocol = protocol_module
        self._lock = socket_lock or asyncio.Lock()
        self._logger = get_logger("tcp_socket", instrument_config.id)
        self._inst_name = instrument_config.name
        self._comm = CommLogger.for_instrument(instrument_config.id)

        # Buffer management
        self._buffer = bytearray()
        self._astm_frames = []       # Kumpulan frame ASTM dalam satu sesi
        self._in_astm_session = False # Sedang dalam sesi ENQ..EOT

        # Statistik
        self._total_bytes = 0
        self._total_results = 0

    # ============================================================
    # Public API
    # ============================================================

    async def handle_data(self, data: bytes, writer: asyncio.StreamWriter) -> bool:
        """
        Proses data yang diterima dari socket.

        Mendeteksi batas message, parsing, dan simpan ke DB.
        Return True jika data berisi query (harus di-handle oleh QueryHandler).

        Args:
            data: Raw bytes dari socket.recv()
            writer: StreamWriter untuk mengirim ACK/response

        Returns:
            True jika data mengandung query trigger (delegate ke QueryHandler)
        """
        self._total_bytes += len(data)
        self._buffer.extend(data)

        protocol = self._config.protocol.upper()

        if protocol in ("ASTM", "COBAS_C111"):
            return await self._handle_astm_data(writer)
        elif protocol == "HL7":
            return await self._handle_hl7_data(writer)
        else:
            # Protocol lain: coba parse langsung
            return await self._handle_generic_data(writer)

    def reset_buffer(self):
        """Reset buffer dan state (dipanggil saat reconnect)."""
        self._buffer.clear()
        self._astm_frames.clear()
        self._in_astm_session = False

    # ============================================================
    # ASTM Data Handling
    # ============================================================

    async def _handle_astm_data(self, writer: asyncio.StreamWriter) -> bool:
        """
        Handle data ASTM.

        Flow ASTM:
        1. Alat kirim ENQ → MidLab balas ACK → sesi dimulai
        2. Alat kirim frame-frame (STX...ETX/ETB + checksum + CRLF)
        3. MidLab balas ACK per frame
        4. Alat kirim EOT → sesi selesai → parse semua frame

        Returns True jika data berisi Q record (query trigger).
        """
        while self._buffer:
            byte = self._buffer[0]

            # --- ENQ: awal sesi dari alat ---
            if byte == ASTM_ENQ:
                self._buffer.pop(0)
                self._in_astm_session = True
                self._astm_frames.clear()
                self._logger.info(f"[{self._inst_name}] ENQ diterima, sesi dimulai")
                # Kirim ACK
                async with self._lock:
                    self._comm.tx(bytes([ASTM_ACK]))
                    writer.write(bytes([ASTM_ACK]))
                    await writer.drain()
                continue

            # --- EOT: akhir sesi ---
            if byte == ASTM_EOT:
                self._buffer.pop(0)
                self._logger.info(
                    f"[{self._inst_name}] EOT diterima, sesi selesai "
                    f"({len(self._astm_frames)} frame)"
                )
                self._in_astm_session = False

                if self._astm_frames:
                    # Cek apakah ada Q record (query) dalam frames
                    raw_combined = b"".join(self._astm_frames)
                    if self._protocol.is_enq(raw_combined):
                        self._logger.info(
                            f"[{self._inst_name}] Q record terdeteksi dalam sesi"
                        )
                        # Kembalikan data ke buffer agar QueryHandler bisa proses
                        # sebenarnya is_enq sudah handle Q record parsing
                        return True

                    await self._parse_and_save(raw_combined)
                    self._astm_frames.clear()
                continue

            # --- Frame data (STX...ETX/ETB) ---
            if byte == ASTM_STX and self._in_astm_session:
                # Cari akhir frame: ETX atau ETB + checksum + CR + LF
                frame = self._extract_astm_frame()
                if frame is None:
                    break  # Belum lengkap, tunggu data berikutnya

                self._astm_frames.append(frame)
                self._logger.info(
                    f"[{self._inst_name}] Frame #{len(self._astm_frames)} "
                    f"diterima ({len(frame)} bytes)"
                )
                # Kirim ACK per frame
                async with self._lock:
                    self._comm.tx(bytes([ASTM_ACK]))
                    writer.write(bytes([ASTM_ACK]))
                    await writer.drain()
                continue

            # --- Data tanpa framing (alat lama) ---
            if not self._in_astm_session:
                # Coba deteksi line-based records (H|, P|, R|, dll)
                msg = self._extract_line_records()
                if msg:
                    await self._parse_and_save(msg)
                    continue
                break  # Tunggu data lebih banyak

            # Byte tidak dikenali dalam sesi — skip
            self._buffer.pop(0)

        return False

    def _extract_astm_frame(self) -> bytes | None:
        """
        Ekstrak satu frame ASTM dari buffer.

        Format: STX <frame_num> <data> (ETX|ETB) <checksum 2 bytes> CR LF

        Returns:
            bytes frame lengkap, atau None jika belum lengkap
        """
        # Cari ETX atau ETB setelah STX
        etx_pos = None
        for i in range(1, len(self._buffer)):
            if self._buffer[i] in (ASTM_ETX, ASTM_ETB):
                etx_pos = i
                break

        if etx_pos is None:
            return None

        # Frame: STX...ETX/ETB + 2 byte checksum + CR + LF = etx_pos + 5
        frame_end = etx_pos + 5  # +1(ETX) +2(checksum) +1(CR) +1(LF)
        if frame_end > len(self._buffer):
            # Coba dengan hanya checksum (tanpa CRLF — beberapa alat)
            frame_end = etx_pos + 3  # +1(ETX) +2(checksum)
            if frame_end > len(self._buffer):
                return None

        frame = bytes(self._buffer[:frame_end])
        del self._buffer[:frame_end]
        return frame

    def _extract_line_records(self) -> bytes | None:
        """
        Ekstrak message dari plain text records (tanpa STX/ETX framing).
        Deteksi boundary: H record di awal, L record di akhir.
        """
        # Cari apakah ada CR/LF terminated complete records
        text = bytes(self._buffer).decode("ascii", errors="replace")

        # Cari L record (terminator) sebagai boundary akhir
        l_pos = text.find("L|")
        if l_pos == -1:
            return None

        # Cari akhir L record (CR atau LF)
        end_pos = l_pos
        for i in range(l_pos, len(text)):
            if text[i] in ("\r", "\n"):
                end_pos = i + 1
                # Jika CRLF, skip keduanya
                if end_pos < len(text) and text[end_pos] in ("\r", "\n"):
                    end_pos += 1
                break
        else:
            end_pos = len(text)

        msg = bytes(self._buffer[:end_pos])
        del self._buffer[:end_pos]
        return msg

    # ============================================================
    # HL7 Data Handling
    # ============================================================

    async def _handle_hl7_data(self, writer: asyncio.StreamWriter) -> bool:
        """
        Handle data HL7 dengan MLLP transport.

        Format MLLP: <VT>(HL7 message)<FS><CR>
        VT = 0x0B, FS = 0x1C, CR = 0x0D

        Returns True jika data berisi query message.
        """
        while True:
            msg = self._extract_mllp_message()
            if msg is None:
                break

            self._logger.info(
                f"[{self._inst_name}] MLLP message diterima ({len(msg)} bytes)"
            )

            # Cek apakah ini query message
            if self._protocol.is_enq(msg):
                self._logger.info(
                    f"[{self._inst_name}] Query message terdeteksi"
                )
                # Simpan message di buffer kembali agar QueryHandler bisa ambil
                # Sebenarnya kita simpan dalam instance variable
                self._last_query_data = msg
                return True

            # Parse dan simpan sebagai result
            await self._parse_and_save(msg)

            # Kirim ACK
            await self._send_hl7_ack(msg, writer)

        return False

    def _extract_mllp_message(self) -> bytes | None:
        """
        Ekstrak satu MLLP message dari buffer.

        Mencari pasangan <VT>...<FS><CR>
        """
        # Cari MLLP_START
        try:
            start = self._buffer.index(MLLP_START)
        except ValueError:
            # Tidak ada VT — clear buffer junk sebelumnya
            self._buffer.clear()
            return None

        # Cari MLLP_END setelah start
        try:
            end = self._buffer.index(MLLP_END, start + 1)
        except ValueError:
            # Belum lengkap
            # Buang data sebelum start jika ada
            if start > 0:
                del self._buffer[:start]
            return None

        # Ambil message lengkap termasuk envelope
        # MLLP: <VT>...<FS><CR> — CR mungkin ada atau tidak
        msg_end = end + 1
        if msg_end < len(self._buffer) and self._buffer[msg_end] == 0x0D:
            msg_end += 1

        msg = bytes(self._buffer[start:msg_end])
        del self._buffer[:msg_end]
        return msg

    async def _send_hl7_ack(self, raw_message: bytes, writer: asyncio.StreamWriter):
        """Kirim ACK message HL7 sebagai response ke message yang diterima."""
        try:
            from protocols.hl7.parser import HL7Parser
            from protocols.hl7.builder import HL7Builder
            from protocols.hl7.constants import ACK_AA

            parser = HL7Parser()
            builder = HL7Builder()

            hl7_bytes = parser.unwrap_mllp(raw_message)
            parsed = parser.parse_message(hl7_bytes)

            # Cari MSH segment
            msh = None
            for seg in parsed.get("segments", []):
                if seg.get("segment_type") == "MSH":
                    msh = seg
                    break

            if msh:
                ack_bytes = builder.build_ack(msh, ACK_AA)
                async with self._lock:
                    self._comm.tx(ack_bytes)
                    writer.write(ack_bytes)
                    await writer.drain()
                self._logger.info(f"[{self._inst_name}] ACK sent")

        except Exception as e:
            self._logger.warning(f"[{self._inst_name}] Gagal kirim HL7 ACK: {e}")

    # ============================================================
    # Generic Data Handling
    # ============================================================

    async def _handle_generic_data(self, writer: asyncio.StreamWriter) -> bool:
        """Handle data untuk protocol selain ASTM/HL7 — parse langsung."""
        data = bytes(self._buffer)
        self._buffer.clear()

        if data:
            await self._parse_and_save(data)

        return False

    # ============================================================
    # Parse & Save
    # ============================================================

    async def _parse_and_save(self, raw_bytes: bytes):
        """
        Parse raw bytes via protocol module dan simpan ke tbl_result.

        Args:
            raw_bytes: Data message lengkap
        """
        instrument_dict = self._config.to_dict()

        try:
            # Parse menggunakan protocol module
            result_dict = self._protocol.parse(raw_bytes, instrument_dict)

            # Simpan ke database
            raw_hex = raw_bytes.hex()
            result_id = await asyncio.get_event_loop().run_in_executor(
                None,
                save_result,
                self._config.id,
                self._config.protocol,
                raw_hex,
                result_dict,
            )

            if result_id:
                self._total_results += 1
                self._logger.info(
                    f"[{self._inst_name}] Result saved: id={result_id}, "
                    f"total={self._total_results}"
                )
            else:
                self._logger.error(
                    f"[{self._inst_name}] Gagal simpan result ke database"
                )

        except Exception as e:
            self._logger.error(
                f"[{self._inst_name}] Error parse/save: {e}"
            )

    # ============================================================
    # Properties
    # ============================================================

    @property
    def last_query_data(self) -> bytes | None:
        """Data query terakhir yang terdeteksi (untuk QueryHandler)."""
        return getattr(self, "_last_query_data", None)

    def clear_last_query(self):
        """Clear query data setelah diproses QueryHandler."""
        self._last_query_data = None

    @property
    def stats(self) -> dict:
        return {
            "total_bytes": self._total_bytes,
            "total_results": self._total_results,
            "buffer_size": len(self._buffer),
        }


# ============================================================
# Unit Test
# ============================================================

if __name__ == "__main__":
    import asyncio

    print("=== Test ResultReceiver ===\n")

    # Mock objects
    class MockConfig:
        id = 1
        name = "TestInstrument"
        protocol = "ASTM"
        ip_address = "127.0.0.1"
        port = 9100
        mode = "unidirectional"
        bidir_mode = None
        broadcast_interval = 30
        connection = "server"
        is_active = True
        def to_dict(self):
            return {"id": self.id, "name": self.name}

    class MockProtocol:
        def parse(self, raw_bytes, instrument):
            return {"instrument_id": 1, "protocol": "ASTM", "results": []}
        def is_enq(self, raw_bytes):
            return b"Q|" in raw_bytes

    # Test 1: buffer management ASTM
    receiver = ResultReceiver(MockConfig(), MockProtocol())
    assert receiver._buffer == bytearray()
    assert receiver._total_bytes == 0
    print("OK: ResultReceiver created")

    # Test 2: extract_astm_frame
    # Simulasi frame: STX + "1H|\\^&\r" + ETX + "XX" + CR + LF
    from protocols.astm.constants import STX, ETX, CR, LF
    frame = bytes([STX]) + b"1H|\\^&\r" + bytes([ETX]) + b"A5" + bytes([CR, LF])
    receiver._buffer = bytearray(frame)
    extracted = receiver._extract_astm_frame()
    assert extracted is not None
    assert len(extracted) > 0
    assert extracted[0] == STX
    print(f"OK: ASTM frame extracted: {len(extracted)} bytes")

    # Test 3: extract_mllp_message
    receiver_hl7 = ResultReceiver(MockConfig(), MockProtocol())
    receiver_hl7._config.protocol = "HL7"
    mllp_msg = b"\x0bMSH|^~\\&|Test\r\x1c\x0d"
    receiver_hl7._buffer = bytearray(mllp_msg)
    extracted = receiver_hl7._extract_mllp_message()
    assert extracted == mllp_msg
    print(f"OK: MLLP message extracted: {len(extracted)} bytes")

    # Test 4: extract_mllp_message partial
    receiver_hl7._buffer = bytearray(b"\x0bMSH|^~\\&|Test\r")
    extracted = receiver_hl7._extract_mllp_message()
    assert extracted is None, "Partial MLLP harus return None"
    print("OK: Partial MLLP returns None")

    # Test 5: line-based records
    receiver2 = ResultReceiver(MockConfig(), MockProtocol())
    line_msg = b"H|\\^&|||Test\r\nP|1||PAT001\r\nR|1|WBC|5.2\r\nL|1|N\r\n"
    receiver2._buffer = bytearray(line_msg)
    extracted = receiver2._extract_line_records()
    assert extracted is not None
    assert b"L|" in extracted
    print(f"OK: Line-based records extracted: {len(extracted)} bytes")

    # Test 6: stats
    assert receiver.stats["total_bytes"] == 0
    assert receiver.stats["total_results"] == 0
    print("OK: Stats benar")

    # Test 7: reset
    receiver._buffer = bytearray(b"junk data")
    receiver._in_astm_session = True
    receiver.reset_buffer()
    assert receiver._buffer == bytearray()
    assert receiver._in_astm_session is False
    print("OK: reset_buffer() benar")

    print("\n=== Semua test ResultReceiver PASSED ===")
