"""
protocols/mindray_bs200e/parser.py — Parser HL7 v2.3.1 Mindray BS-200E

Memecah pesan MLLP menjadi segment dan field, lalu memetakan field sesuai
posisi yang dipakai Mindray (bukan posisi HL7 generic). Beda utama dengan
protocols/hl7/parser.py:

- PID-3 (medical record) sering kosong; nomor pasien ada di PID-2
- PID-5 berisi nama polos, bukan Last^First
- OBR-2 = barcode sampel (yang dipakai LIS), OBR-3 = sample ID internal alat
- OBX-3 = test No. polos, nama tes ada di OBX-4 (bukan OBX-3 komponen 2)
- MSH-16 = tipe hasil (0=sampel, 1=kalibrasi, 2=QC)

Semua accessor toleran terhadap field yang hilang atau berlebih, karena tiap
firmware Mindray bisa sedikit berbeda jumlah field trailing-nya.
"""

from lib.utils import get_logger
from protocols.hl7.parser import HL7Parser
from protocols.mindray_bs200e.constants import (
    FIELD_SEPARATOR, COMPONENT_SEP, SEGMENT_TERMINATOR,
    SEG_MSH, SEG_QRD,
    DEFAULT_MANUFACTURER, DEFAULT_MODEL,
    EMPTY_REFERENCE_RANGE,
    QRD_FILTER_CANCEL,
)


logger = get_logger("mindray_bs200e_parser")


