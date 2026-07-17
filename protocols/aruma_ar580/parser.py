"""
protocols/aruma_ar580/parser.py — Parser ORU^R01 ARUMA AR580 → ResultObject

Referensi: "LIS communication protocol instruction" (Genrui) bab 2.3.1 & bab 3.

Kekhasan alat ini yang menyimpang dari kebiasaan HL7 umum (semua dari dokumen):

  - OBX-3 berbentuk "^WBC^" — kode test ada di KOMPONEN 2, komponen 1 kosong.
  - OBX-6 bisa berisi "10^9/L" — tanda ^ tidak di-escape padahal ia separator
    komponen, jadi field ini WAJIB dibaca mentah.
  - PID-5 bisa berbentuk "&LName&&&" — nama ada di subkomponen.
  - OBX-13 field custom vendor untuk status edit (null/O/E/e).
"""

from lib.models import ResultObject, TestResult
from lib.utils import get_logger

from protocols.aruma_ar580.constants import (
    MLLP_START_BYTE, MLLP_END_BYTE, MLLP_TRAILER,
    FIELD_SEPARATOR, COMPONENT_SEP, SUBCOMPONENT_SEP,
    SEG_MSH, SEG_PID, SEG_PV1, SEG_OBR, SEG_OBX,
    PROTOCOL_NAME,
    VT_ENCAPSULATED,
    METADATA_IDENTIFIERS,
    META_BLOOD_MODE, META_TEST_MODE, META_REMARKS,
    EDIT_LABELS,
    ESCAPE_SEQUENCES,
)


logger = get_logger("protocol_aruma_ar580")


# ============================================================
# Helper — framing & pemecahan field
# ============================================================

def strip_mllp(raw_bytes: bytes) -> bytes:
    """
    Buang envelope MLLP bila ada.

    Bab 1.3 dokumen hanya menyebut "Block is HL7 message" tanpa menyatakan MLLP
    secara eksplisit, jadi pesan telanjang (tanpa <VT>...<FS><CR>) juga diterima.
    """
    data = raw_bytes
    if data.startswith(MLLP_START_BYTE):
        data = data[1:]
    if data.endswith(MLLP_TRAILER):
        data = data[:-len(MLLP_TRAILER)]
    elif data.endswith(MLLP_END_BYTE):
        data = data[:-1]
    return data


def decode_escape(value: str) -> str:
    """
    Kembalikan escape sequence HL7 ke karakter aslinya (dokumen tabel 2).

    \\E\\ didecode terakhir supaya backslash hasilnya tidak ditafsirkan sebagai
    awal sequence lain.
    """
    if "\\" not in value:
        return value
    for token, asli in ESCAPE_SEQUENCES:
        value = value.replace(token, asli)
    return value


def _fields(segment: str) -> list[str]:
    """Pecah segment jadi list field. Index = nomor field HL7 (kecuali MSH)."""
    return segment.split(FIELD_SEPARATOR)


def _field(fields: list[str], index: int) -> str:
    """Ambil field ke-index, string kosong bila di luar batas."""
    return fields[index].strip() if index < len(fields) else ""


def _component(value: str, index: int) -> str:
    """Ambil komponen ke-index (1-based) dari sebuah field."""
    parts = value.split(COMPONENT_SEP)
    return parts[index - 1].strip() if index <= len(parts) else ""


def _nama_pasien(pid5: str) -> str:
    """
    Ambil nama dari PID-5.

    Dokumen tabel 6 mendefinisikan PID-5 sebagai "&LName&&&" — nama berada di
    subkomponen, bukan komponen. Contoh di bab 2.3.1 justru memakai nama polos
    ("name"), jadi kedua bentuk harus jalan.
    """
    if not pid5:
        return ""
    # Bentuk XPN normal: family^given^...
    kandidat = [p for p in pid5.split(COMPONENT_SEP) if p.strip()]
    if not kandidat:
        return ""
    # Bentuk vendor: subkomponen "&LName&&&"
    bagian = [b.strip() for b in kandidat[0].split(SUBCOMPONENT_SEP) if b.strip()]
    if bagian:
        return " ".join(bagian) if len(kandidat) == 1 else bagian[0]
    return ""


