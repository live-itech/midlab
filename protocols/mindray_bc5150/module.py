"""
protocols/mindray_bc5150/module.py — Protocol Module Mindray BC-5150

Implementasi BaseProtocolModule untuk Mindray BC-5150 Auto Hematology Analyzer
(HL7 v2.3.1 + MLLP). Dua sumber: log komunikasi riil alat untuk jalur
unidirectional, dan manual vendor *BC-5000 & BC-5150 HL7 Communication
Protocol V2.0 EN* untuk jalur worklist/bidirectional.

Alur yang terekam di log — dua pesan per sampel, berjarak ~60 detik:

    alat  → ORM^O01   "punya order untuk sampel <id>?"   (ORC|RF||<id>||IP)
    MidLab→ ORR^O02   MSA|AR  — tidak ada order
    alat  → ORU^R01   PID + PV1 + OBR + 29..31 OBX (hasil CBC+DIFF)
    MidLab→ ACK^R01   MSA|AA|<control id>||||0|

Mode yang didukung:

- unidirectional — TERVERIFIKASI dari log. ORM dibalas ORR^O02/MSA|AR oleh
                   ResultReceiver (lihat ANSWERS_QUERY_IN_ACK) dan tidak
                   disimpan ke tbl_result; ORU diparse jadi ResultObject lalu
                   dibalas ACK^R01.
- query          — SESUAI DOK, belum diuji ke alat fisik. Alat selalu jadi
                   inisiator lewat ORM^O01; QueryHandler mencari order di
                   tbl_order lalu membalas ORR^O02 berisi worklist sesuai
                   dok §4.2.4 dan contoh dok §5.5. Set `bidir_mode=query`.
- broadcast      — TIDAK BERLAKU. Alat tidak menerima order yang tidak ia
                   minta; lihat format_order().

Di-load dynamic via protocols.base.load_module("HL7_MINDRAY_BC5150").
"""

from lib.utils import get_logger
from lib.models import (
    ResultObject, PatientInfo, SpecimenInfo, OrderInfo, TestResult,
)
from protocols.base import BaseProtocolModule
from protocols.mindray_bc5150.constants import (
    PROTOCOL_NAME, PROTOCOL_VERSION,
    SEG_MSH, SEG_PID, SEG_OBR, SEG_OBX, SEG_MSA,
    ACK_AA, ACK_AE, ACK_AR,
    EVENT_ORM_O01, EVENT_ORU_R01,
    RESULT_EVENTS, QUERY_EVENTS,
    DIAGNOSTIC_SERV_HEMATOLOGY,
)
from protocols.mindray_bc5150.parser import MindrayBC5150Parser, field
from protocols.mindray_bc5150.builder import MindrayBC5150Builder


