"""
Test driver HL7_ARUMA_AR580 + integrasinya ke TCPSocketService.

Pesan contoh diambil verbatim dari "LIS communication protocol instruction"
(Genrui) bab 2.3.1 — dokumen acuan AR580 (rebrand OEM Genrui KT-6610).
"""

import asyncio

import pytest

from protocols.base import load_module, is_mllp_protocol, _PROTOCOL_REGISTRY
from protocols.aruma_ar580.module import ArumaAR580Module
from services.tcp_socket.receiver import ResultReceiver


PROTOCOL = "HL7_ARUMA_AR580"

INSTRUMENT = {"id": 7, "name": "AR580", "protocol": PROTOCOL}


# Pesan verbatim dari dokumen bab 2.3.1 "The transmission of the test results".
# Ini jangkar test: bila parser benar untuk byte string ini, driver benar.
ORU_DOC = (
    b"\x0b"
    b"MSH|^~\\&|Genrui|KT-6610|||20170712140022||ORU^R01|1275|P|2.3.1|||||CHA|UTF-8|||\r"
    b"PID|1||0706-ZY-190-11||name||19910606|M|||||||||||||||||||||\r"
    b"PV1|1|clinic|internal medicine||||||chuanghao|||||||||||\r"
    # Baris OBR terpotong wrap di PDF; sambungannya "||||20170706090000" (4 pipe),
    # sehingga sampling time jatuh di OBR-14 dan HM di OBR-24 — keduanya sesuai tabel 8.
    b"OBR|1|||||20170706110000|20170706181945|||inspection||||20170706090000||||RD||RD"
    b"||||HM|||||||Genrui||||||||\r"
    b"OBX|1|NM|^WBC^||0.01|10^9/L|4.00-10.00|L|||F||||||||\r"
    b"OBX|2|NM|^Neu#^||0.01|10^9/L|2.00-7.00|L|||F||e||||||\r"
    b"OBX|3|NM|^Lym#^||0.00|10^9/L|0.80-4.00|L|||F||e||||||\r"
    b"OBX|4|NM|^Mon#^||0.00|10^9/L|0.12-1.20|L|||F||e||||||\r"
    b"OBX|5|NM|^Eos#^||0.00|10^9/L|0.02-0.50|L|||F||e||||||\r"
    b"OBX|6|NM|^Bas#^||0.00|10^9/L|0.00-0.10||||F||e||||||\r"
    b"OBX|7|NM|^Neu%^||50.0|%|50.0-70.0||||F||E||||||\r"
    b"OBX|8|NM|^Lym%^||10.0|%|20.0-40.0|L|||F||E||||||\r"
    b"OBX|9|NM|^Mon%^||10.0|%|3.0-12.0||||F||E||||||\r"
    b"OBX|10|NM|^Eos%^||20.0|%|0.5-5.0|H|||F||E||||||\r"
    b"OBX|11|NM|^Bas%^||10.0|%|0.0-1.0|H|||F||E||||||\r"
    b"OBX|12|NM|^RBC^||0.09|10^12/L|3.50-5.50|L|||F||E||||||\r"
    b"OBX|13|NM|^HGB^||106|g/L|120-160|L|||F||||||||\r"
    b"OBX|14|NM|^HCT^||32.9|%|40.0-54.0|L|||F||||||||\r"
    b"OBX|15|NM|^MCV^||87.7|fL|80.0-100.0||||F||||||||\r"
    b"OBX|16|NM|^MCH^||28.2|pg|27.0-34.0||||F||||||||\r"
    b"OBX|17|NM|^MCHC^||322|g/L|320-360||||F||||||||\r"
    b"OBX|18|NM|^RDW-CV^||8.7|%|11.0-16.0|L|||F||||||||\r"
    b"OBX|19|NM|^RDW-SD^||32.0|fL|35.0-56.0|L|||F||||||||\r"
    b"OBX|20|NM|^PLT^||144|10^9/L|100-300||||F||||||||\r"
    b"OBX|21|NM|^MPV^||9.4|fL|6.5-12.0||||F||||||||\r"
    b"OBX|22|NM|^PDW^||16.4||9.0-17.0||||F||||||||\r"
    b"OBX|23|NM|^PCT^||0.135|%|0.108-0.282||||F||||||||\r"
    b"OBX|24|NM|^P-LCC^||37|10^9/L|30-90||||F||||||||\r"
    b"OBX|25|NM|^P-LCR^||25.8|%|11.0-45.0||||F||||||||\r"
    b"OBX|26|IS|^Blood Mode^||whole blood||||||F||||||||\r"
    b"OBX|27|IS|^Test Mode^||CBC+DIFF||||||F||||||||\r"
    b"OBX|28|IS|^Ref Group^||man||||||F||||||||\r"
    b"OBX|29|IS|^Age^||26|years old|||||F||||||||\r"
    b"OBX|30|IS|^Remarks^||remark||||||F||||||||\r"
    b"OBX|31|ED|^DIFFScatter_BMP^||bitmap data||||||F||||||||\r"
    b"OBX|32|ED|^WBCScatter_BMP^||bitmap data||||||F||||||||\r"
    b"OBX|33|ED|^RBCHistogram_BMP^||bitmap data||||||F||||||||\r"
    b"OBX|34|ED|^PLTHistogram_BMP^||bitmap data||||||F||||||||\r"
    b"\x1c\x0d"
)