def _identifier(obx3: str) -> str:
    """
    Ambil kode test dari OBX-3.

    Dokumen memakai bentuk "^WBC^": komponen 1 kosong, kode ada di komponen 2.
    Bila komponen 2 kosong (alat lain / firmware beda), jatuh ke komponen 1.
    """
    return _component(obx3, 2) or _component(obx3, 1)


# ============================================================
# Parser utama
# ============================================================

def parse_oru(raw_bytes: bytes, instrument: dict) -> dict:
    """
    Parse pesan ORU^R01 dari AR580 menjadi ResultObject dict.

    Tidak pernah melempar exception: kegagalan dicatat di parse_errors agar
    receiver tetap bisa menyimpan raw_data untuk diperiksa manual.
    """
    instrument_id = instrument.get("id", 0)
    result = ResultObject(instrument_id=instrument_id, protocol=PROTOCOL_NAME)

    if not raw_bytes:
        result.parse_errors.append("Pesan kosong")
        return result.to_dict()

    # errors="replace" — alat yang salah setel encoding terdegradasi, tidak crash.
    teks = strip_mllp(raw_bytes).decode("utf-8", errors="replace")

    segments = [s.strip("\n") for s in teks.split("\r")]
    segments = [s for s in segments if s.strip()]

    if not any(s.startswith(SEG_MSH) for s in segments):
        result.parse_errors.append("Segment MSH tidak ditemukan — bukan pesan HL7")
        return result.to_dict()

    jumlah_ed = 0

    for segment in segments:
        nama = segment[:3]
        try:
            if nama == SEG_MSH:
                _parse_msh(segment, instrument)
            elif nama == SEG_PID:
                _parse_pid(segment, result)
            elif nama == SEG_PV1:
                _parse_pv1(segment, result)
            elif nama == SEG_OBR:
                _parse_obr(segment, result)
            elif nama == SEG_OBX:
                if _parse_obx(segment, result):
                    jumlah_ed += 1
        except Exception as e:
            result.parse_errors.append(f"Gagal parse segment {nama}: {e}")
            logger.warning(f"Gagal parse segment {nama}: {e}")

    if jumlah_ed:
        # Bitmap histogram/scattergram sengaja tidak disimpan — lihat spec.
        logger.info(
            f"{jumlah_ed} segment OBX bertipe ED (bitmap) dilewati "
            f"untuk sample {result.specimen.sample_id or '-'}"
        )

    return result.to_dict()


def _parse_msh(segment: str, instrument: dict) -> None:
    """
    Catat identitas pengirim.

    MSH-3 TIDAK divalidasi: dokumen acuan tertulis untuk Genrui KT-6610 dan
    AR580 diperlakukan sebagai rebrand-nya, sehingga string sending application
    yang sebenarnya belum dipastikan. Dokumen sendiri tidak konsisten — tabel 5
    menyebut MSH-3=KT-6610/MSH-4=Genrui, tapi contoh bab 2.3.1 memakai urutan
    sebaliknya. Menolak pesan berdasar field ini hanya akan menggagalkan
    integrasi di lapangan tanpa manfaat.
    """
    f = _fields(segment)
    logger.info(
        f"[{instrument.get('name', '-')}] ORU dari "
        f"app={_field(f, 2) or '-'} facility={_field(f, 3) or '-'} "
        f"control_id={_field(f, 9) or '-'}"
    )


