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
from protocols.mindray_bc5150.parser import split_abnormal_flag, unescape
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

def test_is_enq_mengenali_orm_sebagai_query(mod):
    """ORM^O01 adalah trigger query alat (dok §4.1.2); ORU bukan."""
    assert mod.is_enq(ORM) is True
    assert mod.is_enq(ORU) is False
    assert mod.is_enq(b"") is False


def test_handle_enq_mengekstrak_sample_id(mod):
    info = mod.handle_enq(ORM, INSTRUMENT)
    assert info["sample_id"] == "raisya"
    assert info["control_id"] == "389"


def test_handle_enq_menyertakan_konteks_msh(mod):
    """
    QueryHandler membaca key `_msh` untuk format_query_response_full().
    Tanpa itu control ID alat tidak terpantul dan alat tidak bisa mencocokkan
    balasan dengan pertanyaannya.
    """
    info = mod.handle_enq(ORM, INSTRUMENT)
    assert info["_msh"]["control_id"] == "389"
    assert info["_msh"]["message_type"] == "ORM^O01"


def test_handle_ack(mod):
    assert mod.handle_ack(ACK_LEGACY) == "ACK"
    assert mod.handle_ack(ORR_LEGACY) == "NAK"        # MSA|AR
    assert mod.handle_ack(b"") == "UNKNOWN"
    assert mod.handle_ack(ORU) == "UNKNOWN"           # tidak ada MSA


# ============================================================
# Bidirectional — ORR^O02 berisi worklist
#
# Acuan: dok "BC-5000 & BC-5150 HL7 Communication Protocol V2.0 EN",
# §4.2.4 (struktur ORR^O02), Tabel 4-8 (ORC), dan contoh §5.5.
# ============================================================

ORDER = {
    "mid_version": "1.0",
    "order_id": "257",
    "instrument_id": 7,
    "request_datetime": "20090203101020",
    "patient": {
        "patient_id": "test1",
        "name": "Tom",
        "dob": "19950804",
        "gender": "Male",
    },
    "specimen": {
        "sample_id": "257",
        "sample_type": "whole blood",
        "priority": "R",
    },
    "tests": [{"test_code": "CBC+5DIFF", "test_name": "Hematologi Lengkap"}],
}


def _segments(message: bytes) -> dict:
    """
    Pecah pesan MLLP jadi {nama segment: list field} untuk assertion.

    Index list = nomor field HL7. Pada MSH, field separator dihitung sebagai
    MSH-1 supaya `msh[9]` benar-benar MSH-9 dan bukan tergeser satu.
    """
    body = message.replace(b"\x0b", b"").replace(b"\x1c\x0d", b"")
    out = {}
    for line in body.decode().split("\r"):
        if not line:
            continue
        if line[:3] == "MSH":
            fields = ["MSH", "|"] + line[4:].split("|")
        else:
            fields = line.split("|")
        out.setdefault(line[:3], []).append(fields)
    return out


@pytest.fixture
def orr(mod):
    mod._builder = MindrayBC5150Builder(now=_fixed_clock("20141105151358"))
    return mod.format_query_response_full(
        ORDER, INSTRUMENT, mod.handle_enq(ORM, INSTRUMENT)["_msh"]
    )


def test_orr_worklist_memakai_tipe_pesan_dan_ack_yang_benar(orr):
    seg = _segments(orr)
    assert seg["MSH"][0][9] == "ORR^O02"
    assert seg["MSA"][0][1] == "AA"


def test_orr_worklist_memantulkan_control_id_alat(orr):
    """MSA-2 harus sama dengan MSH-10 ORM (dok Tabel 4-2)."""
    assert _segments(orr)["MSA"][0][2] == "389"


def test_orc_memakai_af_dengan_sample_id_di_orc2(orr):
    """
    Dok Tabel 4-8: pada ORR, ORC-1 = "AF" dan sample ID pindah ke ORC-2
    (placer order number) — pada ORM dari alat ia ada di ORC-3.
    """
    orc = _segments(orr)["ORC"][0]
    assert orc[1] == "AF"
    assert orc[2] == "257"


