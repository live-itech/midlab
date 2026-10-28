"""
Test driver HL7_BODITECH_AFIAS-1 (Boditech AFIAS-1, HL7 2.6 OUL^R24).

Driver best-effort: satu-satunya sumber adalah contoh pesan di slide 5
"AFIAS-1 LIS user manual" (Boditech, Rev.00 2025.06). Fixture utama adalah
contoh itu, dibungkus MLLP.
"""

import asyncio
import logging
import socket as socketlib
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from protocols.base import load_module, is_mllp_protocol, _PROTOCOL_REGISTRY
from lib.db import TblResult
from protocols.boditech_afias1.module import BoditechAfias1Module
from services.lis_bridge.result_pusher import build_mid_payload
from services.tcp_socket.config import InstrumentConfig
from services.tcp_socket.receiver import ResultReceiver
from services.tcp_socket.service import TCPSocketService


PROTOCOL = "HL7_BODITECH_AFIAS-1"

SEGMENTS = [
    "MSH|^~\\&||1|AFIAS-6|A60123456789|20200305171553.205+0900||OUL^R24^OUL_R24|1|T|2.6",
    "PID||T45|T45|||||",
    "OBR||T4|G||||||admin",
    "ORC|OK||||||||||||||||A60123456789|5",
    "SPM||T4PKA37F||||||||||||||||20210719",
    "OBX|1|TX|T4||33.26|nmol/L|||||R",
]


def mllp(segments) -> bytes:
    return b"\x0b" + "".join(s + "\r" for s in segments).encode() + b"\x1c\x0d"


OUL = mllp(SEGMENTS)

INSTRUMENT = {"id": 12, "name": "Boditech AFIAS-1", "protocol": PROTOCOL}


@pytest.fixture
def module():
    return BoditechAfias1Module(now=lambda: "20260101120000")


def _with(index, segment):
    """Contoh slide 5 dengan satu segment diganti (None = dihapus)."""
    segs = list(SEGMENTS)
    if segment is None:
        segs.pop(index)
    else:
        segs[index] = segment
    return mllp(segs)


# ============================================================
# Registry
# ============================================================

def test_terdaftar_dan_load_module_mengembalikan_class_afias():
    assert _PROTOCOL_REGISTRY[PROTOCOL] == "protocols.boditech_afias1.module"
    mod = load_module(PROTOCOL)
    assert type(mod) is BoditechAfias1Module
    assert mod.PROTOCOL_NAME == PROTOCOL


def test_dianggap_keluarga_mllp():
    assert is_mllp_protocol(PROTOCOL) is True


# ============================================================
# parse() — contoh slide 5
# ============================================================

def test_parse_contoh_slide5(module):
    parsed = module.parse(OUL, INSTRUMENT)

    assert parsed["protocol"] == PROTOCOL
    assert parsed["instrument_id"] == 12
    assert parsed["message_datetime"] == "2020-03-05T17:15:53.205+09:00"
    assert parsed["patient"]["patient_id"] == "T45"
    assert parsed["specimen"]["sample_id"] == "T45"
    assert parsed["specimen"]["collected_at"] == "2020-03-05T17:15:53.205+09:00"
    assert parsed["order"]["panel"] == "T4"
    assert parsed["results"] == [{
        "test_code": "T4",
        "test_name": "T4",
        "value": "33.26",
        "unit": "nmol/L",
        "reference_range": "",
        "flag": "",
        "status": "R",
    }]
    for comment in (
        "model: AFIAS-6",
        "serial: A60123456789",
        "processing_id: T",
        "obr3: G",
        "operator: admin",
        "orc18: 5",
        "cartridge_lot: T4PKA37F",
        "cartridge_expiry: 2021-07-19",
    ):
        assert comment in parsed["comments"]
    assert parsed["parse_errors"] == []
    assert "qc" not in parsed


def test_id_sampel_dari_pid2_bila_pid3_kosong(module):
    parsed = module.parse(_with(1, "PID||RM-77||||||"), INSTRUMENT)
    assert parsed["specimen"]["sample_id"] == "RM-77"
    assert parsed["parse_errors"] == []


def test_pid3_didahulukan_dari_pid2(module):
    parsed = module.parse(_with(1, "PID||RM-77|BC-001^^^LAB||||"), INSTRUMENT)
    assert parsed["specimen"]["sample_id"] == "BC-001"
    assert parsed["patient"]["patient_id"] == "BC-001"


def test_id_sampel_kosong_dicatat(module):
    parsed = module.parse(_with(1, "PID||||||||"), INSTRUMENT)
    assert any("ID sampel kosong" in e for e in parsed["parse_errors"])
    assert parsed["results"][0]["value"] == "33.26"