@pytest.fixture
def mod():
    return ArumaAR580Module()


@pytest.fixture
def parsed(mod):
    return mod.parse(ORU_DOC, INSTRUMENT)


# ============================================================
# Registrasi & identitas
# ============================================================

def test_protocol_terdaftar_di_registry():
    assert _PROTOCOL_REGISTRY[PROTOCOL] == "protocols.aruma_ar580.module"


def test_load_module_mengembalikan_driver_ar580():
    assert isinstance(load_module(PROTOCOL), ArumaAR580Module)


def test_nama_protocol_memicu_framing_mllp():
    # Prefix "HL7" bersifat fungsional: is_mllp_protocol() memilih framing dari situ.
    assert is_mllp_protocol(PROTOCOL) is True


def test_identitas_module(mod):
    assert mod.PROTOCOL_NAME == PROTOCOL
    assert mod.VERSION == "1.0.0"


# ============================================================
# Parse — header & konteks
# ============================================================

def test_parse_mengisi_instrument_id_dan_protocol(parsed):
    assert parsed["instrument_id"] == 7
    assert parsed["protocol"] == PROTOCOL


def test_parse_tanpa_parse_error_untuk_pesan_dokumen(parsed):
    assert parsed["parse_errors"] == []


def test_msh3_tidak_divalidasi(mod):
    # AR580 diperlakukan sebagai rebrand KT-6610 dan string MSH-3 asli belum
    # diketahui, jadi sending application apa pun harus diterima.
    pesan = ORU_DOC.replace(b"|Genrui|KT-6610|", b"|AR580|Aruma|")
    hasil = mod.parse(pesan, INSTRUMENT)
    assert hasil["parse_errors"] == []
    assert len(hasil["results"]) == 25


def test_parse_mengambil_sample_id_dari_pid3(parsed):
    assert parsed["specimen"]["sample_id"] == "0706-ZY-190-11"
    assert parsed["patient"]["patient_id"] == "0706-ZY-190-11"


def test_parse_mengambil_dob_dan_gender(parsed):
    assert parsed["patient"]["dob"] == "19910606"
    assert parsed["patient"]["gender"] == "M"


def test_parse_mengambil_nama_pasien_sederhana(parsed):
    assert parsed["patient"]["name"] == "name"


def test_parse_nama_pasien_dalam_subkomponen(mod):
    # Tabel 6 dokumen mendefinisikan PID-5 sebagai "&LName&&&" — nama ada di
    # subkomponen, bukan komponen.
    pesan = ORU_DOC.replace(b"||name||19910606|", b"||&Budi Santoso&&&||19910606|")
    hasil = mod.parse(pesan, INSTRUMENT)
    assert hasil["patient"]["name"] == "Budi Santoso"


def test_parse_mengambil_waktu_sampling_dari_obr14(parsed):
    assert parsed["specimen"]["collected_at"] == "20170706090000"


