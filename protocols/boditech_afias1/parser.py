"""
protocols/boditech_afias1/parser.py — Parser HL7 Boditech AFIAS-1

Mengurai OUL^R24 sesuai posisi field contoh slide 5 manual LIS AFIAS-1.
Toleran terhadap pesan dengan atau tanpa envelope MLLP; framing ditentukan
receiver.
"""

import re

from lib.utils import get_logger
from protocols.hl7.parser import HL7Parser
from protocols.boditech_afias1.constants import (
    FIELD_SEPARATOR, COMPONENT_SEP, SEGMENT_TERMINATOR, MESSAGE_ENCODING,
    SEG_MSH,
    MSH_FIELD_MODEL, MSH_FIELD_SERIAL, MSH_FIELD_DATETIME,
    MSH_FIELD_MESSAGE_TYPE, MSH_FIELD_CONTROL_ID, MSH_FIELD_PROCESSING_ID,
    MSH_FIELD_VERSION,
    PID_FIELD_PATIENT_ID, PID_FIELD_IDENTIFIER,
    OBR_FIELD_PLACER_ORDER, OBR_FIELD_FILLER_ORDER, OBR_FIELD_OPERATOR,
    ORC_FIELD_18,
    SPM_FIELD_CARTRIDGE_LOT, SPM_FIELD_CARTRIDGE_EXPIRY,
    OBX_FIELD_VALUE_TYPE, OBX_FIELD_IDENTIFIER, OBX_FIELD_VALUE,
    OBX_FIELD_UNITS, OBX_FIELD_REFERENCE_RANGE, OBX_FIELD_ABNORMAL_FLAGS,
    OBX_FIELD_STATUS,
)


logger = get_logger("boditech_afias1_parser")

# `20200305171553.205+0900` / `20200305171553` / `202003051715` / `20200305`
_TS_RE = re.compile(
    r"^(\d{4})(\d{2})(\d{2})"           # tanggal
    r"(?:(\d{2})(\d{2})(?:(\d{2}))?)?"  # jam opsional
    r"(\.\d+)?"                         # pecahan detik opsional
    r"([+-]\d{4})?$"                    # offset zona opsional
)


def field(fields: list, index: int) -> str:
    """Field ke-`index` (nomor field HL7); string kosong bila tidak ada."""
    if 0 <= index < len(fields):
        return fields[index].strip()
    return ""


def component(value: str, index: int = 0) -> str:
    """Komponen ke-`index` sebuah field (dipisah `^`)."""
    if not value:
        return ""
    parts = value.split(COMPONENT_SEP)
    if 0 <= index < len(parts):
        return parts[index].strip()
    return ""


def to_iso8601(value: str) -> str:
    """
    Timestamp HL7 → ISO8601, pecahan detik dan offset zona dipertahankan.

        "20200305171553.205+0900" → "2020-03-05T17:15:53.205+09:00"
        "20210719"                → "2021-07-19"
        "" / tidak dikenal        → dikembalikan apa adanya
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


class AfiasParser:
    """Parser HL7 AFIAS-1 (arah alat → LIS)."""

    def __init__(self):
        # HL7Parser dipakai untuk envelope MLLP (identik antar vendor).
        self._hl7 = HL7Parser()

    def unwrap_mllp(self, raw_bytes: bytes) -> bytes:
        """Hapus envelope MLLP `<VT>...<FS><CR>` bila ada."""
        return self._hl7.unwrap_mllp(raw_bytes)

    def split_message(self, hl7_bytes: bytes) -> list:
        """Pesan → list segment; tiap segment list field (index = nomor field)."""
        text = hl7_bytes.decode(MESSAGE_ENCODING, errors="replace")
        segments = []
        for line in text.split(SEGMENT_TERMINATOR):
            line = line.strip("\n").strip()
            if len(line) < 3:
                continue
            segments.append(self.split_segment(line))
        return segments

    def split_segment(self, segment_text: str) -> list:
        if segment_text[:3] == SEG_MSH:
            separator = segment_text[3] if len(segment_text) > 3 else FIELD_SEPARATOR
            return [SEG_MSH, separator] + segment_text[4:].split(separator)
        return segment_text.split(FIELD_SEPARATOR)

    def find_segment(self, segments: list, seg_type: str) -> list | None:
        for fields in segments:
            if fields and fields[0] == seg_type:
                return fields
        return None

    def find_all(self, segments: list, seg_type: str) -> list:
        return [fields for fields in segments if fields and fields[0] == seg_type]

    def parse_msh(self, fields: list) -> dict:
        parsed = {
            "model": field(fields, MSH_FIELD_MODEL),
            "serial": field(fields, MSH_FIELD_SERIAL),
            "datetime": field(fields, MSH_FIELD_DATETIME),
            "message_type": field(fields, MSH_FIELD_MESSAGE_TYPE),
            "control_id": field(fields, MSH_FIELD_CONTROL_ID),
            "processing_id": field(fields, MSH_FIELD_PROCESSING_ID),
            "version_id": field(fields, MSH_FIELD_VERSION),
        }
        logger.info(
            f"MSH: type={parsed['message_type']}, control_id={parsed['control_id']}, "
            f"model={parsed['model'] or '-'}, serial={parsed['serial'] or '-'}"
        )
        return parsed

    def parse_pid(self, fields: list) -> dict:
        """ID sampel: PID-3 (komponen pertama) didahulukan, cadangan PID-2."""
        identifier = component(field(fields, PID_FIELD_IDENTIFIER), 0)
        patient_id = field(fields, PID_FIELD_PATIENT_ID)
        return {"sample_id": identifier or patient_id}

    def parse_obr(self, fields: list) -> dict:
        return {
            "placer_order": field(fields, OBR_FIELD_PLACER_ORDER),
            "filler_order": field(fields, OBR_FIELD_FILLER_ORDER),
            "operator": field(fields, OBR_FIELD_OPERATOR),
        }

    def parse_orc(self, fields: list) -> dict:
        return {"orc18": field(fields, ORC_FIELD_18)}

    def parse_spm(self, fields: list) -> dict:
        return {
            "cartridge_lot": field(fields, SPM_FIELD_CARTRIDGE_LOT),
            "cartridge_expiry": field(fields, SPM_FIELD_CARTRIDGE_EXPIRY),
        }

    def parse_obx(self, fields: list) -> dict:
        identifier = field(fields, OBX_FIELD_IDENTIFIER)
        code = component(identifier, 0)
        parsed = {
            "value_type": field(fields, OBX_FIELD_VALUE_TYPE),
            "test_code": code,
            "test_name": component(identifier, 1) or code,
            "value": field(fields, OBX_FIELD_VALUE),
            "unit": component(field(fields, OBX_FIELD_UNITS), 0),
            "reference_range": field(fields, OBX_FIELD_REFERENCE_RANGE),
            "flag": field(fields, OBX_FIELD_ABNORMAL_FLAGS),
            "status": field(fields, OBX_FIELD_STATUS),
        }
        logger.info(f"OBX: {code}={parsed['value']} {parsed['unit']}")
        return parsed

    def get_message_type(self, raw_bytes: bytes) -> str:
        """MSH-9 dari raw bytes (dengan atau tanpa MLLP); "" bila tak terbaca."""
        try:
            msh = self.find_segment(self.split_message(self.unwrap_mllp(raw_bytes)), SEG_MSH)
            if msh:
                return field(msh, MSH_FIELD_MESSAGE_TYPE)
        except Exception as e:
            logger.warning(f"Gagal baca message type: {e}")
        return ""
