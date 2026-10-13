"""
tests/test_qc_aruma_bc5150.py — pembedaan hasil QC vs pasien pada AR580 & BC-5150

Latar: BS-200E sudah menandai QC (`sample_type="qc"`, `status="qc"`), tetapi
AR580 dan BC-5150 tidak — hasil QC tersimpan dan terkirim ke LIS seolah hasil
pasien. AR580 bahkan memakai no. file QC ("1", "2") di PID-3 sehingga hasil
kontrol bisa bentrok dengan sampel pasien bernomor sama.

Selain itu ACK AR580 selalu berlabel MSH-11 "P"; di tcp_2.comm.log ke-13 pesan
QC ("Q") semuanya dikirim ulang ±10 detik kemudian, pesan pasien tidak.
"""

import pytest

from protocols.aruma_ar580.builder import build_ack
from protocols.aruma_ar580.parser import parse_oru
from protocols.mindray_bc5150.module import MindrayBC5150Module


# ============================================================
# ARUMA AR580
# ============================================================

ARUMA = {"id": 2, "name": "Aruma AR580"}

# Verbatim tcp_2.comm.log 2026-08-03 00:28:34 (control ID 62). Dipangkas:
# OBX NM 5-28 dan bitmap ED dibuang, struktur field utuh.
ARUMA_QC = (
    b"\x0b"
    b"MSH|^~\\&|||||20260306002502||ORU^R01|62|Q|2.3.1|||||CHA|UTF-8|||\r"
    b"PID|1||2|DH2606N|||05-08-2026||||||||||||||||||||||||\r"
    b"PV1|1|0|||||||||||||||||||||||||||||\r"
    b"OBR|1||||||20260306002502|||||||||||||||||HM|||||||||||||||2|\r"
    b"OBX|1|NM|^MCV^||92.301|fL|-|||||F|||||||||\r"
    b"OBX|4|NM|^Neu#^||4.92|10^9/L|4.83-1.05|||||F|||||||||\r"
    b"OBX|25|NM|^Lym%^||28.5|%|0.3-0.1|H||||F|||||||||\r"
    b"OBX|29|IS|^Blood Mode^||WH|||||||F|||||||||\r"
    b"OBX|30|IS|^QC Level^||M|||||||F|||||||||\r"
    b"OBX|31|IS|^QC Type^|||||||||F|||||||||\r"
    b"\x1c\r"
)

# Pesan pasien yang sama persis kecuali MSH-11 = P — pembanding.
ARUMA_PASIEN = ARUMA_QC.replace(b"|62|Q|", b"|62|P|")


@pytest.fixture
def aruma_qc():
    return parse_oru(ARUMA_QC, ARUMA)


def test_aruma_qc_ditandai_sample_type_qc(aruma_qc):
    assert aruma_qc["specimen"]["sample_type"] == "qc"
    assert aruma_qc["parse_errors"] == []


def test_aruma_qc_status_semua_hasil_qc(aruma_qc):
    assert aruma_qc["results"]
    assert {r["status"] for r in aruma_qc["results"]} == {"qc"}


def test_aruma_qc_no_file_tidak_jadi_sample_id(aruma_qc):
    # PID-3 = no. file QC — tidak boleh dikira sampel pasien "2" di LIS.
    assert aruma_qc["specimen"]["sample_id"] == ""
    assert aruma_qc["patient"]["patient_id"] == ""
    # PID-7 = expired kontrol, bukan tanggal lahir.
    assert aruma_qc["patient"]["dob"] == ""


def test_aruma_qc_identitas_kontrol_di_comments(aruma_qc):
    comments = aruma_qc["comments"]
    assert "qc_file: 2" in comments
    assert "lot_number: DH2606N" in comments
    assert "expiration_date: 05-08-2026" in comments
    assert "QC Level: M" in comments


def test_aruma_qc_level_dan_type_bukan_hasil(aruma_qc):
    codes = {r["test_code"] for r in aruma_qc["results"]}
    assert codes == {"MCV", "Neu#", "Lym%"}