def test_obr2_sama_dengan_orc2(orr):
    """
    Dok §5.5: "the OBR-2 field indicates the sample ID, which should be the
    same value as in the ORC-2 field; Otherwise, the message will be regarded
    as incorrect." Ini aturan yang paling mudah dilanggar dan paling fatal.
    """
    seg = _segments(orr)
    assert seg["OBR"][0][2] == seg["ORC"][0][2] == "257"


def test_obr3_dikosongkan_pada_worklist(orr):
    """OBR-3 (filler) hanya terisi pada ORU dari alat, bukan pada ORR."""
    assert _segments(orr)["OBR"][0][3] == ""


def test_obr_membawa_jenis_analisis_dan_seksi_hematologi(orr):
    obr = _segments(orr)["OBR"][0]
    assert obr[4] == "00001^Automated Count^99MRC"
    assert obr[24] == "HM"
    # Dok §5.5 mengisi sampai OBR-32; field kosong di antaranya dipertahankan
    assert len(obr) == 33


def test_orr_membawa_identitas_pasien(orr):
    pid = _segments(orr)["PID"][0]
    assert pid[3] == "test1^^^^MR"
    assert pid[5] == "Tom"
    assert pid[7] == "19950804000000"
    assert pid[8] == "Male"


def test_obx_menerjemahkan_sample_type_jadi_blood_mode(orr):
    obx = {s[3]: s[5] for s in _segments(orr)["OBX"]}
    assert obx["08002^Blood Mode^99MRC"] == "W"


def test_obx_menerjemahkan_tes_jadi_test_mode(orr):
    """Alat hematologi menerima mode pemeriksaan, bukan daftar test code."""
    obx = {s[3]: s[5] for s in _segments(orr)["OBX"]}
    assert obx["08003^Test Mode^99MRC"] == "CBC+5DIFF"


def test_obx_menghitung_umur_dari_tanggal_lahir(orr):
    """Umur dipakai alat untuk memilih reference group (dok §5.8)."""
    seg = _segments(orr)
    age = next(s for s in seg["OBX"] if s[3] == "30525-0^Age^LN")
    assert age[5] == "19"      # lahir 1995-08-04, pesan 2014-11-05
    assert age[6] == "yr"


def test_obx_umur_bayi_dikirim_dalam_bulan(mod):
    """
    Umur di bawah setahun dibulatkan jadi `0 yr` oleh versi sebelumnya, dan
    alat menolaknya ("invalid age") saat operator menekan OK di dialog info
    sampel — sampel tidak bisa dijalankan sampai umur diperbaiki manual.

    `mo` bukan tebakan: alat sendiri mengirimnya di ORU (log 4 Agu 2026,
    sampel 4236130 → `OBX|5|NM|30525-0^Age^LN||4|mo`).
    """
    mod._builder = MindrayBC5150Builder(now=_fixed_clock("20260804153559"))
    order = dict(ORDER, patient=dict(ORDER["patient"], dob="20260401"))
    age = next(s for s in _segments(mod.format_query_response(order, INSTRUMENT))["OBX"]
               if s[3] == "30525-0^Age^LN")
    assert age[5] == "4"
    assert age[6] == "mo"


def test_obx_umur_tidak_dikirim_untuk_bayi_di_bawah_sebulan(mod):
    """
    Neonatus (log 4 Agu 2026, sampel 9097415 — lahir 1 Agu, diperiksa 4 Agu)
    tidak punya umur bulat dalam satuan yang terverifikasi. Satuan hari/jam
    belum pernah terlihat dikirim alat, jadi OBX umur dilewati saja dan alat
    memakai PID-7 — lebih aman daripada mengirim `0 yr` yang bikin sampel
    tidak bisa dijalankan.
    """
    mod._builder = MindrayBC5150Builder(now=_fixed_clock("20260804153559"))
    order = dict(ORDER, patient=dict(ORDER["patient"], dob="20260801"))
    out = mod.format_query_response(order, INSTRUMENT)
    assert b"30525-0^Age^LN" not in out
    # Tanggal lahir tetap dikirim: alat masih bisa menurunkan umurnya sendiri.
    assert _segments(out)["PID"][0][7] == "20260801000000"


