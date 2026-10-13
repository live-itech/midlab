"""
tests/test_qc_eazylab.py — QC BS-200E → POST /qc-results (kontrak EazyLab)

Kontrak dari tim EazyLab:
  - QC lewat endpoint khusus /api/v1/instrument/qc-results; QC yang masuk ke
    /results dianggap hasil pasien tanpa order dan tidak masuk grafik.
  - Wajib: message_id, qc.lot_number, qc.level (1/2/3), run_at, results[].
  - Dedup per kontrol+parameter berdasar message_id → harus deterministik.
"""
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from lib.db import TblResult
from lib.lis_client import LisApiError
from protocols.mindray_bs200e.module import MindrayBS200EModule
from services.lis_bridge.result_pusher import (
    ResultPusher, QcPayloadError, build_qc_payload, is_qc_row, summarize_qc_response,
)


INSTRUMENT = {"id": 1, "name": "Mindray BS200E", "protocol": "HL7_MINDRAY_BS200E"}

# Verbatim tcp_1.comm.log 2026-07-21 (control ID 7).
BS200E_QC = (
    b"\x0b"
    b"MSH|^~\\&|Mindray|BS-200E|||20260721131215||ORU^R01|7|P|2.3.1||||2||ASCII|||\r"
    b"OBR|1|22|ALT|Mindray^BS-200E||20260721125412|||||||con min|Lot No.360617"
    b"|20270514000000||M|56.400000|2.800000|54.728437|U/L|||||||||||||||||||||||||||\r"
    b"\x1c\r"
)


def _row(result_json, **kw):
    r = TblResult()
    r.id = kw.get("id", 10)
    r.instrument_id = 1
    r.result_json = result_json
    r.received_at = datetime(2026, 7, 21, 13, 12, 15)
    r.retry_count = kw.get("retry_count", 0)
    return r


def _instrument():
    inst = MagicMock()
    inst.id = 1
    inst.lis_instrument_id = "INST-BS200E"
    return inst


@pytest.fixture
def parsed():
    return MindrayBS200EModule().parse(BS200E_QC, INSTRUMENT)


# ------------------------------------------------------------
# Driver
# ------------------------------------------------------------

def test_driver_qc_memakai_nama_tes_sebagai_kode(parsed):
    # OBR-2 "22" hanya nomor urut; pasien memakai "ALT".
    assert parsed["results"][0]["test_code"] == "ALT"


@pytest.mark.parametrize("nama,kode", [("T-Bill", "T-BIL"), ("D-Bill", "D-BIL"), ("GLUC", "GLUC")])
def test_driver_qc_bilirubin_memakai_kode_pasien(nama, kode):
    # Pesan pasien BS-200E: OBX-3 "T-BIL" / OBX-4 "T-Bill"; pesan QC hanya
    # membawa nama "T-Bill" → harus dikirim dengan kode pasien.
    raw = BS200E_QC.replace(b"|22|ALT|", b"|30|" + nama.encode() + b"|")
    hasil = MindrayBS200EModule().parse(raw, INSTRUMENT)
    assert hasil["results"][0]["test_code"] == kode
    assert hasil["results"][0]["test_name"] == nama
    assert list(hasil["qc"]["targets"]) == [kode]


def test_driver_menghasilkan_blok_qc(parsed):
    assert parsed["qc"] == {
        "lot_number": "360617",
        "level": "M",
        "control_name": "con min",
        "expiry_date": "2027-05-14",
        "run_at": "20260721125412",
        "targets": {"ALT": {"mean": "56.400000", "sd": "2.800000"}},
    }


def test_hasil_pasien_tanpa_blok_qc():
    from tests.test_mindray_bs200e import ORU_SAMPLE
    hasil = MindrayBS200EModule().parse(ORU_SAMPLE, INSTRUMENT)
    assert "qc" not in hasil
    assert not is_qc_row(_row(hasil))


# ------------------------------------------------------------
# Payload
# ------------------------------------------------------------

def test_payload_sesuai_contoh_eazylab(parsed):
    payload = build_qc_payload(_row(parsed), _instrument())
    assert payload["message_id"] == "MID1-360617-L2-20260721T125412"
    assert payload["run_at"] == "2026-07-21T12:54:12+07:00"
    assert payload["message_datetime"] == "2026-07-21T13:12:15+07:00"
    assert payload["protocol"] == "HL7"
    assert payload["qc"] == {
        "lot_number": "360617", "level": "2",
        "control_name": "con min", "expiry_date": "2027-05-14",
    }
    assert payload["results"] == [{
        "test_code": "ALT", "test_name": "ALT", "value": "54.728437", "unit": "U/L",
        "flag": "M", "instrument_target_mean": 56.4, "instrument_target_sd": 2.8,
    }]
    # Tidak ada blok pasien di kontrak QC.
    assert not {"patient", "specimen", "order", "instrument_id"} & payload.keys()


