"""
protocols/mindray_bs200e/builder.py — Builder pesan HL7 v2.3.1 Mindray BS-200E

Membangun pesan arah LIS → alat:
- ACK^R01 — balasan atas hasil (ORU^R01) yang dikirim alat
- QCK^Q02 — balasan atas query order (QRY^Q02), OK atau NF
- DSR^Q03 — data order (patient + sampel + daftar tes) untuk di-download alat

Layout field mengikuti contoh pesan di bab 3 manual, termasuk trailing `|`
pada tiap segment — beberapa firmware Mindray rewel soal jumlah field.
"""

from itertools import count

from lib.utils import get_logger
from protocols.mindray_bs200e.constants import (
    MLLP_START_BYTE, MLLP_TRAILER,
    FIELD_SEPARATOR, ENCODING_CHARACTERS, SEGMENT_TERMINATOR,
    HL7_VERSION, CHARACTER_SET, PROC_PRODUCTION,
    DEFAULT_MANUFACTURER, DEFAULT_MODEL,
    ACK_AA, MSA_TEXT_ACCEPTED, STATUS_CODE_OK, ERR_CODE_OK,
    QUERY_TAG, QAK_OK, QAK_NF,
    EVENT_ACK_R01, EVENT_QCK_Q02, EVENT_DSR_Q03,
    QRD_FORMAT_CODE, QRD_PRIORITY, QRD_QUANTITY_LIMITED,
    QRD_FILTER_QUERY, QRD_RESULTS_LEVEL,
    QRF_DATETIME_QUALIFIER, QRF_STATUS_QUALIFIER, QRF_SELECTION_QUALIFIER,
    DSP_FIXED_LINE_COUNT, DSP_FIRST_TEST_LINE,
    DSP_ADMISSION_NUMBER, DSP_BED_NUMBER, DSP_PATIENT_NAME, DSP_DATE_OF_BIRTH,
    DSP_SEX, DSP_BAR_CODE, DSP_SAMPLE_TIME, DSP_STAT, DSP_SAMPLE_TYPE,
    STAT_YES, STAT_NO, STAT_PRIORITY_VALUES,
)


logger = get_logger("mindray_bs200e_builder")


def to_hl7_timestamp(value: str, pad: bool = True) -> str:
    """
    Ubah timestamp apa pun (ISO8601, `YYYYMMDD`, `YYYYMMDDHHMMSS`) menjadi
    format Mindray `YYYYMMDDHHMMSS`.

    Alat menolak separator, jadi semua karakter non-digit dibuang. Tanggal
    tanpa jam di-pad dengan `000000` (contoh manual: `19620824000000`).
    """
    digits = "".join(ch for ch in (value or "") if ch.isdigit())
    if not digits:
        return ""
    digits = digits[:14]
    if pad and len(digits) < 14:
        digits = digits.ljust(14, "0")
    return digits


def to_stat_flag(priority: str) -> str:
    """Ubah `specimen.priority` OrderObject menjadi flag STAT alat (Y/N)."""
    return STAT_YES if (priority or "").strip().upper() in STAT_PRIORITY_VALUES else STAT_NO