# ============================================================
# Parse — hasil numerik (OBX NM)
# ============================================================

def test_parse_menghasilkan_25_hasil_numerik(parsed):
    # 34 OBX: 25 NM + 5 IS (metadata) + 4 ED (bitmap, dilewati).
    assert len(parsed["results"]) == 25


def test_parse_membaca_test_code_dari_komponen_kedua_obx3(parsed):
    # OBX-3 berbentuk "^WBC^" — kode ada di komponen 2, komponen 1 kosong.
    assert parsed["results"][0]["test_code"] == "WBC"


def test_komponen_kedua_obx3_diprioritaskan(mod):
    # Bila kedua komponen terisi, komponen 2 tetap yang dipakai — dokumen
    # menempatkan kode test di sana. Tanpa test ini, membaca komponen 1 lolos
    # begitu saja karena pada bentuk "^WBC^" komponen 1 selalu kosong.
    pesan = ORU_DOC.replace(b"|^WBC^|", b"|9001^WBC^|")
    hasil = mod.parse(pesan, INSTRUMENT)
    assert hasil["results"][0]["test_code"] == "WBC"


def test_obx3_tanpa_caret_tetap_terbaca(mod):
    # Firmware yang mengirim identifier polos tanpa separator komponen.
    pesan = ORU_DOC.replace(b"|^WBC^|", b"|WBC|")
    hasil = mod.parse(pesan, INSTRUMENT)
    assert hasil["results"][0]["test_code"] == "WBC"


def test_parse_nilai_wbc_sesuai_dokumen(parsed):
    wbc = parsed["results"][0]
    assert wbc["value"] == "0.01"
    assert wbc["unit"] == "10^9/L"
    assert wbc["reference_range"] == "4.00-10.00"
    assert wbc["flag"] == "L"
    assert wbc["status"] == "F"


def test_parse_unit_dengan_caret_tidak_terpotong(parsed):
    # "10^9/L" memakai ^ yang juga separator komponen HL7, tanpa escape.
    # OBX-6 karena itu harus dibaca mentah, tidak di-split per komponen.
    rbc = next(r for r in parsed["results"] if r["test_code"] == "RBC")
    assert rbc["unit"] == "10^12/L"


def test_parse_nilai_hgb_dan_plt_sesuai_dokumen(parsed):
    by_code = {r["test_code"]: r for r in parsed["results"]}
    assert by_code["HGB"]["value"] == "106"
    assert by_code["HGB"]["unit"] == "g/L"
    assert by_code["PLT"]["value"] == "144"


def test_parse_flag_high_terbaca(parsed):
    by_code = {r["test_code"]: r for r in parsed["results"]}
    assert by_code["Eos%"]["flag"] == "H"
    assert by_code["Bas%"]["flag"] == "H"


def test_parse_flag_normal_kosong(parsed):
    by_code = {r["test_code"]: r for r in parsed["results"]}
    assert by_code["MCV"]["flag"] == ""


def test_parse_semua_kode_test_dokumen_ada(parsed):
    kode = [r["test_code"] for r in parsed["results"]]
    assert kode == [
        "WBC", "Neu#", "Lym#", "Mon#", "Eos#", "Bas#",
        "Neu%", "Lym%", "Mon%", "Eos%", "Bas%",
        "RBC", "HGB", "HCT", "MCV", "MCH", "MCHC",
        "RDW-CV", "RDW-SD", "PLT", "MPV", "PDW", "PCT", "P-LCC", "P-LCR",
    ]


def test_parse_unit_kosong_saat_obx6_kosong(parsed):
    by_code = {r["test_code"]: r for r in parsed["results"]}
    assert by_code["PDW"]["unit"] == ""


# ============================================================
# Parse — OBX-13 status edit (field custom vendor)
# ============================================================

def test_parse_menandai_hasil_yang_diedit_aktif(parsed):
    # OBX-13: null=unedited, O=expired reagent, E=active editing, e=passive editing.
    by_code = {r["test_code"]: r for r in parsed["results"]}
    assert by_code["Neu%"]["status"] == "F/E"


