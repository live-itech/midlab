"""
Test driver HL7_MINDRAY_BS240PRO_360E_240E (BS-240Pro / BS-360E / BS-240E).

Fixture diambil dari log komunikasi BS-360E lapangan (7 Okt 2024) dan
DIANONIMKAN: nama pasien, NIK, dan nomor RM diganti nilai fiktif. Kode tes,
nilai QC, lot, dan timestamp dibiarkan asli. Contoh QC multi-kontrol diambil
dari manual "BS-360E & BS-240Pro & BS-240E LIS Interface Manual V1.0" bab 1.3.
"""

import importlib.util
import logging
import pathlib
import sys
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from lib.db import TblResult
from protocols.base import load_module, is_mllp_protocol, _PROTOCOL_REGISTRY
from protocols.mindray_bs240pro.module import MindrayBS240ProModule, resolve_qc_level
from protocols.mindray_bs200e.module import MindrayBS200EModule
from services.lis_bridge.result_pusher import build_qc_payload
from services.tcp_socket.receiver import ResultReceiver


PROTOCOL = "HL7_MINDRAY_BS240PRO_360E_240E"

ORU_SAMPLE = (
    b"\x0b"
    b"MSH|^~\\&|||||20241007002452||ORU^R01|1134|P|2.3.1||||0||ASCII|||\r"
    b"PID|570||||PASIEN UJI|||O|||||||||||||||||||||||\r"
    b"OBR|570|2191|1|^|N|20241007000923|20241007000905|20241007000905||1^10"
    b"||||20241007000905|Serum|||||||||||||||||||||||||||||||||\r"
    b"OBX|1|NM|GLU|Glucose (GOD-POD Method)|105.805265|mg/dL|-|N|||F||105.805265|20241007002451|||0||\r"
    b"OBX|2|NM|ALT|Alanine Aminotransferase|6.181722|U/L|-|N|||F||6.181722|20241007002431|||0||\r"
    b"OBX|3|NM|AST|Aspartate Aminotransferase|9.087522|U/L|-|N|||F||9.087522|20241007002441|||0||\r"
    b"OBX|4|NM|CREA|Creatinine (Sarcosine Oxidase Method)|14.931468|mg/dL|-|N|||F||14.931468|20241007002441|||0||\r"
    b"OBX|5|NM|UREA|Urea|98.026045|mg/dL|-|N|||F||98.026045|20241007002202|||0||\r"
    b"\x1c\x0d"
)

INSTRUMENT = {"id": 9, "name": "Mindray BS-240Pro", "protocol": PROTOCOL}


@pytest.fixture
def module():
    return MindrayBS240ProModule()


# ============================================================
# Registry & identitas
# ============================================================

def test_terdaftar_dan_load_module_mengembalikan_class_bs240pro():
    # load_module memilih class pertama hasil dir(); induk BS-200E tidak boleh
    # ikut terpilih.
    assert _PROTOCOL_REGISTRY[PROTOCOL] == "protocols.mindray_bs240pro.module"
    mod = load_module(PROTOCOL)
    assert type(mod) is MindrayBS240ProModule
    assert mod.PROTOCOL_NAME == PROTOCOL


def test_dianggap_keluarga_mllp():
    assert is_mllp_protocol(PROTOCOL) is True


# ============================================================
# Hasil pasien (MSH-16 = 0)
# ============================================================

def test_parse_hasil_pasien(module):
    parsed = module.parse(ORU_SAMPLE, INSTRUMENT)

    assert parsed["protocol"] == PROTOCOL
    assert parsed["instrument_id"] == 9
    assert parsed["patient"]["name"] == "PASIEN UJI"
    assert parsed["specimen"]["sample_id"] == "2191"      # OBR-2 = Lab No
    assert [r["test_code"] for r in parsed["results"]] == ["GLU", "ALT", "AST", "CREA", "UREA"]
    assert parsed["results"][0] == {
        "test_code": "GLU",
        "test_name": "Glucose (GOD-POD Method)",
        "value": "105.805265",
        "unit": "mg/dL",
        "reference_range": "",
        "flag": "N",
        "status": "F",
    }
    assert parsed["parse_errors"] == []
    assert "qc" not in parsed