class MindrayBC5150Module(BaseProtocolModule):
    """Protocol module Mindray BC-5150 — HL7 v2.3.1 di atas MLLP."""

    # Alat tidak mengirim ACK atas balasan "order tidak ada" (ia langsung
    # lanjut mengirim hasil), jadi QueryHandler tidak boleh menunggu.
    ACK_EXPECTED_ON_NOT_FOUND = False

    # build_ack_response() sudah tahu cara membalas ORM^O01 (dengan
    # ORR^O02/MSA|AR). Dengan flag ini ResultReceiver membalas sendiri saat
    # instrumen dijalankan unidirectional — tanpa QueryHandler, ORM tetap
    # terjawab persis seperti driver lama di log.
    ANSWERS_QUERY_IN_ACK = True

    def __init__(self):
        self._parser = MindrayBC5150Parser()
        self._builder = MindrayBC5150Builder()
        self._logger = get_logger("mindray_bc5150")

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

        Berbeda dari BS-200E yang mengirim satu tes per pesan, BC-5150
        mengirim **seluruh panel dalam satu ORU** (29-31 OBX untuk CBC+DIFF),
        jadi satu pesan = satu baris tbl_result yang lengkap.
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

        # ORM^O01 bukan hasil — ia permintaan order. parse() tetap dipanggil
        # ResultReceiver untuk setiap pesan MLLP, jadi kembalikan objek kosong
        # yang ditandai; should_store_result() yang mencegahnya masuk DB.
        if message_type == EVENT_ORM_O01:
            orc = self._parser.find_segment(segments, "ORC")
            sample_id = self._parser.parse_orc(orc)["sample_id"] if orc else ""
            result.specimen = SpecimenInfo(sample_id=sample_id)
            result.comments.append("order request (ORM^O01) — bukan hasil")
            self._logger.info(
                f"ORM^O01 untuk sample_id={sample_id or '-'} — tidak disimpan"
            )
            return result.to_dict()

        if message_type and message_type not in RESULT_EVENTS:
            result.parse_errors.append(f"Message type bukan hasil: {message_type}")
            self._logger.warning(f"parse() dipanggil untuk message type {message_type}")

        try:
            self._assemble_sample(segments, result)
        except Exception as e:
            result.parse_errors.append(f"Parse gagal: {e}")
            self._logger.error(f"Parse gagal: {e}")

        self._logger.info(
            f"Parse selesai: {len(result.results)} hasil, "
            f"{len(result.comments)} komentar, {len(result.parse_errors)} error"
        )
        return result.to_dict()

    def _assemble_sample(self, segments: list, result: ResultObject):
        """Rakit ResultObject dari PID + OBR + OBX pesan ORU^R01."""
        pid_fields = self._parser.find_segment(segments, SEG_PID)
        if pid_fields:
            pid = self._parser.parse_pid(pid_fields)
            result.patient = PatientInfo(
                patient_id=pid["patient_id"],
                name=pid["name"],
                dob=pid["dob"],
                gender=pid["gender"],
            )

        obr_fields = self._parser.find_segment(segments, SEG_OBR)
        if obr_fields:
            obr = self._parser.parse_obr(obr_fields)
            result.specimen = SpecimenInfo(
                sample_id=obr["sample_id"],
                sample_type=(
                    "whole blood"
                    if obr["service_section"] == DIAGNOSTIC_SERV_HEMATOLOGY
                    else ""
                ),
                # OBR-6 (waktu darah diambil) bila alat mengisinya, kalau tidak
                # jatuh ke OBR-7 (waktu periksa) seperti pesan di log.
                collected_at=obr["collected_at"],
            )
            result.order = OrderInfo(
                order_id=obr["sample_id"],
                panel=obr["panel_name"],
            )
            if not obr["sample_id"]:
                result.parse_errors.append("Sample ID (OBR-3) kosong")
                self._logger.warning("OBR-3 kosong — hasil tanpa identitas sampel")
            if obr["interpreter"]:
                result.comments.append(f"interpreter: {obr['interpreter']}")
        else:
            result.parse_errors.append("Segment OBR tidak ditemukan")

        suspect_tests = []
        obx_found = False

        for fields in segments:
            if self._parser.segment_type(fields) != SEG_OBX:
                continue
            obx_found = True
            obx = self._parser.parse_obx(fields)

            # Mode run (Take/Blood/Test Mode, Ref Group) menggambarkan cara
            # alat menjalankan sampel, bukan hasil pemeriksaan → jadi komentar.
            if obx["is_run_mode"]:
                result.comments.append(f"{obx['run_mode_label']}: {obx['value']}")
                continue

            if obx["suspect"]:
                suspect_tests.append(obx["test_name"] or obx["test_code"])

            result.results.append(TestResult(
                test_code=obx["test_code"],
                test_name=obx["test_name"],
                value=obx["value"],
                unit=obx["unit"],
                reference_range=obx["reference_range"],
                flag=obx["flag"],
                status=obx["status"],
            ))

        # Penanda suspect (repeat `A` pada OBX-8) tidak muat di TestResult.flag
        # tanpa mengubah artinya, jadi dirangkum sekali di komentar lengkap
        # dengan nama tesnya agar keterkaitannya tidak hilang.
        if suspect_tests:
            result.comments.append("suspect: " + ", ".join(suspect_tests))

        if not obx_found:
            result.parse_errors.append("Tidak ada segment OBX (hasil kosong)")

    # ============================================================
    # Hook opsional untuk ResultReceiver
    # ============================================================

    def should_store_result(self, result_dict: dict, raw_bytes: bytes) -> bool:
        """
        Hanya ORU^R01 yang layak masuk tbl_result.

        Tanpa ini, setiap ORM^O01 (satu per sampel) akan menghasilkan baris
        tbl_result kosong yang kemudian dikirim ResultSender/LisBridge ke LIS
        sebagai hasil tanpa isi.
        """
        message_type = self._parser.get_message_type(raw_bytes)
        if message_type in RESULT_EVENTS:
            return True
        self._logger.info(f"Pesan {message_type or '(tak dikenal)'} tidak disimpan")
        return False

    # ============================================================
    # build_ack_response() — dipanggil ResultReceiver untuk tiap pesan
    # ============================================================

    def build_ack_response(self, raw_message: bytes, instrument: dict) -> bytes:
        """
        Bangun balasan yang sesuai dengan jenis pesan yang baru diterima.

        Ini titik penting driver: alat mengirim dua jenis pesan lewat koneksi
        yang sama dan mengharapkan balasan yang berbeda.

            ORU^R01 → ACK^R01 dengan MSA|AA
            ORM^O01 → ORR^O02 dengan MSA|AR (tidak ada order — unidirectional)

        Membalas ACK^R01 atas ORM (atau sebaliknya) membuat alat menganggap
        balasan tidak relevan.
        """
        try:
            segments = self._parser.split_message(
                self._parser.unwrap_mllp(raw_message)
            )
            msh_fields = self._parser.find_segment(segments, SEG_MSH)
            context = self._parser.parse_msh(msh_fields) if msh_fields else {}
        except Exception as e:
            self._logger.error(f"Gagal baca pesan untuk ACK: {e}")
            return b""

        message_type = context.get("message_type", "")

        if message_type == EVENT_ORM_O01:
            return self._builder.build_orr_not_found(context)

        if message_type == EVENT_ORU_R01:
            return self._builder.build_ack_r01(context)

        self._logger.warning(
            f"Message type '{message_type}' tidak dikenal — dibalas ACK^R01"
        )
        return self._builder.build_ack_r01(context)

    # ============================================================
    # Query mode (bidirectional)
    # ============================================================

    def is_enq(self, raw_bytes: bytes) -> bool:
        """
        Deteksi trigger query dari alat: ORM^O01 (dok §4.1.2).

        BC-5150 selalu menjadi inisiator — untuk setiap sampel ia bertanya
        "punya order untuk <sample id>?" lewat ORM^O01, lalu (terlepas dari
        jawabannya) mengirim hasil sekitar 60 detik kemudian.

        True di sini hanya berarti "ini query"; yang menentukan siapa yang
        menjawabnya adalah mode instrumen. ResultReceiver menyerahkannya ke
        QueryHandler bila `bidir_mode` mengandung `query`, dan pada mode
        unidirectional membalasnya sendiri lewat build_ack_response() dengan
        ORR^O02/MSA|AR persis seperti driver lama di log.
        """
        if not raw_bytes:
            return False

        message_type = self._parser.get_message_type(raw_bytes)
        is_query = message_type in QUERY_EVENTS
        if is_query:
            self._logger.info(f"Query terdeteksi: {message_type}")
        return is_query

    def handle_enq(self, raw_bytes: bytes, instrument: dict) -> dict:
        """
        Ekstrak informasi query dari ORM^O01.

        Returns:
            Dict dengan keys: type, sample_id, patient_id, raw_query, dan
            `_msh` berisi konteks pesan alat. QueryHandler meneruskan `_msh`
            ke format_query_response_full() supaya control ID pesan alat bisa
            dipantulkan ke MSA-2 — tanpa itu alat tidak bisa mencocokkan
            balasan dengan pertanyaannya.
        """
        context = self._parser.parse_order_request(raw_bytes)
        sample_id = context.get("sample_id", "")

        self._logger.info(f"ORM^O01 diterima untuk sample_id={sample_id or '-'}")
        return {
            "type": "query",
            "sample_id": sample_id,
            "patient_id": "",
            "raw_query": raw_bytes.decode("utf-8", errors="replace"),
            "control_id": context.get("control_id", ""),
            "_msh": context,
        }

    def format_query_response(self, order: dict, instrument: dict) -> bytes:
        """
        Bangun ORR^O02 berisi order untuk sampel yang diminta alat.

        Dipakai bila konteks ORM tidak tersedia; control ID lalu dibuat sendiri.
        Utamakan format_query_response_full() yang memantulkan control ID alat.
        """
        return self._builder.build_orr_with_order(order, {})

    def format_query_response_full(self, order: dict, instrument: dict,
                                   query_msh: dict) -> bytes:
        """
        Varian format_query_response() yang memantulkan control ID pesan alat
        ke MSA-2 — dipakai QueryHandler bila konteks ORM tersedia.
        """
        return self._builder.build_orr_with_order(order, query_msh or {})

    def format_query_not_found(self, instrument: dict) -> bytes:
        """Bangun ORR^O02 dengan MSA|AR — tidak ada order untuk sampel ini."""
        return self._builder.build_orr_not_found({})

    def format_query_not_found_full(self, instrument: dict,
                                    query_msh: dict) -> bytes:
        """Varian format_query_not_found() yang memantulkan control ID alat."""
        return self._builder.build_orr_not_found(query_msh or {})

    # ============================================================
    # Broadcast
    # ============================================================

    def format_order(self, order: dict, instrument: dict) -> bytes:
        """
        Bangun pesan order tanpa didahului permintaan alat (broadcast mode).

        BC-5150 selalu menjadi inisiator: ia menanyakan order lewat ORM^O01
        untuk setiap sampel. Log menunjukkan MidLab tidak pernah mengirim apa
        pun di luar balasan (`TimerCheckData -> tidak ada yang dikirim`
        sepanjang hari). Pesan ini karena itu out-of-spec — alat kemungkinan
        besar mengabaikannya dan BroadcastWorker akan menandai order `failed`.
        Pakai `bidir_mode=query` bila alat perlu menerima order.
        """
        self._logger.warning(
            f"Broadcast ORR^O02 untuk order {order.get('order_id', '?')} — "
            f"BC-5150 selalu inisiator lewat ORM^O01; gunakan bidir_mode=query"
        )
        return self._builder.build_orr_with_order(order, {})

    # ============================================================
    # handle_ack()
    # ============================================================

    def handle_ack(self, raw_bytes: bytes) -> str:
        """
        Identifikasi balasan alat via segment MSA.

        Pada alur unidirectional alat tidak pernah membalas MidLab — method ini
        hanya terpakai bila alat dijalankan bidirectional.

        Returns:
            'ACK' (MSA|AA), 'NAK' (MSA|AE / MSA|AR), atau 'UNKNOWN'
        """
        if not raw_bytes:
            return "UNKNOWN"

        try:
            segments = self._parser.split_message(
                self._parser.unwrap_mllp(raw_bytes)
            )
        except Exception as e:
            self._logger.warning(f"Gagal decode ACK: {e}")
            return "UNKNOWN"

        msa_fields = self._parser.find_segment(segments, SEG_MSA)
        if msa_fields is None:
            return "UNKNOWN"

        ack_code = field(msa_fields, 1).upper()
        if ack_code == ACK_AA:
            return "ACK"
        if ack_code in (ACK_AE, ACK_AR):
            self._logger.warning(f"Alat membalas {ack_code}")
            return "NAK"
        return "UNKNOWN"