def test_parse_menandai_hasil_edit_pasif(parsed):
    by_code = {r["test_code"]: r for r in parsed["results"]}
    assert by_code["Neu#"]["status"] == "F/e"


def test_parse_status_polos_saat_obx13_kosong(parsed):
    by_code = {r["test_code"]: r for r in parsed["results"]}
    assert by_code["HGB"]["status"] == "F"


# ============================================================
# Parse — OBX IS (metadata, bukan pengukuran)
# ============================================================

def test_metadata_tidak_masuk_results(parsed):
    kode = [r["test_code"] for r in parsed["results"]]
    for meta in ("Blood Mode", "Test Mode", "Ref Group", "Age", "Remarks"):
        assert meta not in kode


def test_blood_mode_jadi_sample_type(parsed):
    assert parsed["specimen"]["sample_type"] == "whole blood"


def test_test_mode_jadi_panel_order(parsed):
    assert parsed["order"]["panel"] == "CBC+DIFF"


def test_remarks_dan_age_masuk_comments(parsed):
    gabung = " | ".join(parsed["comments"])
    assert "remark" in gabung
    assert "26" in gabung


def test_esr_diperlakukan_sebagai_hasil_bukan_metadata(mod):
    # ESR ada di tabel custom OBX dokumen, tapi ia pengukuran sungguhan.
    # Routing metadata harus berdasar nama identifier, bukan value type.
    pesan = ORU_DOC.replace(
        b"OBX|30|IS|^Remarks^||remark||||||F||||||||\r",
        b"OBX|30|IS|^Remarks^||remark||||||F||||||||\r"
        b"OBX|35|NM|^ESR^||12|mm/h|0-15||||F||||||||\r",
    )
    hasil = mod.parse(pesan, INSTRUMENT)
    kode = [r["test_code"] for r in hasil["results"]]
    assert "ESR" in kode


# ============================================================
# Parse — OBX ED (bitmap dilewati)
# ============================================================

def test_bitmap_ed_tidak_masuk_results(parsed):
    kode = [r["test_code"] for r in parsed["results"]]
    for bmp in ("DIFFScatter_BMP", "WBCScatter_BMP",
                "RBCHistogram_BMP", "PLTHistogram_BMP"):
        assert bmp not in kode


def test_bitmap_ed_tidak_muncul_di_nilai_mana_pun(parsed):
    assert "bitmap data" not in str(parsed)


def test_jumlah_bitmap_dilewati_dilaporkan(mod, caplog):
    import logging
    with caplog.at_level(logging.INFO):
        mod.parse(ORU_DOC, INSTRUMENT)
    assert "4" in caplog.text and "ED" in caplog.text


# ============================================================
# Parse — escape sequence
# ============================================================

def test_escape_sequence_didecode_di_remarks(mod):
    # \F\ \S\ \T\ \R\ \E\ dan \.br\ per tabel 2 dokumen. Remarks adalah tempat
    # separator tanpa escape akan merusak parse.
    pesan = ORU_DOC.replace(
        b"^Remarks^||remark|",
        b"^Remarks^||hemolisis \\F\\ ikterik\\.br\\lipemia \\S\\ ok|",
    )
    hasil = mod.parse(pesan, INSTRUMENT)
    gabung = " | ".join(hasil["comments"])
    assert "hemolisis | ikterik" in gabung
    assert "lipemia ^ ok" in gabung


def test_escape_backslash_didecode(mod):
    pesan = ORU_DOC.replace(b"^Remarks^||remark|", b"^Remarks^||a \\E\\ b|")
    hasil = mod.parse(pesan, INSTRUMENT)
    assert "a \\ b" in " | ".join(hasil["comments"])


# ============================================================
# Parse — framing & input rusak
# ============================================================

def test_parse_menerima_pesan_tanpa_wrapper_mllp(mod):
    # Bab 1.3 dokumen hanya menyebut "Block is HL7 message" tanpa menyatakan
    # MLLP eksplisit, jadi frame reader harus toleran terhadap keduanya.
    telanjang = ORU_DOC[1:-2]
    hasil = mod.parse(telanjang, INSTRUMENT)
    assert len(hasil["results"]) == 25
    assert hasil["parse_errors"] == []


