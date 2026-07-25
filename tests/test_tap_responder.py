"""Test responder tapping — semuanya tanpa socket (feed() bebas I/O)."""

import pytest

from services.tap.responder.raw import RawResponder
from services.tap.responder.astm import AstmResponder
from services.tap.responder.mllp import MllpResponder

ENQ, ACK, EOT, STX, ETX = b"\x05", b"\x06", b"\x04", b"\x02", b"\x03"

VT, FS, CR = b"\x0b", b"\x1c", b"\x0d"

ORU = (
    VT
    + b"MSH|^~\\&|Genrui|KT-6610|||20170712140022||ORU^R01|1275|P|2.3.1\r"
    + b"OBX|1|NM|^WBC^||0.01|10^9/L|4.00-10.00|L|||F\r"
    + FS + CR
)


class TestRawResponder:
    def test_tidak_pernah_membalas(self):
        r = RawResponder()
        assert r.feed(b"\x05") == []
        assert r.feed(b"MSH|^~\\&|X|Y\r") == []
        assert r.feed(b"apa pun") == []

    def test_tidak_mendeteksi_pesan(self):
        # RAW tidak punya konsep batas pesan.
        r = RawResponder()
        r.feed(b"\x0bMSH|abc\r\x1c\x0d")
        assert r.messages() == []

    def test_punya_nama(self):
        assert RawResponder().NAME == "RAW"


class TestAstmResponder:
    def test_enq_dibalas_ack(self):
        r = AstmResponder()
        assert r.feed(ENQ) == [ACK]

    def test_frame_dibalas_ack(self):
        r = AstmResponder()
        r.feed(ENQ)
        frame = STX + b"1H|\\^&|||Sysmex" + ETX + b"AB\r\n"
        assert r.feed(frame) == [ACK]

    def test_eot_tidak_dibalas(self):
        # EOT mengakhiri sesi; tidak ada balasan.
        r = AstmResponder()
        r.feed(ENQ)
        assert r.feed(EOT) == []

    def test_sesi_lengkap_jadi_satu_pesan(self):
        r = AstmResponder()
        r.feed(ENQ)
        r.feed(STX + b"1H|\\^&|||Sysmex" + ETX + b"AB\r\n")
        r.feed(STX + b"2L|1|N" + ETX + b"CD\r\n")
        r.feed(EOT)
        pesan = r.messages()
        assert len(pesan) == 1
        assert b"Sysmex" in pesan[0]
        assert b"L|1|N" in pesan[0]

    def test_pesan_hanya_muncul_setelah_eot(self):
        r = AstmResponder()
        r.feed(ENQ)
        r.feed(STX + b"1H|\\^&" + ETX + b"AB\r\n")
        assert r.messages() == []  # sesi belum ditutup

    def test_dua_sesi_jadi_dua_pesan(self):
        r = AstmResponder()
        for tag in (b"A", b"B"):
            r.feed(ENQ)
            r.feed(STX + b"1H|" + tag + ETX + b"XX\r\n")
            r.feed(EOT)
        assert len(r.messages()) == 2

    def test_byte_terpecah_antar_chunk(self):
        # TCP tidak menjamin batas frame — satu frame bisa datang terpotong.
        r = AstmResponder()
        r.feed(ENQ)
        assert r.feed(STX + b"1H|\\^&|||Sys") == []   # belum lengkap
        assert r.feed(b"mex" + ETX + b"AB\r\n") == [ACK]

    def test_dua_frame_dalam_satu_chunk(self):
        r = AstmResponder()
        r.feed(ENQ)
        dua = (STX + b"1H|" + ETX + b"AB\r\n") + (STX + b"2L|" + ETX + b"CD\r\n")
        assert r.feed(dua) == [ACK, ACK]

    def test_enq_tanpa_sesi_sebelumnya_tetap_diack(self):
        r = AstmResponder()
        assert r.feed(ENQ) == [ACK]
        assert r.feed(ENQ) == [ACK]

    def test_nama(self):
        assert AstmResponder().NAME == "ASTM"


class TestMllpResponder:
    def test_ack_memantulkan_control_id(self):
        # Alat kirim ulang dalam 3 detik bila ACK tak diterima (dokumen AR580
        # bab 2.3.1), jadi MSA-2 wajib echo MSH-10 pesan yang diterima.
        r = MllpResponder()
        balasan = r.feed(ORU)
        assert len(balasan) == 1
        assert b"MSA|AA|1275" in balasan[0]

    def test_ack_dibungkus_mllp(self):
        r = MllpResponder()
        ack = r.feed(ORU)[0]
        assert ack.startswith(VT)
        assert ack.endswith(FS + CR)

    def test_ack_bertipe_ack(self):
        r = MllpResponder()
        assert b"ACK" in r.feed(ORU)[0]

    def test_pesan_terkumpul(self):
        r = MllpResponder()
        r.feed(ORU)
        pesan = r.messages()
        assert len(pesan) == 1
        assert pesan[0] == ORU

    def test_byte_terpecah_antar_chunk(self):
        r = MllpResponder()
        assert r.feed(ORU[:20]) == []      # belum ada FS CR
        balasan = r.feed(ORU[20:])
        assert b"MSA|AA|1275" in balasan[0]

    def test_dua_pesan_dalam_satu_chunk(self):
        r = MllpResponder()
        balasan = r.feed(ORU + ORU)
        assert len(balasan) == 2
        assert len(r.messages()) == 2

    def test_pesan_tanpa_msh_tidak_diack(self):
        # Jangan meng-ACK pesan yang tidak bisa diidentifikasi: ACK palsu
        # membuat alat mengira data sudah tersimpan.
        r = MllpResponder()
        assert r.feed(VT + b"bukan hl7 sama sekali" + FS + CR) == []

    def test_pesan_tanpa_msh_tetap_direkam_sebagai_pesan(self):
        r = MllpResponder()
        rusak = VT + b"bukan hl7" + FS + CR
        r.feed(rusak)
        assert r.messages() == [rusak]

    def test_control_id_berbeda_ikut_berubah(self):
        r = MllpResponder()
        lain = ORU.replace(b"|ORU^R01|1275|", b"|ORU^R01|99|")
        assert b"MSA|AA|99" in r.feed(lain)[0]

    def test_pesan_tanpa_wrapper_mllp_diabaikan_sampai_ada_fs(self):
        r = MllpResponder()
        assert r.feed(b"MSH|^~\\&|X|Y\r") == []

    def test_nama(self):
        assert MllpResponder().NAME == "HL7"