def test_ack_r01_memakai_model_bs240pro_saat_msh4_kosong(module):
    # Alat di lapangan mengosongkan MSH-3/4; fallback MSH-6 harus BS-240Pro,
    # bukan BS-200E milik driver induk.
    ack = module.build_ack_response(ORU_SAMPLE, INSTRUMENT)
    assert ack.startswith(b"\x0bMSH|^~\\&|||Mindray|BS-240Pro|")
    assert b"MSA|AA|1134|Message accepted|||0|" in ack


# QC tunggal dari log (DBIL, CTRL L1, OBR-17 "M", flag Westgard alat)
ORU_QC = (
    b"\x0b"
    b"MSH|^~\\&|||||20241007031434||ORU^R01|1141|P|2.3.1||||2||ASCII|||\r"
    b"OBR|255|DBIL|D-Bil-V|^|Serum|20241007003327|20241007003250|20241007031212|||1|8"
    b"|CTRL L1|059324003|20261231||M|1.050000|0.160000|0.525642|mg/dL|||||||RGTE,1-3s,2.7s"
    b"||||||||||||||||||||\r"
    b"\x1c\x0d"
)

# Multi-kontrol, contoh manual bab 1.3 (dua kontrol dalam satu OBR)
ORU_QC_MULTI = (
    b"\x0b"
    b"MSH|^~\\&|||||20120508103014||ORU^R01|1|P|2.3.1||||2||ASCII|||\r"
    b"OBR|1|AST|AST|^|Serum|20160509115017|20160509114958|20160509115857|||2|1^2"
    b"|QUAL1^QUAL2|1111^2222|20300101^20300101||L^M|45.000000^55.000000|5.000000^5.000000"
    b"|0.130291^0.137470|mg/dL|||||||RGTE||||||||||||||||||||\r"
    b"\x1c\x0d"
)


# ============================================================
# Level kontrol
# ============================================================

@pytest.mark.parametrize("name, inst, expected", [
    ("CTRL L1", "M", ("1", True)),       # kasus lapangan: nama menang, konflik
    ("CTRL L2", "M", ("2", False)),
    ("Level 3", "H", ("3", False)),
    ("LV3", "", ("3", False)),
    ("QC-L 2", "L", ("2", True)),
    ("KONTROL NORMAL", "M", ("M", False)),   # tidak ada pola → OBR-17
    ("", "h", ("H", False)),
    ("CTRL1", "L", ("L", False)),        # "L" tanpa batas kata bukan pola level
])
def test_resolve_qc_level(name, inst, expected):
    assert resolve_qc_level(name, inst) == expected


# ============================================================
# QC (MSH-16 = 2)
# ============================================================

def test_parse_qc_tunggal(module, caplog):
    with caplog.at_level(logging.WARNING):
        parsed = module.parse(ORU_QC, INSTRUMENT)

    assert parsed["protocol"] == PROTOCOL
    assert parsed["specimen"]["sample_type"] == "qc"
    assert parsed["specimen"]["collected_at"] == "20241007031212"   # OBR-8
    assert parsed["results"] == [{
        "test_code": "DBIL",            # OBR-2 langsung, tanpa tabel padanan
        "test_name": "D-Bil-V",
        "value": "0.525642",
        "unit": "mg/dL",
        "reference_range": "",
        "flag": "",                     # flag alat hanya di komentar
        "status": "qc",
    }]
    assert parsed["qc"] == {
        "lot_number": "059324003",
        "level": "1",                   # dari nama "CTRL L1", bukan OBR-17 "M"
        "control_name": "CTRL L1",
        "expiry_date": "2026-12-31",
        "run_at": "20241007031212",
        "targets": {"DBIL": {"mean": "1.050000", "sd": "0.160000"}},
    }
    assert "instrument_flags: RGTE,1-3s,2.7s" in parsed["comments"]
    assert "control_no: 8" in parsed["comments"]
    assert parsed["parse_errors"] == []
    assert len(parsed["_qc_controls"]) == 1
    assert any("level dari nama = 1, dari alat = M" in r.message for r in caplog.records)