def test_parse_pesan_kosong_menghasilkan_parse_error(mod):
    hasil = mod.parse(b"", INSTRUMENT)
    assert hasil["parse_errors"] != []
    assert hasil["results"] == []


def test_parse_pesan_tanpa_msh_menghasilkan_parse_error(mod):
    hasil = mod.parse(b"\x0bbukan hl7 sama sekali\x1c\x0d", INSTRUMENT)
    assert hasil["parse_errors"] != []


def test_parse_obx_rusak_tidak_menjatuhkan_seluruh_pesan(mod):
    pesan = ORU_DOC.replace(b"OBX|13|NM|^HGB^||106|g/L|120-160|L|||F||||||||\r", b"OBX\r")
    hasil = mod.parse(pesan, INSTRUMENT)
    assert len(hasil["results"]) == 24
    assert hasil["parse_errors"] != []


def test_parse_lf_sebagai_terminator_juga_diterima(mod):
    hasil = mod.parse(ORU_DOC.replace(b"\r", b"\r\n"), INSTRUMENT)
    assert len(hasil["results"]) == 25


def test_parse_utf8_rusak_tidak_melempar_exception(mod):
    pesan = ORU_DOC.replace(b"||name||", b"||\xff\xfe||")
    hasil = mod.parse(pesan, INSTRUMENT)
    assert len(hasil["results"]) == 25


# ============================================================
# Builder — ACK^R01
# ============================================================

def test_ack_membalas_control_id_dari_pesan(mod):
    # Alat kirim ulang dalam 3 detik bila ACK tak diterima (bab 2.3.1), jadi
    # MSA-2 wajib echo MSH-10 pesan yang diterima — bukan ID buatan sendiri.
    ack = mod.build_ack_response(ORU_DOC, INSTRUMENT)
    assert b"MSA|AA|1275" in ack


def test_ack_dibungkus_mllp(mod):
    ack = mod.build_ack_response(ORU_DOC, INSTRUMENT)
    assert ack.startswith(b"\x0b")
    assert ack.endswith(b"\x1c\x0d")


def test_ack_bertipe_ack_r01_versi_231(mod):
    ack = mod.build_ack_response(ORU_DOC, INSTRUMENT)
    assert b"ACK^R01" in ack
    assert b"|2.3.1|" in ack


def test_ack_hanya_berisi_msh_dan_msa(mod):
    # Tabel 4 dokumen: LIS→PC = MSH + MSA saja.
    ack = mod.build_ack_response(ORU_DOC, INSTRUMENT)
    segmen = [s[:3] for s in ack[1:-2].split(b"\r") if s]
    assert segmen == [b"MSH", b"MSA"]


def test_ack_msa_lengkap_sampai_status_code(mod):
    # Manual menuliskan MSA dengan trailing field sampai MSA-6 (MSA|AA|1275||||).
    # MSA ringkas "MSA|AA|<id>" ditolak alat di lapangan: hasil dikirim ulang
    # dengan control ID sama lalu koneksi di-reset.
    ack = mod.build_ack_response(ORU_DOC, INSTRUMENT)
    assert b"MSA|AA|1275|Message accepted|||0|\r" in ack


def test_ack_tidak_menebak_identitas_alat(mod):
    # MSH-4/5/6 kosong. Fallback "Genrui"/"KT-6610" dulu bocor ke MSH-5/6 saat
    # alat mengirim MSH-3/4 kosong — nama vendor tebakan tidak boleh muncul.
    msh = mod.build_ack_response(ORU_DOC, INSTRUMENT).decode().split("\r")[0]
    f = msh.split("|")
    assert f[2] == "MidLab"          # MSH-3
    assert f[3] == ""                # MSH-4
    assert f[4] == ""                # MSH-5
    assert f[5] == ""                # MSH-6
    assert b"Genrui" not in mod.build_ack_response(ORU_DOC, INSTRUMENT)
    assert b"KT-6610" not in mod.build_ack_response(ORU_DOC, INSTRUMENT)


