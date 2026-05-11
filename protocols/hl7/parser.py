"""
protocols/hl7/parser.py — HL7 Message Parser

Parsing raw bytes HL7 v2.x dari alat lab dengan MLLP transport.
Menangani MLLP unwrapping, segment splitting, dan field-level parsing
untuk MSH, PID, PV1, OBR, OBX, NTE, MSA, QAK, QPD.

Alur: raw_bytes → unwrap_mllp() → parse_message() → parse_X_segment()
"""

from lib.utils import get_logger
from protocols.hl7.constants import (
    MLLP_START, MLLP_END, MLLP_CR,
    MLLP_START_BYTE, MLLP_END_BYTE, MLLP_TRAILER,
    FIELD_SEPARATOR, COMPONENT_SEP, REPEAT_SEP, SUBCOMPONENT_SEP,
    ENCODING_CHARACTERS, SEGMENT_TERMINATOR,
    SEG_MSH, SEG_PID, SEG_PV1, SEG_OBR, SEG_OBX, SEG_NTE,
    SEG_MSA, SEG_QAK, SEG_QPD,
    MSG_QBP, MSG_QRY, MSG_ACK, MSG_ORU,
    QUERY_MESSAGE_TYPES, QUERY_EVENTS,
)


logger = get_logger("hl7_parser")


