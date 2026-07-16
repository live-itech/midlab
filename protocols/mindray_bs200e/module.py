"""
protocols/mindray_bs200e/module.py — Protocol Module Mindray BS-200E

Implementasi BaseProtocolModule untuk Mindray BS-200E Chemistry Analyzer
(HL7 v2.3.1 + MLLP), sesuai Host Interface Manual v6.0.

Mode yang didukung:

- unidirectional  — terima ORU^R01 (hasil sampel / QC / kalibrasi) → ResultObject,
                    balas ACK^R01
- bidirectional query    — alat kirim QRY^Q02 per barcode, MidLab balas
                    QCK^Q02 + DSR^Q03 berisi order, alat balas ACK^Q03.
                    Ini mode bidirectional native alat (alat yang menarik order).
- bidirectional broadcast — MidLab mendorong DSR^Q03 tanpa didahului query.
                    Manual tidak mendefinisikan alur ini (alat selalu menjadi
                    inisiator download), jadi anggap best-effort: pakai `query`
                    bila alat mendukung, `broadcast` hanya bila terbukti diterima
                    firmware setempat.

Di-load dynamic via protocols.base.load_module("HL7_MINDRAY_BS200E").
"""

from lib.utils import get_logger
from lib.models import (
    ResultObject, PatientInfo, SpecimenInfo, OrderInfo, TestResult,
)
from protocols.base import BaseProtocolModule
from protocols.mindray_bs200e.constants import (
    PROTOCOL_NAME, PROTOCOL_VERSION,
    SEG_MSH, SEG_PID, SEG_OBR, SEG_OBX, SEG_MSA,
    ACK_AA, ACK_AE, ACK_AR,
    EVENT_ORU_R01, QUERY_EVENTS,
    RESULT_TYPE_SAMPLE, RESULT_TYPE_CALIBRATION, RESULT_TYPE_QC,
)
from protocols.mindray_bs200e.parser import MindrayParser
from protocols.mindray_bs200e.builder import MindrayBuilder


