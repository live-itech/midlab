"""
Test driver HL7_MINDRAY_BC5150 + integrasinya ke TCPSocketService.

Semua pesan contoh di bawah disalin **verbatim dari log komunikasi alat**
(BC-5150 unidirectional, 2019-05-04), termasuk balasan driver lama yang
dipakai sebagai baseline byte-level.
"""

import asyncio

import pytest

from protocols.base import load_module, is_mllp_protocol, _PROTOCOL_REGISTRY
from protocols.mindray_bc5150.module import MindrayBC5150Module
from protocols.mindray_bc5150.builder import MindrayBC5150Builder
from protocols.mindray_bc5150.parser import split_abnormal_flag
from services.tcp_socket.receiver import ResultReceiver


PROTOCOL = "HL7_MINDRAY_BC5150"

# Log 06:06:44 — alat menanyakan order untuk sampel "raisya"
ORM = (
    b"\x0b"
    b"MSH|^~\\&|||||20190504060725||ORM^O01|389|P|2.3.1||||||UNICODE\r"
    b"ORC|RF||raisya||IP\r"
    b"\x1c\x0d"
)

# Log 06:06:45 — balasan driver lama (baseline)
ORR_LEGACY = (
    b"\x0b"
    b"MSH|^~\\&|LIS||||20081120175238||ORR^O02|389|P|2.3.1||||||UNICODE\r"
    b"MSA|AR|389\r"
    b"\x1c\x0d"
)

# Log 06:07:45 — hasil CBC+DIFF lengkap (dipangkas agar ringkas, struktur utuh)
ORU = (
    b"\x0b"
    b"MSH|^~\\&|||||20190504060825||ORU^R01|389|P|2.3.1||||||UNICODE\r"
    b"PID|1||^^^^MR\r"
    b"PV1|1\r"
    b"OBR|1||raisya|00001^Automated Count^99MRC|||20190504060725"
    b"|||||||||||||||||HM||||||||Service\r"
    b"OBX|1|IS|08001^Take Mode^99MRC||O||||||F\r"
    b"OBX|2|IS|08002^Blood Mode^99MRC||W||||||F\r"
    b"OBX|3|IS|08003^Test Mode^99MRC||CBC+DIFF||||||F\r"
    b"OBX|4|IS|01002^Ref Group^99MRC||General||||||F\r"
    b"OBX|5|NM|6690-2^WBC^LN||2.78|10*3/uL|4.00-10.00|L~N|||F\r"
    b"OBX|6|NM|704-7^BAS#^LN||0.01|10*3/uL|0.00-0.10|A|||F\r"
    b"OBX|8|NM|751-8^NEU#^LN||1.38|10*3/uL|2.00-7.00|L~A|||F\r"
    b"OBX|13|NM|736-9^LYM%^LN||28.4|%|20.0-40.0|N|||F\r"
    b"OBX|15|NM|5905-5^MON%^LN||21.4|%|3.0-12.0|H~A|||F\r"
    b"OBX|24|NM|777-3^PLT^LN||33|10*3/uL|100-300|L~N|||F\r"
    b"OBX|26|NM|32207-3^PDW^LN||17.0||9.0-17.0|N|||F\r"
    b"OBX|30|IS|12007^Lymphopenia^99MRC||T||||||F\r"
    b"OBX|32|IS|12018^Thrombopenia^99MRC||T||||||F\r"
    b"\x1c\x0d"
)

# Log 06:07:45 — balasan driver lama atas ORU (baseline)
ACK_LEGACY = (
    b"\x0b"
    b"MSH|^~\\&|Factory|Chemistry Analyzer|||20190504060745||ACK^R01|389|P"
    b"|2.3.1||||||UNICODE\r"
    b"MSA|AA|389||||0|\r"
    b"\x1c\x0d"
)

INSTRUMENT = {"id": 7, "name": "BC-5150", "protocol": PROTOCOL}


@pytest.fixture
def mod():
    return MindrayBC5150Module()


# ============================================================
# Registrasi & loader
# ============================================================

def test_protocol_terdaftar_di_registry():
    assert PROTOCOL in _PROTOCOL_REGISTRY


def test_load_module_mengembalikan_driver_bc5150():
    loaded = load_module(PROTOCOL)
    assert loaded.PROTOCOL_NAME == PROTOCOL
    assert loaded.VERSION == "1.0.0"


def test_dikenali_sebagai_protocol_mllp():
    # Menentukan ResultReceiver memakai framing MLLP, bukan ENQ/ACK ASTM
    assert is_mllp_protocol(PROTOCOL) is True


# ============================================================
# parse() — ORU^R01
# ============================================================

def test_parse_oru_mengisi_identitas_sampel(mod):
    out = mod.parse(ORU, INSTRUMENT)
    assert out["specimen"]["sample_id"] == "raisya"
    assert out["specimen"]["collected_at"] == "20190504060725"
    assert out["order"]["panel"] == "Automated Count"
    assert out["instrument_id"] == 7
    assert out["protocol"] == PROTOCOL