class HL7Parser:
    """
    Parser untuk pesan HL7 v2.x dengan MLLP transport.

    Mendukung:
    - MLLP envelope unwrapping
    - Segment splitting dan parsing per segment type
    - Message type detection (ORU, QBP, ACK, dll)
    - Custom encoding characters dari MSH-2
    """

    def __init__(self):
        # Default delimiters (bisa di-override dari MSH)
        self.field_sep = FIELD_SEPARATOR
        self.component_sep = COMPONENT_SEP
        self.repeat_sep = REPEAT_SEP
        self.subcomponent_sep = SUBCOMPONENT_SEP

    # ============================================================
    # MLLP Transport Layer
    # ============================================================

    def unwrap_mllp(self, raw_bytes: bytes) -> bytes:
        """
        Hapus MLLP envelope dari raw bytes.

        Format MLLP: <VT>(HL7 message)<FS><CR>
        - VT  = 0x0B (Vertical Tab)
        - FS  = 0x1C (File Separator)
        - CR  = 0x0D (Carriage Return)

        Args:
            raw_bytes: Bytes lengkap termasuk MLLP envelope

        Returns:
            Bytes HL7 message tanpa envelope
        """
        if not raw_bytes:
            logger.warning("Data kosong untuk MLLP unwrap")
            return b""

        data = raw_bytes

        # Hapus MLLP_START (0x0B) di awal
        if data[0:1] == MLLP_START_BYTE:
            data = data[1:]

        # Hapus MLLP trailer (0x1C 0x0D) di akhir
        if data.endswith(MLLP_TRAILER):
            data = data[:-2]
        elif data.endswith(MLLP_END_BYTE):
            data = data[:-1]

        return data

    # ============================================================
    # Message-level parsing
    # ============================================================

    def parse_message(self, hl7_bytes: bytes) -> dict:
        """
        Parse HL7 message bytes menjadi dict berisi semua segment.

        Args:
            hl7_bytes: Bytes HL7 message (sudah di-unwrap dari MLLP)

        Returns:
            Dict: {
                "segments": [list of parsed segments],
                "raw": original text,
                "message_type": str misal "ORU^R01",
            }
        """
        text = hl7_bytes.decode("ascii", errors="replace")

        # Split per segment (CR sebagai separator)
        raw_segments = [s for s in text.split(SEGMENT_TERMINATOR) if s.strip()]

        result = {
            "segments": [],
            "raw": text,
            "message_type": "",
        }

        for seg_text in raw_segments:
            parsed = self._parse_segment(seg_text)
            if parsed:
                result["segments"].append(parsed)

                # Ambil message type dari MSH
                if parsed.get("segment_type") == SEG_MSH:
                    result["message_type"] = parsed.get("message_type", "")

        logger.info(
            f"Parsed {len(result['segments'])} segments, "
            f"message_type={result['message_type']}"
        )
        return result

    def _parse_segment(self, segment_text: str) -> dict | None:
        """Parse satu segment string berdasarkan tipe-nya."""
        if not segment_text or len(segment_text) < 3:
            return None

        seg_type = segment_text[:3]

        # Dispatch ke parser spesifik
        parsers = {
            SEG_MSH: self.parse_msh,
            SEG_PID: self.parse_pid,
            SEG_PV1: self.parse_pv1,
            SEG_OBR: self.parse_obr,
            SEG_OBX: self.parse_obx,
            SEG_NTE: self.parse_nte,
            SEG_MSA: self.parse_msa,
            SEG_QAK: self.parse_qak,
            SEG_QPD: self.parse_qpd,
        }

        parser_func = parsers.get(seg_type)
        if parser_func:
            return parser_func(segment_text)

        # Segment type dikenali tapi tidak ada parser khusus
        fields = segment_text.split(self.field_sep)
        return {"segment_type": seg_type, "fields": fields, "raw": segment_text}

    def _split_fields(self, segment: str) -> list:
        """Split segment menjadi fields berdasarkan field separator."""
        return segment.split(self.field_sep)

    def _split_components(self, field_value: str) -> list:
        """Split field menjadi komponen berdasarkan component separator."""
        return field_value.split(self.component_sep)

    def _split_repeats(self, field_value: str) -> list:
        """Split field menjadi repeat values."""
        return field_value.split(self.repeat_sep)

    # ============================================================
    # Segment-specific parsers
    # ============================================================

    def parse_msh(self, segment: str) -> dict:
        """
        Parse MSH (Message Header) segment.

        MSH spesial: MSH-1 adalah field separator itu sendiri (|),
        jadi field numbering dimulai dari MSH-1 = "|".

        Fields penting:
        - MSH-1:  Field Separator (|)
        - MSH-2:  Encoding Characters (^~\\&)
        - MSH-3:  Sending Application
        - MSH-4:  Sending Facility
        - MSH-5:  Receiving Application
        - MSH-6:  Receiving Facility
        - MSH-7:  Date/Time of Message
        - MSH-9:  Message Type (e.g. ORU^R01)
        - MSH-10: Message Control ID
        - MSH-11: Processing ID
        - MSH-12: Version ID
        """
        # MSH khusus: karakter pertama setelah "MSH" adalah field separator
        if len(segment) < 4:
            return {"segment_type": SEG_MSH, "raw": segment}

        # Update field separator dari MSH-1
        self.field_sep = segment[3]

        # Split fields — tapi MSH-1 = separator itu sendiri
        # Jadi kita split mulai dari karakter ke-4
        parts = segment[4:].split(self.field_sep)
        # parts[0] = MSH-2 (encoding chars), parts[1] = MSH-3, dst.

        # Update encoding characters dari MSH-2
        if parts and len(parts[0]) >= 4:
            self.component_sep = parts[0][0]
            self.repeat_sep = parts[0][1]
            # parts[0][2] = escape char
            self.subcomponent_sep = parts[0][3]

        # Message type (MSH-9) — komponen: message_type^trigger_event^structure
        message_type = ""
        msg_type_field = parts[7] if len(parts) > 7 else ""
        if msg_type_field:
            mt_parts = self._split_components(msg_type_field)
            if len(mt_parts) >= 2:
                message_type = f"{mt_parts[0]}^{mt_parts[1]}"
            elif len(mt_parts) == 1:
                message_type = mt_parts[0]

        parsed = {
            "segment_type": SEG_MSH,
            "field_separator": self.field_sep,
            "encoding_characters": parts[0] if parts else "",
            "sending_application": parts[1] if len(parts) > 1 else "",
            "sending_facility": parts[2] if len(parts) > 2 else "",
            "receiving_application": parts[3] if len(parts) > 3 else "",
            "receiving_facility": parts[4] if len(parts) > 4 else "",
            "datetime": parts[5] if len(parts) > 5 else "",
            "security": parts[6] if len(parts) > 6 else "",
            "message_type": message_type,
            "message_type_raw": msg_type_field,
            "message_control_id": parts[8] if len(parts) > 8 else "",
            "processing_id": parts[9] if len(parts) > 9 else "",
            "version_id": parts[10] if len(parts) > 10 else "",
            "raw": segment,
        }

        logger.info(
            f"MSH: type={parsed['message_type']}, "
            f"control_id={parsed['message_control_id']}, "
            f"sender={parsed['sending_application']}"
        )
        return parsed

    def parse_pid(self, segment: str) -> dict:
        """
        Parse PID (Patient Identification) segment.

        Fields penting:
        - PID-3:  Patient Identifier List (patient_id)
        - PID-5:  Patient Name (last^first^middle^suffix^prefix)
        - PID-7:  Date/Time of Birth
        - PID-8:  Administrative Sex (M/F/U)
        """
        fields = self._split_fields(segment)

        # PID-3: Patient ID — bisa berisi komponen (id^check_digit^authority...)
        patient_id = ""
        if len(fields) > 3 and fields[3]:
            id_parts = self._split_components(fields[3])
            patient_id = id_parts[0] if id_parts else fields[3]

        # PID-5: Patient Name — last^first^middle^suffix^prefix
        name = ""
        last_name = ""
        first_name = ""
        if len(fields) > 5 and fields[5]:
            name_parts = self._split_components(fields[5])
            last_name = name_parts[0] if len(name_parts) > 0 else ""
            first_name = name_parts[1] if len(name_parts) > 1 else ""
            # Gabung nama: first last
            name_list = [p for p in [first_name, last_name] if p]
            name = " ".join(name_list)

        parsed = {
            "segment_type": SEG_PID,
            "set_id": fields[1] if len(fields) > 1 else "",
            "patient_id_external": fields[2] if len(fields) > 2 else "",
            "patient_id": patient_id,
            "patient_id_raw": fields[3] if len(fields) > 3 else "",
            "alternate_patient_id": fields[4] if len(fields) > 4 else "",
            "name": name,
            "last_name": last_name,
            "first_name": first_name,
            "name_raw": fields[5] if len(fields) > 5 else "",
            "dob": fields[7] if len(fields) > 7 else "",
            "gender": fields[8] if len(fields) > 8 else "",
        }

        logger.info(
            f"PID: patient_id={parsed['patient_id']}, name={parsed['name']}, "
            f"gender={parsed['gender']}"
        )
        return parsed

    def parse_pv1(self, segment: str) -> dict:
        """
        Parse PV1 (Patient Visit) segment.

        Fields penting:
        - PV1-2:  Patient Class (I=inpatient, O=outpatient, E=emergency)
        - PV1-7:  Attending Doctor
        - PV1-19: Visit Number
        """
        fields = self._split_fields(segment)

        # PV1-7: Attending Doctor — id^last^first^...
        physician = ""
        if len(fields) > 7 and fields[7]:
            doc_parts = self._split_components(fields[7])
            doc_names = [p for p in doc_parts[1:3] if p]
            physician = " ".join(doc_names) if doc_names else (doc_parts[0] if doc_parts else "")

        parsed = {
            "segment_type": SEG_PV1,
            "set_id": fields[1] if len(fields) > 1 else "",
            "patient_class": fields[2] if len(fields) > 2 else "",
            "assigned_location": fields[3] if len(fields) > 3 else "",
            "physician": physician,
            "visit_number": fields[19] if len(fields) > 19 else "",
        }

        logger.info(f"PV1: class={parsed['patient_class']}, physician={parsed['physician']}")
        return parsed

    def parse_obr(self, segment: str) -> dict:
        """
        Parse OBR (Observation Request) segment.

        Fields penting:
        - OBR-2:  Placer Order Number (order_id)
        - OBR-3:  Filler Order Number
        - OBR-4:  Universal Service Identifier (test_code^test_name)
        - OBR-7:  Observation Date/Time
        - OBR-14: Specimen Received Date/Time
        - OBR-15: Specimen Source
        """
        fields = self._split_fields(segment)

        # OBR-4: Universal Service ID — code^name^coding_system
        test_code = ""
        test_name = ""
        panel = ""
        if len(fields) > 4 and fields[4]:
            svc_parts = self._split_components(fields[4])
            test_code = svc_parts[0] if len(svc_parts) > 0 else ""
            test_name = svc_parts[1] if len(svc_parts) > 1 else ""
            panel = fields[4]

        # OBR-2: Placer Order Number — bisa komponen
        order_id = ""
        if len(fields) > 2 and fields[2]:
            id_parts = self._split_components(fields[2])
            order_id = id_parts[0] if id_parts else fields[2]

        # OBR-3: Filler Order Number (sample_id dari alat)
        sample_id = ""
        if len(fields) > 3 and fields[3]:
            filler_parts = self._split_components(fields[3])
            sample_id = filler_parts[0] if filler_parts else fields[3]

        parsed = {
            "segment_type": SEG_OBR,
            "set_id": fields[1] if len(fields) > 1 else "",
            "order_id": order_id,
            "sample_id": sample_id,
            "test_code": test_code,
            "test_name": test_name,
            "panel": panel,
            "observation_datetime": fields[7] if len(fields) > 7 else "",
            "specimen_received": fields[14] if len(fields) > 14 else "",
            "specimen_source": fields[15] if len(fields) > 15 else "",
            "result_status": fields[25] if len(fields) > 25 else "",
        }

        logger.info(
            f"OBR: order_id={parsed['order_id']}, sample_id={parsed['sample_id']}, "
            f"test={parsed['test_code']}"
        )
        return parsed

    def parse_obx(self, segment: str) -> dict:
        """
        Parse OBX (Observation/Result) segment.

        Fields penting:
        - OBX-2:  Value Type (NM=numeric, ST=string, CE=coded entry)
        - OBX-3:  Observation Identifier (test_code^test_name)
        - OBX-5:  Observation Value
        - OBX-6:  Units (code^text^system)
        - OBX-7:  Reference Range
        - OBX-8:  Abnormal Flags (N=normal, H=high, L=low, A=abnormal)
        - OBX-11: Observation Result Status (F=final, P=preliminary)
        """
        fields = self._split_fields(segment)

        # OBX-3: Observation Identifier — code^name^coding_system
        test_code = ""
        test_name = ""
        if len(fields) > 3 and fields[3]:
            obs_parts = self._split_components(fields[3])
            test_code = obs_parts[0] if len(obs_parts) > 0 else ""
            test_name = obs_parts[1] if len(obs_parts) > 1 else ""

        # OBX-6: Units — code^text^coding_system
        unit = ""
        if len(fields) > 6 and fields[6]:
            unit_parts = self._split_components(fields[6])
            unit = unit_parts[0] if unit_parts else fields[6]

        parsed = {
            "segment_type": SEG_OBX,
            "set_id": fields[1] if len(fields) > 1 else "",
            "value_type": fields[2] if len(fields) > 2 else "",
            "test_code": test_code,
            "test_name": test_name,
            "observation_id_raw": fields[3] if len(fields) > 3 else "",
            "observation_sub_id": fields[4] if len(fields) > 4 else "",
            "value": fields[5] if len(fields) > 5 else "",
            "unit": unit,
            "reference_range": fields[7] if len(fields) > 7 else "",
            "flag": fields[8] if len(fields) > 8 else "",
            "probability": fields[9] if len(fields) > 9 else "",
            "nature": fields[10] if len(fields) > 10 else "",
            "status": fields[11] if len(fields) > 11 else "",
            "observation_datetime": fields[14] if len(fields) > 14 else "",
        }

        logger.info(
            f"OBX: test={parsed['test_code']}, value={parsed['value']} "
            f"{parsed['unit']}, flag={parsed['flag']}, status={parsed['status']}"
        )
        return parsed

    def parse_nte(self, segment: str) -> dict:
        """
        Parse NTE (Notes and Comments) segment.

        Fields:
        - NTE-1: Set ID
        - NTE-2: Source of Comment
        - NTE-3: Comment
        """
        fields = self._split_fields(segment)

        parsed = {
            "segment_type": SEG_NTE,
            "set_id": fields[1] if len(fields) > 1 else "",
            "source": fields[2] if len(fields) > 2 else "",
            "comment": fields[3] if len(fields) > 3 else "",
        }

        logger.info(f"NTE: {parsed['comment'][:50]}")
        return parsed

    def parse_msa(self, segment: str) -> dict:
        """
        Parse MSA (Message Acknowledgement) segment.

        Fields:
        - MSA-1: Acknowledgement Code (AA, AE, AR)
        - MSA-2: Message Control ID (dari message yang di-ACK)
        - MSA-3: Text Message (opsional, info error)
        """
        fields = self._split_fields(segment)

        parsed = {
            "segment_type": SEG_MSA,
            "ack_code": fields[1] if len(fields) > 1 else "",
            "message_control_id": fields[2] if len(fields) > 2 else "",
            "text_message": fields[3] if len(fields) > 3 else "",
        }

        logger.info(f"MSA: ack={parsed['ack_code']}, ref={parsed['message_control_id']}")
        return parsed

    def parse_qak(self, segment: str) -> dict:
        """
        Parse QAK (Query Acknowledgement) segment.

        Fields:
        - QAK-1: Query Tag
        - QAK-2: Query Response Status (OK, NF=not found, AE=error)
        """
        fields = self._split_fields(segment)

        parsed = {
            "segment_type": SEG_QAK,
            "query_tag": fields[1] if len(fields) > 1 else "",
            "query_response_status": fields[2] if len(fields) > 2 else "",
        }

        logger.info(f"QAK: tag={parsed['query_tag']}, status={parsed['query_response_status']}")
        return parsed

    def parse_qpd(self, segment: str) -> dict:
        """
        Parse QPD (Query Parameter Definition) segment.

        Fields:
        - QPD-1: Message Query Name (code^name)
        - QPD-2: Query Tag
        - QPD-3+: Parameter fields (varies per query type)
                  Untuk QBP^Q22: QPD-3 biasanya patient_id atau sample_id
        """
        fields = self._split_fields(segment)

        # QPD-1: Query name
        query_name = ""
        if len(fields) > 1 and fields[1]:
            qn_parts = self._split_components(fields[1])
            query_name = qn_parts[0] if qn_parts else fields[1]

        # QPD-3: Parameter pertama — biasanya patient_id atau sample_id
        param_value = ""
        if len(fields) > 3 and fields[3]:
            param_value = fields[3]

        parsed = {
            "segment_type": SEG_QPD,
            "query_name": query_name,
            "query_name_raw": fields[1] if len(fields) > 1 else "",
            "query_tag": fields[2] if len(fields) > 2 else "",
            "parameter_value": param_value,
            "parameters_raw": fields[3:] if len(fields) > 3 else [],
        }

        logger.info(
            f"QPD: query={parsed['query_name']}, tag={parsed['query_tag']}, "
            f"param={parsed['parameter_value']}"
        )
        return parsed

    # ============================================================
    # Message type detection
    # ============================================================

    def get_message_type(self, raw_bytes: bytes) -> str:
        """
        Ekstrak message type dari raw bytes (bisa dengan/tanpa MLLP).

        Args:
            raw_bytes: Data mentah dari socket

        Returns:
            String message type, misal 'ORU^R01' atau 'QBP^Q22'.
            Kosong jika tidak bisa dideteksi.
        """
        try:
            data = self.unwrap_mllp(raw_bytes)
            text = data.decode("ascii", errors="replace")

            # Cari MSH segment
            for line in text.split(SEGMENT_TERMINATOR):
                line = line.strip()
                if line.startswith("MSH"):
                    msh = self.parse_msh(line)
                    return msh.get("message_type", "")

        except Exception as e:
            logger.warning(f"Gagal get_message_type: {e}")

        return ""

    def is_query_message(self, raw_bytes: bytes) -> bool:
        """
        Cek apakah raw bytes merupakan query message (QBP^Q22, QRY^Q01, dll).

        Args:
            raw_bytes: Data dari socket

        Returns:
            True jika merupakan query message
        """
        msg_type = self.get_message_type(raw_bytes)
        if not msg_type:
            return False

        # Cek full event (misal QBP^Q22)
        if msg_type in QUERY_EVENTS:
            return True

        # Cek message type saja (misal QBP)
        base_type = msg_type.split("^")[0] if "^" in msg_type else msg_type
        return base_type in QUERY_MESSAGE_TYPES