class MindrayBS200EModule(BaseProtocolModule):
    """Protocol module Mindray BS-200E — HL7 v2.3.1 di atas MLLP."""

    # Manual bab 3: bila sampel tidak ada di LIS, alat tidak membalas QCK NF
    # sama sekali. QueryHandler membaca flag ini agar tidak menunggu ACK yang
    # tak akan datang.
    ACK_EXPECTED_ON_NOT_FOUND = False

    def __init__(self):
        self._parser = MindrayParser()
        self._builder = MindrayBuilder()
        self._logger = get_logger("mindray_bs200e")

    # ============================================================
    # Properties
    # ============================================================

    @property
    def PROTOCOL_NAME(self) -> str:
        return PROTOCOL_NAME

    @property
    def VERSION(self) -> str:
        return PROTOCOL_VERSION

    # ============================================================
    # parse() — ORU^R01 → ResultObject
    # ============================================================

    def parse(self, raw_bytes: bytes, instrument: dict) -> dict:
        """
        Parse pesan ORU^R01 dari alat menjadi ResultObject dict.

        Satu pesan ORU membawa satu tes (manual bab 2) — sampel dengan banyak
        tes datang sebagai beberapa pesan ORU berurutan, masing-masing disimpan
        sebagai satu baris tbl_result dan digabung di sisi LIS via barcode.
        """
        instrument_id = instrument.get("id", 0)
        result = ResultObject(instrument_id=instrument_id, protocol=PROTOCOL_NAME)

        self._logger.info(
            f"Mulai parse {len(raw_bytes)} bytes dari instrument {instrument_id}"
        )

        if not raw_bytes:
            result.parse_errors.append("Data kosong")
            return result.to_dict()

        try:
            segments = self._parser.split_message(self._parser.unwrap_mllp(raw_bytes))
        except Exception as e:
            result.parse_errors.append(f"Gagal decode pesan: {e}")
            self._logger.error(f"Gagal decode pesan: {e}")
            return result.to_dict()

        if not segments:
            result.parse_errors.append("Tidak ada segment valid setelah decode")
            return result.to_dict()

        msh_fields = self._parser.find_segment(segments, SEG_MSH)
        if msh_fields is None:
            result.parse_errors.append("Segment MSH tidak ditemukan")
            self._logger.warning("Pesan tanpa MSH — diabaikan")
            return result.to_dict()

        msh = self._parser.parse_msh(msh_fields)

        if msh.get("datetime"):
            result.message_datetime = msh["datetime"]

        message_type = msh.get("message_type", "")
        if message_type and message_type != EVENT_ORU_R01:
            result.parse_errors.append(f"Message type bukan hasil: {message_type}")
            self._logger.warning(f"parse() dipanggil untuk message type {message_type}")

        result_type = msh.get("result_type", "") or RESULT_TYPE_SAMPLE

        try:
            if result_type == RESULT_TYPE_QC:
                self._assemble_qc(segments, result)
            elif result_type == RESULT_TYPE_CALIBRATION:
                self._assemble_calibration(segments, result)
            else:
                self._assemble_sample(segments, result)
        except Exception as e:
            result.parse_errors.append(f"Parse gagal: {e}")
            self._logger.error(f"Parse gagal: {e}")

        self._logger.info(
            f"Parse selesai: type={result_type}, {len(result.results)} hasil, "
            f"{len(result.comments)} komentar, {len(result.parse_errors)} error"
        )
        return result.to_dict()

    def _assemble_sample(self, segments: list, result: ResultObject):
        """Rakit ResultObject untuk hasil sampel (MSH-16 = 0): PID + OBR + OBX."""
        pid_fields = self._parser.find_segment(segments, SEG_PID)
        if pid_fields:
            pid = self._parser.parse_pid(pid_fields)
            result.patient = PatientInfo(
                patient_id=pid["patient_id"],
                name=pid["name"],
                dob=pid["dob"],
                gender=pid["gender"],
            )
            if pid.get("bed_number"):
                result.comments.append(f"bed: {pid['bed_number']}")
            if pid.get("blood_type"):
                result.comments.append(f"blood_type: {pid['blood_type']}")

        obr_fields = self._parser.find_segment(segments, SEG_OBR)
        if obr_fields:
            obr = self._parser.parse_obr_sample(obr_fields)

            # OBR-3 adalah nomor internal alat — manual melarang server
            # memakainya, jadi barcode diutamakan sebagai identitas sampel.
            sample_id = obr["barcode"] or obr["internal_sample_id"]
            if not obr["barcode"] and obr["internal_sample_id"]:
                result.parse_errors.append(
                    "Barcode (OBR-2) kosong — memakai sample ID internal alat (OBR-3)"
                )
                self._logger.warning("Barcode kosong, fallback ke OBR-3")

            result.specimen = SpecimenInfo(
                sample_id=sample_id,
                sample_type=obr["sample_type"],
                collected_at=obr["observation_datetime"],
            )
            result.order = OrderInfo(order_id=sample_id)
            result.patient.physician = obr["attending_doctor"]

            for label, value in (
                ("clinical_info", obr["clinical_info"]),
                ("sample_characteristic", obr["sample_characteristic"]),
                ("sender", obr["sender"]),
                ("department", obr["department"]),
            ):
                if value:
                    result.comments.append(f"{label}: {value}")
        else:
            result.parse_errors.append("Segment OBR tidak ditemukan")

        obx_found = False
        for fields in segments:
            if self._parser.segment_type(fields) != SEG_OBX:
                continue
            obx_found = True
            obx = self._parser.parse_obx(fields)
            result.results.append(TestResult(
                test_code=obx["test_code"],
                test_name=obx["test_name"],
                value=obx["value"],
                unit=obx["unit"],
                reference_range=obx["reference_range"],
                flag=obx["flag"],
                status=obx["status"],
            ))

        if not obx_found:
            result.parse_errors.append("Tidak ada segment OBX (hasil kosong)")

    def _assemble_qc(self, segments: list, result: ResultObject):
        """
        Rakit ResultObject untuk hasil QC (MSH-16 = 2).

        Pesan QC tidak punya PID/OBX — seluruh datanya ada di OBR.
        """
        obr_fields = self._parser.find_segment(segments, SEG_OBR)
        if obr_fields is None:
            result.parse_errors.append("Pesan QC tanpa segment OBR")
            return

        qc = self._parser.parse_obr_qc(obr_fields)

        result.specimen = SpecimenInfo(
            sample_type="qc",
            collected_at=qc["qc_datetime"],
        )
        result.results.append(TestResult(
            test_code=qc["test_code"],
            test_name=qc["test_name"],
            value=qc["value"],
            unit=qc["unit"],
            flag=qc["level"],       # H / M / L — level konsentrasi kontrol
            status="qc",
        ))

        for label, value in (
            ("control_name", qc["control_name"]),
            ("lot_number", qc["lot_number"]),
            ("expiration_date", qc["expiration_date"]),
            ("mean_value", qc["mean_value"]),
            ("standard_deviation", qc["standard_deviation"]),
        ):
            if value:
                result.comments.append(f"{label}: {value}")

    def _assemble_calibration(self, segments: list, result: ResultObject):
        """
        Rakit ResultObject untuk hasil kalibrasi (MSH-16 = 1).

        BS-200E tidak mengirim kalibrasi menurut manual; ringkasan tetap direkam
        agar seri lain di keluarga yang sama tidak kehilangan data.
        """
        obr_fields = self._parser.find_segment(segments, SEG_OBR)
        if obr_fields is None:
            result.parse_errors.append("Pesan kalibrasi tanpa segment OBR")
            return

        cal = self._parser.parse_obr_calibration(obr_fields)

        result.specimen = SpecimenInfo(
            sample_type="calibration",
            collected_at=cal["calibration_datetime"],
        )
        result.results.append(TestResult(
            test_code=cal["test_code"],
            test_name=cal["test_name"],
            value=cal["value"],
            flag=cal["calibration_rule"],
            status="calibration",
        ))

        for label, value in (
            ("calibrator_name", cal["calibrator_name"]),
            ("lot_number", cal["lot_number"]),
            ("k_factor", cal["k_factor"]),
            ("calibrator_count", cal["calibrator_count"]),
        ):
            if value:
                result.comments.append(f"{label}: {value}")

    # ============================================================
    # ACK^R01 — dipanggil ResultReceiver setelah hasil disimpan
    # ============================================================

    def build_ack_response(self, raw_message: bytes, instrument: dict) -> bytes:
        """
        Bangun ACK^R01 atas pesan yang baru diterima.

        ResultReceiver memanggil hook ini bila ada, supaya ACK memakai layout
        Mindray (MSH-5/6 = Manufacturer/Model, MSA-6 status code) dan bukan
        ACK HL7 generic.

        Returns:
            Bytes ACK ber-envelope MLLP, atau b"" bila pesan tak punya MSH.
        """
        try:
            segments = self._parser.split_message(self._parser.unwrap_mllp(raw_message))
            msh_fields = self._parser.find_segment(segments, SEG_MSH)
            if msh_fields is None:
                self._logger.warning("Tidak bisa ACK: pesan tanpa MSH")
                return b""

            return self._builder.build_ack_r01(self._parser.parse_msh(msh_fields))

        except Exception as e:
            self._logger.warning(f"Gagal bangun ACK: {e}")
            return b""

    # ============================================================
    # Query handling — QRY^Q02 → QCK^Q02 + DSR^Q03
    # ============================================================

    def is_enq(self, raw_bytes: bytes) -> bool:
        """True bila pesan adalah QRY^Q02 (permintaan order dari alat)."""
        if not raw_bytes:
            return False

        message_type = self._parser.get_message_type(raw_bytes)
        is_query = message_type in QUERY_EVENTS
        if is_query:
            self._logger.info(f"Query terdeteksi: {message_type}")
        return is_query

    def handle_enq(self, raw_bytes: bytes, instrument: dict) -> dict:
        """
        Parse QRY^Q02, ekstrak barcode sampel yang diminta alat.

        Returns:
            Dict {type, sample_id, patient_id, raw_query, _msh}. Key `_msh`
            berisi konteks query lengkap (control ID + QRD/QRF mentah) yang
            dipakai QueryHandler saat memanggil format_query_response_full().
        """
        info = {
            "type": "query",
            "sample_id": "",
            "patient_id": "",
            "raw_query": raw_bytes.hex(),
        }

        try:
            context = self._parser.parse_query(raw_bytes)
            info["_msh"] = context

            qrd = context.get("qrd", {})
            info["sample_id"] = qrd.get("barcode", "")

            if qrd.get("is_cancel"):
                info["type"] = "cancel"
                self._logger.info("Alat membatalkan group download")
            elif qrd.get("is_group_query"):
                info["type"] = "group_query"
                # Group download minta semua sampel hari itu; QueryHandler hanya
                # mencari satu order per sample_id, jadi query tanpa barcode
                # akan dibalas NF.
                self._logger.warning(
                    "Group download (QRD-8 kosong) belum didukung — akan dibalas NF. "
                    "Pakai download per barcode di alat."
                )

            self._logger.info(f"Query parsed: barcode={info['sample_id'] or '(kosong)'}")

        except Exception as e:
            self._logger.warning(f"Gagal parse query: {e}")

        return info

    def format_query_response(self, order: dict, instrument: dict) -> bytes:
        """
        Bangun response query tanpa konteks pesan asli.

        Dipakai bila QueryHandler tidak menyertakan konteks query; control ID
        dan QRD/QRF dibangun sendiri.
        """
        return self.format_query_response_full(order, instrument, {})

    def format_query_response_full(self, order: dict, instrument: dict,
                                   query_msh: dict) -> bytes:
        """
        Bangun response lengkap untuk order yang ditemukan: QCK^Q02 (QAK OK)
        diikuti DSR^Q03 berisi data order, dikirim dalam satu kali write.

        Manual bab 3: LIS membalas QCK dulu, lalu mengirim DSR; alat hanya
        meng-ACK DSR (dengan ACK^Q03), bukan QCK.
        """
        context = query_msh or {}
        self._logger.info(
            f"Membangun QCK+DSR untuk order "
            f"{order.get('order_id', '?')} / barcode "
            f"{(order.get('specimen', {}) or {}).get('sample_id', '?')}"
        )

        qck = self._builder.build_qck_q02(context, found=True)
        dsr = self._builder.build_dsr_q03(order, context)
        return qck + dsr

    def format_query_not_found(self, instrument: dict) -> bytes:
        """QCK^Q02 dengan QAK NF — tanpa konteks pesan asli."""
        return self.format_query_not_found_full(instrument, {})

    def format_query_not_found_full(self, instrument: dict,
                                    query_msh: dict) -> bytes:
        """
        QCK^Q02 dengan QAK NF. Manual: bila sampel tidak ada di LIS, DSR tidak
        dikirim sama sekali dan alat tidak membalas apa pun.
        """
        self._logger.info("Order tidak ditemukan — kirim QCK^Q02 QAK NF")
        return self._builder.build_qck_q02(query_msh or {}, found=False)

    # ============================================================
    # Broadcast — DSR^Q03 tanpa query
    # ============================================================

    def format_order(self, order: dict, instrument: dict) -> bytes:
        """
        Bangun DSR^Q03 untuk didorong ke alat (broadcast mode).

        Manual hanya mendefinisikan download order yang dipicu QRY^Q02 dari
        alat, jadi pesan ini out-of-spec: sebagian firmware mengabaikannya dan
        BroadcastWorker akan menandai order `failed` karena ACK tidak datang.
        Mode `query` adalah jalur yang didukung resmi.
        """
        self._logger.warning(
            f"Broadcast DSR^Q03 untuk order {order.get('order_id', '?')} — "
            f"manual Mindray tidak mendefinisikan order tanpa QRY; "
            f"gunakan bidir_mode=query bila alat menolak"
        )
        return self._builder.build_dsr_q03(order, {})

    # ============================================================
    # handle_ack() — ACK^R01 / ACK^Q03 dari alat
    # ============================================================

    def handle_ack(self, raw_bytes: bytes) -> str:
        """
        Identifikasi balasan alat via segment MSA.

        MSA-1: AA = accepted, AE = error, AR = rejected. MSA-6 berisi status
        code (0 = sukses, 1xx = error, 2xx = reject) — dicatat untuk diagnosis.
        """
        if not raw_bytes:
            return "UNKNOWN"

        try:
            segments = self._parser.split_message(self._parser.unwrap_mllp(raw_bytes))
            msa_fields = self._parser.find_segment(segments, SEG_MSA)
            if msa_fields is None:
                self._logger.warning(f"Balasan tanpa MSA: {raw_bytes[:32].hex()}")
                return "UNKNOWN"

            msa = self._parser.parse_msa(msa_fields)
            code = msa["ack_code"]

            if code == ACK_AA:
                self._logger.info(f"ACK (AA) diterima untuk control_id={msa['control_id']}")
                return "ACK"

            if code in (ACK_AE, ACK_AR):
                self._logger.warning(
                    f"NAK ({code}) diterima: {msa['text_message']} "
                    f"[status={msa['error_condition']}]"
                )
                return "NAK"

            self._logger.warning(f"Ack code tidak dikenali: {code!r}")

        except Exception as e:
            self._logger.warning(f"Gagal parse ACK: {e}")

        return "UNKNOWN"