def test_ack_msh_lengkap_sampai_msh21(mod):
    # Timestamp wajib jatuh di MSH-7 dan MSH ditutup trailing "|||" (MSH-19..21),
    # sama seperti MSH pesan alat.
    msh = mod.build_ack_response(ORU_DOC, INSTRUMENT).decode().split("\r")[0]
    f = msh.split("|")
    assert len(f) == 21              # MSH..MSH-21 (MSH-1 = separator itu sendiri)
    assert len(f[6]) == 14           # MSH-7 YYYYMMDDHHMMSS
    assert f[8] == "ACK^R01"         # MSH-9
    assert f[9] == "1275"            # MSH-10
    assert f[16] == "CHA"            # MSH-17
    assert f[17] == "UTF-8"          # MSH-18
    assert f[18:] == ["", "", ""]    # MSH-19..21
    assert msh.endswith("UTF-8|||")


def test_ack_control_id_berbeda_ikut_berubah(mod):
    pesan = ORU_DOC.replace(b"|ORU^R01|1275|", b"|ORU^R01|99|")
    ack = mod.build_ack_response(pesan, INSTRUMENT)
    assert b"MSA|AA|99" in ack


def test_pesan_tanpa_msh_tidak_di_ack(mod):
    assert mod.build_ack_response(b"", INSTRUMENT) == b""
    assert mod.build_ack_response(b"\x0bbukan hl7\x1c\x0d", INSTRUMENT) == b""


# ============================================================
# handle_ack — MSA masuk
# ============================================================

@pytest.mark.parametrize("kode,harapan", [
    (b"AA", "ACK"),
    (b"AE", "NAK"),
    (b"AR", "NAK"),
    (b"CA", "NAK"),
    (b"CE", "NAK"),
    (b"CR", "NAK"),
])
def test_handle_ack_memetakan_kode_msa(mod, kode, harapan):
    pesan = b"\x0bMSH|^~\\&|Genrui|KT-6610|||1||ACK^R01|1|P|2.3.1\rMSA|" + kode + b"|1\r\x1c\x0d"
    assert mod.handle_ack(pesan) == harapan


def test_handle_ack_tanpa_msa_unknown(mod):
    assert mod.handle_ack(ORU_DOC) == "UNKNOWN"


# ============================================================
# Method di luar lingkup unidirectional
# ============================================================

def test_is_enq_selalu_false(mod):
    # ORM^O01 tidak dispesifikasi dokumen — alat tidak pernah query.
    assert mod.is_enq(ORU_DOC) is False
    assert mod.is_enq(b"\x05") is False


@pytest.mark.parametrize("panggil", [
    lambda m: m.format_order({}, INSTRUMENT),
    lambda m: m.handle_enq(b"", INSTRUMENT),
    lambda m: m.format_query_response({}, INSTRUMENT),
])
def test_method_bidirectional_gagal_keras(mod, panggil):
    # Gagal keras, bukan diam-diam kirim payload ngawur ke hematology analyzer.
    with pytest.raises(NotImplementedError):
        panggil(mod)


def test_format_query_not_found_mengembalikan_kosong(mod):
    assert mod.format_query_not_found(INSTRUMENT) == b""


# ============================================================
# Integrasi — ResultReceiver
# ============================================================

@pytest.mark.asyncio
async def test_receiver_membalas_ack_lewat_hook_module(mod):
    # receiver.py memanggil build_ack_response() bila protocol module
    # menyediakannya — driver ini harus masuk lewat kait yang sama.
    terkirim = []

    class WriterPalsu:
        def write(self, data):
            terkirim.append(data)

        async def drain(self):
            pass

    class CommPalsu:
        def tx(self, data):
            pass

    receiver = object.__new__(ResultReceiver)
    receiver._protocol = mod
    receiver._logger = __import__("logging").getLogger("test")
    receiver._inst_name = "AR580"
    receiver._comm = CommPalsu()
    receiver._lock = asyncio.Lock()
    receiver._config = type("C", (), {"to_dict": lambda self: INSTRUMENT})()

    await ResultReceiver._send_hl7_ack(receiver, ORU_DOC, WriterPalsu())

    assert terkirim, "receiver tidak mengirim apa pun"
    assert b"MSA|AA|1275" in terkirim[0]