# ============================================================
# Unit Test
# ============================================================

if __name__ == "__main__":
    print("=== Test HL7Parser ===\n")
    parser = HL7Parser()

    # Test 1: unwrap_mllp
    mllp_msg = b"\x0bMSH|^~\\&|...\x1c\x0d"
    unwrapped = parser.unwrap_mllp(mllp_msg)
    assert unwrapped == b"MSH|^~\\&|...", f"Unwrap salah: {unwrapped}"
    print("OK: unwrap_mllp() menghapus MLLP envelope")

    # Test unwrap tanpa envelope
    plain_msg = b"MSH|^~\\&|test"
    unwrapped2 = parser.unwrap_mllp(plain_msg)
    assert unwrapped2 == b"MSH|^~\\&|test"
    print("OK: unwrap_mllp() toleran tanpa envelope")

    # Test unwrap kosong
    assert parser.unwrap_mllp(b"") == b""
    print("OK: unwrap_mllp() handle empty")

    # Test 2: parse_msh
    msh_str = "MSH|^~\\&|Sysmex|Lab|LIS|Hospital|20240101120000||ORU^R01|MSG001|P|2.5.1"
    msh = parser.parse_msh(msh_str)
    print(f"\nMSH parsed:")
    print(f"  sending_app: {msh['sending_application']}")
    print(f"  message_type: {msh['message_type']}")
    print(f"  control_id: {msh['message_control_id']}")
    print(f"  version: {msh['version_id']}")
    assert msh["segment_type"] == "MSH"
    assert msh["sending_application"] == "Sysmex"
    assert msh["message_type"] == "ORU^R01"
    assert msh["message_control_id"] == "MSG001"
    assert msh["version_id"] == "2.5.1"
    print("OK: parse_msh() benar")

    # Test 3: parse_pid
    pid_str = "PID|1||PAT001^^^MR||Doe^John^M||19900515|M"
    pid = parser.parse_pid(pid_str)
    print(f"\nPID parsed:")
    print(f"  patient_id: {pid['patient_id']}")
    print(f"  name: {pid['name']}")
    print(f"  dob: {pid['dob']}")
    print(f"  gender: {pid['gender']}")
    assert pid["patient_id"] == "PAT001"
    assert pid["name"] == "John Doe"
    assert pid["gender"] == "M"
    assert pid["dob"] == "19900515"
    print("OK: parse_pid() benar")

    # Test 4: parse_obr
    obr_str = "OBR|1|ORD001|SAMP001|CBC^Complete Blood Count|||20240101120000"
    obr = parser.parse_obr(obr_str)
    print(f"\nOBR parsed:")
    print(f"  order_id: {obr['order_id']}")
    print(f"  sample_id: {obr['sample_id']}")
    print(f"  test_code: {obr['test_code']}")
    print(f"  test_name: {obr['test_name']}")
    assert obr["order_id"] == "ORD001"
    assert obr["sample_id"] == "SAMP001"
    assert obr["test_code"] == "CBC"
    assert obr["test_name"] == "Complete Blood Count"
    print("OK: parse_obr() benar")

    # Test 5: parse_obx
    obx_str = "OBX|1|NM|WBC^White Blood Cell||5.2|10^3/uL|4.0-10.0|N|||F"
    obx = parser.parse_obx(obx_str)
    print(f"\nOBX parsed:")
    print(f"  test_code: {obx['test_code']}")
    print(f"  value: {obx['value']}")
    print(f"  unit: {obx['unit']}")
    print(f"  reference_range: {obx['reference_range']}")
    print(f"  flag: {obx['flag']}")
    print(f"  status: {obx['status']}")
    assert obx["test_code"] == "WBC"
    assert obx["test_name"] == "White Blood Cell"
    assert obx["value"] == "5.2"
    assert obx["unit"] == "10"  # "10^3/uL" → component split di "^"
    assert obx["reference_range"] == "4.0-10.0"
    assert obx["flag"] == "N"
    assert obx["status"] == "F"
    print("OK: parse_obx() benar")

    # Test 6: parse_msa
    msa_str = "MSA|AA|MSG001|Message accepted"
    msa = parser.parse_msa(msa_str)
    assert msa["ack_code"] == "AA"
    assert msa["message_control_id"] == "MSG001"
    assert msa["text_message"] == "Message accepted"
    print("\nOK: parse_msa() benar")

    # Test 7: parse_qpd
    qpd_str = "QPD|Q22^Find Candidates|QRY001|PAT001"
    qpd = parser.parse_qpd(qpd_str)
    assert qpd["query_name"] == "Q22"
    assert qpd["query_tag"] == "QRY001"
    assert qpd["parameter_value"] == "PAT001"
    print("OK: parse_qpd() benar")

    # Test 8: parse_nte
    nte_str = "NTE|1|L|Hemolyzed sample"
    nte = parser.parse_nte(nte_str)
    assert nte["comment"] == "Hemolyzed sample"
    print("OK: parse_nte() benar")

    # Test 9: parse_pv1
    pv1_str = "PV1|1|O|||||DOC001^Smith^John"
    pv1 = parser.parse_pv1(pv1_str)
    assert pv1["patient_class"] == "O"
    assert "Smith" in pv1["physician"]
    print("OK: parse_pv1() benar")

    # Test 10: parse_qak
    qak_str = "QAK|QRY001|OK"
    qak = parser.parse_qak(qak_str)
    assert qak["query_tag"] == "QRY001"
    assert qak["query_response_status"] == "OK"
    print("OK: parse_qak() benar")

    # Test 11: parse_message — full ORU message
    print("\n--- Full ORU^R01 Message Test ---")
    oru_msg = (
        "MSH|^~\\&|Sysmex|Lab|LIS|Hospital|20240101120000||ORU^R01|MSG001|P|2.5.1\r"
        "PID|1||PAT001^^^MR||Doe^John||19900515|M\r"
        "PV1|1|O\r"
        "OBR|1|ORD001|SAMP001|CBC^Complete Blood Count|||20240101120000\r"
        "OBX|1|NM|WBC^White Blood Cell||5.2|10*3/uL|4.0-10.0|N|||F\r"
        "OBX|2|NM|RBC^Red Blood Cell||4.8|10*6/uL|3.5-5.5|N|||F\r"
        "NTE|1|L|Normal results\r"
    )
    result = parser.parse_message(oru_msg.encode("ascii"))
    print(f"  Segments: {len(result['segments'])}")
    print(f"  Message type: {result['message_type']}")
    seg_types = [s["segment_type"] for s in result["segments"]]
    print(f"  Segment types: {seg_types}")
    assert result["message_type"] == "ORU^R01"
    assert seg_types == ["MSH", "PID", "PV1", "OBR", "OBX", "OBX", "NTE"]
    print("OK: Full ORU message parsed benar")

    # Test 12: get_message_type
    raw_oru = b"\x0bMSH|^~\\&|Test||LIS||20240101||ORU^R01|M1|P|2.5.1\r\x1c\x0d"
    assert parser.get_message_type(raw_oru) == "ORU^R01"
    print("\nOK: get_message_type() benar")

    # Test 13: is_query_message
    raw_qbp = b"\x0bMSH|^~\\&|Inst||LIS||20240101||QBP^Q22|Q1|P|2.5.1\r\x1c\x0d"
    assert parser.is_query_message(raw_qbp) is True
    assert parser.is_query_message(raw_oru) is False
    print("OK: is_query_message() benar")

    print("\n=== Semua test HL7Parser PASSED ===")