def test_obx_umur_tidak_pernah_bernilai_nol(mod):
    """
    Nol adalah satu-satunya nilai yang pasti ditolak alat, berapa pun
    satuannya — kunci lewat rentang tanggal, bukan satu contoh.
    """
    mod._builder = MindrayBC5150Builder(now=_fixed_clock("20260804153559"))
    for dob in ("20260804", "20260803", "20260715", "20260705", "20260201"):
        order = dict(ORDER, patient=dict(ORDER["patient"], dob=dob))
        seg = _segments(mod.format_query_response(order, INSTRUMENT))
        for obx in seg["OBX"]:
            if obx[3] == "30525-0^Age^LN":
                assert obx[5] not in ("", "0"), f"umur nol terkirim untuk dob={dob}"


def test_obx_umur_bulan_dihitung_penuh(mod):
    """Ulang tahun bulanan yang belum lewat tidak boleh dibulatkan ke atas."""
    mod._builder = MindrayBC5150Builder(now=_fixed_clock("20260804153559"))

    order = dict(ORDER, patient=dict(ORDER["patient"], dob="20260704"))
    age = next(s for s in _segments(mod.format_query_response(order, INSTRUMENT))["OBX"]
               if s[3] == "30525-0^Age^LN")
    assert (age[5], age[6]) == ("1", "mo")     # genap sebulan tepat hari ini

    # Sehari lebih muda: belum genap sebulan, jadi umur tidak dikirim.
    order = dict(ORDER, patient=dict(ORDER["patient"], dob="20260705"))
    assert b"30525-0^Age^LN" not in mod.format_query_response(order, INSTRUMENT)


def test_obx_umur_setahun_lebih_tetap_dalam_tahun(mod):
    """Batas atas satuan bulan: 12 bulan dilaporkan sebagai 1 tahun."""
    mod._builder = MindrayBC5150Builder(now=_fixed_clock("20260804153559"))
    order = dict(ORDER, patient=dict(ORDER["patient"], dob="20250801"))
    age = next(s for s in _segments(mod.format_query_response(order, INSTRUMENT))["OBX"]
               if s[3] == "30525-0^Age^LN")
    assert age[5] == "1"
    assert age[6] == "yr"


def test_test_mode_tidak_dikirim_bila_order_tidak_menyebutnya(mod):
    """Menebak mode pemeriksaan lebih berbahaya daripada membiarkan default alat."""
    order = dict(ORDER, tests=[{"test_code": "XYZ", "test_name": "Panel Lain"}])
    out = mod.format_query_response(order, INSTRUMENT)
    assert b"08003^Test Mode" not in out


def test_blood_mode_tidak_dikirim_bila_sample_type_tak_dikenal(mod):
    order = dict(ORDER, specimen={"sample_id": "257", "sample_type": "serum"})
    out = mod.format_query_response(order, INSTRUMENT)
    assert b"08002^Blood Mode" not in out


def test_blood_mode_mengenali_sample_type_bahasa_indonesia(mod):
    """
    EazyApp mengirim `sample_type` dalam Bahasa Indonesia ("Darah Vena"), bukan
    istilah dok vendor. Tanpa mapping ini Blood Mode tidak pernah terkirim
    untuk order dari lapangan.
    """
    order = dict(ORDER, specimen={"sample_id": "257", "sample_type": "Darah Vena"})
    obx = {s[3]: s[5] for s in _segments(mod.format_query_response(order, INSTRUMENT))["OBX"]}
    assert obx["08002^Blood Mode^99MRC"] == "W"


# ============================================================
# Draw time (OBR-6)
#
# BC-5150 menolak worklist dengan draw time kosong: operator menekan OK di
# dialog info sampel dan alat memunculkan error draw time. Sejak 4 Agu 2026
# EazyApp mengirim `specimen.collected_at`, tapi order lama maupun LIS lain
# bisa tetap mengosongkannya — rantai fallback tetap dipertahankan.
# ============================================================

def _obr(message: bytes) -> list:
    return _segments(message)["OBR"][0]


def test_draw_time_memakai_collected_at_bila_ada(mod):
    order = dict(ORDER, specimen=dict(ORDER["specimen"],
                                      collected_at="20090203094500"))
    assert _obr(mod.format_query_response(order, INSTRUMENT))[6] == "20090203094500"


