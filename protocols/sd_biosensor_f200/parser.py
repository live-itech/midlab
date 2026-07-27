"""
protocols/sd_biosensor_f200/parser.py — Parser IHE PCD-01 SD Biosensor STANDARD F

Menangani tiga hal yang khas PCD-01 dan tidak ada di driver HL7 lain repo ini:

1. **Timestamp ber-offset zona** — `20260713114138-0500`. Offset dipertahankan
   saat dikonversi ke ISO8601 supaya tidak ada informasi yang hilang. Ini
   penting: pada log, alat berjalan di zona -0500 sementara lab di +0700, jadi
   membuang offset akan menggeser jam pemeriksaan 12 jam.

2. **Reference range `[4.00;15.00]`** — dinormalkan ke `4.00-15.00` agar seragam
   dengan driver lain dan payload EazyApp.

3. **NTE yang artinya bergantung posisi** — segment NTE yang sama-sama `NTE|1`
   berarti hal berbeda tergantung induknya (OBR = info alat, OBX = nilai
   turunan). Parser melacak segment induk, bukan menebak dari isi teks.
"""

import re

from lib.utils import get_logger
from protocols.hl7.parser import HL7Parser
from protocols.sd_biosensor_f200.constants import (
    FIELD_SEPARATOR, COMPONENT_SEP, SEGMENT_TERMINATOR,
    MESSAGE_ENCODING,
    SEG_MSH,
    EMPTY_REFERENCE_RANGE,
    RANGE_OPEN, RANGE_CLOSE, RANGE_SEPARATOR,
    NTE_DEVICE_INFO_PREFIX, DEVICE_INFO_KEYS,
    MSH_FIELD_DEVICE_ID, MSH_FIELD_DATETIME, MSH_FIELD_MESSAGE_TYPE,
    MSH_FIELD_CONTROL_ID, MSH_FIELD_VERSION, MSH_FIELD_ACCEPT_ACK,
    MSH_FIELD_APP_ACK, MSH_FIELD_CHARSET, MSH_FIELD_PROFILE,
    PID_FIELD_ID, PID_FIELD_NAME,
    OBR_FIELD_PLACER_ORDER, OBR_FIELD_FILLER_ORDER, OBR_FIELD_SERVICE_ID,
    OBR_FIELD_OBSERVATION_TIME, OBR_FIELD_OBSERVATION_END,
    OBX_FIELD_VALUE_TYPE, OBX_FIELD_IDENTIFIER, OBX_FIELD_SUB_ID,
    OBX_FIELD_VALUE, OBX_FIELD_UNITS, OBX_FIELD_REFERENCE_RANGE,
    OBX_FIELD_ABNORMAL_FLAGS, OBX_FIELD_STATUS, OBX_FIELD_OBSERVATION_TIME,
    OBX_FIELD_OBSERVER, OBX_FIELD_ANALYSIS_TIME,
    NTE_FIELD_COMMENT,
)


logger = get_logger("sd_biosensor_f200_parser")


# `20260713114138-0500` / `20260713114138` / `202607131141` / `20260713`
_TS_RE = re.compile(
    r"^(\d{4})(\d{2})(\d{2})"           # tanggal
    r"(?:(\d{2})(\d{2})(?:(\d{2}))?)?"  # jam opsional
    r"(\.\d+)?"                         # pecahan detik opsional
    r"([+-]\d{4})?$"                    # offset zona opsional
)

# `eAG = 134.11 mg/dL` → nama, nilai, satuan
_DERIVED_RE = re.compile(
    r"^\s*([A-Za-z][A-Za-z0-9_%/-]*)\s*=\s*"
    r"(-?\d+(?:\.\d+)?)\s*"
    r"(.*?)\s*$"
)


def field(fields: list, index: int) -> str:
    """Ambil field ke-`index` (nomor field HL7); string kosong bila tidak ada."""
    if 0 <= index < len(fields):
        return fields[index].strip()
    return ""


def component(value: str, index: int = 0) -> str:
    """Ambil komponen ke-`index` dari sebuah field (dipisah `^`)."""
    if not value:
        return ""
    parts = value.split(COMPONENT_SEP)
    if 0 <= index < len(parts):
        return parts[index].strip()
    return ""