def _parse_pid(segment: str, result: ResultObject) -> None:
    """PID — identitas pasien. PID-3 dipakai ganda sebagai Patient ID & Sample No."""
    f = _fields(segment)

    # Dokumen tabel 6: PID-3 = "Patient ID (Sample No.)" — satu field, dua makna.
    pid3 = decode_escape(_field(f, 3))
    result.patient.patient_id = pid3
    result.specimen.sample_id = pid3

    result.patient.name = decode_escape(_nama_pasien(_field(f, 5)))
    result.patient.dob = _field(f, 7)
    result.patient.gender = _field(f, 8)

    rekam_medis = decode_escape(_field(f, 4))
    if rekam_medis:
        result.comments.append(f"No. rekam medis: {rekam_medis}")


def _parse_pv1(segment: str, result: ResultObject) -> None:
    """PV1 — PV1-3 departemen, PV1-7 dokter penanggung jawab."""
    f = _fields(segment)

    departemen = decode_escape(_component(_field(f, 3), 1))
    if departemen:
        result.comments.append(f"Departemen: {departemen}")

    result.patient.physician = decode_escape(_component(_field(f, 7), 1))


def _parse_obr(segment: str, result: ResultObject) -> None:
    """OBR — OBR-14 waktu sampling, OBR-2/3 nomor order bila ada."""
    f = _fields(segment)

    result.specimen.collected_at = _field(f, 14)
    result.order.order_id = _field(f, 2) or _field(f, 3)


def _parse_obx(segment: str, result: ResultObject) -> bool:
    """
    OBX — satu hasil, satu metadata, atau satu bitmap.

    Returns:
        True bila segment ini bitmap ED yang dilewati (untuk dihitung pemanggil).
    """
    f = _fields(segment)

    value_type = _field(f, 2)
    identifier = _identifier(_field(f, 3))
    value = decode_escape(_field(f, 5))

    # Bitmap histogram/scattergram: OBX-5 bisa sampai 65536 karakter dan akan
    # membengkakkan result_json sekaligus berisiko memotong raw_data TEXT (64KB).
    if value_type == VT_ENCAPSULATED:
        return True

    # Routing metadata berdasar NAMA identifier, bukan value type: ESR ada di
    # tabel custom OBX dokumen tapi ia pengukuran sungguhan.
    if identifier in METADATA_IDENTIFIERS:
        _terapkan_metadata(identifier, value, _field(f, 6), result)
        return False

    if not identifier:
        raise ValueError("OBX tanpa observation identifier (OBX-3)")

    result.results.append(TestResult(
        test_code=identifier,
        test_name=identifier,
        value=value,
        # OBX-6 dibaca mentah: unit "10^9/L" memakai ^ tanpa escape.
        unit=_field(f, 6),
        reference_range=_field(f, 7),
        flag=_field(f, 8),
        status=_status_dengan_edit(_field(f, 11), _field(f, 13)),
    ))
    return False


def _status_dengan_edit(obx11: str, obx13: str) -> str:
    """
    Gabungkan status hasil (OBX-11) dengan penanda edit (OBX-13).

    OBX-13 adalah field custom vendor: null=unedited, O=expired reagent,
    E=active editing, e=passive editing. Nilai yang pernah diedit tangan penting
    bagi lab, jadi ikut dibawa: "F/E", "F/e", "F/O".
    """
    label = EDIT_LABELS.get(obx13)
    return f"{obx11}/{label}" if label else obx11


def _terapkan_metadata(identifier: str, value: str, unit: str,
                       result: ResultObject) -> None:
    """Rutekan OBX metadata ke konteks specimen/order, bukan ke results[]."""
    if not value:
        return

    if identifier == META_BLOOD_MODE:
        result.specimen.sample_type = value
    elif identifier == META_TEST_MODE:
        result.order.panel = value
    elif identifier == META_REMARKS:
        result.comments.append(value)
    else:
        # Ref Group / Age / Blood Type — tidak ada field di ResultObject.
        result.comments.append(f"{identifier}: {value} {unit}".strip())
