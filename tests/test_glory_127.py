"""
Test driver CUSTOM_GLORY_127 (Glory 127 chemistry analyzer).

Kedua pesan contoh disalin **verbatim dari hasil tapping lapangan**
(Hercules, serial COM5 115200 8N1, 2026-08-04). Setiap record berpasangan
dengan foto layar Result List alat untuk record yang sama, sehingga pemetaan
field terkonfirmasi ke label alat — bukan tebakan:

  SGPT      → Program Name SGPT, Nr 510, RATE -0.0069, CONC 11.0388 U/L,
              Normal 0.0000–40.0000, tanpa flag
  LDL CHOL  → Program Name LDL CHOL, Nr 506, OD 0.7136, CONC 189.0841 mg/dL,
              Normal 0.0000–130.0000, flag H

Perhatikan layar menampilkan 4 desimal sedangkan kabel 3 desimal; yang
dikirim ke LIS adalah angka kabel.

Lihat docs/superpowers/specs/2026-08-04-glory-127-design.md
"""

import asyncio
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from lib.db import TblResult
from protocols.base import _PROTOCOL_REGISTRY, is_mllp_protocol, load_module
from protocols.glory_127.module import Glory127Module
from services.lis_bridge.result_pusher import build_mid_payload
from services.tcp_socket.receiver import ResultReceiver


PROTOCOL = "CUSTOM_GLORY_127"

# Hasil kinetik (layar menulis "RATE"), di dalam rentang normal
SGPT = (
    b"<<<SGPT,510,,2000-01-01 00:06:57,-0.007,0.000,11.039,U/L,"
    b"0.0000,40.0000,7.9000,500.0000,01,00,GLORY 127,>>>"
)

# Hasil endpoint (layar menulis "OD"), di atas batas atas → layar tampilkan H
LDL = (
    b"<<<LDL CHOL,506,,2000-01-01 00:45:13,0.714,0.000,189.084,mg/dL,"
    b"0.0000,130.0000,0.0000,400.0000,00,00,GLORY 127,>>>"
)

INSTRUMENT = {"id": 7, "name": "Glory 127", "protocol": PROTOCOL}

# Jam server palsu — alat masih di 2000-01-01, jadi keduanya harus terpisah jelas
NOW = "2026-08-04T12:31:07+07:00"


@pytest.fixture
def mod():
    return Glory127Module(now=lambda: NOW)


# ============================================================
# Parsing — record asli dari tapping
# ============================================================

def test_sgpt_menghasilkan_satu_hasil_klinis(mod):
    out = mod.parse(SGPT, INSTRUMENT)

    assert len(out["results"]) == 1
    r = out["results"][0]
    assert r["test_code"] == "SGPT"
    assert r["value"] == "11.039"
    assert r["unit"] == "U/L"


def test_sample_id_diambil_dari_nr(mod):
    """Dikonfirmasi user: lab memakai Nr, bukan Ref (Ref kosong di lapangan)."""
    assert mod.parse(SGPT, INSTRUMENT)["specimen"]["sample_id"] == "510"
    assert mod.parse(LDL, INSTRUMENT)["specimen"]["sample_id"] == "506"


def test_nama_tes_bespasi_utuh(mod):
    """`LDL CHOL` mengandung spasi — jangan terpotong di delimiter."""
    r = mod.parse(LDL, INSTRUMENT)["results"][0]
    assert r["test_code"] == "LDL CHOL"
    assert r["value"] == "189.084"
    assert r["unit"] == "mg/dL"


def test_value_selalu_string(mod):
    """docs/API.md:265 — value selalu string, supaya 'POS'/'>10000' muat."""
    assert isinstance(mod.parse(SGPT, INSTRUMENT)["results"][0]["value"], str)


def test_protocol_dan_instrument_id_ikut_instrumennya(mod):
    out = mod.parse(SGPT, INSTRUMENT)
    assert out["protocol"] == PROTOCOL
    assert out["instrument_id"] == 7


def test_pasien_kosong_karena_alat_tidak_mengirimnya(mod):
    """Mengarang identitas pasien lebih berbahaya daripada membiarkan kosong."""
    p = mod.parse(SGPT, INSTRUMENT)["patient"]
    assert p["patient_id"] == ""
    assert p["name"] == ""


# ============================================================
# Flag — diturunkan, karena tidak ada di kabel
# ============================================================

def test_flag_h_diturunkan_saat_di_atas_batas_atas(mod):
    """189.084 > 130.0000 — foto layar alat menampilkan H."""
    assert mod.parse(LDL, INSTRUMENT)["results"][0]["flag"] == "H"


