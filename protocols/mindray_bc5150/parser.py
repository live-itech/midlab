"""
protocols/mindray_bc5150/parser.py — Parser HL7 v2.3.1 Mindray BC-5150

Memecah pesan MLLP menjadi segment/field lalu memetakannya sesuai posisi yang
benar-benar dipakai BC-5150. Semua posisi field di bawah diverifikasi langsung
dari log komunikasi alat, bukan dari asumsi HL7 generic:

    ORC|RF||raisya||IP
        ORC-1 = RF (order control)   ORC-3 = sample ID   ORC-5 = IP

    OBR|1||raisya|00001^Automated Count^99MRC|||20190504060725|...|HM|...|Service
        OBR-3 = sample ID   OBR-4 = service ID   OBR-7 = waktu periksa
        OBR-24 = HM         OBR-32 = interpreter

    OBX|5|NM|6690-2^WBC^LN||2.78|10*3/uL|4.00-10.00|L~N|||F
        OBX-3 = code^name^system   OBX-5 = nilai   OBX-6 = satuan
        OBX-7 = reference range    OBX-8 = flag    OBX-11 = status

Semua accessor toleran terhadap field yang hilang atau berlebih — jumlah field
trailing berbeda antar firmware.
"""

from lib.utils import get_logger
from protocols.hl7.parser import HL7Parser
from protocols.mindray_bc5150.constants import (
    FIELD_SEPARATOR, COMPONENT_SEP, REPEAT_SEP, SEGMENT_TERMINATOR,
    MESSAGE_ENCODING,
    SEG_MSH, SEG_ORC,
    EMPTY_REFERENCE_RANGE,
    RUN_MODE_CODES,
    ABNORMAL_INDICATORS, FLAG_NORMAL, FLAG_SUSPECT,
    ESCAPE_SEQUENCES,
    ORC_FIELD_ORDER_CONTROL, ORC_FIELD_SAMPLE_ID, ORC_FIELD_ORDER_STATUS,
    OBR_FIELD_SAMPLE_ID, OBR_FIELD_SERVICE_ID, OBR_FIELD_OBSERVATION_TIME,
    OBR_FIELD_DRAW_TIME, OBR_FIELD_SENDER, OBR_FIELD_CLINICAL_INFO,
    OBR_FIELD_RECEIVED_TIME,
    OBR_FIELD_SERVICE_SECTION, OBR_FIELD_INTERPRETER,
)


logger = get_logger("mindray_bc5150_parser")


def field(fields: list, index: int) -> str:
    """
    Ambil field ke-`index` (nomor field HL7) dari hasil split_segment().
    Return string kosong bila field tidak ada.
    """
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


def unescape(value: str) -> str:
    """
    Pulihkan escape sequence HL7 (dok §3) menjadi karakter aslinya.

    Kebalikan dari builder.escape(). `\\E\\` dipulihkan paling akhir supaya
    backslash hasil substitusi tidak ikut dibaca sebagai awal escape lain.
    """
    if not value or "\\" not in value:
        return value

    text = value
    for original, sequence in reversed(ESCAPE_SEQUENCES):
        if original == "\\":
            continue
        text = text.replace(sequence, original)
    return text.replace("\\E\\", "\\")


def normalize_reference_range(value: str) -> str:
    """
    Samakan format reference range ke `low-high` agar seragam dengan payload
    EazyApp. BC-5150 mengirim `4.00-10.00`; tes tanpa range dikirim kosong.
    Sebagian firmware Mindray memakai backslash (warisan ASTM).
    """
    text = (value or "").strip()
    if text in EMPTY_REFERENCE_RANGE:
        return ""
    return text.replace("\\", "-")


def split_abnormal_flag(value: str) -> tuple[str, bool]:
    """
    Pecah OBX-8 BC-5150 menjadi (flag_abnormal, suspect).

    Alat menggabungkan dua informasi berbeda dalam satu field ber-repeat:

        "N"    → ("N", False)   in-range, tanpa alarm
        "A"    → ("N", True)    in-range, kanal kena alarm
        "L~N"  → ("L", False)   di bawah range
        "L~A"  → ("L", True)    di bawah range + alarm
        "H~A"  → ("H", True)    di atas range + alarm
        ""     → ("",  False)   alat tidak mengirim flag (mis. OBX tipe IS)

    Memisahkannya penting: `flag` pada ResultObject dipakai LIS sebagai
    indikator L/H/N standar, sedangkan penanda suspect adalah informasi
    kualitas run yang akan salah arti bila ikut dikirim sebagai flag.
    """
    text = (value or "").strip()
    if not text:
        return "", False

    repeats = [r.strip() for r in text.split(REPEAT_SEP) if r.strip()]
    if not repeats:
        return "", False

    suspect = FLAG_SUSPECT in repeats
    abnormal = repeats[0] if repeats[0] in ABNORMAL_INDICATORS else FLAG_NORMAL
    return abnormal, suspect


