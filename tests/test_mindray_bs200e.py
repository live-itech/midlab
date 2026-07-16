"""
Test driver HL7_MINDRAY_BS200E + integrasinya ke TCPSocketService.

Pesan contoh diambil dari Mindray Host Interface Manual v6.0 bab 3.
Test detail per-segment ada di self-test tiap file module
(`python3 -m protocols.mindray_bs200e.parser|builder|module`).
"""

import asyncio

import pytest

from protocols.base import load_module, is_mllp_protocol, _PROTOCOL_REGISTRY
from protocols.mindray_bs200e.module import MindrayBS200EModule
from services.tcp_socket.receiver import ResultReceiver


PROTOCOL = "HL7_MINDRAY_BS200E"

ORU_SAMPLE = (
    b"\x0b"
    b"MSH|^~\\&|Mindray|BS-200E|||20070719145353||ORU^R01|1|P|2.3.1||||0||ASCII|||\r"
    b"PID|1|854||12|Tommy||19830719145307|F|A||||||||||||||||||||||\r"
    b"OBR|1|0000000002|2|Mindray^BS-200E|Y||20070719145300||||||||serum|||||||||||||\r"
    b"OBX|1|NM|2|test2|5.000000|g/ml|-||||F|||||||\r"
    b"\x1c\x0d"
)

ORU_QC = (
    b"\x0b"
    b"MSH|^~\\&|Mindray|BS-200E|||20070720120202||ORU^R01|1|P|2.3.1||||2||ASCII|||\r"
    b"OBR|1|1|test1|Mindray^BS-200E||20070720120143|||||||QUAL1|1111|20080720000000||H"
    b"|5.000000|2.000000|0.11029|g/ml|||||||||||||||\r"
    b"\x1c\x0d"
)

QRY = (
    b"\x0b"
    b"MSH|^~\\&|Mindray|BS-200E|||20070723170707||QRY^Q02|5|P|2.3.1||||||ASCII|||\r"
    b"QRD|20070723170707|R|D|1|||RD|34567743|OTH|||T|\r"
    b"QRF|BS-200E|20070723000000|20070723170749|||RCT|COR|ALL||\r"
    b"\x1c\x0d"
)

ORDER = {
    "order_id": "ORD-1",
    "request_datetime": "2026-07-16T10:00:00",
    "patient": {"patient_id": "123", "name": "Tom", "dob": "19620824", "gender": "M"},
    "specimen": {"sample_id": "34567743", "sample_type": "Urine", "priority": "R"},
    "tests": [{"test_code": "1", "test_name": "GLU"}],
}

INSTRUMENT = {"id": 7, "name": "Mindray BS-200E", "protocol": PROTOCOL}


@pytest.fixture
def module():
    return MindrayBS200EModule()


# ============================================================
# Registry & dispatch
# ============================================================

def test_terdaftar_di_registry_dan_bisa_diload():
    assert PROTOCOL in _PROTOCOL_REGISTRY
    mod = load_module(PROTOCOL)
    assert mod.PROTOCOL_NAME == PROTOCOL
    assert isinstance(mod, MindrayBS200EModule)


def test_dianggap_keluarga_mllp():
    # Menentukan TCPSocketService memakai framing MLLP, bukan handshake ASTM.
    assert is_mllp_protocol(PROTOCOL) is True
    assert is_mllp_protocol("ASTM") is False
    assert is_mllp_protocol("COBAS_C111") is False


# ============================================================
# parse()
# ============================================================

def test_parse_hasil_sampel(module):
    parsed = module.parse(ORU_SAMPLE, INSTRUMENT)

    assert parsed["protocol"] == PROTOCOL
    assert parsed["instrument_id"] == 7
    assert parsed["patient"]["patient_id"] == "854"
    assert parsed["patient"]["name"] == "Tommy"
    assert parsed["patient"]["gender"] == "F"
    # Barcode (OBR-2), bukan sample ID internal alat (OBR-3)
    assert parsed["specimen"]["sample_id"] == "0000000002"
    assert parsed["specimen"]["sample_type"] == "serum"
    assert parsed["results"] == [{
        "test_code": "2",
        "test_name": "test2",
        "value": "5.000000",
        "unit": "g/ml",
        "reference_range": "",   # "-" pada OBX-7 berarti tidak ada range
        "flag": "",
        "status": "F",
    }]
    assert parsed["parse_errors"] == []


def test_parse_hasil_qc(module):
    parsed = module.parse(ORU_QC, INSTRUMENT)

    assert parsed["specimen"]["sample_type"] == "qc"
    assert len(parsed["results"]) == 1
    assert parsed["results"][0]["test_code"] == "1"
    assert parsed["results"][0]["value"] == "0.11029"
    assert parsed["results"][0]["status"] == "qc"
    assert "control_name: QUAL1" in parsed["comments"]
    assert parsed["parse_errors"] == []


@pytest.mark.parametrize("raw", [b"", b"sampah", b"\x0bMSH|rusak\x1c\x0d"])
def test_parse_input_rusak_tidak_melempar(module, raw):
    parsed = module.parse(raw, INSTRUMENT)
    assert parsed["parse_errors"], "input rusak harus dilaporkan lewat parse_errors"
    assert parsed["results"] == []