def test_dua_obx_dua_hasil(module):
    segs = SEGMENTS + ["OBX|2|NM|TSH^Thyroid Stimulating Hormone||1.2|uIU/mL|0.4-4.0|N|||F"]
    parsed = module.parse(mllp(segs), INSTRUMENT)
    assert [r["test_code"] for r in parsed["results"]] == ["T4", "TSH"]
    assert parsed["results"][1]["test_name"] == "Thyroid Stimulating Hormone"
    assert parsed["results"][1]["reference_range"] == "0.4-4.0"
    assert parsed["results"][1]["flag"] == "N"


def test_tanpa_obx_dicatat(module):
    parsed = module.parse(_with(5, None), INSTRUMENT)
    assert parsed["results"] == []
    assert any("Tidak ada OBX" in e for e in parsed["parse_errors"])


def test_message_type_lain_dicatat(module):
    raw = OUL.replace(b"OUL^R24^OUL_R24", b"ORU^R01^ORU_R01")
    parsed = module.parse(raw, INSTRUMENT)
    assert any("bukan hasil" in e for e in parsed["parse_errors"])


@pytest.mark.parametrize("raw", [b"", b"sampah", b"\x0bPID||T45\r\x1c\x0d", b"\x0bMSH|rusak\x1c\x0d"])
def test_input_rusak_tidak_melempar(module, raw):
    parsed = module.parse(raw, INSTRUMENT)
    assert parsed["parse_errors"]


def test_pesan_tanpa_mllp_tetap_bisa_diparse(module):
    # Parser sendiri toleran; framing ditentukan receiver.
    raw = "".join(s + "\r" for s in SEGMENTS).encode()
    assert module.parse(raw, INSTRUMENT)["specimen"]["sample_id"] == "T45"


# ============================================================
# ACK^R24
# ============================================================

def test_ack_r24(module):
    ack = module.build_ack_response(OUL, INSTRUMENT)

    assert ack.startswith(b"\x0b") and ack.endswith(b"\x1c\x0d")
    segs = ack[1:-2].decode().split("\r")
    assert segs[0] == (
        "MSH|^~\\&|MidLab||AFIAS-6|A60123456789|20260101120000||ACK^R24^ACK|1|T|2.6"
    )
    assert segs[1] == "MSA|AA|1"


def test_ack_processing_id_default_p(module):
    raw = OUL.replace(b"|OUL^R24^OUL_R24|1|T|2.6", b"|OUL^R24^OUL_R24|1||2.6")
    msh = module.build_ack_response(raw, INSTRUMENT)[1:].decode().split("\r")[0]
    assert msh.split("|")[10] == "P"


def test_ack_pesan_tak_terbaca_kosong(module):
    assert module.build_ack_response(b"\x0bPID||T45\r\x1c\x0d", INSTRUMENT) == b""


def test_ack_atas_ack_tidak_dibalas_cegah_loop_echo(module, caplog):
    ack_sendiri = module.build_ack_response(OUL, INSTRUMENT)
    with caplog.at_level(logging.INFO):
        assert module.build_ack_response(ack_sendiri, INSTRUMENT) == b""
    assert any("ACK dari alat tidak dibalas" in r.getMessage() for r in caplog.records)


def test_pesan_non_ack_lain_tetap_di_ack(module):
    qc = OUL.replace(b"OUL^R24^OUL_R24", b"QCM^R99^QCM_R99")
    assert b"ACK^R24^ACK" in module.build_ack_response(qc, INSTRUMENT)


# ============================================================
# Filter penyimpanan & bidirectional
# ============================================================

def test_should_store_result_hanya_oul_r24(module):
    assert module.should_store_result({}, OUL) is True
    ack = mllp(["MSH|^~\\&|||||20200305||ACK^R24^ACK|1|P|2.6", "MSA|AA|1"])
    assert module.should_store_result({}, ack) is False
    assert module.should_store_result({}, b"sampah") is False


def test_tidak_ada_query(module):
    assert module.is_enq(OUL) is False
    assert module.handle_enq(OUL, INSTRUMENT)["type"] == "unsupported"


def test_order_dan_query_tidak_didukung(module, caplog):
    with caplog.at_level(logging.WARNING):
        assert module.format_order({"order_id": "X"}, INSTRUMENT) == b""
        assert module.format_query_response({}, INSTRUMENT) == b""
        assert module.format_query_not_found(INSTRUMENT) == b""
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 3


@pytest.mark.parametrize("code, expected", [("AA", "ACK"), ("AE", "NAK"), ("AR", "NAK"), ("XX", "UNKNOWN")])
def test_handle_ack(module, code, expected):
    raw = mllp(["MSH|^~\\&|||||20200305||ACK^R24^ACK|1|P|2.6", f"MSA|{code}|1"])
    assert module.handle_ack(raw) == expected