def test_parse_qc_multi_kontrol_qc_utama_kontrol_pertama(module):
    parsed = module.parse(ORU_QC_MULTI, INSTRUMENT)

    assert parsed["qc"]["lot_number"] == "1111"
    assert parsed["qc"]["level"] == "L"          # QUAL1 tanpa pola → OBR-17
    assert parsed["results"][0]["value"] == "0.130291"
    assert len(parsed["_qc_controls"]) == 2
    assert parsed["parse_errors"] == []


def test_split_results_satu_baris_per_kontrol(module):
    parsed = module.parse(ORU_QC_MULTI, INSTRUMENT)
    rows = module.split_results(parsed)

    assert len(rows) == 2
    assert [r["qc"]["lot_number"] for r in rows] == ["1111", "2222"]
    assert [r["qc"]["level"] for r in rows] == ["L", "M"]
    assert [r["qc"]["control_name"] for r in rows] == ["QUAL1", "QUAL2"]
    assert rows[1]["qc"]["targets"] == {"AST": {"mean": "55.000000", "sd": "5.000000"}}
    assert [r["results"][0]["value"] for r in rows] == ["0.130291", "0.137470"]
    assert "control_name: QUAL2" in rows[1]["comments"]
    assert "control_name: QUAL1" not in rows[1]["comments"]
    assert rows[0]["message_id"] != rows[1]["message_id"]
    assert all("_qc_controls" not in r for r in rows)


def test_split_results_qc_tunggal_dan_pasien_satu_baris(module):
    qc_rows = module.split_results(module.parse(ORU_QC, INSTRUMENT))
    assert len(qc_rows) == 1 and "_qc_controls" not in qc_rows[0]
    assert qc_rows[0]["qc"]["lot_number"] == "059324003"

    pasien = module.parse(ORU_SAMPLE, INSTRUMENT)
    assert module.split_results(pasien) == [pasien]


def test_obr11_tidak_cocok_dicatat_parse_errors(module):
    raw = ORU_QC_MULTI.replace(b"|||2|1^2|", b"|||3|1^2|")
    parsed = module.parse(raw, INSTRUMENT)
    assert any("OBR-11" in e for e in parsed["parse_errors"])
    assert len(module.split_results(parsed)) == 2


# ============================================================
# Query (QRY^Q02 → QCK^Q02 + DSR^Q03) dan broadcast (DSR^Q03, MSH-15 = P)
# ============================================================

# QRY^Q02 dari log (barcode 241000223710; MSH-3/4 dan QRF-1 kosong)
QRY = (
    b"\x0b"
    b"MSH|^~\\&|||||20241007065739||QRY^Q02|1167|P|2.3.1||||||ASCII|||\r"
    b"QRD|20241007065739|R|D|313|||RD|241000223710|OTH|||T|\r"
    b"QRF||||||RCT|COR|ALL||\r"
    b"\x1c\x0d"
)

ORDER = {
    "order_id": "ORD-240",
    "request_datetime": "2024-10-07T07:08:13",
    "patient": {"patient_id": "RM-0001", "name": "PASIEN QUERY", "dob": "19840515", "gender": "M"},
    "specimen": {"sample_id": "241000223710", "sample_type": "serum", "priority": "R"},
    "tests": [{"test_code": "CREA", "test_name": "Creatinine"}, {"test_code": "UREA", "test_name": "Urea"}],
}


def _msh_fields(payload: bytes, event: bytes) -> list:
    """Field segmen MSH dari pesan `event` di dalam payload (bisa >1 pesan)."""
    for message in payload.split(b"\x0b"):
        if event in message:
            msh = message.split(b"\r")[0].decode()
            return msh.split("|")
    raise AssertionError(f"{event!r} tidak ada di payload")