def test_flag_n_saat_di_dalam_rentang(mod):
    """11.039 ada di 0.0000–40.0000 — layar tidak menampilkan flag."""
    assert mod.parse(SGPT, INSTRUMENT)["results"][0]["flag"] == "N"


def test_flag_l_saat_di_bawah_batas_bawah(mod):
    raw = SGPT.replace(b"11.039", b"-5.000").replace(b"0.0000,40.0000", b"1.0000,40.0000")
    assert mod.parse(raw, INSTRUMENT)["results"][0]["flag"] == "L"


def test_flag_kosong_saat_rentang_tidak_dipakai(mod):
    """Low dan high dua-duanya 0 = rentang tidak diset; jangan mengarang flag."""
    raw = SGPT.replace(b"0.0000,40.0000", b"0.0000,0.0000")
    out = mod.parse(raw, INSTRUMENT)
    assert out["results"][0]["flag"] == ""
    assert out["results"][0]["reference_range"] == ""


# ============================================================
# Reference range
# ============================================================

def test_reference_range_dirapikan(mod):
    """Kontrak mencontohkan '3.5-5.5', bukan '0.0000-40.0000'."""
    assert mod.parse(SGPT, INSTRUMENT)["results"][0]["reference_range"] == "0-40"
    assert mod.parse(LDL, INSTRUMENT)["results"][0]["reference_range"] == "0-130"


def test_reference_range_pertahankan_desimal_bermakna(mod):
    raw = SGPT.replace(b"0.0000,40.0000", b"3.5000,5.5000")
    assert mod.parse(raw, INSTRUMENT)["results"][0]["reference_range"] == "3.5-5.5"


# ============================================================
# Waktu — jam server vs jam alat
# ============================================================

def test_message_datetime_pakai_jam_server(mod):
    """
    docs/API.md:262 mendefinisikannya sebagai saat hasil di-parse MidLab.
    Jam alat masih 2000-01-01, tidak boleh bocor ke sini.
    """
    out = mod.parse(SGPT, INSTRUMENT)
    assert out["message_datetime"] == NOW
    assert "2000" not in out["message_datetime"]


def test_jam_alat_tetap_tersimpan(mod):
    """Tidak ada informasi yang hilang walau tidak dipakai sebagai timestamp."""
    meta = mod.parse(SGPT, INSTRUMENT)["instrument_meta"]
    assert meta["instrument_time"] == "2000-01-01 00:06:57"


def test_collected_at_kosong(mod):
    """Jam flebotomi tidak diketahui — pencocokan order lewat sample_id."""
    assert mod.parse(SGPT, INSTRUMENT)["specimen"]["collected_at"] == ""


# ============================================================
# instrument_meta — field yang belum diketahui semantiknya
# ============================================================

def test_field_tak_dikenal_disimpan_mentah(mod):
    """
    Field 5/10/11/13 tidak muncul di layar mana pun dan tidak ada dokumen
    vendor. Disimpan apa adanya supaya bisa dianalisis nanti, tanpa ditebak.
    """
    meta = mod.parse(SGPT, INSTRUMENT)["instrument_meta"]
    assert meta["unknown"] == {
        "f5": "0.000", "f10": "7.9000", "f11": "500.0000", "f13": "00",
    }


def test_meta_menyimpan_konteks_alat(mod):
    meta = mod.parse(SGPT, INSTRUMENT)["instrument_meta"]
    assert meta["nr"] == "510"
    assert meta["ref"] == ""
    assert meta["rate_or_od"] == "-0.007"
    assert meta["mode"] == "01"
    assert meta["device_name"] == "GLORY 127"


# ============================================================
# Error handling
# ============================================================

def test_parse_bersih_tidak_meninggalkan_error(mod):
    assert mod.parse(SGPT, INSTRUMENT)["parse_errors"] == []


def test_jumlah_field_kurang_tetap_disimpan_dengan_catatan(mod):
    """Selama program name + CONC terbaca, hasilnya masih berguna."""
    raw = b"<<<SGPT,510,,2000-01-01 00:06:57,-0.007,0.000,11.039,U/L,0.0000,40.0000,>>>"
    out = mod.parse(raw, INSTRUMENT)

    assert out["parse_errors"] != []
    assert out["results"][0]["value"] == "11.039"
    assert mod.should_store_result(out, raw) is True


