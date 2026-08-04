"""
Test pemilihan order pada QueryHandler, termasuk pengiriman ulang (re-serve).

Latar: BC-5150 menanyakan sample ID yang sama berkali-kali kalau operator
mengulang run — terekam 4 Agu 2026 pada sampel 9097415, 11 kali dalam 20 menit.
Semua pertanyaan sesudah yang pertama dibalas "tidak ada order" karena order
sudah berstatus `sent`, sehingga operator harus mengetik ulang demografi
pasien secara manual.
"""

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest

from lib.timeutil import now_naive
from services.tcp_socket.query_handler import (
    QueryHandler, REDELIVERY_WINDOW_HOURS, _select_order,
)


def _order(order_id, sample_id, status, patient_id="P1", age_hours=0):
    return SimpleNamespace(
        id=order_id,
        instrument_status=status,
        created_at=now_naive() - timedelta(hours=age_hours),
        order_json={
            "specimen": {"sample_id": sample_id},
            "patient": {"patient_id": patient_id},
        },
    )


def _cutoff():
    return now_naive() - timedelta(hours=REDELIVERY_WINDOW_HOURS)


# ============================================================
# Pemilihan order
# ============================================================

def test_order_pending_dilayani_seperti_biasa():
    orders = [_order(1, "9097415", "pending")]
    assert _select_order(orders, "9097415", "", _cutoff()).id == 1


def test_order_yang_sudah_terkirim_dilayani_ulang():
    """
    Ini inti perbaikannya: alat bertanya lagi untuk sampel yang sama, dan
    jawaban "tidak ada order" memaksa operator mengetik manual.
    """
    orders = [_order(1, "9097415", "sent")]
    assert _select_order(orders, "9097415", "", _cutoff()).id == 1


def test_order_gagal_kirim_juga_dilayani_ulang():
    """Order `failed` justru paling butuh kesempatan kedua."""
    orders = [_order(1, "9097415", "failed")]
    assert _select_order(orders, "9097415", "", _cutoff()).id == 1


def test_order_pending_menang_atas_order_yang_sudah_terkirim():
    """Sample ID dipakai ulang LIS untuk order baru — yang baru yang benar."""
    orders = [_order(1, "9097415", "sent"), _order(2, "9097415", "pending")]
    assert _select_order(orders, "9097415", "", _cutoff()).id == 2


def test_pengiriman_ulang_memakai_order_terbaru():
    orders = [
        _order(1, "9097415", "sent", age_hours=6),
        _order(2, "9097415", "sent", age_hours=1),
    ]
    assert _select_order(orders, "9097415", "", _cutoff()).id == 2


def test_pengiriman_ulang_tidak_jatuh_ke_patient_id():
    """
    Fallback patient_id hanya boleh untuk order yang belum pernah terkirim.
    Kalau tidak, alat yang menanyakan sampel kedua pasien yang sama akan
    dikirimi worklist sampel pertamanya — demografi benar, sampel salah.
    """
    orders = [_order(1, "9097415", "sent", patient_id="RM2600032")]
    assert _select_order(orders, "5439069", "RM2600032", _cutoff()) is None


def test_fallback_patient_id_tetap_jalan_untuk_order_pending():
    orders = [_order(1, "9097415", "pending", patient_id="RM2600032")]
    assert _select_order(orders, "5439069", "RM2600032", _cutoff()).id == 1


def test_order_terkirim_di_luar_jendela_tidak_dilayani_ulang():
    """
    Batas waktu menjaga dari sample ID yang didaur ulang LIS: setelah lewat
    jendela, pertanyaan alat kembali dijawab not-found seperti dulu.
    """
    orders = [_order(1, "9097415", "sent", age_hours=REDELIVERY_WINDOW_HOURS + 1)]
    assert _select_order(orders, "9097415", "", _cutoff()) is None


def test_order_pending_lama_tetap_dilayani():
    """Jendela waktu hanya berlaku untuk pengiriman ulang, bukan order baru."""
    orders = [_order(1, "9097415", "pending", age_hours=200)]
    assert _select_order(orders, "9097415", "", _cutoff()).id == 1


def test_sample_id_tidak_cocok_tetap_not_found():
    orders = [_order(1, "9097415", "sent")]
    assert _select_order(orders, "1234567", "", _cutoff()) is None


# ============================================================
# Status setelah pengiriman ulang
# ============================================================

class _Cfg:
    id = 1
    name = "BC-5150"
    protocol = "HL7_MINDRAY_BC5150"

    def to_dict(self):
        return {"id": self.id, "name": self.name, "protocol": self.protocol}


class _Protocol:
    def handle_enq(self, raw, instrument):
        return {"type": "query", "sample_id": "9097415", "patient_id": ""}


def _handler(order, send_ok, updates):
    handler = QueryHandler(_Cfg(), _Protocol(), None, None, asyncio.Lock())

    async def _lookup(sample_id, patient_id):
        return order

    async def _send(order_json, instrument_dict, query_msh=None):
        return send_ok

    async def _update(order_id, success):
        updates.append((order_id, success))

    handler._lookup_order = _lookup
    handler._send_query_response = _send
    handler._update_order_result = _update
    return handler


def test_pengiriman_ulang_yang_gagal_tidak_menurunkan_status_sent():
    """
    Order yang sudah pernah sampai ke alat tidak boleh berubah jadi `failed`
    hanya karena pengiriman ulangnya tidak di-ACK — pengiriman pertamanya
    tetap sah, dan Order Monitor akan menampilkan alarm palsu.
    """
    updates = []
    handler = _handler(_order(7, "9097415", "sent"), send_ok=False, updates=updates)

    assert asyncio.run(handler.handle_query(b"query")) is True
    assert updates == []


def test_pengiriman_ulang_yang_berhasil_tidak_menulis_ulang_status():
    updates = []
    handler = _handler(_order(7, "9097415", "sent"), send_ok=True, updates=updates)

    assert asyncio.run(handler.handle_query(b"query")) is True
    assert updates == []


@pytest.mark.parametrize("status", ["pending", "failed"])
def test_order_yang_belum_sampai_tetap_diperbarui_statusnya(status):
    updates = []
    handler = _handler(_order(7, "9097415", status), send_ok=True, updates=updates)

    assert asyncio.run(handler.handle_query(b"query")) is True
    assert updates == [(7, True)]