def test_handle_ack_tanpa_msa(module):
    assert module.handle_ack(b"") == "UNKNOWN"
    assert module.handle_ack(OUL) == "UNKNOWN"


def test_should_store_result_tipe_non_ack_dibuang_dengan_warning(module, caplog):
    qc = OUL.replace(b"OUL^R24^OUL_R24", b"QCM^R99^QCM_R99")
    with caplog.at_level(logging.INFO):
        assert module.should_store_result({}, qc) is False
    rec = [r for r in caplog.records if "tidak disimpan" in r.getMessage()]
    assert rec and rec[0].levelno == logging.WARNING and "QCM^R99" in rec[0].getMessage()


def test_should_store_result_ack_hanya_info(module, caplog):
    ack = mllp(["MSH|^~\\&|||||20200305||ACK^R24^ACK|1|P|2.6", "MSA|AA|1"])
    with caplog.at_level(logging.INFO):
        assert module.should_store_result({}, ack) is False
    rec = [r for r in caplog.records if "tidak disimpan" in r.getMessage()]
    assert rec and all(r.levelno == logging.INFO for r in rec)


# ============================================================
# Integrasi ResultReceiver
# ============================================================

class _FakeConfig:
    id = 12
    name = "Boditech AFIAS-1"
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
def receiver_logger_bersih():
    # ResultReceiver meng-cache handler file di logger midlab.tcp_socket.12;
    # buang setelah test agar tidak bocor ke test lain.
    yield
    lg = logging.getLogger("midlab.tcp_socket.12")
    for h in list(lg.handlers):
        h.close()
        lg.removeHandler(h)


async def test_receiver_pesan_terpotong_disimpan_dan_di_ack_sekali(
    monkeypatch, module, receiver_logger_bersih
):
    saved = []
    monkeypatch.setattr(
        "services.tcp_socket.receiver.save_result",
        lambda inst_id, proto, raw_hex, result: saved.append(result) or len(saved),
    )
    receiver = ResultReceiver(_FakeConfig(), module)
    writer = _FakeWriter()

    potong = len(OUL) // 2
    assert await receiver.handle_data(OUL[:potong], writer) is False
    assert saved == [] and writer.written == b""

    await receiver.handle_data(OUL[potong:], writer)

    assert len(saved) == 1
    assert saved[0]["specimen"]["sample_id"] == "T45"
    assert writer.written.count(b"ACK^R24^ACK") == 1


async def test_receiver_ack_dari_alat_tidak_dibalas_dan_tidak_disimpan(
    monkeypatch, module, receiver_logger_bersih
):
    saved = []
    monkeypatch.setattr(
        "services.tcp_socket.receiver.save_result",
        lambda *a: saved.append(a) or 1,
    )
    receiver = ResultReceiver(_FakeConfig(), module)
    writer = _FakeWriter()

    await receiver.handle_data(module.build_ack_response(OUL, INSTRUMENT), writer)

    assert saved == []
    assert writer.written == b""


async def test_receiver_hl7_polos_memicu_warning(
    monkeypatch, module, caplog, receiver_logger_bersih
):
    saved = []
    monkeypatch.setattr(
        "services.tcp_socket.receiver.save_result",
        lambda *a: saved.append(a) or 1,
    )
    receiver = ResultReceiver(_FakeConfig(), module)
    raw = "".join(s + "\r" for s in SEGMENTS).encode()

    with caplog.at_level(logging.WARNING):
        await receiver.handle_data(raw, _FakeWriter())

    assert saved == []
    pesan = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("tanpa MLLP" in m and f"({len(raw)} byte)" in m and "MSH|" in m for m in pesan)


async def test_receiver_sampah_sebelum_vt_memicu_warning_tapi_pesan_tetap_masuk(
    monkeypatch, module, caplog, receiver_logger_bersih
):
    saved = []
    monkeypatch.setattr(
        "services.tcp_socket.receiver.save_result",
        lambda *a: saved.append(a) or 1,
    )
    receiver = ResultReceiver(_FakeConfig(), module)

    with caplog.at_level(logging.WARNING):
        await receiver.handle_data(b"NOISE" + OUL, _FakeWriter())

    assert len(saved) == 1
    assert any("tanpa MLLP" in r.getMessage() and "NOISE" in r.getMessage()
               for r in caplog.records if r.levelno == logging.WARNING)


async def test_receiver_mllp_normal_tanpa_warning(
    monkeypatch, module, caplog, receiver_logger_bersih
):
    monkeypatch.setattr("services.tcp_socket.receiver.save_result", lambda *a: 1)
    receiver = ResultReceiver(_FakeConfig(), module)

    with caplog.at_level(logging.WARNING):
        # CR/LF sisa di antara pesan bukan data — tidak boleh memicu WARNING.
        await receiver.handle_data(OUL + b"\r\n" + OUL, _FakeWriter())

    assert not [r for r in caplog.records
                if r.levelno >= logging.WARNING and "tanpa MLLP" in r.getMessage()]


