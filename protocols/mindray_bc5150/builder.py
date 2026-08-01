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

from datetime import datetime
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
    ORC_AFFIRM,
    ESCAPE_SEQUENCES,
    SERVICE_AUTOMATED_COUNT, DIAGNOSTIC_SERV_HEMATOLOGY,
    OBR_FIELD_PLACER_SAMPLE_ID, OBR_FIELD_SERVICE_ID, OBR_FIELD_DRAW_TIME,
    OBR_FIELD_SENDER, OBR_FIELD_CLINICAL_INFO, OBR_FIELD_RECEIVED_TIME,
    OBR_FIELD_SERVICE_SECTION, OBR_FIELD_INTERPRETER, OBR_FIELD_COUNT,
    OBX_CODE_BLOOD_MODE, OBX_CODE_TEST_MODE, OBX_CODE_AGE, OBX_CODE_REMARK,
    BLOOD_MODE_BY_SAMPLE_TYPE, VALID_TEST_MODES, AGE_UNIT_YEAR,
    VALUE_TYPE_CODED, VALUE_TYPE_NUMERIC, VALUE_TYPE_STRING,
    OBSERVATION_STATUS_FINAL,
)


logger = get_logger("mindray_bc5150_builder")


def to_hl7_timestamp(value: str, pad: bool = True) -> str:
    """
    Ubah timestamp apa pun (ISO8601, `YYYYMMDD`, `YYYYMMDDHHMMSS`) menjadi
    format alat `YYYYMMDDHHMMSS`.

    Nilai ber-offset zona (`2026-08-01T06:49:18+00:00`, bentuk yang dipakai
    EazyApp) dikonversi dulu ke jam dinding lab. Membuang offsetnya begitu
    saja — yang dilakukan versi sebelumnya — menggeser draw time 7 jam untuk
    order yang datang dalam UTC.

    Nilai tanpa offset dianggap sudah jam lab dan tidak digeser: alat dan
    driver lama sama-sama mengirim `YYYYMMDDHHMMSS` polos.

    Semua karakter non-digit dibuang; tanggal tanpa jam di-pad `000000`.
    """
    aware = _parse_offset_timestamp(value)
    if aware is not None:
        from lib import timeutil
        return timeutil.stamp("%Y%m%d%H%M%S", timeutil.to_local(aware))

    digits = "".join(ch for ch in (value or "") if ch.isdigit())
    if not digits:
        return ""
    digits = digits[:14]
    if pad and len(digits) < 14:
        digits = digits.ljust(14, "0")
    return digits


def _parse_offset_timestamp(value: str):
    """
    Baca `value` sebagai ISO8601 **ber-offset**; None bila bukan.

    Hanya nilai yang membawa zona yang dikembalikan — sisanya (jam polos tanpa
    zona, format non-ISO, string kosong) dibiarkan lewat ke jalur digit biasa
    supaya jam lokal tidak pernah tergeser oleh asumsi zona.
    """
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def escape(value: str) -> str:
    """
    Ganti delimiter di dalam teks bebas dengan escape sequence HL7 (dok §3).

    Wajib untuk setiap nilai yang berasal dari LIS — nama pasien, remark,
    diagnosa. Satu `|` yang lolos akan memecah segment dan menggeser seluruh
    pemetaan field sesudahnya, dan alat akan membaca order yang salah.
    """
    if not value:
        return ""
    text = str(value)
    for original, sequence in ESCAPE_SEQUENCES:
        text = text.replace(original, sequence)
    return text


def _padded_segment(name: str, assignments: dict, field_count: int) -> str:
    """
    Rakit segment dengan field kosong dipertahankan sampai `field_count`.

    Dipakai OBR pada ORR^O02: dok §5.5 menempatkan nilai di OBR-2, OBR-4,
    OBR-6, OBR-10, OBR-14, OBR-24 dan OBR-32, jadi field kosong di antaranya
    tidak boleh dipangkas — posisi field-lah yang memberi arti.
    """
    fields = [""] * (field_count + 1)
    fields[0] = name
    for index, value in assignments.items():
        fields[index] = value or ""
    return FIELD_SEPARATOR.join(fields)