def test_draw_time_jatuh_ke_request_datetime_bila_collected_at_kosong(mod):
    """
    Order dari LIS umumnya tidak membawa jam flebotomi. Jam order dibuat
    selisihnya menit dari jam pengambilan — jauh lebih baik daripada kosong,
    yang membuat alat menolak worklist.
    """
    assert _obr(mod.format_query_response(ORDER, INSTRUMENT))[6] == "20090203101020"


def test_draw_time_jatuh_ke_jam_lab_bila_order_tanpa_waktu_sama_sekali(mod):
    """Jam lab dikurangi margin aman, bukan jam sekarang persis — lihat blok
    clock skew di bawah."""
    mod._builder = MindrayBC5150Builder(now=_fixed_clock("20141105151358"))
    order = dict(ORDER, request_datetime="",
                 specimen={"sample_id": "257", "sample_type": "whole blood"})
    assert _obr(mod.format_query_response(order, INSTRUMENT))[6] == "20141105150800"


def test_draw_time_ber_offset_dikonversi_ke_jam_lab(mod):
    """
    EazyApp mengirim ISO8601 ber-offset UTC (`+00:00`). Membuang offsetnya
    begitu saja membuat alat menerima jam 7 jam lebih awal dari jam dinding
    lab — draw time sampel pagi jadi tercatat dini hari.
    """
    order = dict(ORDER, specimen=dict(ORDER["specimen"],
                                      collected_at="2026-08-01T06:49:18+00:00"))
    assert _obr(mod.format_query_response(order, INSTRUMENT))[6] == "20260801134918"


def test_draw_time_memakai_collected_at_dari_payload_eazyapp_asli(mod):
    """
    Payload EazyApp apa adanya (GET /orders/pending, 4 Agu 2026): jam
    flebotomi ber-offset UTC dan berumur beberapa hari.

    Yang dikunci di sini: nilainya dipakai — bukan `request_datetime` yang
    hanya berjarak detik dari saat worklist dibangun — dikonversi ke jam
    dinding lab (13:47 UTC → 20:47 WIB), dan tidak ikut dimundurkan margin
    aman karena sudah lampau menurut jam alat.
    """
    mod._builder = MindrayBC5150Builder(now=_fixed_clock("20260804105541"))
    order = {
        "mid_version": "1.0",
        "order_id": "LAB-20260801-0003",
        "request_datetime": "2026-08-04T03:50:26+00:00",
        "patient": {"patient_id": "MRN-20260801-0001", "name": "testing",
                    "dob": "2009-06-23", "gender": "F"},
        "specimen": {"sample_id": "5505629", "sample_type": "Darah Vena",
                     "collected_at": "2026-08-01T13:47:00+00:00",
                     "priority": "routine"},
        "tests": [{"test_code": "HGB", "test_name": "HGB"}],
    }
    obr = _obr(mod.format_query_response(order, INSTRUMENT))
    assert obr[6] == "20260801204700"
    assert obr[14] == "20260804105026"     # OBR-14 tetap jam order dibuat


def test_received_time_ber_offset_dikonversi_ke_jam_lab(mod):
    """OBR-14 (waktu order dibuat) datang dari sumber yang sama, bug yang sama."""
    order = dict(ORDER, request_datetime="2026-08-01T06:49:18+00:00")
    assert _obr(mod.format_query_response(order, INSTRUMENT))[14] == "20260801134918"


def test_timestamp_tanpa_offset_tetap_dibaca_sebagai_jam_lab(mod):
    """
    Alat dan driver lama mengirim `YYYYMMDDHHMMSS` polos tanpa zona. Nilai
    seperti itu sudah jam dinding lab dan tidak boleh digeser.
    """
    from protocols.mindray_bc5150.builder import to_hl7_timestamp
    assert to_hl7_timestamp("20190504060725") == "20190504060725"
    assert to_hl7_timestamp("19950804") == "19950804000000"


# ============================================================
# Draw time vs jam alat yang tertinggal (clock skew)
#
# Jam BC-5150 di lapangan tertinggal dari jam server: terukur +77 detik pada
# 28 Jul 2026 dan +90 detik pada 3 Agu 2026 (152 pesan masuk, tcp_1.comm.log),
# jadi melar ~2 detik/hari dan menyetel ulang jam alat tidak menyelesaikan.
#
# Alat menolak draw time yang lebih baru dari jamnya sendiri: dari 9 worklist
# yang terkirim, 5 yang draw time-nya di depan jam alat kembali dengan OBR-6
# kosong (operator melihat dialog "invalid draw time"), 4 yang di belakang
# diterima. Karena itu draw time dimundurkan dengan margin aman.
# ============================================================