def test_conc_bukan_angka_tidak_disimpan(mod):
    raw = SGPT.replace(b"11.039", b"----")
    out = mod.parse(raw, INSTRUMENT)

    assert mod.should_store_result(out, raw) is False


def test_program_name_kosong_tidak_disimpan(mod):
    raw = SGPT.replace(b"<<<SGPT,", b"<<<,")
    out = mod.parse(raw, INSTRUMENT)

    assert mod.should_store_result(out, raw) is False


def test_hasil_normal_disimpan(mod):
    out = mod.parse(SGPT, INSTRUMENT)
    assert mod.should_store_result(out, SGPT) is True


# ============================================================
# Identitas module & mode
# ============================================================

def test_identitas_module(mod):
    assert mod.PROTOCOL_NAME == PROTOCOL
    assert mod.VERSION


def test_delimiter_diumumkan_ke_receiver(mod):
    """Receiver memakai atribut ini untuk memotong frame."""
    assert mod.FRAME_START == b"<<<"
    assert mod.FRAME_END == b">>>"


def test_unidirectional_tidak_menjawab_enq(mod):
    """Alat hanya mendorong hasil; tidak ada jalur query."""
    assert mod.is_enq(SGPT) is False


# ============================================================
# Registrasi di loader
# ============================================================

def test_terdaftar_di_registry():
    assert _PROTOCOL_REGISTRY[PROTOCOL] == "protocols.glory_127.module"


def test_bisa_di_load_dynamic():
    assert isinstance(load_module(PROTOCOL), Glory127Module)


def test_bukan_protokol_mllp():
    """
    Bukan HL7 dan bukan ASTM → receiver harus menjatuhkannya ke cabang
    generic, bukan menunggu <VT>...<FS><CR> atau handshake ENQ/ACK.
    """
    assert is_mllp_protocol(PROTOCOL) is False


# ============================================================
# Kontrak EazyApp
# ============================================================

def _row(result_json):
    row = TblResult()
    row.id = 9
    row.instrument_id = 7
    row.result_json = result_json
    row.received_at = datetime(2026, 8, 4, 12, 31, 7)
    row.retry_count = 0
    return row


def _inst():
    m = MagicMock()
    m.id = 7
    m.lis_instrument_id = "INST-GLORY127"
    return m


def test_protocol_dinormalkan_ke_keluarga_astm(mod):
    """
    docs/API.md:261 hanya mengenal ASTM/HL7/COBAS_C111. Tanpa entry eksplisit,
    'CUSTOM_GLORY_127' diteruskan apa adanya — persis pola yang bikin 44 hasil
    F2400 kena 422 dan mandek permanen.
    """
    payload = build_mid_payload(_row(mod.parse(SGPT, INSTRUMENT)), _inst())
    assert payload["protocol"] == "ASTM"


def test_instrument_meta_tidak_bocor_ke_lis(mod):
    """Kunci top-level di luar kontrak berisiko ditolak EazyApp."""
    parsed = mod.parse(SGPT, INSTRUMENT)
    assert "instrument_meta" in parsed  # tersimpan di tbl_result

    payload = build_mid_payload(_row(parsed), _inst())
    assert "instrument_meta" not in payload  # tapi tidak dikirim


def test_payload_lis_memuat_seluruh_field_kontrak(mod):
    """Bentuk minimal yang dijanjikan docs/API.md §2.3."""
    payload = build_mid_payload(_row(mod.parse(LDL, INSTRUMENT)), _inst())

    for key in ("mid_version", "instrument_id", "protocol", "message_id",
                "message_datetime", "patient", "specimen", "order",
                "results", "parse_errors"):
        assert key in payload, f"field kontrak '{key}' hilang"

    r = payload["results"][0]
    assert r["test_code"] == "LDL CHOL"
    assert r["value"] == "189.084"
    assert r["unit"] == "mg/dL"
    assert r["reference_range"] == "0-130"
    assert r["flag"] == "H"
    assert r["status"] == "final"  # 'F' dipetakan oleh _STATUS_MAP


# ============================================================
# Framing di ResultReceiver
#
# Converter RS232-to-LAN mem-flush menurut timer/ukuran buffer, bukan menurut
# batas pesan — jadi record bisa terbelah antar recv() atau menempel dalam satu
# paket. Frame baru boleh diproses setelah `>>>` diterima.
# ============================================================

class _Cfg:
    id = 7
    name = "Glory 127"
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