class MindrayBuilder:
    """Builder pesan HL7 Mindray BS-200E (arah LIS → alat)."""

    def __init__(self, now=None):
        """
        Args:
            now: callable pengganti jam sistem yang mengembalikan string
                 `YYYYMMDDHHMMSS` — dipakai unit test untuk mengunci MSH-7.
        """
        # MSH-10 wajib unik dan naik dari 1 — dipakai saat MidLab yang memulai
        # pesan (broadcast). Untuk balasan, control ID query dipantulkan.
        self._control_id = count(1)
        self._now = now or _now_timestamp

    # ============================================================
    # Helper
    # ============================================================

    def next_control_id(self) -> str:
        return str(next(self._control_id))

    def _wrap_mllp(self, segments: list) -> bytes:
        """Gabung segment (tiap-tiap diakhiri CR) lalu bungkus envelope MLLP."""
        body = "".join(f"{seg}{SEGMENT_TERMINATOR}" for seg in segments)
        return MLLP_START_BYTE + body.encode("ascii", errors="replace") + MLLP_TRAILER

    def _build_msh(self, event: str, control_id: str, context: dict,
                   result_type: str = "") -> str:
        """
        Bangun MSH arah LIS → alat.

        Sesuai contoh manual, MSH-3/MSH-4 (aplikasi/fasilitas pengirim) dibiarkan
        kosong dan MSH-5/MSH-6 diisi Manufacturer/Model alat. MSH-16 hanya
        terisi pada pesan ORU/ACK^R01 (tipe hasil), kosong untuk pesan lain.

        MSH-7 memakai jam sistem MidLab saat pesan dibuat (manual: "Time of the
        current message"), bukan jam pesan yang dibalas.
        """
        context = context or {}
        manufacturer = context.get("manufacturer") or DEFAULT_MANUFACTURER
        model = context.get("model") or DEFAULT_MODEL

        fields = [
            "MSH",
            ENCODING_CHARACTERS,   # MSH-2
            "",                    # MSH-3  Sending Application
            "",                    # MSH-4  Sending Facility
            manufacturer,          # MSH-5  Receiving Application
            model,                 # MSH-6  Receiving Facility
            self._now(),           # MSH-7
            "",                    # MSH-8  Security
            event,                 # MSH-9
            control_id,            # MSH-10
            PROC_PRODUCTION,       # MSH-11
            HL7_VERSION,           # MSH-12
            "", "", "",            # MSH-13..15
            result_type,           # MSH-16 tipe hasil (0/1/2), kosong non-ORU
            "",                    # MSH-17 Country Code
            CHARACTER_SET,         # MSH-18
            "", "", "",            # MSH-19..21
        ]
        return FIELD_SEPARATOR.join(fields)

    def _build_msa(self, control_id: str) -> str:
        """MSA|AA|<control id>|Message accepted|||0|"""
        return FIELD_SEPARATOR.join(
            ["MSA", ACK_AA, control_id, MSA_TEXT_ACCEPTED, "", "", STATUS_CODE_OK, ""]
        )

    def _build_err(self) -> str:
        """ERR|0| — tidak ada error."""
        return FIELD_SEPARATOR.join(["ERR", ERR_CODE_OK, ""])

    def _build_qak(self, status: str) -> str:
        """QAK|SR|<OK|NF>|"""
        return FIELD_SEPARATOR.join(["QAK", QUERY_TAG, status, ""])

    def _build_dsp(self, set_id: int, data_line: str) -> str:
        """DSP|<set id>||<data>||| — data ada di DSP-3."""
        return FIELD_SEPARATOR.join(["DSP", str(set_id), "", data_line, "", "", ""])

    def _build_dsc(self, continuation: str = "") -> str:
        """
        DSC|<pointer>| — pointer kosong menandai pesan DSR terakhir dari
        satu rangkaian group download.
        """
        return FIELD_SEPARATOR.join(["DSC", continuation, ""])

    def _build_qrd(self, context: dict, barcode: str) -> str:
        """Pantulkan QRD milik query; bila tidak ada (broadcast), bangun baru."""
        raw = (context or {}).get("qrd_raw")
        if raw:
            return raw

        return FIELD_SEPARATOR.join([
            "QRD",
            self._now(),             # QRD-1
            QRD_FORMAT_CODE,         # QRD-2
            QRD_PRIORITY,            # QRD-3
            (context or {}).get("query_id", "1"),   # QRD-4
            "", "",                  # QRD-5..6
            QRD_QUANTITY_LIMITED,    # QRD-7
            barcode,                 # QRD-8
            QRD_FILTER_QUERY,        # QRD-9
            "", "",                  # QRD-10..11
            QRD_RESULTS_LEVEL,       # QRD-12
            "",
        ])

    def _build_qrf(self, context: dict) -> str:
        """Pantulkan QRF milik query; bila tidak ada (broadcast), bangun baru."""
        raw = (context or {}).get("qrf_raw")
        if raw:
            return raw

        now = self._now()
        model = (context or {}).get("model") or DEFAULT_MODEL
        return FIELD_SEPARATOR.join([
            "QRF",
            model,                      # QRF-1
            now[:8] + "000000",         # QRF-2 — sejak jam 0 hari ini
            now,                        # QRF-3
            "", "",                     # QRF-4..5
            QRF_DATETIME_QUALIFIER,     # QRF-6
            QRF_STATUS_QUALIFIER,       # QRF-7
            QRF_SELECTION_QUALIFIER,    # QRF-8
            "", "",
        ])

    # ============================================================
    # ACK^R01 — balasan hasil
    # ============================================================

    def build_ack_r01(self, context: dict) -> bytes:
        """
        Bangun ACK^R01 atas pesan ORU^R01 dari alat.

        Args:
            context: hasil MindrayParser.parse_msh() pesan ORU

        Returns:
            Bytes pesan ACK ber-envelope MLLP
        """
        context = context or {}
        control_id = context.get("control_id", "1")
        segments = [
            self._build_msh(
                EVENT_ACK_R01, control_id, context,
                result_type=context.get("result_type", ""),
            ),
            self._build_msa(control_id),
        ]
        logger.info(f"ACK^R01 dibangun untuk control_id={control_id}")
        return self._wrap_mllp(segments)

    # ============================================================
    # QCK^Q02 — balasan query
    # ============================================================

    def build_qck_q02(self, context: dict, found: bool) -> bytes:
        """
        Bangun QCK^Q02 atas pesan QRY^Q02 dari alat.

        Args:
            context: konteks query dari MindrayParser.parse_query()
            found: True bila order ada (QAK OK), False bila tidak (QAK NF)
        """
        context = context or {}
        control_id = context.get("control_id", "1")
        status = QAK_OK if found else QAK_NF

        segments = [
            self._build_msh(EVENT_QCK_Q02, control_id, context),
            self._build_msa(control_id),
            self._build_err(),
            self._build_qak(status),
        ]
        logger.info(f"QCK^Q02 dibangun: status={status}, control_id={control_id}")
        return self._wrap_mllp(segments)

    # ============================================================
    # DSR^Q03 — data order
    # ============================================================

    def build_dsr_q03(self, order: dict, context: dict,
                      continuation: str = "") -> bytes:
        """
        Bangun DSR^Q03 berisi satu order (satu sampel).

        Args:
            order: OrderObject dict dari tbl_order.order_json
            context: konteks query (untuk pantulan control ID, QRD, QRF)
            continuation: DSC-1; kosong = pesan terakhir rangkaian

        Returns:
            Bytes pesan DSR ber-envelope MLLP
        """
        context = context or {}
        control_id = context.get("control_id", "") or self.next_control_id()

        specimen = order.get("specimen", {}) or {}
        barcode = specimen.get("sample_id", "")

        segments = [
            self._build_msh(EVENT_DSR_Q03, control_id, context),
            self._build_msa(control_id),
            self._build_err(),
            self._build_qak(QAK_OK),
            self._build_qrd(context, barcode),
            self._build_qrf(context),
        ]
        segments.extend(self._build_dsp_lines(order))
        segments.append(self._build_dsc(continuation))

        logger.info(
            f"DSR^Q03 dibangun: barcode={barcode}, "
            f"{len(order.get('tests', []) or [])} tes, control_id={control_id}"
        )
        return self._wrap_mllp(segments)

    def _build_dsp_lines(self, order: dict) -> list:
        """
        Bangun baris DSP 1..28 (data pasien & sampel) diikuti satu baris per tes.

        Urutan baris dikunci oleh manual — baris yang tidak punya sumber data di
        OrderObject tetap dikirim kosong agar penomoran tidak bergeser.
        """
        patient = order.get("patient", {}) or {}
        specimen = order.get("specimen", {}) or {}
        tests = order.get("tests", []) or []

        # Baris fixed: index list = set-ID DSP.
        lines = {
            DSP_ADMISSION_NUMBER: patient.get("patient_id", ""),
            DSP_BED_NUMBER: "",
            DSP_PATIENT_NAME: patient.get("name", ""),
            DSP_DATE_OF_BIRTH: to_hl7_timestamp(patient.get("dob", "")),
            DSP_SEX: (patient.get("gender", "") or "").upper()[:1],
            DSP_BAR_CODE: specimen.get("sample_id", ""),
            DSP_SAMPLE_TIME: to_hl7_timestamp(order.get("request_datetime", "")),
            DSP_STAT: to_stat_flag(specimen.get("priority", "")),
            DSP_SAMPLE_TYPE: (specimen.get("sample_type", "") or "").lower(),
        }

        segments = [
            self._build_dsp(set_id, lines.get(set_id, ""))
            for set_id in range(1, DSP_FIXED_LINE_COUNT + 1)
        ]

        # Baris tes: TestNo^TestName^Unit^NormalRange. OrderObject hanya
        # menyimpan kode & nama; unit dan range dibiarkan kosong seperti
        # contoh manual (`1^^^`) karena alat memakai definisinya sendiri.
        for offset, test in enumerate(tests):
            data_line = "^".join([
                test.get("test_code", ""),
                test.get("test_name", ""),
                "",
                "",
            ])
            segments.append(self._build_dsp(DSP_FIRST_TEST_LINE + offset, data_line))

        return segments