def test_draw_time_dimundurkan_bila_melewati_batas_aman(mod):
    """
    `request_datetime` dari EazyApp hanya berjarak detik dari saat worklist
    dibangun, jadi menurut jam alat ia jatuh di masa depan dan ditolak.
    """
    mod._builder = MindrayBC5150Builder(now=_fixed_clock("20141105151358"))
    order = dict(ORDER, request_datetime="20141105151350")
    assert _obr(mod.format_query_response(order, INSTRUMENT))[6] == "20141105150800"


def test_draw_time_di_masa_lalu_tidak_digeser(mod):
    """Jam flebotomi asli dari LIS sudah aman — jangan dipalsukan."""
    mod._builder = MindrayBC5150Builder(now=_fixed_clock("20141105151358"))
    order = dict(ORDER, specimen=dict(ORDER["specimen"],
                                      collected_at="20141105144537"))
    assert _obr(mod.format_query_response(order, INSTRUMENT))[6] == "20141105144537"


def test_draw_time_hasil_clamp_dibulatkan_ke_menit(mod):
    """Alat memotong detik ke `:00` saat menyimpan; kirim bentuk yang sama
    supaya nilai di layar alat sama persis dengan yang dikirim."""
    mod._builder = MindrayBC5150Builder(now=_fixed_clock("20141105151358"))
    order = dict(ORDER, request_datetime="", specimen={"sample_id": "257"})
    assert _obr(mod.format_query_response(order, INSTRUMENT))[6].endswith("00")


def test_draw_time_batas_aman_menutupi_skew_alat(mod):
    """
    Margin harus lebih besar dari skew terukur (90 detik) dengan ruang untuk
    drift bertahun. Nilai di bawah batas ini membuat bug-nya kambuh.
    """
    from protocols.mindray_bc5150.constants import DRAW_TIME_SAFETY_MARGIN_SECONDS
    assert DRAW_TIME_SAFETY_MARGIN_SECONDS >= 300


def test_pv1_hanya_dibangun_bila_ada_lokasi(mod):
    assert b"PV1" not in mod.format_query_response(ORDER, INSTRUMENT)

    with_visit = dict(ORDER, visit={"point_of_care": "ICU", "bed": "BedNO1"})
    out = mod.format_query_response(with_visit, INSTRUMENT)
    assert b"PV1|1||ICU^^BedNO1" in out


def test_worklist_meng_escape_delimiter_pada_teks_bebas(mod):
    """
    Dok §3: `|` di dalam teks bebas harus jadi `\\F\\`. Bila lolos, satu
    remark bisa menggeser seluruh pemetaan field dan alat membaca order salah.
    """
    order = dict(ORDER, remark="cito|ulang", patient=dict(
        ORDER["patient"], name="Tom^Jr"
    ))
    out = mod.format_query_response(order, INSTRUMENT)
    seg = _segments(out)

    assert seg["PID"][0][5] == "Tom\\S\\Jr"
    remark = next(s for s in seg["OBX"] if s[3] == "01001^Remark^99MRC")
    assert remark[5] == "cito\\F\\ulang"


def test_unescape_membalik_escape_sequence():
    assert unescape("cito\\F\\ulang") == "cito|ulang"
    assert unescape("a\\S\\b\\T\\c\\R\\d") == "a^b&c~d"
    assert unescape("100\\E\\200") == "100\\200"
    assert unescape("tanpa escape") == "tanpa escape"


def test_worklist_dibungkus_mllp(orr):
    assert orr.startswith(b"\x0b") and orr.endswith(b"\x1c\x0d")


def test_not_found_tetap_memakai_msa_ar(mod):
    """Jalur "tidak ada order" tidak berubah — bentuknya terbukti di log."""
    out = mod.format_query_not_found_full(INSTRUMENT, {"control_id": "389"})
    assert b"ORR^O02" in out
    assert b"MSA|AR|389" in out
    assert b"ORC" not in out


# ============================================================
# Processing ID (MSH-11) — dok §4.3.1 & §5.4
# ============================================================