def test_message_id_stabil_untuk_run_yang_sama(parsed):
    # Alat retransmit / tombol kirim ulang → id sama → EazyLab balas duplicate.
    a = build_qc_payload(_row(parsed, id=1), _instrument())
    b = build_qc_payload(_row(MindrayBS200EModule().parse(BS200E_QC, INSTRUMENT), id=2), _instrument())
    assert a["message_id"] == b["message_id"]


def test_message_id_beda_untuk_run_lain(parsed):
    lain = BS200E_QC.replace(b"20260721125412", b"20260722081500")
    p2 = MindrayBS200EModule().parse(lain, INSTRUMENT)
    a = build_qc_payload(_row(parsed), _instrument())
    b = build_qc_payload(_row(p2), _instrument())
    assert a["message_id"] != b["message_id"]


@pytest.mark.parametrize("raw,expected", [("L", "1"), ("M", "2"), ("H", "3"), ("2", "2")])
def test_level_dikonversi(parsed, raw, expected):
    parsed["qc"]["level"] = raw
    assert build_qc_payload(_row(parsed), _instrument())["qc"]["level"] == expected


@pytest.mark.parametrize("field", ["lot_number", "level", "run_at"])
def test_field_wajib_kosong_ditolak(parsed, field):
    parsed["qc"][field] = ""
    with pytest.raises(QcPayloadError, match=field):
        build_qc_payload(_row(parsed), _instrument())


def test_qc_tanpa_blok_ditolak():
    rj = {"specimen": {"sample_type": "qc"}, "results": [{"test_code": "WBC", "value": "1"}]}
    assert is_qc_row(_row(rj))
    with pytest.raises(QcPayloadError):
        build_qc_payload(_row(rj), _instrument())


# ------------------------------------------------------------
# Respons
# ------------------------------------------------------------

RESP_201 = {
    "success": True,
    "data": {
        "recorded_count": 1, "duplicate_count": 0, "unmapped_count": 1, "rejected_count": 1,
        "results": [
            {"test_code": "ALT", "status": "recorded", "z_score": 3.4,
             "westgard_flags": ["1-3s"], "rejected": True},
            {"test_code": "T-Bill", "status": "unmapped", "reason": "unmapped_parameter"},
        ],
    },
}


def test_ringkasan_respons():
    summary, issues = summarize_qc_response(RESP_201)
    assert summary == "recorded=1 duplicate=0 unmapped=1 rejected=1"
    assert issues == [
        "ALT: ditolak Westgard (1-3s)",
        "T-Bill: unmapped (unmapped_parameter)",
    ]


# ------------------------------------------------------------
# ResultPusher
# ------------------------------------------------------------

def _pusher(rows, client):
    update = MagicMock()
    pusher = ResultPusher(
        instrument=_instrument(), client=client,
        poll_results_fn=MagicMock(return_value=rows),
        update_status_fn=update, retry_max=3,
    )
    return pusher, update


async def test_qc_dikirim_ke_qc_results_bukan_results(parsed):
    client = AsyncMock()
    client.post_qc_results = AsyncMock(return_value=(201, {"data": {"recorded_count": 1}}))
    pusher, update = _pusher([_row(parsed)], client)
    await pusher.run_once()
    client.post_qc_results.assert_awaited_once()
    client.post_results.assert_not_awaited()
    update.assert_called_with(10, "sent", None)


async def test_qc_unmapped_tetap_sent_dengan_catatan(parsed):
    client = AsyncMock()
    client.post_qc_results = AsyncMock(return_value=(201, RESP_201))
    pusher, update = _pusher([_row(parsed)], client)
    await pusher.run_once()
    args = update.call_args.args
    assert args[1] == "sent"
    assert "T-Bill: unmapped (unmapped_parameter)" in args[2]


async def test_qc_422_failed_permanen(parsed):
    client = AsyncMock()
    client.post_qc_results = AsyncMock(side_effect=LisApiError(
        status=422, message="qc.lot_number required", retryable=False,
    ))
    pusher, update = _pusher([_row(parsed)], client)
    await pusher.run_once()
    assert update.call_args.args[1] == "failed"


async def test_qc_5xx_retry(parsed):
    client = AsyncMock()
    client.post_qc_results = AsyncMock(side_effect=LisApiError(
        status=503, message="down", retryable=True,
    ))
    pusher, update = _pusher([_row(parsed)], client)
    await pusher.run_once()
    assert update.call_args.args[1] == "pending"
    assert update.call_args.kwargs == {"increment_retry": True}


async def test_qc_tanpa_data_kontrol_tidak_dikirim():
    rj = {"specimen": {"sample_type": "qc"}, "results": [{"test_code": "WBC", "value": "1"}]}
    client = AsyncMock()
    pusher, update = _pusher([_row(rj)], client)
    await pusher.run_once()
    client.post_qc_results.assert_not_awaited()
    client.post_results.assert_not_awaited()
    args = update.call_args.args
    assert args[1] == "failed"
    assert args[2].startswith("QC:")