# ============================================================
# Unit Test
# ============================================================

if __name__ == "__main__":
    print("=== Test MindrayBS200EModule ===\n")
    mod = MindrayBS200EModule()
    instrument = {"id": 7, "name": "Mindray BS-200E"}

    # --- Identitas ---
    assert mod.PROTOCOL_NAME == "HL7_MINDRAY_BS200E"
    assert mod.VERSION == "1.0.0"
    print(f"OK: {mod.PROTOCOL_NAME} v{mod.VERSION}")

    # --- parse(): hasil sampel, contoh persis dari manual bab 3 ---
    oru = (
        b"\x0b"
        b"MSH|^~\\&|Mindray|BS-200E|||20070719145353||ORU^R01|1|P|2.3.1||||0||ASCII|||\r"
        b"PID|1|854||12|Tommy||19830719145307|F|A||||||||||||||||||||||\r"
        b"OBR|1|0000000002|2|Mindray^BS-200E|Y||20070719145300||||||diabetes||serum|"
        b"Dr Ratna||hemolysis||Dr Bagus|Interna||||||||||||||||||||||||\r"
        b"OBX|1|NM|2|test2|5.000000|g/ml|-||||F|||||||\r"
        b"\x1c\x0d"
    )
    parsed = mod.parse(oru, instrument)

    assert parsed["instrument_id"] == 7
    assert parsed["protocol"] == "HL7_MINDRAY_BS200E"
    assert parsed["message_datetime"] == "20070719145353"
    assert parsed["patient"]["patient_id"] == "854"
    assert parsed["patient"]["name"] == "Tommy"
    assert parsed["patient"]["dob"] == "19830719145307"
    assert parsed["patient"]["gender"] == "F"
    assert parsed["patient"]["physician"] == "Dr Bagus", parsed["patient"]["physician"]
    assert parsed["specimen"]["sample_id"] == "0000000002"
    assert parsed["specimen"]["sample_type"] == "serum"
    assert parsed["specimen"]["collected_at"] == "20070719145300"
    assert parsed["order"]["order_id"] == "0000000002"
    assert len(parsed["results"]) == 1
    assert parsed["results"][0]["test_code"] == "2"
    assert parsed["results"][0]["test_name"] == "test2"
    assert parsed["results"][0]["value"] == "5.000000"
    assert parsed["results"][0]["unit"] == "g/ml"
    assert parsed["results"][0]["reference_range"] == ""
    assert parsed["results"][0]["status"] == "F"
    assert parsed["parse_errors"] == [], parsed["parse_errors"]
    assert "blood_type: A" in parsed["comments"]
    assert "clinical_info: diabetes" in parsed["comments"]
    assert "sample_characteristic: hemolysis" in parsed["comments"]
    print("OK: parse() — hasil sampel lengkap")

    # --- parse(): beberapa OBX dalam satu pesan (varian firmware) ---
    oru_multi = (
        b"\x0b"
        b"MSH|^~\\&|Mindray|BS-200E|||20260716101500||ORU^R01|9|P|2.3.1||||0||ASCII|||\r"
        b"PID|1|1001||5|Budi||19800101000000|M|O||||||||||||||||||||||\r"
        b"OBR|1|BC-778899|3|Mindray^BS-200E|N||20260716101400||||||||serum|||||||||||||||||\r"
        b"OBX|1|NM|GLU^Glucose||5.5|mmol/L|3.9-6.1|N||||F|||||||\r"
        b"OBX|2|NM|CHOL^Cholesterol||7.2|mmol/L|0.0-5.2|H||||F|||||||\r"
        b"\x1c\x0d"
    )
    multi = mod.parse(oru_multi, instrument)
    assert len(multi["results"]) == 2
    assert multi["results"][0]["test_code"] == "GLU"
    assert multi["results"][1]["test_code"] == "CHOL"
    assert multi["results"][1]["flag"] == "H"
    assert multi["results"][1]["reference_range"] == "0.0-5.2"
    assert multi["specimen"]["sample_id"] == "BC-778899"
    print("OK: parse() — multi-OBX & varian OBX-3 code^name")

    # --- parse(): hasil QC (MSH-16 = 2), contoh manual ---
    oru_qc = (
        b"\x0b"
        b"MSH|^~\\&|Mindray|BS-200E|||20070720120202||ORU^R01|1|P|2.3.1||||2||ASCII|||\r"
        b"OBR|1|1|test1|Mindray^BS-200E||20070720120143|||||||QUAL1|1111|20080720000000||H"
        b"|5.000000|2.000000|0.11029|g/ml|||||||||||||||||||||||||||\r"
        b"\x1c\x0d"
    )
    qc = mod.parse(oru_qc, instrument)
    assert len(qc["results"]) == 1
    assert qc["results"][0]["test_code"] == "1"
    assert qc["results"][0]["test_name"] == "test1"
    assert qc["results"][0]["value"] == "0.11029"
    assert qc["results"][0]["unit"] == "g/ml"
    assert qc["results"][0]["flag"] == "H"
    assert qc["results"][0]["status"] == "qc"
    assert qc["specimen"]["sample_type"] == "qc"
    assert qc["specimen"]["collected_at"] == "20070720120143"
    assert "control_name: QUAL1" in qc["comments"]
    assert "lot_number: 1111" in qc["comments"]
    assert "mean_value: 5.000000" in qc["comments"]
    assert "standard_deviation: 2.000000" in qc["comments"]
    assert qc["parse_errors"] == []
    print("OK: parse() — hasil QC")

    # --- parse(): kalibrasi (MSH-16 = 1) ---
    oru_cal = (
        b"\x0b"
        b"MSH|^~\\&|Mindray|BS-200E|||20070720120202||ORU^R01|1|P|2.3.1||||1||ASCII|||\r"
        b"OBR|1|211|Ap211|Mindray^BS-200E|||20070720120143||1|0.98|2||Cal-A|LOT9||||||1.234|\r"
        b"\x1c\x0d"
    )
    cal = mod.parse(oru_cal, instrument)
    assert len(cal["results"]) == 1
    assert cal["results"][0]["status"] == "calibration"
    assert cal["results"][0]["test_code"] == "211"
    assert cal["results"][0]["test_name"] == "Ap211"
    assert cal["specimen"]["sample_type"] == "calibration"
    assert "calibrator_name: Cal-A" in cal["comments"]
    print("OK: parse() — hasil kalibrasi")

    # --- parse(): input rusak tidak boleh melempar exception ---
    assert mod.parse(b"", instrument)["parse_errors"] == ["Data kosong"]
    assert mod.parse(b"\x0bsampah\x1c\x0d", instrument)["parse_errors"] != []
    tanpa_obx = mod.parse(
        b"\x0bMSH|^~\\&|Mindray|BS-200E|||20260716||ORU^R01|1|P|2.3.1||||0||ASCII|||\r"
        b"PID|1|1||||||M|\rOBR|1|BC1|2|Mindray^BS-200E|\r\x1c\x0d",
        instrument,
    )
    assert "Tidak ada segment OBX (hasil kosong)" in tanpa_obx["parse_errors"]
    print("OK: parse() — input rusak/kosong ditangani lewat parse_errors")

    # --- parse(): barcode kosong → fallback OBR-3 + peringatan ---
    tanpa_barcode = mod.parse(
        b"\x0bMSH|^~\\&|Mindray|BS-200E|||20260716||ORU^R01|1|P|2.3.1||||0||ASCII|||\r"
        b"OBR|1||42|Mindray^BS-200E|\rOBX|1|NM|2|test2|5|g/ml|-||||F|\r\x1c\x0d",
        instrument,
    )
    assert tanpa_barcode["specimen"]["sample_id"] == "42"
    assert any("Barcode" in e for e in tanpa_barcode["parse_errors"])
    print("OK: parse() — fallback sample ID internal saat barcode kosong")

    # --- build_ack_response() ---
    # MSH-7 memakai jam MidLab, jadi hanya bagian di luar timestamp yang dicek.
    ack = mod.build_ack_response(oru, instrument)
    assert ack.startswith(b"\x0bMSH|^~\\&|||Mindray|BS-200E|")
    assert b"||ACK^R01|1|P|2.3.1||||0||ASCII|||" in ack
    assert b"MSA|AA|1|Message accepted|||0|" in ack
    assert ack.endswith(b"\x1c\x0d")
    print("OK: build_ack_response() — ACK^R01 sesuai manual")

    # ACK memantulkan control ID & tipe hasil pesan yang dibalas
    ack_for_qc = mod.build_ack_response(oru_qc, instrument)
    assert b"||ACK^R01|1|P|2.3.1||||2||ASCII|||" in ack_for_qc
    print("OK: build_ack_response() — MSH-16 QC dipantulkan")

    assert mod.build_ack_response(b"", instrument) == b""
    assert mod.build_ack_response(b"\x0bbukan hl7\x1c\x0d", instrument) == b""
    print("OK: build_ack_response() — pesan tanpa MSH tidak di-ACK")

    # --- is_enq() ---
    qry = (
        b"\x0b"
        b"MSH|^~\\&|Mindray|BS-200E|||20070723170707||QRY^Q02|5|P|2.3.1||||||ASCII|||\r"
        b"QRD|20070723170707|R|D|1|||RD|34567743|OTH|||T|\r"
        b"QRF|BS-200E|20070723000000|20070723170749|||RCT|COR|ALL||\r"
        b"\x1c\x0d"
    )
    assert mod.is_enq(qry) is True
    assert mod.is_enq(oru) is False, "ORU bukan query"
    assert mod.is_enq(oru_qc) is False
    assert mod.is_enq(b"") is False
    assert mod.is_enq(b"sampah") is False
    print("OK: is_enq() — hanya QRY^Q02 yang memicu query handler")

    # --- handle_enq() ---
    enq = mod.handle_enq(qry, instrument)
    assert enq["type"] == "query"
    assert enq["sample_id"] == "34567743"
    assert enq["raw_query"] == qry.hex()
    assert enq["_msh"]["control_id"] == "5"
    assert enq["_msh"]["qrd_raw"] == "QRD|20070723170707|R|D|1|||RD|34567743|OTH|||T|"
    print("OK: handle_enq() — barcode + konteks query")

    qry_group = qry.replace(b"|34567743|OTH|", b"||OTH|")
    assert mod.handle_enq(qry_group, instrument)["type"] == "group_query"
    qry_cancel = qry.replace(b"|34567743|OTH|", b"||CAN|")
    assert mod.handle_enq(qry_cancel, instrument)["type"] == "cancel"
    print("OK: handle_enq() — group query & cancel dikenali")

    # --- format_query_response_full() ---
    order = {
        "order_id": "ORD-1",
        "request_datetime": "2026-07-16T10:00:00",
        "patient": {"patient_id": "123", "name": "Tom", "dob": "19620824", "gender": "M"},
        "specimen": {"sample_id": "34567743", "sample_type": "Urine", "priority": "S"},
        "tests": [{"test_code": "1", "test_name": "GLU"}, {"test_code": "3", "test_name": "UA"}],
    }
    resp = mod.format_query_response_full(order, instrument, enq["_msh"])
    # Satu payload = dua pesan MLLP: QCK lalu DSR
    assert resp.count(b"\x0b") == 2 and resp.count(b"\x1c\x0d") == 2
    assert b"QCK^Q02|5|" in resp
    assert b"QAK|SR|OK|" in resp
    assert b"DSR^Q03|5|" in resp
    assert b"DSP|21||34567743|||" in resp
    assert b"DSP|24||Y|||" in resp             # priority S → STAT
    assert b"DSP|26||urine|||" in resp
    assert b"DSP|29||1^GLU^^|||" in resp
    assert b"DSP|30||3^UA^^|||" in resp
    assert b"DSC||" in resp
    assert resp.index(b"QCK^Q02") < resp.index(b"DSR^Q03"), "QCK harus mendahului DSR"
    print("OK: format_query_response_full() — QCK^Q02 + DSR^Q03 dalam satu payload")

    # Tanpa konteks: tetap valid, control ID dibangun sendiri
    resp_plain = mod.format_query_response(order, instrument)
    assert b"QCK^Q02" in resp_plain and b"DSR^Q03" in resp_plain
    print("OK: format_query_response() — tanpa konteks query tetap valid")

    # --- format_query_not_found_full() ---
    nf = mod.format_query_not_found_full(instrument, enq["_msh"])
    assert b"QCK^Q02|5|" in nf
    assert b"QAK|SR|NF|" in nf
    assert b"DSR^Q03" not in nf, "manual: DSR tidak dikirim bila sampel tidak ada"
    assert b"MSA|AA|5|" in nf
    print("OK: format_query_not_found_full() — QCK NF tanpa DSR")

    assert b"QAK|SR|NF|" in mod.format_query_not_found(instrument)
    print("OK: format_query_not_found() — tanpa konteks tetap valid")

    # --- format_order() (broadcast) ---
    bc = mod.format_order(order, instrument)
    assert bc.startswith(b"\x0b") and bc.endswith(b"\x1c\x0d")
    assert b"DSR^Q03" in bc
    assert b"QCK^Q02" not in bc, "broadcast tidak didahului query, jadi tanpa QCK"
    assert b"DSP|21||34567743|||" in bc
    assert b"DSP|29||1^GLU^^|||" in bc
    print("OK: format_order() — DSR^Q03 broadcast")

    # --- handle_ack() ---
    ack_q03 = (
        b"\x0bMSH|^~\\&|Mindray|BS-200E|||20070723170707||ACK^Q03|1|P|2.3.1||||||ASCII|||\r"
        b"MSA|AA|1|Message accepted|||0|\r"
        b"ERR|0|\r\x1c\x0d"
    )
    assert mod.handle_ack(ack_q03) == "ACK"

    nak_ae = ack_q03.replace(b"MSA|AA|1|Message accepted|||0|",
                             b"MSA|AE|1|Required field missing|||101|")
    assert mod.handle_ack(nak_ae) == "NAK"

    nak_ar = ack_q03.replace(b"MSA|AA|1|Message accepted|||0|",
                             b"MSA|AR|1|Application record locked|||206|")
    assert mod.handle_ack(nak_ar) == "NAK"

    assert mod.handle_ack(b"") == "UNKNOWN"
    assert mod.handle_ack(b"sampah") == "UNKNOWN"
    assert mod.handle_ack(oru) == "UNKNOWN", "ORU tanpa MSA bukan ACK"
    print("OK: handle_ack() — AA/AE/AR + input tak dikenal")

    # --- Round-trip: DSR yang dibangun bisa dibaca kembali ---
    from protocols.mindray_bs200e.parser import MindrayParser
    rt = MindrayParser()
    dsr_only = resp[resp.index(b"\x0b", 1):]
    rt_segments = rt.split_message(rt.unwrap_mllp(dsr_only))
    rt_msh = rt.parse_msh(rt.find_segment(rt_segments, "MSH"))
    assert rt_msh["message_type"] == "DSR^Q03"
    assert rt_msh["control_id"] == "5"
    dsp_count = sum(1 for s in rt_segments if rt.segment_type(s) == "DSP")
    assert dsp_count == 30, f"28 fixed + 2 tes, dapat {dsp_count}"
    print("OK: round-trip build → parse DSR^Q03")

    print("\n=== Semua test MindrayBS200EModule PASSED ===")
