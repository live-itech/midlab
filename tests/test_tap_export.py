"""Test export — .bin dan Python bytes literal."""

from services.tap.export import to_python_bytes, rx_bytes, messages_from_events


ORU = (
    b"\x0b"
    b"MSH|^~\\&|Genrui|KT-6610|||20170712140022||ORU^R01|1275|P|2.3.1\r"
    b"OBX|1|NM|^WBC^||0.01|10^9/L|4.00-10.00|L|||F\r"
    b"\x1c\x0d"
)


class TestToPythonBytes:
    def test_hasil_eval_identik_dengan_asli(self):
        # Ini SATU-SATUNYA jaminan yang penting: literal yang di-export, saat
        # ditempel ke test, harus menghasilkan byte yang persis sama.
        assert eval(to_python_bytes(ORU).strip()) == ORU

    def test_eval_identik_untuk_byte_biner_penuh(self):
        semua = bytes(range(256))
        assert eval(to_python_bytes(semua).strip()) == semua

    def test_dipecah_per_segment_cr(self):
        hasil = to_python_bytes(ORU)
        assert hasil.count("\n") >= 2  # MSH, OBX, trailer di baris terpisah

    def test_byte_kontrol_jadi_escape_hex(self):
        assert "\\x0b" in to_python_bytes(ORU)

    def test_backslash_di_escape(self):
        # MSH|^~\& — backslash HL7 harus jadi \\ di literal Python.
        assert eval(to_python_bytes(b"MSH|^~\\&|X\r").strip()) == b"MSH|^~\\&|X\r"

    def test_petik_ganda_di_escape(self):
        assert eval(to_python_bytes(b'ada "petik" di sini').strip()) == b'ada "petik" di sini'

    def test_data_kosong(self):
        assert to_python_bytes(b"") == ""

    def test_indent_dipakai(self):
        assert to_python_bytes(b"abc\r", indent="  ").startswith("  b\"")


class TestRxBytes:
    def test_hanya_mengambil_rx(self):
        ev = [
            {"dir": "rx", "hex": "0b4d5348"},
            {"dir": "tx", "hex": "06"},
            {"dir": "rx", "hex": "1c0d"},
        ]
        assert rx_bytes(ev) == b"\x0bMSH\x1c\x0d"

    def test_meta_diabaikan(self):
        ev = [
            {"dir": "rx", "hex": "05"},
            {"dir": "meta", "event": "message_complete", "index": 0},
        ]
        assert rx_bytes(ev) == b"\x05"

    def test_kosong(self):
        assert rx_bytes([]) == b""


class TestMessagesFromEvents:
    def test_memecah_rx_pada_penanda_pesan(self):
        ev = [
            {"dir": "rx", "hex": "0b41"},                          # \x0bA
            {"dir": "meta", "event": "message_complete", "index": 0},
            {"dir": "rx", "hex": "0b42"},                          # \x0bB
            {"dir": "meta", "event": "message_complete", "index": 1},
        ]
        assert messages_from_events(ev) == [b"\x0bA", b"\x0bB"]

    def test_tanpa_penanda_list_kosong(self):
        # Basis RAW tidak punya batas pesan — export per-pesan tidak tersedia.
        assert messages_from_events([{"dir": "rx", "hex": "0b41"}]) == []

    def test_rx_setelah_penanda_terakhir_diabaikan(self):
        ev = [
            {"dir": "rx", "hex": "0b41"},
            {"dir": "meta", "event": "message_complete", "index": 0},
            {"dir": "rx", "hex": "0b42"},   # pesan belum selesai
        ]
        assert messages_from_events(ev) == [b"\x0bA"]