def test_query_ok_qck_lalu_dsr(module):
    info = module.handle_enq(QRY, INSTRUMENT)
    assert info["type"] == "query"
    assert info["sample_id"] == "241000223710"

    resp = module.format_query_response_full(ORDER, INSTRUMENT, info["_msh"])
    assert resp.index(b"QCK^Q02") < resp.index(b"DSR^Q03")
    assert b"QAK|SR|OK|" in resp
    assert b"DSP|3||PASIEN QUERY|||" in resp
    assert b"DSP|21||241000223710|||" in resp
    assert b"DSP|26||Serum|||" in resp            # kapital, seperti di log
    assert b"DSP|29||CREA^Creatinine^^|||" in resp
    assert b"DSP|30||UREA^Urea^^|||" in resp
    assert b"QRD|20241007065739|R|D|313|||RD|241000223710|OTH|||T|" in resp   # QRD dipantulkan
    dsr_msh = _msh_fields(resp, b"DSR^Q03")
    assert dsr_msh[5] == "BS-240Pro"              # MSH-6
    assert dsr_msh[14] == ""                      # MSH-15 kosong untuk balasan query


def test_query_not_found(module):
    context = module.handle_enq(QRY, INSTRUMENT)["_msh"]
    resp = module.format_query_not_found_full(INSTRUMENT, context)
    assert b"QAK|SR|NF|" in resp
    assert b"DSR^Q03" not in resp


@pytest.mark.parametrize("sample_type, expected", [
    ("plasma", b"DSP|26||Plasma|||"),
    ("URINE", b"DSP|26||Urine|||"),
    ("Whole Blood", b"DSP|26||Whole Blood|||"),   # di luar tabel → apa adanya
    ("", b"DSP|26|||||"),
])
def test_dsp26_jenis_sampel(module, sample_type, expected):
    order = dict(ORDER, specimen=dict(ORDER["specimen"], sample_type=sample_type))
    assert expected in module.format_query_response(order, INSTRUMENT)


def test_broadcast_dsr_msh15_p_tanpa_warning(module, caplog):
    with caplog.at_level(logging.WARNING):
        payload = module.format_order(ORDER, INSTRUMENT)

    assert b"QCK^Q02" not in payload
    msh = _msh_fields(payload, b"DSR^Q03")
    assert msh[14] == "P"                 # MSH-15 = P (manual: LIS kirim info sampel)
    assert msh[5] == "BS-240Pro"
    assert b"|RD|241000223710|OTH|" in payload     # QRD-8 = barcode
    assert b"QRF|BS-240Pro|" in payload
    assert b"DSP|21||241000223710|||" in payload
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


ACK_Q03_AA = (
    b"\x0bMSH|^~\\&|||||20241007070813||ACK^Q03|1|P|2.3.1||||||ASCII|||\r"
    b"MSA|AA|1|Message accepted|||0|\r\x1c\x0d"
)


def test_handle_ack_q03(module):
    assert module.handle_ack(ACK_Q03_AA) == "ACK"
    assert module.handle_ack(ACK_Q03_AA.replace(b"MSA|AA|", b"MSA|AE|")) == "NAK"


# ============================================================
# Receiver: split_results (satu pesan -> beberapa baris tbl_result)
# ============================================================

class _FakeConfig:
    id = 9
    name = "Mindray BS-240Pro"
    protocol = PROTOCOL

    def to_dict(self):
        return dict(INSTRUMENT)


class _FakeWriter:
    def __init__(self):
        self.written = b""

    def write(self, data):
        self.written += data

    async def drain(self):
        pass


@pytest.fixture
def _bersihkan_logger_receiver():
    # ResultReceiver memanggil get_logger("tcp_socket", 9) yang meng-cache
    # handler file di logger "midlab.tcp_socket.9". Buang setelah test agar
    # test_utils_logger tidak mewarisi handler ke /tmp/midlab.
    yield
    lg = logging.getLogger("midlab.tcp_socket.9")
    for h in list(lg.handlers):
        h.close()
        lg.removeHandler(h)