class MindrayBC5150Parser:
    """Parser pesan HL7 Mindray BC-5150."""

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

        Index list = nomor field HL7, jadi `fields[9]` pada MSH benar-benar
        MSH-9. Untuk MSH, field separator disisipkan sebagai MSH-1 supaya
        penomoran konsisten dengan segment lain.

        Decode memakai UTF-8 (MSH-18 = UNICODE), bukan ASCII seperti BS-200E.
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
            # MSH-1 adalah field separator itu sendiri, jadi tidak ikut ter-split.
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

        Pesan dari BC-5150 mengosongkan MSH-3..MSH-6 (lihat log), jadi tidak ada
        identitas pengirim yang bisa dipantulkan — balasan memakai nilai tetap
        dari constants.
        """
        parsed = {
            "sending_app": field(fields, 3),
            "sending_facility": field(fields, 4),
            "datetime": field(fields, 7),
            "message_type": field(fields, 9),
            "control_id": field(fields, 10),
            "processing_id": field(fields, 11),
            "version_id": field(fields, 12),
            "character_set": field(fields, 18),
        }
        logger.info(
            f"MSH: type={parsed['message_type']}, control_id={parsed['control_id']}, "
            f"charset={parsed['character_set'] or '-'}"
        )
        return parsed

    def parse_pid(self, fields: list) -> dict:
        """
        Petakan PID.

        Pada log alat mengirim `PID|1||^^^^MR` — identitas pasien kosong karena
        BC-5150 hanya meneruskan apa yang diketik di alat. Parser tetap membaca
        PID-3/PID-5/PID-7/PID-8 agar lab yang mengisi data pasien di alat tidak
        kehilangan informasi itu.
        """
        parsed = {
            "patient_id": component(field(fields, 3), 0),
            "name": self._patient_name(field(fields, 5)),
            "dob": field(fields, 7),
            "gender": field(fields, 8),
        }
        if parsed["patient_id"] or parsed["name"]:
            logger.info(
                f"PID: id={parsed['patient_id'] or '-'}, name={parsed['name'] or '-'}"
            )
        return parsed

    def _patient_name(self, value: str) -> str:
        """
        Rakit nama pasien dari PID-5.

        HL7 memakai `Last^First^Middle`; sebagian firmware Mindray mengirim nama
        polos tanpa komponen. Dua-duanya dirapikan jadi satu string.
        """
        if not value:
            return ""
        parts = [unescape(p.strip()) for p in value.split(COMPONENT_SEP) if p.strip()]
        if not parts:
            return ""
        if len(parts) == 1:
            return parts[0]
        # Last^First^Middle → "First Middle Last"
        last, rest = parts[0], parts[1:]
        return " ".join(rest + [last])

    def parse_orc(self, fields: list) -> dict:
        """
        Petakan ORC dari pesan ORM^O01.

        ORC-3 adalah satu-satunya pembawa sample ID pada permintaan order —
        pada log nilainya bisa berupa barcode (`1905040011`) maupun teks bebas
        yang diketik operator (`raisya`, `irfan`, `j`).
        """
        parsed = {
            "order_control": field(fields, ORC_FIELD_ORDER_CONTROL),
            "sample_id": field(fields, ORC_FIELD_SAMPLE_ID),
            "order_status": field(fields, ORC_FIELD_ORDER_STATUS),
        }
        logger.info(
            f"ORC: control={parsed['order_control']}, "
            f"sample_id={parsed['sample_id'] or '-'}"
        )
        return parsed

    def parse_obr(self, fields: list) -> dict:
        """
        Petakan OBR dari pesan ORU^R01.

        Dok Tabel 4-6 membedakan tiga waktu yang mudah tertukar:

            OBR-6   waktu darah diambil dari pasien  → specimen.collected_at
            OBR-7   waktu alat memeriksa sampel      → waktu hasil
            OBR-14  waktu order dibuat

        Alat pada log hanya mengisi OBR-7, jadi `collected_at` jatuh kembali ke
        OBR-7 bila OBR-6 kosong — tapi bila lab mengisi waktu pengambilan,
        nilai itulah yang dipakai.
        """
        service_id = field(fields, OBR_FIELD_SERVICE_ID)
        draw_time = field(fields, OBR_FIELD_DRAW_TIME)
        observation_time = field(fields, OBR_FIELD_OBSERVATION_TIME)
        parsed = {
            "sample_id": field(fields, OBR_FIELD_SAMPLE_ID),
            "panel_code": component(service_id, 0),
            "panel_name": component(service_id, 1),
            "draw_datetime": draw_time,
            "observation_datetime": observation_time,
            "collected_at": draw_time or observation_time,
            "received_datetime": field(fields, OBR_FIELD_RECEIVED_TIME),
            "sender": field(fields, OBR_FIELD_SENDER),
            "clinical_info": unescape(field(fields, OBR_FIELD_CLINICAL_INFO)),
            "service_section": field(fields, OBR_FIELD_SERVICE_SECTION),
            "interpreter": field(fields, OBR_FIELD_INTERPRETER),
        }
        logger.info(
            f"OBR: sample_id={parsed['sample_id'] or '-'}, "
            f"panel={parsed['panel_name'] or '-'}, "
            f"time={parsed['observation_datetime'] or '-'}"
        )
        return parsed

    def parse_pv1(self, fields: list) -> dict:
        """
        Petakan PV1 (dok §4.3.4).

        PV1-3 memakai tipe PL: `<point of care>^<room>^<bed>`. Contoh dok
        `PV1|1||ICU^^BedNO1`, contoh pesan sampel `PV1|1|Neike|Hema^^BN1|...`
        dengan PV1-2 = tipe pasien dan PV1-20 = cara bayar.

        Alat pada log hanya mengirim `PV1|1` (kosong), tapi lab yang mengisi
        bangsal/bed di alat tidak boleh kehilangan informasi itu.
        """
        location = field(fields, 3)
        parsed = {
            "patient_type": field(fields, 2),
            "point_of_care": component(location, 0),
            "room": component(location, 1),
            "bed": component(location, 2),
            "charge_type": field(fields, 20),
        }
        if any(parsed.values()):
            logger.info(
                f"PV1: poc={parsed['point_of_care'] or '-'}, "
                f"bed={parsed['bed'] or '-'}"
            )
        return parsed

    def parse_obx(self, fields: list) -> dict:
        """
        Petakan OBX BC-5150.

        OBX-3 selalu `code^name^codingsystem` (`6690-2^WBC^LN` untuk parameter
        standar, `10002^PCT^99MRC` untuk parameter khas Mindray). Flag OBX-8
        dipecah menjadi indikator abnormal + penanda suspect.
        """
        identifier = field(fields, 3)
        raw_flag = field(fields, 8)
        abnormal, suspect = split_abnormal_flag(raw_flag)

        test_code = component(identifier, 0)
        parsed = {
            "value_type": field(fields, 2),
            "test_code": test_code,
            "test_name": component(identifier, 1),
            "coding_system": component(identifier, 2),
            "value": unescape(field(fields, 5)),
            "unit": field(fields, 6),
            "reference_range": normalize_reference_range(field(fields, 7)),
            "flag": abnormal,
            "suspect": suspect,
            "raw_flag": raw_flag,
            "status": field(fields, 11),
            "is_run_mode": test_code in RUN_MODE_CODES,
            "run_mode_label": RUN_MODE_CODES.get(test_code, ""),
        }
        logger.info(
            f"OBX: test={parsed['test_code']}^{parsed['test_name']}, "
            f"value={parsed['value']} {parsed['unit']}, "
            f"flag={parsed['flag'] or '-'}{' suspect' if suspect else ''}"
        )
        return parsed

    def parse_msa(self, fields: list) -> dict:
        """Petakan MSA: MSA-1 = AA/AE/AR, MSA-2 = control ID, MSA-6 = status."""
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
                return field(msh, 9)
        except Exception as e:
            logger.warning(f"Gagal baca message type: {e}")
        return ""

    def parse_order_request(self, raw_bytes: bytes) -> dict:
        """
        Parse pesan ORM^O01 menjadi konteks permintaan order: data MSH + ORC.

        Dipakai handle_enq() dan saat membangun balasan ORR^O02 (control ID
        pesan alat harus dipantulkan ke MSA-2).
        """
        context = {}
        try:
            segments = self.split_message(self.unwrap_mllp(raw_bytes))
        except Exception as e:
            logger.warning(f"Gagal parse ORM^O01: {e}")
            return context

        msh = self.find_segment(segments, SEG_MSH)
        if msh:
            context.update(self.parse_msh(msh))

        orc = self.find_segment(segments, SEG_ORC)
        if orc:
            context["orc"] = self.parse_orc(orc)
            context["sample_id"] = context["orc"]["sample_id"]

        return context