def age_in_years(dob: str, today: str) -> str:
    """
    Hitung umur penuh dalam tahun dari `dob` terhadap tanggal `today`.

    Dua-duanya `YYYYMMDD...`; string kosong bila dob tidak bisa dibaca. Umur
    dikirim karena BC-5150 memakainya untuk memilih reference group — hasil
    pasien anak akan dinilai terhadap range dewasa bila field ini kosong.
    """
    dob_digits = to_hl7_timestamp(dob, pad=False)
    if len(dob_digits) < 8 or len(today) < 8:
        return ""

    try:
        born = (int(dob_digits[0:4]), int(dob_digits[4:6]), int(dob_digits[6:8]))
        now = (int(today[0:4]), int(today[4:6]), int(today[6:8]))
    except ValueError:
        return ""

    years = now[0] - born[0] - ((now[1], now[2]) < (born[1], born[2]))
    if years < 0 or years > 150:
        return ""
    return str(years)


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
                   sending_app: str, sending_facility: str,
                   processing_id: str = PROC_PRODUCTION) -> str:
        """
        Bangun MSH arah LIS → alat.

        Alat mengosongkan MSH-3..MSH-6 pada pesannya, dan driver lama tidak
        mengisi MSH-5/MSH-6 pada balasan — pola itu dipertahankan. MSH-7 memakai
        jam lab saat pesan dibuat.

        MSH-11 memantulkan processing ID pesan yang dibalas: dok Tabel 4-1
        menyatakan balasan harus konsisten dengan pesan sebelumnya, dan dok §5.4
        menegaskan balasan hasil QC memakai `Q` — bukan `P`.
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
            processing_id,         # MSH-11
            HL7_VERSION,           # MSH-12
            "", "", "", "", "",    # MSH-13..17
            CHARACTER_SET,         # MSH-18
        ]
        return FIELD_SEPARATOR.join(fields)

    @staticmethod
    def _processing_id(context: dict) -> str:
        """Processing ID pesan alat, jatuh ke `P` bila alat tidak mengirimnya."""
        return (context or {}).get("processing_id") or PROC_PRODUCTION

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
                processing_id=self._processing_id(context),
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
                processing_id=self._processing_id(context),
            ),
            FIELD_SEPARATOR.join(["MSA", ORR_ACK_NOT_FOUND, control_id]),
        ]
        logger.info(f"ORR^O02 (MSA|AR) dibangun untuk control_id={control_id}")
        return self._wrap_mllp(segments)

    def build_orr_with_order(self, order: dict, context: dict) -> bytes:
        """
        Bangun ORR^O02 berisi worklist untuk sampel yang ditanyakan alat.

        Bentuknya mengikuti dok §4.2.4 dan contoh dok §5.5:

            MSH   ORR^O02, MSH-11 mengikuti pesan alat
            MSA   AA + control ID pesan alat
            PID   identitas pasien
            PV1   bangsal / bed (hanya bila ada isinya)
            ORC   AF + sample ID di ORC-2
            OBR   sample ID di OBR-2, jenis analisis di OBR-4
            OBX   mode darah, mode pemeriksaan, umur, remark

        Dua aturan dok yang kalau dilanggar membuat alat menolak pesan:

        1. ORC-1 harus `AF` ("affirm the re-filled order"), bukan `NW`, dan
           sample ID pindah ke ORC-2 — pada ORM dari alat ia ada di ORC-3.
        2. "the OBR-2 field indicates the sample ID, which should be the same
           value as in the ORC-2 field; Otherwise, the message will be regarded
           as incorrect" (dok §5.5). Jadi sample ID ditulis di OBR-2, dan
           OBR-3 justru dibiarkan kosong.

        Alat hematologi tidak menerima daftar test code seperti alat kimia:
        yang bisa diperintahkan lewat worklist adalah cara sampel dijalankan
        (Blood Mode / Test Mode) plus konteks pasien. `order["tests"]` karena
        itu dipakai untuk menentukan Test Mode, bukan dipetakan satu-satu.

        ⚠️ Belum diverifikasi terhadap alat fisik — log sumber driver ini
        seluruhnya unidirectional. Bentuk di atas sesuai dok vendor; rekam
        sesi Tapping Data saat pertama kali dipakai di lapangan.

        Args:
            order: OrderObject dari tbl_order.order_json
            context: konteks ORM^O01 yang dibalas
        """
        context = context or {}
        control_id = context.get("control_id") or self.next_control_id()

        order = order or {}
        specimen = order.get("specimen") or {}
        patient = order.get("patient") or {}

        sample_id = (
            specimen.get("sample_id")
            or order.get("order_id")
            or context.get("sample_id", "")
        )

        segments = [
            self._build_msh(
                EVENT_ORR_O02, control_id,
                ORR_SENDING_APP, ORR_SENDING_FACILITY,
                processing_id=self._processing_id(context),
            ),
            FIELD_SEPARATOR.join(["MSA", ORR_ACK_FOUND, control_id]),
            self._build_pid(patient),
        ]

        pv1 = self._build_pv1(order)
        if pv1:
            segments.append(pv1)

        # ORC-2 = sample ID (dok Tabel 4-8). ORC-3 dan ORC-5 dikosongkan —
        # keduanya hanya terisi pada arah ORM.
        segments.append(FIELD_SEPARATOR.join(["ORC", ORC_AFFIRM, sample_id]))
        segments.append(self._build_obr(order, sample_id))
        segments.extend(self._build_order_obx(order, patient))

        logger.info(
            f"ORR^O02 (MSA|AA) dibangun untuk sample_id={sample_id or '-'}, "
            f"control_id={control_id}"
        )
        return self._wrap_mllp(segments)

    # ------------------------------------------------------------
    # Segment ORR^O02
    # ------------------------------------------------------------

    def _build_pid(self, patient: dict) -> str:
        """
        PID untuk worklist — dok §4.3.3, contoh `PID|1||test1^^^^MR||^Tom||...`

        PID-3 memakai sufiks `^^^^MR` (identifier type code) persis seperti
        pesan alat, supaya alat mengenali nilainya sebagai patient ID.
        """
        patient = patient or {}
        patient_id = escape(patient.get("patient_id", ""))
        return FIELD_SEPARATOR.join([
            "PID",
            "1",                                          # PID-1
            "",                                           # PID-2
            f"{patient_id}^^^^MR" if patient_id else "",  # PID-3
            "",                                           # PID-4
            escape(patient.get("name", "")),              # PID-5
            "",                                           # PID-6
            to_hl7_timestamp(patient.get("dob", "")),     # PID-7
            escape(patient.get("gender", "")),            # PID-8
        ])

    def _build_pv1(self, order: dict) -> str:
        """
        PV1 untuk worklist — dok §4.3.4, `PV1|1||ICU^^BedNO1`.

        OrderObject MidLab tidak punya field bangsal/bed baku, jadi PV1 hanya
        dibangun bila LIS menyertakannya lewat key opsional `visit`. Tanpa itu
        segment ini dilewati — mengirim `PV1|1` kosong tidak menambah apa pun.
        """
        visit = (order or {}).get("visit") or {}
        if not isinstance(visit, dict):
            return ""

        point_of_care = escape(visit.get("point_of_care", ""))
        room = escape(visit.get("room", ""))
        bed = escape(visit.get("bed", ""))
        if not any((point_of_care, room, bed)):
            return ""

        location = f"{point_of_care}^{room}^{bed}".rstrip("^")
        return FIELD_SEPARATOR.join(["PV1", "1", "", location])

    def _draw_time(self, order: dict) -> str:
        """
        Waktu pengambilan darah untuk OBR-6, dengan fallback berlapis.

        BC-5150 memvalidasi field ini saat operator menekan OK di dialog info
        sampel: draw time kosong = worklist ditolak, operator harus mengetik
        manual tiap sampel. Karena itu field ini tidak pernah dikirim kosong.

        Urutan sumber, dari yang paling benar:
        1. `specimen.collected_at` — jam flebotomi asli dari LIS.
        2. `request_datetime` — jam order dibuat; selisihnya menit dan alat
           memakainya untuk peringatan umur sampel, jadi cukup akurat.
        3. Jam lab saat worklist dibangun — sampel baru saja diambil bila
           alat menanyakannya sekarang.
        """
        order = order or {}
        specimen = order.get("specimen") or {}

        for candidate in (specimen.get("collected_at"),
                          order.get("request_datetime")):
            stamp = to_hl7_timestamp(candidate or "")
            if stamp:
                return stamp
        return self._now()

    def _build_obr(self, order: dict, sample_id: str) -> str:
        """
        OBR untuk worklist — sample ID di OBR-2 (dok §5.5), bukan OBR-3.

        Field kosong di antara nilai dipertahankan sampai OBR-32 karena arti
        setiap nilai ditentukan posisinya.
        """
        order = order or {}

        return _padded_segment("OBR", {
            1: "1",
            OBR_FIELD_PLACER_SAMPLE_ID: sample_id,
            OBR_FIELD_SERVICE_ID: SERVICE_AUTOMATED_COUNT,
            OBR_FIELD_DRAW_TIME: self._draw_time(order),
            OBR_FIELD_SENDER: escape(order.get("sender", "")),
            OBR_FIELD_CLINICAL_INFO: escape(order.get("clinical_info", "")),
            OBR_FIELD_RECEIVED_TIME: to_hl7_timestamp(
                order.get("request_datetime", "")
            ),
            OBR_FIELD_SERVICE_SECTION: DIAGNOSTIC_SERV_HEMATOLOGY,
            OBR_FIELD_INTERPRETER: escape(order.get("operator", "")),
        }, OBR_FIELD_COUNT)

    def _build_order_obx(self, order: dict, patient: dict) -> list:
        """
        OBX worklist: Blood Mode, Test Mode, Age, Remark (dok §5.5).

        Setiap OBX hanya dibangun bila datanya benar-benar ada. Mengirim OBX
        kosong berisiko: alat akan membaca string kosong sebagai perintah mode
        dan menolak sampel, padahal tanpa OBX ia cukup memakai setelan sendiri.
        """
        order = order or {}
        specimen = order.get("specimen") or {}
        entries = []

        blood_mode = BLOOD_MODE_BY_SAMPLE_TYPE.get(
            (specimen.get("sample_type") or "").strip().lower()
        )
        if blood_mode:
            entries.append((VALUE_TYPE_CODED, OBX_CODE_BLOOD_MODE, blood_mode, ""))

        test_mode = self._test_mode(order)
        if test_mode:
            entries.append((VALUE_TYPE_CODED, OBX_CODE_TEST_MODE, test_mode, ""))

        age = age_in_years((patient or {}).get("dob", ""), self._now())
        if age:
            entries.append((VALUE_TYPE_NUMERIC, OBX_CODE_AGE, age, AGE_UNIT_YEAR))

        remark = order.get("remark", "")
        if remark:
            entries.append((VALUE_TYPE_STRING, OBX_CODE_REMARK, escape(remark), ""))

        segments = []
        for idx, (value_type, identifier, value, unit) in enumerate(entries, start=1):
            segments.append(FIELD_SEPARATOR.join([
                "OBX",
                str(idx),                  # OBX-1
                value_type,                # OBX-2
                identifier,                # OBX-3
                "",                        # OBX-4
                value,                     # OBX-5
                unit,                      # OBX-6
                "", "", "", "",            # OBX-7..10
                OBSERVATION_STATUS_FINAL,  # OBX-11
            ]))
        return segments

    @staticmethod
    def _test_mode(order: dict) -> str:
        """
        Tentukan Test Mode dari daftar tes yang di-order.

        LIS mengirim tes sebagai `test_code`/`test_name`; yang relevan untuk
        alat hematologi hanya apakah panel yang diminta CBC saja atau CBC
        dengan hitung jenis. Bila tidak ada yang cocok dengan enumerasi dok,
        Test Mode tidak dikirim dan alat memakai setelan defaultnya sendiri —
        lebih aman daripada menebak.
        """
        for test in (order or {}).get("tests") or []:
            for candidate in (test.get("test_code", ""), test.get("test_name", "")):
                normalized = (candidate or "").strip().upper().replace(" ", "")
                if normalized in VALID_TEST_MODES:
                    return normalized
        return ""


def _now_timestamp() -> str:
    """Waktu lokal lab sekarang dalam format `YYYYMMDDHHMMSS`."""
    from lib import timeutil
    return timeutil.stamp("%Y%m%d%H%M%S")