def to_iso8601(value: str) -> str:
    """
    Ubah timestamp HL7 alat menjadi ISO8601, **offset zona dipertahankan**.

        "20260713114138-0500" → "2026-07-13T11:41:38-05:00"
        "20260713114138"      → "2026-07-13T11:41:38"
        "20260713"            → "2026-07-13"
        "" / tidak dikenal    → dikembalikan apa adanya

    Offset sengaja tidak dikonversi ke zona lab di sini: konversi adalah
    keputusan lapisan penyimpanan/tampilan, sedangkan driver harus melaporkan
    apa yang dikatakan alat berikut konteks zonanya.
    """
    text = (value or "").strip()
    if not text:
        return ""

    m = _TS_RE.match(text)
    if not m:
        logger.warning(f"Format timestamp tidak dikenal: {text!r}")
        return text

    year, month, day, hour, minute, second, frac, offset = m.groups()

    iso = f"{year}-{month}-{day}"
    if hour is not None:
        iso += f"T{hour}:{minute}:{second or '00'}"
        if frac:
            iso += frac
    if offset:
        iso += f"{offset[:3]}:{offset[3:]}"
    return iso


def normalize_reference_range(value: str) -> str:
    """
    Ubah `[4.00;15.00]` menjadi `4.00-15.00`.

    Bentuk lain diteruskan setelah dirapikan, supaya firmware yang memakai
    format HL7 biasa tetap terbaca.
    """
    text = (value or "").strip()
    if text in EMPTY_REFERENCE_RANGE:
        return ""

    if text.startswith(RANGE_OPEN) and text.endswith(RANGE_CLOSE):
        inner = text[1:-1].strip()
        if RANGE_SEPARATOR in inner:
            low, _, high = inner.partition(RANGE_SEPARATOR)
            low, high = low.strip(), high.strip()
            if not low and not high:
                return ""
            return f"{low}-{high}"
        return inner

    return text.replace("\\", "-")


def parse_device_info(comment: str) -> dict:
    """
    Pecah NTE informasi alat menjadi dict.

        "Device Information,Date of manufacture=20260215,LotNo=014,
         SerialNo=00000,Kind=FLine1"
        → {"manufactured": "20260215", "lot_no": "014",
           "serial_no": "00000", "device_kind": "FLine1"}

    Kunci yang tidak dikenal tetap disertakan apa adanya agar firmware yang
    menambah field baru tidak diam-diam kehilangan data.
    """
    info = {}
    for chunk in (comment or "").split(","):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        key, _, val = chunk.partition("=")
        key, val = key.strip(), val.strip()
        info[DEVICE_INFO_KEYS.get(key, key)] = val
    return info


def parse_derived_values(comment: str) -> list:
    """
    Pecah NTE nilai turunan menjadi list dict.

        "eAG = 134.11 mg/dL, IFCC = 45.36 %"
        → [{"name": "eAG",  "value": "134.11", "unit": "mg/dL"},
           {"name": "IFCC", "value": "45.36",  "unit": "%"}]

    Satuan diteruskan apa adanya — lihat catatan IFCC di constants.py.
    """
    values = []
    for chunk in (comment or "").split(","):
        m = _DERIVED_RE.match(chunk)
        if not m:
            continue
        name, value, unit = m.groups()
        values.append({"name": name, "value": value, "unit": unit})
    return values


