"""
Test driver HL7_SD_BIOSENSOR_F2400 (IHE PCD-01 / HL7 v2.6) + integrasi TCPSocketService.

Pesan contoh disalin **verbatim dari log komunikasi alat** SD Biosensor
STANDARD F2400 (2026-07-13), termasuk balasan driver lama sebagai baseline
byte-level. Log sumbernya berasal dari F2400; lini FLine memakai profil yang
sama untuk F2400 dan F200 — lihat
docstring modul.
"""

import asyncio

import pytest

from protocols.base import load_module, is_mllp_protocol, _PROTOCOL_REGISTRY
from protocols.sd_biosensor_f2400.module import SDBiosensorF2400Module
from protocols.sd_biosensor_f2400.builder import SDBiosensorF2400Builder
from protocols.sd_biosensor_f2400.parser import (
    to_iso8601, normalize_reference_range, parse_device_info, parse_derived_values,
)
from services.tcp_socket.receiver import ResultReceiver


PROTOCOL = "HL7_SD_BIOSENSOR_F2400"
GUID = "{b73a7615-2687-47bf-862e-b4465cde8332}"

ORU = (
    b"\x0b"
    b"MSH|^~\\&|FA24E01AA0173^70b3d57372500457^EUI-64| |||20260713114140-0500||"
    b"ORU^R01^ORU_R01|" + GUID.encode() + b"|P|2.6|||AL|NE||UNICODE UTF-8|"
    b"EN^English^ISO639||IHE_PCD_ORU_R01^IHE PCD^1.3.6.1.4.1.19376.1.6.4.1^ISO\r"
    b"PID|||2607130052||^^^^^^U\r"
    b"OBR|1|7054138a-64e2-470a-82a2-0286f837fcf0^FA24E01AA0173^70b3d57372500457^GUID"
    b"|7054138a-64e2-470a-82a2-0286f837fcf0^FA24E01AA0173^70b3d57372500457^GUID"
    b"|4548-4^Hemoglobin A1c^LN|||20260713114138-0500|20260713114138-0500\r"
    b"NTE|1||Device Information,Date of manufacture=20260215,LotNo=014,"
    b"SerialNo=00000,Kind=FLine1\r"
    b"OBX|1|NM|4548-4^Hemoglobin A1c^LN|1.0.0.7|6.3|%^Percent^NGSP|[4.00;15.00]"
    b"||||F|||20260713114138-0500||admin|||20260713114138-0500\r"
    b"NTE|1||eAG = 134.11 mg/dL, IFCC = 45.36 %\r"
    b"\x1c\x0d"
)

# Balasan driver lama atas pesan di atas (baseline)
ACK_LEGACY = (
    b"\x0b"
    b"MSH|^~\\&|Factory|Chemistry Analyzer|||20260713113952||ACK^R01|"
    + GUID.encode() + b"|P|2.3.1||||||UNICODE\r"
    b"MSA|AA|" + GUID.encode() + b"||||0|\r"
    b"\x1c\x0d"
)

INSTRUMENT = {"id": 91, "name": "SD F2400", "protocol": PROTOCOL}


@pytest.fixture
def mod():
    return SDBiosensorF2400Module()


# ============================================================
# Registrasi & loader
# ============================================================

def test_protocol_terdaftar():
    assert PROTOCOL in _PROTOCOL_REGISTRY


def test_load_module():
    loaded = load_module(PROTOCOL)
    assert loaded.PROTOCOL_NAME == PROTOCOL
    assert loaded.VERSION == "1.0.0"


def test_dikenali_sebagai_protocol_mllp():
    assert is_mllp_protocol(PROTOCOL) is True


# ============================================================
# Helper konversi — bagian paling khas PCD-01
# ============================================================

@pytest.mark.parametrize("raw,expected", [
    ("20260713114138-0500", "2026-07-13T11:41:38-05:00"),
    ("20260713114138+0700", "2026-07-13T11:41:38+07:00"),
    ("20260713114138", "2026-07-13T11:41:38"),
    ("202607131141", "2026-07-13T11:41:00"),
    ("20260713", "2026-07-13"),
    ("", ""),
    ("bukan-tanggal", "bukan-tanggal"),
])
def test_to_iso8601_mempertahankan_offset(raw, expected):
    assert to_iso8601(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("[4.00;15.00]", "4.00-15.00"),
    ("[0;10]", "0-10"),
    ("[]", ""),
    ("", ""),
    ("-", ""),
    ("4.00-15.00", "4.00-15.00"),
])
def test_normalize_reference_range(raw, expected):
    assert normalize_reference_range(raw) == expected


def test_parse_device_info():
    out = parse_device_info(
        "Device Information,Date of manufacture=20260215,LotNo=014,"
        "SerialNo=00000,Kind=FLine1"
    )
    assert out == {
        "manufactured": "20260215",
        "lot_no": "014",
        "serial_no": "00000",
        "device_kind": "FLine1",
    }


def test_parse_derived_values():
    out = parse_derived_values("eAG = 134.11 mg/dL, IFCC = 45.36 %")
    assert out == [
        {"name": "eAG", "value": "134.11", "unit": "mg/dL"},
        {"name": "IFCC", "value": "45.36", "unit": "%"},
    ]