@pytest.fixture
def saved(monkeypatch):
    """Rekam pemanggilan save_result: (instrument_id, protocol, raw_hex, result)."""
    rows = []
    monkeypatch.setattr(
        "services.tcp_socket.receiver.save_result",
        lambda *a, **k: rows.append(a) or len(rows),
    )
    return rows


def _receiver():
    return ResultReceiver(_Cfg(), Glory127Module(now=lambda: NOW))


def test_satu_frame_utuh_tersimpan(saved):
    asyncio.run(_receiver().handle_data(SGPT, _Writer()))

    assert len(saved) == 1
    assert saved[0][3]["results"][0]["test_code"] == "SGPT"


def test_raw_data_menyimpan_delimiter(saved):
    """Byte-identik dengan kabel, supaya bisa diaudit ulang belakangan."""
    asyncio.run(_receiver().handle_data(SGPT, _Writer()))

    assert bytes.fromhex(saved[0][2]) == SGPT


def test_alat_tidak_dibalas_apa_apa(saved):
    """Unidirectional: tidak ada ACK/handshake yang diharapkan alat."""
    writer = _Writer()
    asyncio.run(_receiver().handle_data(SGPT, writer))

    assert writer.sent == []


def test_frame_terbelah_menunggu_penutup(saved):
    """Baru diproses setelah `>>>` sampai — jangan proses separuh."""
    receiver = _receiver()
    writer = _Writer()
    potong = len(SGPT) // 2

    asyncio.run(receiver.handle_data(SGPT[:potong], writer))
    assert saved == []

    asyncio.run(receiver.handle_data(SGPT[potong:], writer))
    assert len(saved) == 1
    assert saved[0][3]["results"][0]["value"] == "11.039"


def test_dua_frame_dalam_satu_paket(saved):
    """'Data Sync' mendorong banyak hasil beruntun."""
    asyncio.run(_receiver().handle_data(SGPT + LDL, _Writer()))

    assert len(saved) == 2
    assert [s[3]["results"][0]["test_code"] for s in saved] == ["SGPT", "LDL CHOL"]


def test_frame_tanpa_penutup_tidak_pernah_diproses(saved):
    asyncio.run(_receiver().handle_data(SGPT.replace(b">>>", b""), _Writer()))

    assert saved == []


def test_sampah_sebelum_delimiter_dibuang_dan_dicatat(saved, caplog):
    """
    Byte nyasar sebelum `<<<` berarti pesan sebelumnya terpotong — record ini
    tetap harus terbaca, tapi kejadiannya wajib terlihat.
    """
    with caplog.at_level("WARNING"):
        asyncio.run(_receiver().handle_data(b"\x00sampah" + SGPT, _Writer()))

    assert len(saved) == 1
    assert any(r.levelname == "WARNING" for r in caplog.records)


def test_delimiter_pembuka_yang_ikut_terbelah_tidak_hilang(saved):
    """
    `<<<` tiga byte, jadi bisa terbelah antar recv() — beda dari MLLP yang
    penanda awalnya satu byte. Membuang buffer 'tanpa <<<' secara membabi buta
    akan menelan potongan pembuka ini dan menghilangkan satu hasil.
    """
    receiver = _receiver()
    writer = _Writer()

    asyncio.run(receiver.handle_data(SGPT[:2], writer))   # '<<'
    asyncio.run(receiver.handle_data(SGPT[2:], writer))   # '<SGPT,...>>>'

    assert len(saved) == 1
    assert saved[0][3]["results"][0]["test_code"] == "SGPT"


def test_buffer_tanpa_delimiter_sama_sekali_dicatat(saved, caplog):
    with caplog.at_level("WARNING"):
        asyncio.run(_receiver().handle_data(b"data ngawur tanpa delimiter", _Writer()))

    assert saved == []
    assert any(r.levelname == "WARNING" for r in caplog.records)


def test_pesan_tanpa_hasil_klinis_tidak_masuk_db(saved):
    """CONC '----' = tes gagal; jangan bikin baris sampah yang gagal retry."""
    asyncio.run(_receiver().handle_data(SGPT.replace(b"11.039", b"----"), _Writer()))

    assert saved == []


def test_frame_rusak_tidak_menghentikan_frame_berikutnya(saved):
    """Satu record buruk tidak boleh menjatuhkan sisa batch."""
    rusak = SGPT.replace(b"11.039", b"----")
    asyncio.run(_receiver().handle_data(rusak + LDL, _Writer()))

    assert len(saved) == 1
    assert saved[0][3]["results"][0]["test_code"] == "LDL CHOL"