def test_aruma_qc_target_ditulis_ulang(aruma_qc):
    rng = {r["test_code"]: r["reference_range"] for r in aruma_qc["results"]}
    assert rng["Neu#"] == "4.83±1.05"
    assert rng["Lym%"] == "0.3±0.1"
    # Tanpa target ("-") dibiarkan apa adanya.
    assert rng["MCV"] == "-"


def test_aruma_pasien_tidak_terpengaruh():
    parsed = parse_oru(ARUMA_PASIEN, ARUMA)
    assert parsed["specimen"]["sample_type"] == "WH"
    assert parsed["specimen"]["sample_id"] == "2"
    assert all(r["status"] != "qc" for r in parsed["results"])
    neu = next(r for r in parsed["results"] if r["test_code"] == "Neu#")
    assert neu["reference_range"] == "4.83-1.05"


def test_aruma_ack_qc_memantulkan_msh11_q():
    ack = build_ack(ARUMA_QC, ARUMA, timestamp="20260803002835")
    assert b"||ACK^R01|62|Q|2.3.1|" in ack
    assert b"MSA|AA|62|" in ack


def test_aruma_ack_pasien_tetap_p():
    ack = build_ack(ARUMA_PASIEN, ARUMA, timestamp="20260803002835")
    assert b"||ACK^R01|62|P|2.3.1|" in ack


# ============================================================
# MINDRAY BC-5150
# ============================================================

BC5150 = {"id": 3, "name": "BC-5150", "protocol": "HL7_MINDRAY_BC5150"}

# Struktur dari ORU di tcp_3.comm.log; penanda QC menurut dok §4.3.1 (MSH-11 Q)
# dan §5.7 Tabel 4-10 (OBR-4 00003^LJ QCR^99MRC). Belum ada QC sungguhan di log.
BC5150_PASIEN = (
    b"\x0b"
    b"MSH|^~\\&|||||20261004044142||ORU^R01|83|P|2.3.1||||||UNICODE\r"
    b"PID|1||^^^^MR\r"
    b"PV1|1\r"
    b"OBR|1||HANIF|00001^Automated Count^99MRC|||20261004044041"
    b"|||||||||||||||||HM||||||||Administrator\r"
    b"OBX|1|IS|08001^Take Mode^99MRC||O||||||F\r"
    b"OBX|5|NM|6690-2^WBC^LN||6.87|10*9/L|4.00-10.00|N|||F\r"
    b"OBX|6|NM|704-7^BAS#^LN||0.02|10*9/L|0.00-0.10|N|||F\r"
    b"\x1c\x0d"
)

BC5150_QC_LENGKAP = (
    BC5150_PASIEN
    .replace(b"|83|P|", b"|83|Q|")
    .replace(b"|HANIF|00001^Automated Count^", b"||00003^LJ QCR^")
)


@pytest.fixture
def bc5150():
    return MindrayBC5150Module()


def test_bc5150_qc_ditandai(bc5150):
    parsed = bc5150.parse(BC5150_QC_LENGKAP, BC5150)
    assert parsed["specimen"]["sample_type"] == "qc"
    assert {r["status"] for r in parsed["results"]} == {"qc"}
    # QC tanpa OBR-3 bukan error.
    assert parsed["parse_errors"] == []


def test_bc5150_qc_cukup_dari_msh11(bc5150):
    raw = BC5150_PASIEN.replace(b"|83|P|", b"|83|Q|")
    assert bc5150.parse(raw, BC5150)["specimen"]["sample_type"] == "qc"


def test_bc5150_qc_cukup_dari_obr4(bc5150):
    raw = BC5150_PASIEN.replace(b"00001^Automated Count^", b"00003^LJ QCR^")
    assert bc5150.parse(raw, BC5150)["specimen"]["sample_type"] == "qc"


def test_bc5150_pasien_tidak_terpengaruh(bc5150):
    parsed = bc5150.parse(BC5150_PASIEN, BC5150)
    assert parsed["specimen"]["sample_type"] == "whole blood"
    assert parsed["specimen"]["sample_id"] == "HANIF"
    assert {r["status"] for r in parsed["results"]} == {"F"}


def test_bc5150_ack_qc_memantulkan_msh11_q(bc5150):
    ack = bc5150.build_ack_response(BC5150_QC_LENGKAP, BC5150)
    assert b"|ACK^R01|83|Q|" in ack