def _now_timestamp() -> str:
    """Waktu lokal lab sekarang dalam format `YYYYMMDDHHMMSS`."""
    from lib import timeutil
    return timeutil.stamp("%Y%m%d%H%M%S")


# ============================================================
# Unit Test
# ============================================================

if __name__ == "__main__":
    print("=== Test MindrayBuilder ===\n")
    # Jam dikunci agar MSH-7 bisa dibandingkan persis dengan contoh manual.
    clock = {"now": "20070719145307"}
    b = MindrayBuilder(now=lambda: clock["now"])

    # --- to_hl7_timestamp ---
    assert to_hl7_timestamp("20070719145353") == "20070719145353"
    assert to_hl7_timestamp("19620824") == "19620824000000"
    assert to_hl7_timestamp("1962-08-24") == "19620824000000"
    assert to_hl7_timestamp("2026-07-16T14:30:05+07:00") == "20260716143005"
    assert to_hl7_timestamp("") == ""
    assert to_hl7_timestamp(None) == ""
    print("OK: to_hl7_timestamp()")

    # --- to_stat_flag ---
    assert to_stat_flag("S") == "Y"
    assert to_stat_flag("stat") == "Y"
    assert to_stat_flag("cito") == "Y"
    assert to_stat_flag("R") == "N"
    assert to_stat_flag("") == "N"
    print("OK: to_stat_flag()")

    ctx = {
        "manufacturer": "Mindray",
        "model": "BS-200E",
        "control_id": "1",
        "result_type": "0",
    }

    # --- ACK^R01: bandingkan dengan contoh manual bab 3 ---
    ack = b.build_ack_r01(ctx)
    ack_text = ack[1:-2].decode("ascii")
    expected_ack = (
        "MSH|^~\\&|||Mindray|BS-200E|20070719145307||ACK^R01|1|P|2.3.1||||0||ASCII|||\r"
        "MSA|AA|1|Message accepted|||0|\r"
    )
    assert ack[0:1] == b"\x0b", "harus diawali MLLP start"
    assert ack.endswith(b"\x1c\x0d"), "harus diakhiri MLLP trailer"
    assert ack_text == expected_ack, f"\ndapat    : {ack_text!r}\ndiharap  : {expected_ack!r}"
    print("OK: build_ack_r01() — byte-identik dengan contoh manual")

    # ACK QC memantulkan MSH-16 = 2
    ack_qc = b.build_ack_r01({**ctx, "result_type": "2", "control_id": "2"})
    assert b"||||2||ASCII" in ack_qc
    assert b"MSA|AA|2|" in ack_qc
    print("OK: build_ack_r01() — MSH-16 QC dipantulkan")

    # --- QCK^Q02 ---
    clock["now"] = "20070723170707"
    query_ctx = {
        "manufacturer": "Mindray",
        "model": "BS-200E",
        "control_id": "1",
        "qrd_raw": "QRD|20070723170707|R|D|1|||RD|34567743|OTH|||T|",
        "qrf_raw": "QRF|BS-200E|20070723170749|20070723170749|||RCT|COR|ALL||",
    }
    qck = b.build_qck_q02(query_ctx, found=True)
    qck_text = qck[1:-2].decode("ascii")
    expected_qck = (
        "MSH|^~\\&|||Mindray|BS-200E|20070723170707||QCK^Q02|1|P|2.3.1||||||ASCII|||\r"
        "MSA|AA|1|Message accepted|||0|\r"
        "ERR|0|\r"
        "QAK|SR|OK|\r"
    )
    assert qck_text == expected_qck, f"\ndapat   : {qck_text!r}\ndiharap : {expected_qck!r}"
    print("OK: build_qck_q02(found=True) — byte-identik dengan contoh manual")

    qck_nf = b.build_qck_q02(query_ctx, found=False)
    assert b"QAK|SR|NF|" in qck_nf
    assert b"QCK^Q02" in qck_nf
    print("OK: build_qck_q02(found=False) — QAK NF")

    # --- DSR^Q03 ---
    order = {
        "order_id": "ORD-1",
        "request_datetime": "2007-07-23T16:00:00",
        "patient": {
            "patient_id": "123",
            "name": "Tom",
            "dob": "19620824",
            "gender": "M",
        },
        "specimen": {
            "sample_id": "34567743",
            "sample_type": "Urine",
            "priority": "R",
        },
        "tests": [
            {"test_code": "1", "test_name": ""},
            {"test_code": "3", "test_name": ""},
        ],
    }
    dsr = b.build_dsr_q03(order, query_ctx)
    dsr_text = dsr[1:-2].decode("ascii")
    dsr_lines = dsr_text.split("\r")[:-1]

    assert dsr_lines[0].startswith("MSH|^~\\&|||Mindray|BS-200E|20070723170707||DSR^Q03|1|P|2.3.1")
    assert dsr_lines[1] == "MSA|AA|1|Message accepted|||0|"
    assert dsr_lines[2] == "ERR|0|"
    assert dsr_lines[3] == "QAK|SR|OK|"
    # QRD/QRF dipantulkan apa adanya dari query
    assert dsr_lines[4] == query_ctx["qrd_raw"]
    assert dsr_lines[5] == query_ctx["qrf_raw"]
    print("OK: build_dsr_q03() — header + QRD/QRF dipantulkan dari query")

    dsp_lines = [l for l in dsr_lines if l.startswith("DSP|")]
    assert len(dsp_lines) == 30, f"28 baris fixed + 2 tes, dapat {len(dsp_lines)}"
    assert dsp_lines[0] == "DSP|1||123|||"
    assert dsp_lines[1] == "DSP|2|||||"
    assert dsp_lines[2] == "DSP|3||Tom|||"
    assert dsp_lines[3] == "DSP|4||19620824000000|||"
    assert dsp_lines[4] == "DSP|5||M|||"
    assert dsp_lines[20] == "DSP|21||34567743|||", dsp_lines[20]
    assert dsp_lines[22] == "DSP|23||20070723160000|||", dsp_lines[22]
    assert dsp_lines[23] == "DSP|24||N|||"
    assert dsp_lines[25] == "DSP|26||urine|||", dsp_lines[25]
    assert dsp_lines[28] == "DSP|29||1^^^|||", dsp_lines[28]
    assert dsp_lines[29] == "DSP|30||3^^^|||", dsp_lines[29]
    print("OK: build_dsr_q03() — 28 baris fixed + baris tes sesuai urutan manual")

    assert dsr_lines[-1] == "DSC||", dsr_lines[-1]
    print("OK: build_dsr_q03() — DSC kosong menandai pesan terakhir")

    dsr_cont = b.build_dsr_q03(order, query_ctx, continuation="1")
    assert dsr_cont.decode("ascii").split("\r")[-3] == "DSC|1|"
    print("OK: build_dsr_q03() — DSC continuation terisi")

    # --- STAT order ---
    stat_order = {**order, "specimen": {**order["specimen"], "priority": "S"}}
    assert b"DSP|24||Y|||" in b.build_dsr_q03(stat_order, query_ctx)
    print("OK: build_dsr_q03() — priority STAT → Y")

    # --- Tanpa konteks query (broadcast): QRD/QRF dibangun sendiri ---
    dsr_bc = b.build_dsr_q03(order, {})
    bc_text = dsr_bc.decode("ascii")
    assert "DSR^Q03" in bc_text
    assert "QRD|" in bc_text and "|34567743|OTH|" in bc_text
    assert "QRF|BS-200E|" in bc_text
    assert "DSP|21||34567743|||" in bc_text
    print("OK: build_dsr_q03() — broadcast tanpa query membangun QRD/QRF sendiri")

    # --- Control ID naik dari 1 saat MidLab yang memulai ---
    b2 = MindrayBuilder()
    assert b2.next_control_id() == "1"
    assert b2.next_control_id() == "2"
    print("OK: next_control_id() naik dari 1")

    # --- Order kosong tidak bikin crash ---
    empty = b.build_dsr_q03({}, {})
    assert empty.count(b"DSP|") == 28, "tetap 28 baris fixed"
    print("OK: build_dsr_q03() — order kosong tetap valid")

    print("\n=== Semua test MindrayBuilder PASSED ===")