def test_parse_oru_mengambil_semua_hasil_numerik(mod):
    out = mod.parse(ORU, INSTRUMENT)
    codes = [r["test_code"] for r in out["results"]]
    names = [r["test_name"] for r in out["results"]]

    # 7 OBX numerik + 2 alarm morfologi; 4 OBX mode-run tidak ikut
    assert len(out["results"]) == 9
    assert "WBC" in names and "PLT" in names
    assert "6690-2" in codes
    # Mode run tidak boleh bocor ke results
    assert "08001" not in codes
    assert "Take Mode" not in names


def test_parse_oru_memetakan_nilai_satuan_dan_range(mod):
    out = mod.parse(ORU, INSTRUMENT)
    wbc = next(r for r in out["results"] if r["test_name"] == "WBC")
    assert wbc["value"] == "2.78"
    assert wbc["unit"] == "10*3/uL"
    assert wbc["reference_range"] == "4.00-10.00"
    assert wbc["status"] == "F"


def test_parse_oru_menormalkan_flag_abnormal(mod):
    """OBX-8 ber-repeat dipisah jadi flag L/H/N — bukan 'L~N' mentah."""
    out = mod.parse(ORU, INSTRUMENT)
    by_name = {r["test_name"]: r for r in out["results"]}

    assert by_name["WBC"]["flag"] == "L"      # L~N
    assert by_name["NEU#"]["flag"] == "L"     # L~A
    assert by_name["MON%"]["flag"] == "H"     # H~A
    assert by_name["LYM%"]["flag"] == "N"     # N
    assert by_name["BAS#"]["flag"] == "N"     # A → in-range, tapi suspect


def test_parse_oru_mode_run_jadi_komentar_bukan_hasil(mod):
    out = mod.parse(ORU, INSTRUMENT)
    comments = out["comments"]
    assert "take_mode: O" in comments
    assert "blood_mode: W" in comments
    assert "test_mode: CBC+DIFF" in comments
    assert "ref_group: General" in comments


def test_parse_oru_merangkum_tes_suspect(mod):
    """Penanda `A` pada OBX-8 tidak boleh hilang begitu saja."""
    out = mod.parse(ORU, INSTRUMENT)
    suspect = [c for c in out["comments"] if c.startswith("suspect:")]
    assert len(suspect) == 1
    assert "BAS#" in suspect[0]
    assert "NEU#" in suspect[0]
    assert "MON%" in suspect[0]
    assert "WBC" not in suspect[0]      # WBC = L~N, tidak suspect


def test_parse_oru_menyertakan_alarm_morfologi(mod):
    out = mod.parse(ORU, INSTRUMENT)
    names = [r["test_name"] for r in out["results"]]
    assert "Lymphopenia" in names
    assert "Thrombopenia" in names


def test_parse_oru_tanpa_error(mod):
    out = mod.parse(ORU, INSTRUMENT)
    assert out["parse_errors"] == []


def test_parse_oru_pdw_tanpa_satuan(mod):
    """PDW dikirim tanpa satuan — jangan sampai range ikut tergeser."""
    out = mod.parse(ORU, INSTRUMENT)
    pdw = next(r for r in out["results"] if r["test_name"] == "PDW")
    assert pdw["value"] == "17.0"
    assert pdw["unit"] == ""
    assert pdw["reference_range"] == "9.0-17.0"


# ============================================================
# parse() — ORM^O01 tidak boleh jadi hasil
# ============================================================

def test_parse_orm_tidak_menghasilkan_result(mod):
    out = mod.parse(ORM, INSTRUMENT)
    assert out["results"] == []
    assert out["specimen"]["sample_id"] == "raisya"


def test_should_store_result_menolak_orm(mod):
    assert mod.should_store_result(mod.parse(ORM, INSTRUMENT), ORM) is False


def test_should_store_result_menerima_oru(mod):
    assert mod.should_store_result(mod.parse(ORU, INSTRUMENT), ORU) is True


# ============================================================
# Balasan — dibandingkan byte-per-byte dengan log driver lama
# ============================================================

def _fixed_clock(stamp):
    return lambda: stamp


def test_ack_r01_identik_dengan_log(mod):
    mod._builder = MindrayBC5150Builder(now=_fixed_clock("20190504060745"))
    assert mod.build_ack_response(ORU, INSTRUMENT) == ACK_LEGACY


def test_orr_o02_identik_dengan_log_kecuali_timestamp(mod):
    """
    Driver lama membekukan MSH-7 ORR di `20081120175238` (bug — nilainya sama
    sepanjang log setahun). Builder ini memakai jam nyata; sisanya harus sama.
    """
    mod._builder = MindrayBC5150Builder(now=_fixed_clock("20190504060725"))
    out = mod.build_ack_response(ORM, INSTRUMENT)
    assert out == ORR_LEGACY.replace(b"20081120175238", b"20190504060725")
    assert b"MSA|AR|389" in out


def test_balasan_dibedakan_menurut_jenis_pesan(mod):
    """Kesalahan paling fatal: membalas ORM dengan ACK^R01 atau sebaliknya."""
    assert b"ORR^O02" in mod.build_ack_response(ORM, INSTRUMENT)
    assert b"ACK^R01" in mod.build_ack_response(ORU, INSTRUMENT)


