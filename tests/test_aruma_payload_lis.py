"""
tests/test_aruma_payload_lis.py — bentuk results[] AR580 untuk LIS

Latar: LisBridgeService menolak semua hasil Aruma dengan HTTP 422
"The results.29.value field is required", sementara Mindray lolos.

Dua sebab, keduanya hanya muncul pada pesan alat SUNGGUHAN (42 OBX), bukan
pada contoh di manual (34 OBX) yang jadi jangkar test_aruma_ar580.py:

1. Tujuh identifier OBX `IS` tidak terdaftar di METADATA_IDENTIFIERS sehingga
   masuk results[] sebagai seolah hasil lab.
2. Entri dengan value kosong tetap dikirim.

Pesan di bawah disalin dari /var/log/midlab/tcp_2.comm.log.
"""

import pytest

from protocols.aruma_ar580.parser import parse_oru


INSTRUMENT = {"id": 2, "name": "Aruma AR580"}

# Pesan verbatim alat (dipangkas: OBX 1-3 mewakili blok NM, OBX 29-42 utuh
# karena di situlah masalahnya). Struktur field dipertahankan persis.
ORU_LAPANGAN = (
    b"\x0b"
    b"MSH|^~\\&|||||20260221111755||ORU^R01|201|P|2.3.1|||||CHA|UTF-8|||\r"
    b"PID|1||32|||||O|||||||||||||||||||||||\r"
    b"PV1|1|||||||||||||||||||||\r"
    b"OBR|1|||||20260217163251|20260217163751|||||||20260217160251"
    b"||||Admin|1|||||HM|||||||||||||3||32|\r"
    b"OBX|1|NM|^WBC^||10.48|10^9/L|4.00-10.00|H||||F|||||||||\r"
    b"OBX|2|NM|^HGB^||11.9|g/dL|11.0-16.0|||||F|||||||||\r"
    b"OBX|3|NM|^PLT^||288|10^9/L|100-300|||||F|||||||||\r"
    b"OBX|29|IS|^Take Mode^||A|||||||F|||||||||\r"
    b"OBX|30|IS|^Blood Mode^||WH|||||||F|||||||||\r"
    b"OBX|31|IS|^Test Mode^||CBC+DIFF|||||||F|||||||||\r"
    b"OBX|32|IS|^Low Mode^|||||||||F|||||||||\r"
    b"OBX|33|IS|^Ref Group^||General|||||||F|||||||||\r"
    b"OBX|34|IS|^Age^|||||||||F|||||||||\r"
    b"OBX|35|IS|^Remarks^|||||||||F|||||||||\r"
    b"OBX|36|IS|^Blood Type^|||||||||F|||||||||\r"
    b"OBX|37|IS|^ESR^|||||||||F|||||||||\r"
    b"OBX|38|IS|^Recheck flag^||Y|||||||F|||||||||\r"
    b"OBX|39|IS|^WBC Alarm^|||||||||F|||||||||\r"
    b"OBX|40|IS|^RBC Alarm^||Microcytosis|||||||F|||||||||\r"
    b"OBX|41|IS|^PLT Alarm^|||||||||F|||||||||\r"
    b"OBX|42|IS|^Print_BMP^||C|||||||F|||||||||\r"
    b"\x1c\r"
)


@pytest.fixture
def hasil():
    return parse_oru(ORU_LAPANGAN, INSTRUMENT)


# ============================================================
# Kontrak payload LIS
# ============================================================

def test_tidak_ada_entri_tanpa_value(hasil):
    # Ini yang memicu HTTP 422 dari EazyApp.
    kosong = [r["test_code"] for r in hasil["results"] if not r["value"]]
    assert kosong == [], f"entri tanpa value akan ditolak LIS: {kosong}"


def test_esr_kosong_tidak_dikirim(hasil):
    # ESR pengukuran sungguhan, tapi kosong bila tidak diperiksa.
    assert "ESR" not in [r["test_code"] for r in hasil["results"]]


@pytest.mark.parametrize("identifier", [
    "Take Mode", "Low Mode", "Recheck flag",
    "WBC Alarm", "RBC Alarm", "PLT Alarm", "Print_BMP",
])
def test_field_non_pengukuran_tidak_di_results(hasil, identifier):
    # Field operasional/alarm bukan hasil lab; LIS tidak boleh menerimanya
    # sebagai baris pemeriksaan.
    assert identifier not in [r["test_code"] for r in hasil["results"]]


def test_results_hanya_berisi_pengukuran(hasil):
    assert [r["test_code"] for r in hasil["results"]] == ["WBC", "HGB", "PLT"]


# ============================================================
# Informasi tidak boleh hilang, hanya berpindah
# ============================================================

def test_alarm_bernilai_tetap_tersimpan_di_comments(hasil):
    # "Microcytosis" punya arti klinis — dipindah, bukan dibuang.
    gabungan = " | ".join(hasil["comments"])
    assert "Microcytosis" in gabungan


@pytest.mark.parametrize("potongan", [
    "Take Mode: A",
    "Recheck flag: Y",
    "Print_BMP: C",
])
def test_metadata_bernilai_masuk_comments(hasil, potongan):
    assert potongan in " | ".join(hasil["comments"])


def test_metadata_kosong_tidak_mengotori_comments(hasil):
    # _terapkan_metadata() melewati value kosong — Low Mode/Age/PLT Alarm
    # tidak boleh muncul sebagai comment hampa.
    gabungan = " | ".join(hasil["comments"])
    for identifier in ["Low Mode", "Age", "PLT Alarm", "WBC Alarm"]:
        assert f"{identifier}:" not in gabungan


def test_metadata_berfield_tetap_ke_field_semestinya(hasil):
    # Blood Mode dan Test Mode punya rumah sendiri di ResultObject.
    assert hasil["specimen"]["sample_type"] == "WH"
    assert hasil["order"]["panel"] == "CBC+DIFF"


def test_tanpa_parse_error(hasil):
    assert hasil["parse_errors"] == []