class SDBiosensorF200Parser:
    """Parser pesan IHE PCD-01 SD Biosensor STANDARD F."""

    def __init__(self):
        # HL7Parser dipakai untuk MLLP envelope handling (identik antar vendor).
        self._hl7 = HL7Parser()

    # ============================================================
    # Transport
    # ============================================================

    def unwrap_mllp(self, raw_bytes: bytes) -> bytes:
        """Hapus envelope MLLP `<VT>...<FS><CR>`."""
        return self._hl7.unwrap_mllp(raw_bytes)

    # ============================================================
    # Segment / field splitting
    # ============================================================

    def split_message(self, hl7_bytes: bytes) -> list:
        """
        Pecah pesan HL7 menjadi list segment, tiap segment berupa list field.

        Index list = nomor field HL7. Decode UTF-8 sesuai MSH-18
        (`UNICODE UTF-8`).
        """
        text = hl7_bytes.decode(MESSAGE_ENCODING, errors="replace")
        segments = []

        for line in text.split(SEGMENT_TERMINATOR):
            line = line.strip("\n").strip()
            if not line or len(line) < 3:
                continue
            segments.append(self.split_segment(line))

        return segments

    def split_segment(self, segment_text: str) -> list:
        """Pecah satu segment menjadi list field (index = nomor field HL7)."""
        if segment_text[:3] == SEG_MSH:
            separator = segment_text[3] if len(segment_text) > 3 else FIELD_SEPARATOR
            rest = segment_text[4:].split(separator)
            return [SEG_MSH, separator] + rest

        return segment_text.split(FIELD_SEPARATOR)

    def segment_type(self, fields: list) -> str:
        return fields[0] if fields else ""

    def find_segment(self, segments: list, seg_type: str) -> list | None:
        """Cari segment pertama dengan tipe tertentu."""
        for fields in segments:
            if self.segment_type(fields) == seg_type:
                return fields
        return None

    # ============================================================
    # Segment-specific mapping
    # ============================================================

    def parse_msh(self, fields: list) -> dict:
        """
        Petakan MSH ke dict konteks pesan.

        MSH-3 membawa identitas alat `<serial>^<EUI-64>^EUI-64`; nilainya
        berbeda per unit fisik, jadi tidak boleh di-hardcode di mana pun.
        """
        device_id = field(fields, MSH_FIELD_DEVICE_ID)
        parsed = {
            "device_id": device_id,
            "device_serial": component(device_id, 0),
            "device_eui64": component(device_id, 1),
            "datetime": field(fields, MSH_FIELD_DATETIME),
            "message_type": field(fields, MSH_FIELD_MESSAGE_TYPE),
            "control_id": field(fields, MSH_FIELD_CONTROL_ID),
            "version_id": field(fields, MSH_FIELD_VERSION),
            "accept_ack_type": field(fields, MSH_FIELD_ACCEPT_ACK),
            "app_ack_type": field(fields, MSH_FIELD_APP_ACK),
            "character_set": field(fields, MSH_FIELD_CHARSET),
            "profile": component(field(fields, MSH_FIELD_PROFILE), 0),
        }
        logger.info(
            f"MSH: type={parsed['message_type']}, "
            f"control_id={parsed['control_id']}, "
            f"device={parsed['device_serial'] or '-'}, "
            f"v{parsed['version_id'] or '-'}"
        )
        return parsed

    def parse_pid(self, fields: list) -> dict:
        """
        Petakan PID.

        Pada log, PID-3 berisi nomor sampel berformat `YYMMDD` + urutan
        (`2607130052` = 2026-07-13 sampel ke-52) dan PID-5 hanya `^^^^^^U`
        (nama tidak diisi). PID-3 karena itu satu-satunya identitas yang bisa
        dipakai mencocokkan hasil dengan order di LIS.
        """
        parsed = {
            "patient_id": component(field(fields, PID_FIELD_ID), 0),
            "name": self._patient_name(field(fields, PID_FIELD_NAME)),
        }
        logger.info(f"PID: id={parsed['patient_id'] or '-'}")
        return parsed

    def _patient_name(self, value: str) -> str:
        """
        Rakit nama pasien dari PID-5 (`Last^First^Middle`).

        Komponen ke-7 adalah name type code (`U` = unspecified pada log), bukan
        bagian nama — jadi hanya tiga komponen pertama yang dipakai.
        """
        if not value:
            return ""
        parts = [p.strip() for p in value.split(COMPONENT_SEP)[:3] if p.strip()]
        if not parts:
            return ""
        if len(parts) == 1:
            return parts[0]
        last, rest = parts[0], parts[1:]
        return " ".join(rest + [last])

    def parse_obr(self, fields: list) -> dict:
        """Petakan OBR. OBR-2/OBR-3 berisi GUID unik per pemeriksaan."""
        service_id = field(fields, OBR_FIELD_SERVICE_ID)
        placer = field(fields, OBR_FIELD_PLACER_ORDER)
        parsed = {
            "order_guid": component(placer, 0),
            "filler_guid": component(field(fields, OBR_FIELD_FILLER_ORDER), 0),
            "test_code": component(service_id, 0),
            "test_name": component(service_id, 1),
            "coding_system": component(service_id, 2),
            "observation_datetime": to_iso8601(
                field(fields, OBR_FIELD_OBSERVATION_TIME)
            ),
            "observation_end": to_iso8601(
                field(fields, OBR_FIELD_OBSERVATION_END)
            ),
        }
        logger.info(
            f"OBR: test={parsed['test_name'] or '-'}, "
            f"guid={parsed['order_guid'][:8] if parsed['order_guid'] else '-'}"
        )
        return parsed

    def parse_obx(self, fields: list) -> dict:
        """
        Petakan OBX.

        OBX-4 adalah containment tree PCD-01 (`1.0.0.7`) — penanda posisi
        pengukuran di hierarki alat, bukan sub-ID biasa. Disimpan agar tidak
        hilang, tapi tidak dipakai sebagai identitas tes.
        """
        identifier = field(fields, OBX_FIELD_IDENTIFIER)
        parsed = {
            "value_type": field(fields, OBX_FIELD_VALUE_TYPE),
            "test_code": component(identifier, 0),
            "test_name": component(identifier, 1),
            "coding_system": component(identifier, 2),
            "containment_id": field(fields, OBX_FIELD_SUB_ID),
            "value": field(fields, OBX_FIELD_VALUE),
            "unit": component(field(fields, OBX_FIELD_UNITS), 0),
            "unit_text": component(field(fields, OBX_FIELD_UNITS), 1),
            "unit_system": component(field(fields, OBX_FIELD_UNITS), 2),
            "reference_range": normalize_reference_range(
                field(fields, OBX_FIELD_REFERENCE_RANGE)
            ),
            "flag": field(fields, OBX_FIELD_ABNORMAL_FLAGS),
            "status": field(fields, OBX_FIELD_STATUS),
            "observation_datetime": to_iso8601(
                field(fields, OBX_FIELD_OBSERVATION_TIME)
            ),
            "observer": field(fields, OBX_FIELD_OBSERVER),
            "analysis_datetime": to_iso8601(
                field(fields, OBX_FIELD_ANALYSIS_TIME)
            ),
        }
        logger.info(
            f"OBX: test={parsed['test_code']}^{parsed['test_name']}, "
            f"value={parsed['value']} {parsed['unit']}, "
            f"ref={parsed['reference_range'] or '-'}"
        )
        return parsed

    def parse_nte(self, fields: list) -> dict:
        """
        Petakan NTE menjadi dict berisi teks mentah + hasil pecahannya.

        Jenis NTE ditentukan pemanggil lewat segment induk, bukan di sini —
        lihat iter_groups().
        """
        comment = field(fields, NTE_FIELD_COMMENT)
        return {
            "comment": comment,
            "is_device_info": comment.startswith(NTE_DEVICE_INFO_PREFIX),
        }

    def parse_msa(self, fields: list) -> dict:
        """Petakan MSA: MSA-1 = AA/AE/AR, MSA-2 = control ID."""
        return {
            "ack_code": field(fields, 1),
            "control_id": field(fields, 2),
            "text_message": field(fields, 3),
            "error_condition": field(fields, 6),
        }

    # ============================================================
    # Message-level helpers
    # ============================================================

    def get_message_type(self, raw_bytes: bytes) -> str:
        """Ambil MSH-9 dari raw bytes (dengan atau tanpa envelope MLLP)."""
        try:
            segments = self.split_message(self.unwrap_mllp(raw_bytes))
            msh = self.find_segment(segments, SEG_MSH)
            if msh:
                return field(msh, MSH_FIELD_MESSAGE_TYPE)
        except Exception as e:
            logger.warning(f"Gagal baca message type: {e}")
        return ""

    def iter_groups(self, segments: list) -> list:
        """
        Kelompokkan segment menjadi struktur PCD-01: OBR → [OBX → [NTE]].

        Ini yang menyelesaikan ambiguitas NTE. Dua NTE dalam satu pesan
        sama-sama bernomor `NTE|1`, tapi artinya ditentukan induknya:

            OBR
            NTE  ← informasi alat (lot, serial, kind)
            OBX
            NTE  ← nilai turunan (eAG, IFCC)

        Returns:
            List dict: {"obr": dict|None, "obr_notes": list,
                        "observations": [{"obx": dict, "notes": list}]}
        """
        groups = []
        current = None
        current_obx = None

        for fields in segments:
            seg = self.segment_type(fields)

            if seg == "OBR":
                current = {
                    "obr": self.parse_obr(fields),
                    "obr_notes": [],
                    "observations": [],
                }
                current_obx = None
                groups.append(current)

            elif seg == "OBX":
                if current is None:
                    # OBX tanpa OBR — tetap ditampung agar hasil tidak hilang
                    current = {"obr": None, "obr_notes": [], "observations": []}
                    groups.append(current)
                current_obx = {"obx": self.parse_obx(fields), "notes": []}
                current["observations"].append(current_obx)

            elif seg == "NTE":
                note = self.parse_nte(fields)
                if current is None:
                    continue
                if current_obx is not None:
                    current_obx["notes"].append(note)
                else:
                    current["obr_notes"].append(note)

        return groups