def test_control_id_dipantulkan(mod):
    """MSA-2 harus memantulkan MSH-10 pesan alat, bukan counter sendiri."""
    oru = ORU.replace(b"|ORU^R01|389|", b"|ORU^R01|412|")
    assert b"MSA|AA|412" in mod.build_ack_response(oru, INSTRUMENT)


def test_balasan_dibungkus_mllp(mod):
    for msg in (ORM, ORU):
        out = mod.build_ack_response(msg, INSTRUMENT)
        assert out.startswith(b"\x0b")
        assert out.endswith(b"\x1c\x0d")


# ============================================================
# split_abnormal_flag() — semua bentuk yang muncul di log
# ============================================================

@pytest.mark.parametrize("raw,flag,suspect", [
    ("N", "N", False),
    ("A", "N", True),
    ("L~N", "L", False),
    ("L~A", "L", True),
    ("H~N", "H", False),
    ("H~A", "H", True),
    ("", "", False),
])
def test_split_abnormal_flag(raw, flag, suspect):
    assert split_abnormal_flag(raw) == (flag, suspect)


# ============================================================
# handle_enq / handle_ack
# ============================================================

def test_is_enq_selalu_false_agar_orm_tetap_dibalas(mod):
    """Lihat docstring is_enq(): True akan membuat ORM tidak pernah dibalas."""
    assert mod.is_enq(ORM) is False
    assert mod.is_enq(ORU) is False


def test_handle_enq_mengekstrak_sample_id(mod):
    info = mod.handle_enq(ORM, INSTRUMENT)
    assert info["sample_id"] == "raisya"
    assert info["control_id"] == "389"


def test_handle_ack(mod):
    assert mod.handle_ack(ACK_LEGACY) == "ACK"
    assert mod.handle_ack(ORR_LEGACY) == "NAK"        # MSA|AR
    assert mod.handle_ack(b"") == "UNKNOWN"
    assert mod.handle_ack(ORU) == "UNKNOWN"           # tidak ada MSA


# ============================================================
# Ketahanan terhadap pesan cacat
# ============================================================

def test_parse_data_kosong(mod):
    out = mod.parse(b"", INSTRUMENT)
    assert "Data kosong" in out["parse_errors"]


def test_parse_tanpa_msh(mod):
    out = mod.parse(b"\x0bPID|1||^^^^MR\r\x1c\x0d", INSTRUMENT)
    assert any("MSH" in e for e in out["parse_errors"])


def test_parse_oru_tanpa_obx(mod):
    msg = (
        b"\x0bMSH|^~\\&|||||20190504060825||ORU^R01|389|P|2.3.1||||||UNICODE\r"
        b"OBR|1||X1|00001^Automated Count^99MRC|||20190504060725\r\x1c\x0d"
    )
    out = mod.parse(msg, INSTRUMENT)
    assert any("OBX" in e for e in out["parse_errors"])


def test_parse_oru_sample_id_kosong(mod):
    msg = ORU.replace(b"OBR|1||raisya|", b"OBR|1|||")
    out = mod.parse(msg, INSTRUMENT)
    assert any("Sample ID" in e for e in out["parse_errors"])


def test_parse_toleran_nama_pasien_non_ascii(mod):
    """MSH-18 = UNICODE, jadi decode harus UTF-8 bukan ASCII."""
    msg = ORU.replace(b"PID|1||^^^^MR", "PID|1||P9^^^^MR||Ayu Wulandari".encode())
    out = mod.parse(msg, INSTRUMENT)
    assert out["patient"]["patient_id"] == "P9"
    assert out["patient"]["name"] == "Ayu Wulandari"


# ============================================================
# Integrasi ResultReceiver — ORM tidak boleh tersimpan
# ============================================================

class _Cfg:
    id = 7
    name = "BC-5150"
    protocol = PROTOCOL
    mode = "unidirectional"
    bidir_mode = None

    def to_dict(self):
        return {"id": self.id, "name": self.name, "protocol": self.protocol}


class _Writer:
    def __init__(self):
        self.sent = []

    def write(self, data):
        self.sent.append(data)

    async def drain(self):
        return None


def test_receiver_membalas_orm_dan_oru_dengan_pesan_berbeda(monkeypatch):
    """
    Verifikasi jalur lengkap: dua pesan lewat satu koneksi, dua jenis balasan,
    dan hanya ORU yang masuk database.
    """
    saved = []
    monkeypatch.setattr(
        "services.tcp_socket.receiver.save_result",
        lambda *a, **k: saved.append(a) or 1,
    )

    receiver = ResultReceiver(_Cfg(), MindrayBC5150Module())
    writer = _Writer()

    asyncio.run(receiver.handle_data(ORM, writer))
    asyncio.run(receiver.handle_data(ORU, writer))

    assert len(writer.sent) == 2
    assert b"ORR^O02" in writer.sent[0]
    assert b"ACK^R01" in writer.sent[1]

    # Hanya ORU yang tersimpan — ORM tidak boleh jadi baris kosong di tbl_result
    assert len(saved) == 1