def field(fields: list, index: int) -> str:
    """
    Ambil field ke-`index` (nomor field HL7) dari list hasil split_segment().
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


def normalize_reference_range(value: str) -> str:
    """
    Samakan format reference range ke `low-high` agar seragam dengan payload
    EazyApp. Mindray mengirim `-` untuk tes tanpa range, dan sebagian firmware
    memakai backslash sebagai pemisah (warisan ASTM).
    """
    text = (value or "").strip()
    if text in EMPTY_REFERENCE_RANGE:
        return ""
    return text.replace("\\", "-")


class MindrayParser:
    """Parser pesan HL7 Mindray BS-200E."""

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
        """
        text = hl7_bytes.decode("ascii", errors="replace")
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

        MSH-3/MSH-4 pesan alat berisi Manufacturer/Model — nilainya dipantulkan
        ke MSH-5/MSH-6 saat MidLab membalas, sesuai contoh di manual.
        """
        parsed = {
            "manufacturer": field(fields, 3) or DEFAULT_MANUFACTURER,
            "model": field(fields, 4) or DEFAULT_MODEL,
            "datetime": field(fields, 7),
            "message_type": field(fields, 9),
            "control_id": field(fields, 10),
            "processing_id": field(fields, 11),
            "version_id": field(fields, 12),
            "result_type": field(fields, 16),   # 0=sampel, 1=kalibrasi, 2=QC
            "character_set": field(fields, 18),
        }
        logger.info(
            f"MSH: type={parsed['message_type']}, control_id={parsed['control_id']}, "
            f"result_type={parsed['result_type'] or '-'}"
        )
        return parsed

    def parse_pid(self, fields: list) -> dict:
        """
        Petakan PID versi Mindray.

        PID-2 = nomor pasien, PID-3 = no. rekam medis, PID-4 = no. bed,
        PID-5 = nama, PID-7 = tanggal lahir, PID-8 = jenis kelamin,
        PID-9 = golongan darah.
        """
        # Manual menempatkan medical record di PID-3, tapi contoh pesan alat
        # mengisinya kosong dan hanya memakai PID-2. Ambil yang terisi.
        patient_id = component(field(fields, 3)) or component(field(fields, 2))

        # Nama biasanya polos ("Tommy"); tetap tangani bila ada Last^First.
        name_raw = field(fields, 5)
        name_parts = [p.strip() for p in name_raw.split(COMPONENT_SEP) if p.strip()]
        if len(name_parts) >= 2:
            name = f"{name_parts[1]} {name_parts[0]}"   # First Last
        else:
            name = name_parts[0] if name_parts else ""

        parsed = {
            "patient_id": patient_id,
            "bed_number": field(fields, 4),
            "name": name,
            "dob": field(fields, 7),
            "gender": field(fields, 8),
            "blood_type": field(fields, 9),
        }
        logger.info(
            f"PID: patient_id={parsed['patient_id']}, name={parsed['name']}, "
            f"gender={parsed['gender']}"
        )
        return parsed

    def parse_obr_sample(self, fields: list) -> dict:
        """
        Petakan OBR untuk hasil sampel (MSH-16 = 0).

        OBR-2 = barcode sampel (dipakai sebagai sample_id LIS),
        OBR-3 = sample ID internal alat (manual: jangan dianalisa server),
        OBR-7 = waktu pemeriksaan, OBR-15 = jenis sampel.
        """
        parsed = {
            "barcode": field(fields, 2),
            "internal_sample_id": field(fields, 3),
            "priority": field(fields, 5),              # Y = STAT
            "observation_datetime": field(fields, 7),
            "clinical_info": field(fields, 13),
            "sample_type": field(fields, 15),
            "sender": field(fields, 16),
            "sample_characteristic": field(fields, 18),  # icterus/hemolysis/lipemia
            "attending_doctor": field(fields, 20),
            "department": field(fields, 21),
        }
        logger.info(
            f"OBR: barcode={parsed['barcode']}, sample_type={parsed['sample_type']}"
        )
        return parsed

    def parse_obr_qc(self, fields: list) -> dict:
        """
        Petakan OBR untuk hasil QC (MSH-16 = 2).

        Pada pesan QC tidak ada OBX — seluruh hasil ada di OBR:
        OBR-2/3 = test No./nama, OBR-6 = waktu QC, OBR-13..21 = data kontrol.
        """
        parsed = {
            "test_code": field(fields, 2),
            "test_name": field(fields, 3),
            "qc_datetime": field(fields, 6),
            "control_name": field(fields, 13),
            "lot_number": field(fields, 14),
            "expiration_date": field(fields, 15),
            "level": field(fields, 17),        # H / M / L
            "mean_value": field(fields, 18),
            "standard_deviation": field(fields, 19),
            "value": field(fields, 20),
            "unit": field(fields, 21),
        }
        logger.info(
            f"OBR(QC): test={parsed['test_code']}, control={parsed['control_name']}, "
            f"value={parsed['value']}"
        )
        return parsed

    def parse_obr_calibration(self, fields: list) -> dict:
        """
        Petakan OBR untuk hasil kalibrasi (MSH-16 = 1).

        BS-200E tidak mengirim kalibrasi menurut manual, tapi seri lain di
        keluarga yang sama bisa — ringkasannya tetap direkam agar tidak hilang.
        """
        parsed = {
            "test_code": field(fields, 2),
            "test_name": field(fields, 3),
            "calibration_datetime": field(fields, 7),
            "calibration_rule": field(fields, 9),
            "k_factor": field(fields, 10),
            "calibrator_count": field(fields, 11),
            "calibrator_name": field(fields, 13),
            "lot_number": field(fields, 14),
            "value": field(fields, 20),        # calibration value
        }
        logger.info(
            f"OBR(CAL): test={parsed['test_code']}, rule={parsed['calibration_rule']}"
        )
        return parsed

    def parse_obx(self, fields: list) -> dict:
        """
        Petakan OBX versi Mindray.

        OBX-3 = test No. (polos, bukan code^name), OBX-4 = nama tes,
        OBX-13 = hasil original, OBX-16 = petugas.
        Tetap tangani bentuk `code^name` di OBX-3 kalau firmware mengirimnya.
        """
        identifier = field(fields, 3)
        test_code = component(identifier, 0)
        test_name = component(identifier, 1) or field(fields, 4)

        parsed = {
            "value_type": field(fields, 2),
            "test_code": test_code,
            "test_name": test_name,
            "value": field(fields, 5),
            "unit": component(field(fields, 6)),
            "reference_range": normalize_reference_range(field(fields, 7)),
            "flag": field(fields, 8),
            "status": field(fields, 11),
            "original_value": field(fields, 13),
            "observation_datetime": field(fields, 14),
            "operator": field(fields, 16),
        }
        logger.info(
            f"OBX: test={parsed['test_code']}, value={parsed['value']} "
            f"{parsed['unit']}, flag={parsed['flag']}"
        )
        return parsed

    def parse_qrd(self, fields: list) -> dict:
        """
        Petakan QRD dari pesan QRY^Q02.

        QRD-8 = barcode sampel (kosong berarti group download seluruh sampel
        hari itu), QRD-9 = OTH (query) atau CAN (batalkan group download).
        """
        subject_filter = field(fields, 9).upper()
        parsed = {
            "query_datetime": field(fields, 1),
            "query_id": field(fields, 4),
            "barcode": field(fields, 8),
            "subject_filter": subject_filter,
            "is_cancel": subject_filter == QRD_FILTER_CANCEL,
            "is_group_query": not field(fields, 8),
        }
        logger.info(
            f"QRD: barcode={parsed['barcode'] or '(group)'}, "
            f"filter={parsed['subject_filter']}"
        )
        return parsed

    def parse_qrf(self, fields: list) -> dict:
        """Petakan QRF: QRF-1 = model alat, QRF-2/3 = rentang waktu query."""
        return {
            "model": field(fields, 1),
            "start_datetime": field(fields, 2),
            "end_datetime": field(fields, 3),
        }

    def parse_msa(self, fields: list) -> dict:
        """Petakan MSA: MSA-1 = AA/AE/AR, MSA-2 = control ID, MSA-6 = status code."""
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

    def parse_query(self, raw_bytes: bytes) -> dict:
        """
        Parse pesan QRY^Q02 menjadi konteks query lengkap: data MSH, QRD, QRF,
        plus raw QRD/QRF untuk dipantulkan di pesan DSR^Q03.
        """
        segments = self.split_message(self.unwrap_mllp(raw_bytes))

        context = {}
        msh = self.find_segment(segments, SEG_MSH)
        if msh:
            context.update(self.parse_msh(msh))

        qrd = self.find_segment(segments, SEG_QRD)
        if qrd:
            context["qrd"] = self.parse_qrd(qrd)
            context["qrd_raw"] = FIELD_SEPARATOR.join(qrd)

        qrf = self.find_segment(segments, "QRF")
        if qrf:
            context["qrf"] = self.parse_qrf(qrf)
            context["qrf_raw"] = FIELD_SEPARATOR.join(qrf)

        return context


# ============================================================
# Unit Test
# ============================================================

if __name__ == "__main__":
    print("=== Test MindrayParser ===\n")
    p = MindrayParser()

    # --- field() / component() helpers ---
    assert field(["OBR", "1", "0000000002"], 2) == "0000000002"
    assert field(["OBR", "1"], 9) == ""
    assert component("Mindray^BS-200", 1) == "BS-200"
    assert component("", 0) == ""
    print("OK: helper field()/component()")

    # --- normalize_reference_range ---
    assert normalize_reference_range("-") == ""
    assert normalize_reference_range("") == ""
    assert normalize_reference_range("3.5-5.5") == "3.5-5.5"
    assert normalize_reference_range("20.0\\30.0") == "20.0-30.0"
    print("OK: normalize_reference_range()")

    # --- split_segment: MSH menjaga penomoran field ---
    msh_str = ("MSH|^~\\&|Mindray|BS-200E|||20070719145353||ORU^R01|1|P|2.3.1"
               "||||0||ASCII|||")
    msh = p.split_segment(msh_str)
    assert msh[0] == "MSH"
    assert msh[1] == "|"
    assert msh[2] == "^~\\&"
    assert msh[9] == "ORU^R01"
    assert msh[10] == "1"
    assert msh[16] == "0", f"MSH-16 harus '0', dapat {msh[16]!r}"
    assert msh[18] == "ASCII"
    print("OK: split_segment() MSH — penomoran field benar")

    parsed_msh = p.parse_msh(msh)
    assert parsed_msh["manufacturer"] == "Mindray"
    assert parsed_msh["model"] == "BS-200E"
    assert parsed_msh["message_type"] == "ORU^R01"
    assert parsed_msh["control_id"] == "1"
    assert parsed_msh["result_type"] == "0"
    print("OK: parse_msh()")

    # MSH tanpa Manufacturer/Model → fallback default
    msh_kosong = p.split_segment("MSH|^~\\&|||||20070719145353||ORU^R01|1|P|2.3.1")
    fallback = p.parse_msh(msh_kosong)
    assert fallback["manufacturer"] == "Mindray"
    assert fallback["model"] == "BS-200E"
    print("OK: parse_msh() fallback Manufacturer/Model")

    # --- PID: nomor pasien di PID-2, PID-3 kosong (contoh manual) ---
    pid = p.split_segment("PID|1|854||12|Tommy||19830719145307|F|A||||||||||||||||||||||")
    parsed_pid = p.parse_pid(pid)
    assert parsed_pid["patient_id"] == "854", parsed_pid["patient_id"]
    assert parsed_pid["bed_number"] == "12"
    assert parsed_pid["name"] == "Tommy"
    assert parsed_pid["dob"] == "19830719145307"
    assert parsed_pid["gender"] == "F"
    assert parsed_pid["blood_type"] == "A"
    print("OK: parse_pid() — patient_id fallback PID-3 → PID-2")

    # PID-3 terisi → dipakai duluan
    pid2 = p.parse_pid(p.split_segment("PID|1|854|RM-99|12|Doe^John||19830719|M|"))
    assert pid2["patient_id"] == "RM-99"
    assert pid2["name"] == "John Doe", pid2["name"]
    print("OK: parse_pid() — PID-3 prioritas, nama Last^First")

    # --- OBR sampel ---
    obr = p.split_segment(
        "OBR|1|0000000002|2|Mindray^BS-200|Y||||||||||serum|||||||||||||||||||||||||||||||"
    )
    parsed_obr = p.parse_obr_sample(obr)
    assert parsed_obr["barcode"] == "0000000002"
    assert parsed_obr["internal_sample_id"] == "2"
    assert parsed_obr["priority"] == "Y"
    assert parsed_obr["sample_type"] == "serum"
    print("OK: parse_obr_sample()")

    # --- OBR QC ---
    obr_qc = p.split_segment(
        "OBR|1|1|test1|Mindray^BS-200||20070720120143|||||||QUAL1|1111|20080720000000||H"
        "|5.000000|2.000000|0.11029|g/ml|||||||||||||||||||||||||||"
    )
    parsed_qc = p.parse_obr_qc(obr_qc)
    assert parsed_qc["test_code"] == "1"
    assert parsed_qc["test_name"] == "test1"
    assert parsed_qc["qc_datetime"] == "20070720120143"
    assert parsed_qc["control_name"] == "QUAL1"
    assert parsed_qc["lot_number"] == "1111"
    assert parsed_qc["expiration_date"] == "20080720000000"
    assert parsed_qc["level"] == "H"
    assert parsed_qc["mean_value"] == "5.000000"
    assert parsed_qc["standard_deviation"] == "2.000000"
    assert parsed_qc["value"] == "0.11029"
    assert parsed_qc["unit"] == "g/ml"
    print("OK: parse_obr_qc()")

    # --- OBX ---
    obx = p.split_segment("OBX|1|NM|2|test2|5.000000|g/ml|-||||F|||||||")
    parsed_obx = p.parse_obx(obx)
    assert parsed_obx["value_type"] == "NM"
    assert parsed_obx["test_code"] == "2"
    assert parsed_obx["test_name"] == "test2"
    assert parsed_obx["value"] == "5.000000"
    assert parsed_obx["unit"] == "g/ml"
    assert parsed_obx["reference_range"] == ""      # "-" → kosong
    assert parsed_obx["status"] == "F"
    print("OK: parse_obx() — test No. di OBX-3, nama di OBX-4")

    # OBX dengan code^name di OBX-3 (varian firmware)
    obx2 = p.parse_obx(p.split_segment("OBX|1|NM|GLU^Glucose||5.5|mmol/L|3.9-6.1|H|||F"))
    assert obx2["test_code"] == "GLU"
    assert obx2["test_name"] == "Glucose"
    assert obx2["reference_range"] == "3.9-6.1"
    assert obx2["flag"] == "H"
    print("OK: parse_obx() — varian code^name tetap tertangani")

    # --- QRD / QRF ---
    qrd = p.parse_qrd(p.split_segment("QRD|20070723170707|R|D|1|||RD|34567743|OTH|||T|"))
    assert qrd["barcode"] == "34567743"
    assert qrd["subject_filter"] == "OTH"
    assert qrd["is_cancel"] is False
    assert qrd["is_group_query"] is False
    print("OK: parse_qrd() — query per barcode")

    qrd_group = p.parse_qrd(p.split_segment("QRD|20070320170000|R|D|1|||RD||OTH|||T|"))
    assert qrd_group["is_group_query"] is True
    qrd_cancel = p.parse_qrd(p.split_segment("QRD|20070723170000|R|D|1|||RD||CAN|||T|"))
    assert qrd_cancel["is_cancel"] is True
    print("OK: parse_qrd() — group query & cancel terdeteksi")

    qrf = p.parse_qrf(p.split_segment("QRF|BS-200|20070723170749|20070723170749|||RCT|COR|ALL||"))
    assert qrf["model"] == "BS-200"
    assert qrf["start_datetime"] == "20070723170749"
    print("OK: parse_qrf()")

    # --- parse_query() end-to-end ---
    qry_raw = (
        b"\x0b"
        b"MSH|^~\\&|Mindray|BS-200E|||20070723170707||QRY^Q02|7|P|2.3.1||||||ASCII|||\r"
        b"QRD|20070723170707|R|D|1|||RD|34567743|OTH|||T|\r"
        b"QRF|BS-200E|20070723170749|20070723170749|||RCT|COR|ALL||\r"
        b"\x1c\x0d"
    )
    ctx = p.parse_query(qry_raw)
    assert ctx["message_type"] == "QRY^Q02"
    assert ctx["control_id"] == "7"
    assert ctx["model"] == "BS-200E"
    assert ctx["qrd"]["barcode"] == "34567743"
    assert ctx["qrd_raw"].startswith("QRD|20070723170707")
    assert ctx["qrf"]["model"] == "BS-200E"
    print("OK: parse_query() end-to-end")

    # --- get_message_type ---
    assert p.get_message_type(qry_raw) == "QRY^Q02"
    assert p.get_message_type(b"") == ""
    assert p.get_message_type(b"sampah") == ""
    print("OK: get_message_type()")

    # --- MSA ---
    msa = p.parse_msa(p.split_segment("MSA|AA|1|Message accepted|||0|"))
    assert msa["ack_code"] == "AA"
    assert msa["control_id"] == "1"
    assert msa["error_condition"] == "0"
    print("OK: parse_msa()")

    print("\n=== Semua test MindrayParser PASSED ===")
