"""Test deteksi protokol + hint baud rate."""

import pytest

from services.tap.detect import (
    detect_protocol, should_hint_baud, is_query, build_responder,
    BAUD_HINT_THRESHOLD,
)
from services.tap.responder.astm import AstmResponder
from services.tap.responder.mllp import MllpResponder
from services.tap.responder.raw import RawResponder


class TestDetectProtocol:
    def test_enq_pertama_berarti_astm(self):
        # Heuristik dari PANDUAN-ALAT-BARU.md bab 2.
        assert detect_protocol(b"\x05") == "ASTM"

    def test_vt_pertama_berarti_hl7(self):
        assert detect_protocol(b"\x0bMSH|^~\\&|X") == "HL7"

    def test_string_msh_berarti_hl7(self):
        # Tanpa wrapper MLLP — bab 1.3 dokumen AR580 hanya menyebut "Block is
        # HL7 message", jadi pesan telanjang mungkin.
        assert detect_protocol(b"MSH|^~\\&|Genrui|KT-6610") == "HL7"

    def test_data_tak_dikenal_none(self):
        assert detect_protocol(b"\xff\xfe\x00\x01") is None

    def test_kosong_none(self):
        assert detect_protocol(b"") is None

    def test_astm_menang_atas_msh_di_belakang(self):
        # ENQ di byte pertama lebih kuat daripada string MSH di tengah data.
        assert detect_protocol(b"\x05MSH|") == "ASTM"


class TestHintBaud:
    def test_hint_saat_banyak_byte_tanpa_pesan(self):
        # Salah setel baud menghasilkan byte sampah yang mirip masalah protokol.
        assert should_hint_baud("HL7", bytes_seen=300, messages_found=0) is True

    def test_tidak_hint_bila_ada_pesan(self):
        assert should_hint_baud("HL7", bytes_seen=300, messages_found=1) is False

    def test_tidak_hint_bila_byte_masih_sedikit(self):
        assert should_hint_baud("HL7", bytes_seen=10, messages_found=0) is False

    def test_tidak_hint_untuk_raw(self):
        # RAW memang tidak punya konsep pesan — nol pesan itu normal.
        assert should_hint_baud("RAW", bytes_seen=9999, messages_found=0) is False

    def test_ambang_tepat_di_batas(self):
        assert should_hint_baud("ASTM", BAUD_HINT_THRESHOLD - 1, 0) is False
        assert should_hint_baud("ASTM", BAUD_HINT_THRESHOLD, 0) is True


class TestIsQuery:
    def test_hl7_qbp_adalah_query(self):
        m = b"\x0bMSH|^~\\&|A|B|||1||QBP^Q22|5|P|2.5\rQPD|Q22|1|34567\r\x1c\x0d"
        assert is_query(m, "HL7") is True

    def test_hl7_qry_adalah_query(self):
        m = b"\x0bMSH|^~\\&|Mindray|BS-200E|||1||QRY^Q02|5|P|2.3.1\r\x1c\x0d"
        assert is_query(m, "HL7") is True

    def test_hl7_oru_bukan_query(self):
        m = b"\x0bMSH|^~\\&|Genrui|KT-6610|||1||ORU^R01|1275|P|2.3.1\r\x1c\x0d"
        assert is_query(m, "HL7") is False

    def test_astm_record_q_adalah_query(self):
        m = b"\x021H|\\^&\x03AB\r\n\x022Q|1|^12345||ALL||||||||O\x03CD\r\n"
        assert is_query(m, "ASTM") is True

    def test_astm_tanpa_record_q_bukan_query(self):
        m = b"\x021H|\\^&\x03AB\r\n\x022R|1|^^^Na|140|mmol/L\x03CD\r\n"
        assert is_query(m, "ASTM") is False

    def test_raw_tidak_pernah_query(self):
        # RAW tidak punya konsep record/pesan, jadi tidak bisa disimpulkan.
        assert is_query(b"apa pun", "RAW") is False

    def test_pesan_kosong(self):
        assert is_query(b"", "HL7") is False


class TestBuildResponder:
    @pytest.mark.parametrize("basis,kelas", [
        ("ASTM", AstmResponder),
        ("HL7", MllpResponder),
        ("RAW", RawResponder),
    ])
    def test_membangun_responder_sesuai_basis(self, basis, kelas):
        assert isinstance(build_responder(basis), kelas)

    def test_basis_tak_dikenal_ditolak(self):
        with pytest.raises(ValueError, match="tidak dikenali"):
            build_responder("SOAP")