# ============================================================
# Payload ke EazyLab (/results)
# ============================================================

def test_payload_results_dari_hasil_afias(module):
    row = TblResult()
    row.id = 1
    row.result_json = module.parse(OUL, INSTRUMENT)
    row.received_at = datetime(2020, 3, 5, 15, 15, 53)
    inst = MagicMock()
    inst.id = 12
    inst.lis_instrument_id = "INST-AFIAS1"

    payload = build_mid_payload(row, inst)

    assert payload["protocol"] == "HL7"
    assert payload["specimen"]["sample_id"] == "T45"
    assert payload["specimen"]["collected_at"] == "2020-03-05T17:15:53.205+09:00"
    assert payload["message_datetime"] == "2020-03-05T17:15:53.205+09:00"
    assert [(r["test_code"], r["value"], r["status"]) for r in payload["results"]] == [
        ("T4", "33.26", "final")
    ]


# ============================================================
# End-to-end TCPSocketService — connection server & client
#
# Socket localhost sungguhan. start() tidak dipanggil karena memasang
# signal handler SIGTERM/SIGINT pada proses pytest; _run_*_mode() dipanggil
# langsung dengan _running = True, lalu stop().
# ============================================================

def _port_bebas() -> int:
    with socketlib.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _config(connection: str, port: int) -> InstrumentConfig:
    return InstrumentConfig(
        id=12, name="Boditech AFIAS-1", ip_address="127.0.0.1", port=port,
        protocol=PROTOCOL, mode="unidirectional", bidir_mode=None,
        broadcast_interval=30, connection=connection, is_active=True,
    )


async def _baca_satu_pesan(reader) -> bytes:
    data = b""
    while b"\x1c\x0d" not in data:
        chunk = await reader.read(4096)
        if not chunk:
            break
        data += chunk
    return data


@pytest.fixture
def e2e_tersimpan(monkeypatch, receiver_logger_bersih):
    saved = []
    monkeypatch.setattr(
        "services.tcp_socket.receiver.save_result",
        lambda inst_id, proto, raw_hex, result: saved.append(result) or len(saved),
    )
    # Status koneksi ditulis ke tbl_lis_event_queue — jangan sentuh DB.
    monkeypatch.setattr("services.tcp_socket.service.enqueue_lis_event", lambda **k: None)
    return saved


async def test_e2e_connection_server_alat_yang_konek(e2e_tersimpan):
    port = _port_bebas()
    svc = TCPSocketService(_config("server", port))
    svc._running = True
    task = asyncio.create_task(svc._run_server_mode())
    try:
        for _ in range(100):
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                break
            except OSError:
                await asyncio.sleep(0.05)
        else:
            pytest.fail("TCPSocketService tidak listen")

        writer.write(OUL)
        await writer.drain()
        ack = await asyncio.wait_for(_baca_satu_pesan(reader), 5)
        writer.close()
    finally:
        await svc.stop()
        await asyncio.wait_for(task, 5)

    assert b"ACK^R24^ACK" in ack and b"MSA|AA|1" in ack
    assert [r["specimen"]["sample_id"] for r in e2e_tersimpan] == ["T45"]


async def test_e2e_connection_client_midlab_yang_konek(e2e_tersimpan):
    # Test berperan sebagai alat / converter RS232-to-LAN yang listen.
    ack_diterima = asyncio.get_running_loop().create_future()

    async def alat(reader, writer):
        writer.write(OUL)
        await writer.drain()
        ack_diterima.set_result(await _baca_satu_pesan(reader))
        # Python 3.12: server.wait_closed() menunggu semua koneksi tertutup;
        # tanpa ini teardown menggantung selamanya.
        writer.close()

    server = await asyncio.start_server(alat, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    svc = TCPSocketService(_config("client", port))
    svc._running = True
    task = asyncio.create_task(svc._run_client_mode())
    try:
        ack = await asyncio.wait_for(ack_diterima, 5)
    finally:
        await svc.stop()
        await asyncio.wait_for(task, 10)
        server.close()
        await server.wait_closed()

    assert b"ACK^R24^ACK" in ack and b"MSA|AA|1" in ack
    assert [r["specimen"]["sample_id"] for r in e2e_tersimpan] == ["T45"]


def test_driver_tidak_mengunci_arah_koneksi(module):
    # Deklarasi transport ini mengubah perilaku koneksi TCPSocketService;
    # AFIAS-1 tidak memerlukannya di mode mana pun.
    assert not getattr(module, "PING_BEFORE_RECONNECT", False)
    assert not getattr(module, "IDLE_RX_TIMEOUT", 0)