# ============================================================
# parse() — ORU^R01
# ============================================================

def test_parse_identitas_sampel(mod):
    out = mod.parse(ORU, INSTRUMENT)
    # PID-3, bukan GUID di OBR — GUID tidak dikenal sistem lain
    assert out["specimen"]["sample_id"] == "2607130052"
    assert out["order"]["order_id"] == "2607130052"
    assert out["patient"]["patient_id"] == "2607130052"
    assert out["order"]["panel"] == "Hemoglobin A1c"


def test_parse_waktu_pemeriksaan_mempertahankan_offset(mod):
    out = mod.parse(ORU, INSTRUMENT)
    assert out["specimen"]["collected_at"] == "2026-07-13T11:41:38-05:00"
    assert out["message_datetime"] == "2026-07-13T11:41:40-05:00"


def test_parse_hasil_utama(mod):
    out = mod.parse(ORU, INSTRUMENT)
    a1c = next(r for r in out["results"] if r["test_code"] == "4548-4")
    assert a1c["test_name"] == "Hemoglobin A1c"
    assert a1c["value"] == "6.3"
    assert a1c["unit"] == "%"
    assert a1c["reference_range"] == "4.00-15.00"
    assert a1c["status"] == "F"


def test_parse_nilai_turunan_jadi_hasil_tersendiri(mod):
    """eAG dan IFCC dilaporkan rutin bersama HbA1c — harus bisa dipetakan LIS."""
    out = mod.parse(ORU, INSTRUMENT)
    by_code = {r["test_code"]: r for r in out["results"]}

    assert by_code["eAG"]["value"] == "134.11"
    assert by_code["eAG"]["unit"] == "mg/dL"
    assert by_code["IFCC"]["value"] == "45.36"
    assert by_code["IFCC"]["status"] == "F"
    assert len(out["results"]) == 3      # A1c + eAG + IFCC


def test_parse_teks_turunan_asli_tetap_disimpan(mod):
    out = mod.parse(ORU, INSTRUMENT)
    assert any(c.startswith("derived:") for c in out["comments"])


def test_parse_informasi_alat_jadi_komentar(mod):
    """Lot & serial reagen adalah jejak traceability yang diminta saat audit."""
    out = mod.parse(ORU, INSTRUMENT)
    comments = out["comments"]
    assert "lot_no: 014" in comments
    assert "serial_no: 00000" in comments
    assert "device_kind: FLine1" in comments
    assert "manufactured: 20260215" in comments
    assert "device: FA24E01AA0173" in comments


def test_parse_tanpa_error(mod):
    assert mod.parse(ORU, INSTRUMENT)["parse_errors"] == []


def test_nte_dibedakan_menurut_segment_induk(mod):
    """
    Dua NTE dalam pesan ini sama-sama `NTE|1`. Yang membedakan hanya posisinya
    (setelah OBR vs setelah OBX) — kalau tertukar, info alat akan jadi hasil.
    """
    out = mod.parse(ORU, INSTRUMENT)
    codes = [r["test_code"] for r in out["results"]]
    assert "Device Information" not in codes
    assert "lot_no" not in codes
    assert "eAG" in codes


# ============================================================
# ACK — dibandingkan byte-per-byte dengan log
# ============================================================

def test_ack_identik_dengan_log(mod):
    mod._builder = SDBiosensorF2400Builder(now=lambda: "20260713113952")
    assert mod.build_ack_response(ORU, INSTRUMENT) == ACK_LEGACY


def test_control_id_guid_dipantulkan_utuh(mod):
    """Kurung kurawal ikut — alat mencocokkan string persis."""
    out = mod.build_ack_response(ORU, INSTRUMENT)
    assert f"MSA|AA|{GUID}".encode() in out
    assert GUID.encode() in out


def test_ack_dibungkus_mllp(mod):
    out = mod.build_ack_response(ORU, INSTRUMENT)
    assert out.startswith(b"\x0b")
    assert out.endswith(b"\x1c\x0d")


def test_ack_varian_pcd_conformant():
    """Opsi balasan v2.6 yang memantulkan identitas alat."""
    builder = SDBiosensorF2400Builder(now=lambda: "20260713113952")
    out = builder.build_ack_r01(
        {"control_id": GUID, "device_id": "FA24E01AA0173^70b3d57372500457^EUI-64"},
        pcd_conformant=True,
    )
    assert b"|2.6|" in out
    assert b"FA24E01AA0173" in out
    assert f"MSA|AA|{GUID}".encode() in out


def test_should_store_result_menerima_oru(mod):
    assert mod.should_store_result(mod.parse(ORU, INSTRUMENT), ORU) is True


# ============================================================
# Bidirectional tidak didukung — harus gagal terang-terangan
# ============================================================

def test_is_enq_selalu_false(mod):
    assert mod.is_enq(ORU) is False