def test_ack_qc_memantulkan_processing_id_q(mod):
    """
    Dok §5.4: balasan hasil QC hanya berbeda pada MSH-11 = Q. Menjawab QC
    dengan "P" membuat alat menganggap balasan itu untuk pesan lain.
    """
    qc = ORU.replace(b"|ORU^R01|389|P|", b"|ORU^R01|389|Q|")
    out = mod.build_ack_response(qc, INSTRUMENT)
    assert b"|ACK^R01|389|Q|2.3.1|" in out


def test_ack_sampel_tetap_memakai_p(mod):
    assert b"|ACK^R01|389|P|2.3.1|" in mod.build_ack_response(ORU, INSTRUMENT)


# ============================================================
# Field OBR yang sebelumnya terabaikan — dok Tabel 4-6
# ============================================================

def test_parse_obr_membedakan_waktu_ambil_dan_waktu_periksa(mod):
    """
    OBR-6 = waktu darah diambil, OBR-7 = waktu alat memeriksa. Bila lab
    mengisi OBR-6, itulah collected_at yang benar.
    """
    msg = ORU.replace(
        b"OBR|1||raisya|00001^Automated Count^99MRC|||20190504060725",
        b"OBR|1||raisya|00001^Automated Count^99MRC||20190504053000|20190504060725",
    )
    out = mod.parse(msg, INSTRUMENT)
    assert out["specimen"]["collected_at"] == "20190504053000"


def test_parse_obr_jatuh_ke_waktu_periksa_bila_obr6_kosong(mod):
    """Alat pada log hanya mengisi OBR-7 — perilaku lama harus tetap."""
    out = mod.parse(ORU, INSTRUMENT)
    assert out["specimen"]["collected_at"] == "20190504060725"


def test_parse_pv1_membaca_bangsal_dan_bed(mod):
    pv1 = mod._parser.split_segment("PV1|1|Neike|Hema^^BN1")
    parsed = mod._parser.parse_pv1(pv1)
    assert parsed["patient_type"] == "Neike"
    assert parsed["point_of_care"] == "Hema"
    assert parsed["bed"] == "BN1"


def test_parse_membuka_escape_pada_nilai_obx(mod):
    msg = ORU.replace(
        b"OBX|30|IS|12007^Lymphopenia^99MRC||T|",
        b"OBX|30|ST|01001^Catatan^99MRC||cito\\F\\ulang|",
    )
    out = mod.parse(msg, INSTRUMENT)
    catatan = next(r for r in out["results"] if r["test_name"] == "Catatan")
    assert catatan["value"] == "cito|ulang"


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
    has_query = False

    def to_dict(self):
        return {"id": self.id, "name": self.name, "protocol": self.protocol}


class _CfgQuery(_Cfg):
    mode = "bidirectional"
    bidir_mode = "query"
    has_query = True


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


def test_receiver_menyerahkan_orm_ke_query_handler_pada_mode_query(monkeypatch):
    """
    Pada `bidir_mode=query`, ORM tidak boleh dibalas langsung oleh receiver —
    ia harus diserahkan ke QueryHandler yang mencari order di tbl_order dulu.
    """
    monkeypatch.setattr(
        "services.tcp_socket.receiver.save_result", lambda *a, **k: 1
    )

    receiver = ResultReceiver(_CfgQuery(), MindrayBC5150Module())
    writer = _Writer()

    is_query = asyncio.run(receiver.handle_data(ORM, writer))

    assert is_query is True
    assert receiver.last_query_data == ORM
    assert writer.sent == []      # belum ada balasan — QueryHandler yang kirim


def test_receiver_membalas_orm_sendiri_pada_mode_unidirectional():
    """
    Kebalikannya: tanpa QueryHandler, receiver wajib membalas sendiri.
    Kalau tidak, alat menunggu balasan yang tak pernah datang.
    """
    receiver = ResultReceiver(_Cfg(), MindrayBC5150Module())
    writer = _Writer()

    is_query = asyncio.run(receiver.handle_data(ORM, writer))

    assert is_query is False
    assert len(writer.sent) == 1
    assert b"ORR^O02" in writer.sent[0]
    assert b"MSA|AR|389" in writer.sent[0]
