"""
protocols/mindray_bc5150/builder.py — Builder pesan HL7 v2.3.1 Mindray BC-5150

Membangun pesan arah LIS → alat:

- ORR^O02 — balasan atas permintaan order (ORM^O01) dari alat
- ACK^R01 — balasan atas hasil (ORU^R01) dari alat

Layout field mengikuti byte yang terekam di log alat. Contoh dari log
(2019-05-04 06:07:45), dengan `<CR>` sebagai pemisah segment:

    <VT>MSH|^~\\&|Factory|Chemistry Analyzer|||20190504060745||ACK^R01|389|P|
        2.3.1||||||UNICODE<CR>MSA|AA|389||||0|<CR><FS><CR>

    <VT>MSH|^~\\&|LIS||||20190504060725||ORR^O02|389|P|2.3.1||||||UNICODE<CR>
        MSA|AR|389<CR><FS><CR>

Satu penyimpangan yang disengaja dari log: driver lama selalu menulis MSH-7
ORR^O02 sebagai `20081120175238` — timestamp beku dari 2008 yang jelas bug
(nilainya sama persis di seluruh log setahun penuh). Builder ini memakai jam
lab saat pesan dibuat. Alat tidak memvalidasi field ini.
"""

from itertools import count

from lib.utils import get_logger
from protocols.mindray_bc5150.constants import (
    MLLP_START_BYTE, MLLP_TRAILER,
    FIELD_SEPARATOR, ENCODING_CHARACTERS, SEGMENT_TERMINATOR,
    MESSAGE_ENCODING,
    HL7_VERSION, CHARACTER_SET, PROC_PRODUCTION,
    ORR_SENDING_APP, ORR_SENDING_FACILITY,
    ACK_SENDING_APP, ACK_SENDING_FACILITY,
    ACK_AA, STATUS_CODE_OK,
    ORR_ACK_FOUND, ORR_ACK_NOT_FOUND,
    EVENT_ORR_O02, EVENT_ACK_R01,
    ORC_NEW_ORDER, ORC_STATUS_IN_PROCESS,
    CODING_MINDRAY,
)


logger = get_logger("mindray_bc5150_builder")


def to_hl7_timestamp(value: str, pad: bool = True) -> str:
    """
    Ubah timestamp apa pun (ISO8601, `YYYYMMDD`, `YYYYMMDDHHMMSS`) menjadi
    format alat `YYYYMMDDHHMMSS`.

    Semua karakter non-digit dibuang; tanggal tanpa jam di-pad `000000`.
    """
    digits = "".join(ch for ch in (value or "") if ch.isdigit())
    if not digits:
        return ""
    digits = digits[:14]
    if pad and len(digits) < 14:
        digits = digits.ljust(14, "0")
    return digits