# ============================================================
# Query mode
# ============================================================

def test_is_enq_hanya_untuk_qry(module):
    assert module.is_enq(QRY) is True
    assert module.is_enq(ORU_SAMPLE) is False


def test_handle_enq_ambil_barcode(module):
    info = module.handle_enq(QRY, INSTRUMENT)
    assert info["type"] == "query"
    assert info["sample_id"] == "34567743"
    assert info["_msh"]["control_id"] == "5"


def test_query_response_qck_lalu_dsr(module):
    context = module.handle_enq(QRY, INSTRUMENT)["_msh"]
    resp = module.format_query_response_full(ORDER, INSTRUMENT, context)

    assert resp.index(b"QCK^Q02") < resp.index(b"DSR^Q03")
    assert b"QAK|SR|OK|" in resp
    assert b"DSP|21||34567743|||" in resp     # barcode — field wajib
    assert b"DSP|29||1^GLU^^|||" in resp      # baris tes pertama
    assert b"DSC||" in resp                   # penanda pesan terakhir


def test_query_not_found_tanpa_dsr(module):
    context = module.handle_enq(QRY, INSTRUMENT)["_msh"]
    resp = module.format_query_not_found_full(INSTRUMENT, context)

    assert b"QAK|SR|NF|" in resp
    assert b"DSR^Q03" not in resp


def test_not_found_tidak_menunggu_ack(module):
    # QueryHandler membaca flag ini; alat tidak membalas QCK NF sama sekali.
    assert module.ACK_EXPECTED_ON_NOT_FOUND is False


@pytest.mark.parametrize("msa,expected", [
    (b"MSA|AA|1|Message accepted|||0|", "ACK"),
    (b"MSA|AE|1|Required field missing|||101|", "NAK"),
    (b"MSA|AR|1|Application record locked|||206|", "NAK"),
])
def test_handle_ack(module, msa, expected):
    raw = (
        b"\x0bMSH|^~\\&|Mindray|BS-200E|||20070723170707||ACK^Q03|1|P|2.3.1||||||ASCII|||\r"
        + msa + b"\r\x1c\x0d"
    )
    assert module.handle_ack(raw) == expected


def test_handle_ack_input_tak_dikenal(module):
    assert module.handle_ack(b"") == "UNKNOWN"
    assert module.handle_ack(ORU_SAMPLE) == "UNKNOWN"


# ============================================================
# Integrasi ResultReceiver — framing MLLP + ACK
# ============================================================

class _FakeConfig:
    id = 7
    name = "Mindray BS-200E"
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


async def test_receiver_parse_simpan_dan_ack(monkeypatch, module):
    """Satu pesan ORU utuh → tersimpan ke tbl_result → alat di-ACK."""
    saved = {}

    def _fake_save(instrument_id, protocol, raw_hex, result_dict):
        saved.update(
            instrument_id=instrument_id, protocol=protocol, result=result_dict
        )
        return 123

    monkeypatch.setattr("services.tcp_socket.receiver.save_result", _fake_save)

    receiver = ResultReceiver(_FakeConfig(), module)
    writer = _FakeWriter()

    is_query = await receiver.handle_data(ORU_SAMPLE, writer)

    assert is_query is False
    assert saved["protocol"] == PROTOCOL
    assert saved["result"]["specimen"]["sample_id"] == "0000000002"

    # ACK^R01 versi Mindray, bukan ACK HL7 generic
    assert writer.written.startswith(b"\x0bMSH|^~\\&|||Mindray|BS-200E|")
    assert b"||ACK^R01|1|P|2.3.1||||0||ASCII|||" in writer.written
    assert b"MSA|AA|1|Message accepted|||0|" in writer.written
    assert writer.written.endswith(b"\x1c\x0d")


async def test_receiver_delegasikan_query_ke_query_handler(module):
    """QRY^Q02 tidak disimpan sebagai hasil, tapi diteruskan ke QueryHandler."""
    receiver = ResultReceiver(_FakeConfig(), module)
    writer = _FakeWriter()

    is_query = await receiver.handle_data(QRY, writer)

    assert is_query is True
    assert receiver.last_query_data == QRY
    assert writer.written == b"", "query tidak boleh di-ACK oleh receiver"


async def test_receiver_gabung_pesan_terpotong(monkeypatch, module):
    """Pesan yang tiba terpotong antar TCP segment tetap dirakit utuh."""
    calls = []
    monkeypatch.setattr(
        "services.tcp_socket.receiver.save_result",
        lambda *a: calls.append(a) or 1,
    )

    receiver = ResultReceiver(_FakeConfig(), module)
    writer = _FakeWriter()

    potong = len(ORU_SAMPLE) // 2
    assert await receiver.handle_data(ORU_SAMPLE[:potong], writer) is False
    assert calls == [], "pesan belum utuh, belum boleh disimpan"

    await receiver.handle_data(ORU_SAMPLE[potong:], writer)
    assert len(calls) == 1, "setelah bagian kedua tiba, hasil disimpan sekali"