async def test_receiver_simpan_satu_baris_per_kontrol(
    monkeypatch, module, _bersihkan_logger_receiver
):
    saved = []

    def _fake_save(instrument_id, protocol, raw_hex, result_dict):
        saved.append((raw_hex, result_dict))
        return len(saved)

    monkeypatch.setattr("services.tcp_socket.receiver.save_result", _fake_save)
    receiver = ResultReceiver(_FakeConfig(), module)
    writer = _FakeWriter()

    assert await receiver.handle_data(ORU_QC_MULTI, writer) is False

    assert [r["qc"]["lot_number"] for _, r in saved] == ["1111", "2222"]
    assert saved[0][0] == saved[1][0] == ORU_QC_MULTI.hex()   # raw sama di tiap baris
    assert all("_qc_controls" not in r for _, r in saved)
    assert writer.written.count(b"ACK^R01") == 1               # satu pesan, satu ACK


async def test_receiver_satu_baris_gagal_tidak_menghentikan_lainnya(
    monkeypatch, module, _bersihkan_logger_receiver
):
    calls = []

    def _fake_save(instrument_id, protocol, raw_hex, result_dict):
        calls.append(result_dict["qc"]["lot_number"])
        return None if len(calls) == 1 else 77

    monkeypatch.setattr("services.tcp_socket.receiver.save_result", _fake_save)
    receiver = ResultReceiver(_FakeConfig(), module)

    await receiver.handle_data(ORU_QC_MULTI, _FakeWriter())

    assert calls == ["1111", "2222"]


async def test_receiver_tanpa_hook_tetap_satu_baris(
    monkeypatch, _bersihkan_logger_receiver
):
    # Driver tanpa split_results (BS-200E) tidak berubah perilakunya.
    bs200e = MindrayBS200EModule()
    assert not hasattr(bs200e, "split_results")
    calls = []
    monkeypatch.setattr(
        "services.tcp_socket.receiver.save_result",
        lambda *a: calls.append(a) or 1,
    )

    class _Cfg200E(_FakeConfig):
        protocol = "HL7_MINDRAY_BS200E"

    receiver = ResultReceiver(_Cfg200E(), bs200e)
    await receiver.handle_data(ORU_SAMPLE, _FakeWriter())

    assert len(calls) == 1


# ============================================================
# qc_backfill: hook split_results dan payload /qc-results
# ============================================================

def _inst240():
    m = MagicMock()
    m.id = 9
    m.name = "Mindray BS-240Pro"
    m.lis_instrument_id = "INST-BS240PRO"
    return m


def _row(result_json):
    row = TblResult()
    row.id = 1
    row.result_json = result_json
    row.received_at = datetime(2024, 10, 7, 3, 25, 7)
    return row


def test_payload_qc_results_dari_baris_bs240pro(module):
    row_json = module.split_results(module.parse(ORU_QC, INSTRUMENT))[0]
    payload = build_qc_payload(_row(row_json), _inst240())

    assert payload["protocol"] == "HL7"
    assert payload["qc"]["lot_number"] == "059324003"
    assert payload["qc"]["level"] == "1"
    assert payload["qc"]["control_name"] == "CTRL L1"
    assert payload["qc"]["expiry_date"] == "2026-12-31"
    assert payload["run_at"].startswith("2024-10-07T03:12:12")
    assert payload["message_id"] == "MID9-059324003-L1-20241007T031212"
    assert payload["results"] == [{
        "test_code": "DBIL",
        "test_name": "D-Bil-V",
        "value": "0.525642",
        "unit": "mg/dL",
        "flag": "",
        "instrument_target_mean": 1.05,
        "instrument_target_sd": 0.16,
    }]


def _load_qc_backfill(monkeypatch):
    # Skrip menyisipkan /opt/midlab ke sys.path saat di-import; kembalikan
    # sys.path setelah test agar import berikutnya tidak lari ke produksi.
    monkeypatch.setattr(sys, "path", list(sys.path))
    path = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "qc_backfill.py"
    spec = importlib.util.spec_from_file_location("qc_backfill_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_qc_backfill_satu_payload_per_kontrol(monkeypatch, module):
    backfill = _load_qc_backfill(monkeypatch)
    monkeypatch.setattr(backfill, "raw_messages", lambda _id: iter([ORU_QC_MULTI, ORU_SAMPLE]))

    payloads = backfill.collect_payloads(_inst240(), module, "2016")

    assert sorted(p["qc"]["lot_number"] for p in payloads) == ["1111", "2222"]