class MindrayBC5150Builder:
    """Builder pesan HL7 Mindray BC-5150 (arah LIS → alat)."""

    def __init__(self, now=None):
        """
        Args:
            now: callable pengganti jam sistem yang mengembalikan string
                 `YYYYMMDDHHMMSS` — dipakai unit test untuk mengunci MSH-7.
        """
        # Dipakai hanya bila MidLab yang memulai pesan; untuk balasan, control
        # ID pesan alat selalu dipantulkan.
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
        return (
            MLLP_START_BYTE
            + body.encode(MESSAGE_ENCODING, errors="replace")
            + MLLP_TRAILER
        )

    def _build_msh(self, event: str, control_id: str,
                   sending_app: str, sending_facility: str) -> str:
        """
        Bangun MSH arah LIS → alat.

        Alat mengosongkan MSH-3..MSH-6 pada pesannya, dan driver lama tidak
        mengisi MSH-5/MSH-6 pada balasan — pola itu dipertahankan. MSH-7 memakai
        jam lab saat pesan dibuat.
        """
        fields = [
            "MSH",
            ENCODING_CHARACTERS,   # MSH-2
            sending_app,           # MSH-3  Sending Application
            sending_facility,      # MSH-4  Sending Facility
            "",                    # MSH-5  Receiving Application
            "",                    # MSH-6  Receiving Facility
            self._now(),           # MSH-7
            "",                    # MSH-8  Security
            event,                 # MSH-9
            control_id,            # MSH-10
            PROC_PRODUCTION,       # MSH-11
            HL7_VERSION,           # MSH-12
            "", "", "", "", "",    # MSH-13..17
            CHARACTER_SET,         # MSH-18
        ]
        return FIELD_SEPARATOR.join(fields)

    # ============================================================
    # ACK^R01 — balasan hasil
    # ============================================================

    def build_ack_r01(self, context: dict) -> bytes:
        """
        Bangun ACK^R01 atas pesan ORU^R01 dari alat.

        Bentuk MSA persis seperti log: `MSA|AA|<control id>||||0|` — MSA-3
        dikosongkan (berbeda dari BS-200E yang mengisi "Message accepted") dan
        MSA-6 berisi status code 0.

        Args:
            context: hasil MindrayBC5150Parser.parse_msh() pesan ORU
        """
        context = context or {}
        control_id = context.get("control_id") or "1"

        segments = [
            self._build_msh(
                EVENT_ACK_R01, control_id,
                ACK_SENDING_APP, ACK_SENDING_FACILITY,
            ),
            FIELD_SEPARATOR.join(
                ["MSA", ACK_AA, control_id, "", "", "", STATUS_CODE_OK, ""]
            ),
        ]
        logger.info(f"ACK^R01 dibangun untuk control_id={control_id}")
        return self._wrap_mllp(segments)

    # ============================================================
    # ORR^O02 — balasan permintaan order
    # ============================================================

    def build_orr_not_found(self, context: dict) -> bytes:
        """
        Bangun ORR^O02 dengan MSA|AR — "tidak ada order untuk sampel ini".

        Ini jalur yang dipakai mode unidirectional dan satu-satunya bentuk ORR
        yang terverifikasi di log. Alat menerimanya lalu tetap mengirim hasil
        sekitar 60 detik kemudian.
        """
        context = context or {}
        control_id = context.get("control_id") or "1"

        segments = [
            self._build_msh(
                EVENT_ORR_O02, control_id,
                ORR_SENDING_APP, ORR_SENDING_FACILITY,
            ),
            FIELD_SEPARATOR.join(["MSA", ORR_ACK_NOT_FOUND, control_id]),
        ]
        logger.info(f"ORR^O02 (MSA|AR) dibangun untuk control_id={control_id}")
        return self._wrap_mllp(segments)

    def build_orr_with_order(self, order: dict, context: dict) -> bytes:
        """
        Bangun ORR^O02 dengan MSA|AA + ORC/OBR berisi order.

        ⚠️ BELUM TERVERIFIKASI TERHADAP ALAT. Log sumber driver ini seluruhnya
        unidirectional — MidLab tidak pernah mengirim order, sehingga bentuk
        pesan di bawah disusun dari struktur ORR^O02 HL7 v2.3.1 dan pola ORM
        yang dikirim alat, bukan dari byte yang terbukti diterima BC-5150.
        Uji dengan Tapping Data sebelum dipakai produksi.

        Args:
            order: OrderObject dari tbl_order.order_json
            context: konteks ORM^O01 yang dibalas
        """
        context = context or {}
        control_id = context.get("control_id") or self.next_control_id()

        order = order or {}
        specimen = order.get("specimen") or {}
        patient = order.get("patient") or {}
        tests = order.get("tests") or []

        sample_id = (
            specimen.get("sample_id")
            or order.get("order_id")
            or context.get("sample_id", "")
        )

        segments = [
            self._build_msh(
                EVENT_ORR_O02, control_id,
                ORR_SENDING_APP, ORR_SENDING_FACILITY,
            ),
            FIELD_SEPARATOR.join(["MSA", ORR_ACK_FOUND, control_id]),
            FIELD_SEPARATOR.join([
                "PID",
                "1",
                "",
                f"{patient.get('patient_id', '')}{'^^^^MR'}",
                "",
                patient.get("name", ""),
                "",
                to_hl7_timestamp(patient.get("dob", "")),
                patient.get("gender", ""),
            ]),
        ]

        # Satu pasang ORC+OBR per tes yang diminta.
        for idx, test in enumerate(tests, start=1):
            test_code = test.get("test_code", "")
            test_name = test.get("test_name", "")
            segments.append(FIELD_SEPARATOR.join([
                "ORC", ORC_NEW_ORDER, "", sample_id, "", ORC_STATUS_IN_PROCESS,
            ]))
            segments.append(FIELD_SEPARATOR.join([
                "OBR",
                str(idx),
                "",
                sample_id,
                f"{test_code}^{test_name}^{CODING_MINDRAY}",
            ]))

        if not tests:
            segments.append(FIELD_SEPARATOR.join([
                "ORC", ORC_NEW_ORDER, "", sample_id, "", ORC_STATUS_IN_PROCESS,
            ]))

        logger.info(
            f"ORR^O02 (MSA|AA) dibangun untuk sample_id={sample_id or '-'}, "
            f"{len(tests)} tes — format belum terverifikasi terhadap alat"
        )
        return self._wrap_mllp(segments)


def _now_timestamp() -> str:
    """Waktu lokal lab sekarang dalam format `YYYYMMDDHHMMSS`."""
    from lib import timeutil
    return timeutil.stamp("%Y%m%d%H%M%S")