def test_format_order_menolak_bukan_mengarang(mod):
    """
    Mengirim pesan karangan ke alat klinis lebih berbahaya daripada gagal
    terang-terangan — PCD-01 DEC tidak punya jalur order.
    """
    assert mod.format_order({"order_id": "X"}, INSTRUMENT) == b""
    assert mod.format_query_response({}, INSTRUMENT) == b""
    assert mod.format_query_not_found(INSTRUMENT) == b""


def test_handle_ack(mod):
    assert mod.handle_ack(ACK_LEGACY) == "ACK"
    assert mod.handle_ack(b"") == "UNKNOWN"
    assert mod.handle_ack(ORU) == "UNKNOWN"


# ============================================================
# Ketahanan
# ============================================================

def test_parse_data_kosong(mod):
    assert "Data kosong" in mod.parse(b"", INSTRUMENT)["parse_errors"]


def test_parse_tanpa_msh(mod):
    out = mod.parse(b"\x0bPID|||123\r\x1c\x0d", INSTRUMENT)
    assert any("MSH" in e for e in out["parse_errors"])


def test_parse_tanpa_obx(mod):
    msg = (
        b"\x0bMSH|^~\\&|D^E^EUI-64| |||20260713114140-0500||ORU^R01^ORU_R01|"
        + GUID.encode() + b"|P|2.6|||AL|NE||UNICODE UTF-8\r"
        b"PID|||2607130052\r\x1c\x0d"
    )
    out = mod.parse(msg, INSTRUMENT)
    assert any("OBX" in e or "OBR" in e for e in out["parse_errors"])


def test_parse_sample_id_kosong(mod):
    out = mod.parse(ORU.replace(b"PID|||2607130052", b"PID|||"), INSTRUMENT)
    assert any("sampel" in e.lower() for e in out["parse_errors"])


def test_parse_toleran_model_dan_serial_berbeda(mod):
    """
    Log berasal dari F2400; unit FLine lain akan punya serial & EUI-64 lain, dan
    mungkin Kind= lain. Tidak boleh ada yang di-hardcode.
    """
    msg = (ORU
           .replace(b"FA24E01AA0173", b"F200XYZ99999")
           .replace(b"70b3d57372500457", b"70b3d5aabbccddee")
           .replace(b"Kind=FLine1", b"Kind=FLine2"))
    out = mod.parse(msg, INSTRUMENT)
    assert out["parse_errors"] == []
    assert "device: F200XYZ99999" in out["comments"]
    assert "device_kind: FLine2" in out["comments"]
    assert len(out["results"]) == 3


def test_parse_parameter_selain_hba1c(mod):
    """Parameter lain lini FLine (mis. CRP) memakai pola OBX yang sama."""
    msg = (ORU
           .replace(b"4548-4^Hemoglobin A1c^LN", b"1988-5^C reactive protein^LN")
           .replace(b"|6.3|%^Percent^NGSP|[4.00;15.00]", b"|12.4|mg/L^mg/L^UCUM|[0;5]")
           .replace(b"NTE|1||eAG = 134.11 mg/dL, IFCC = 45.36 %\r", b""))
    out = mod.parse(msg, INSTRUMENT)
    assert out["parse_errors"] == []
    assert len(out["results"]) == 1
    crp = out["results"][0]
    assert crp["test_name"] == "C reactive protein"
    assert crp["value"] == "12.4"
    assert crp["unit"] == "mg/L"
    assert crp["reference_range"] == "0-5"


def test_parse_obx_dengan_abnormal_flag(mod):
    """OBX-8 kosong di log, tapi harus terbaca kalau alat mengisinya."""
    msg = ORU.replace(b"|[4.00;15.00]||||F|", b"|[4.00;15.00]|H|||F|")
    out = mod.parse(msg, INSTRUMENT)
    a1c = next(r for r in out["results"] if r["test_code"] == "4548-4")
    assert a1c["flag"] == "H"


# ============================================================
# Integrasi ResultReceiver
# ============================================================

class _Cfg:
    id = 91
    name = "SD F2400"
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


def test_receiver_menyimpan_hasil_dan_membalas_ack(monkeypatch):
    saved = []
    monkeypatch.setattr(
        "services.tcp_socket.receiver.save_result",
        lambda *a, **k: saved.append(a) or 1,
    )

    receiver = ResultReceiver(_Cfg(), SDBiosensorF2400Module())
    writer = _Writer()

    asyncio.run(receiver.handle_data(ORU, writer))

    assert len(saved) == 1
    assert len(writer.sent) == 1
    assert b"ACK^R01" in writer.sent[0]
    assert GUID.encode() in writer.sent[0]


def test_receiver_menangani_dua_pesan_dalam_satu_paket(monkeypatch):
    """TCP bisa menggabungkan dua pesan MLLP dalam satu segmen."""
    saved = []
    monkeypatch.setattr(
        "services.tcp_socket.receiver.save_result",
        lambda *a, **k: saved.append(a) or 1,
    )

    second = ORU.replace(GUID.encode(), b"{5d9d6588-20ca-4d0a-b1cf-8c2809e889f9}")
    receiver = ResultReceiver(_Cfg(), SDBiosensorF2400Module())
    writer = _Writer()

    asyncio.run(receiver.handle_data(ORU + second, writer))

    assert len(saved) == 2
    assert len(writer.sent) == 2
