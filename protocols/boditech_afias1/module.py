"""
protocols/boditech_afias1/module.py — Protocol Module Boditech AFIAS-1

Driver **best-effort** untuk immunoassay analyzer POCT Boditech AFIAS-1, mode
LIS "HL7" (HL7 v2.6, OUL^R24), disusun dari satu-satunya contoh pesan di
slide 5 "AFIAS-1 LIS user manual" (Boditech, Rev.00 2025.06).

## Alur (unidirectional)

    alat  → OUL^R24^OUL_R24   MSH + PID + OBR + ORC + SPM + OBX
    MidLab→ ACK^R24^ACK       MSA|AA|<MSH-10>

## Koneksi

Driver tidak bergantung arah koneksi — keduanya didukung:

- connection='server' — mode Network alat: alat diisi Server IP + Port dan
  alat yang konek ke MidLab
- connection='client' — mis. lewat converter RS232-to-LAN yang diset sebagai
  TCP server: MidLab yang konek ke IP:port converter

## Belum diverifikasi (wajib dicek saat pemasangan pertama)

1. Framing MLLP atau polos — data tanpa <VT> memicu WARNING di receiver
2. Apakah alat menunggu ACK
3. Letak barcode sampel (dipakai PID-3, cadangan PID-2)
4. Format QC — semua pesan diperlakukan sebagai hasil pasien
5. Arti OBR-3, ORC-18, OBX-11 — disimpan sebagai komentar

Di-load dynamic via protocols.base.load_module("HL7_BODITECH_AFIAS-1").
"""

from lib.utils import get_logger
from lib.models import (
    ResultObject, PatientInfo, SpecimenInfo, OrderInfo, TestResult,
)
from protocols.base import BaseProtocolModule
from protocols.boditech_afias1.constants import (
    PROTOCOL_NAME, PROTOCOL_VERSION,
    SEG_MSH, SEG_PID, SEG_OBR, SEG_OBX, SEG_ORC, SEG_SPM, SEG_MSA,
    ACK_AA, ACK_AE, ACK_AR,
    EVENT_RESULT_PREFIX,
)
from protocols.boditech_afias1.parser import AfiasParser, field, to_iso8601
from protocols.boditech_afias1.builder import AfiasBuilder


class BoditechAfias1Module(BaseProtocolModule):
    """Protocol module Boditech AFIAS-1 — HL7 v2.6 OUL^R24 (best-effort)."""

    # Alat tidak pernah meminta order.
    ACK_EXPECTED_ON_NOT_FOUND = False

    def __init__(self, now=None):
        """
        Args:
            now: callable pengganti jam sistem (`YYYYMMDDHHMMSS`) untuk MSH-7
                 ACK — dipakai unit test.
        """
        self._parser = AfiasParser()
        self._builder = AfiasBuilder(now=now)
        self._logger = get_logger("boditech_afias1")

    @property
    def PROTOCOL_NAME(self) -> str:
        return PROTOCOL_NAME

    @property
    def VERSION(self) -> str:
        return PROTOCOL_VERSION

    # ============================================================
    # parse() — OUL^R24 → ResultObject
    # ============================================================

    def parse(self, raw_bytes: bytes, instrument: dict) -> dict:
        instrument_id = instrument.get("id", 0)
        result = ResultObject(instrument_id=instrument_id, protocol=PROTOCOL_NAME)

        if not raw_bytes:
            result.parse_errors.append("Data kosong")
            return result.to_dict()

        try:
            segments = self._parser.split_message(self._parser.unwrap_mllp(raw_bytes))
        except Exception as e:
            result.parse_errors.append(f"Gagal decode pesan: {e}")
            self._logger.error(f"Gagal decode pesan: {e}")
            return result.to_dict()

        msh_fields = self._parser.find_segment(segments, SEG_MSH)
        if msh_fields is None:
            result.parse_errors.append("Segment MSH tidak ditemukan")
            self._logger.warning("Pesan tanpa MSH — diabaikan")
            return result.to_dict()

        try:
            self._assemble(segments, msh_fields, result)
        except Exception as e:
            result.parse_errors.append(f"Parse gagal: {e}")
            self._logger.error(f"Parse gagal: {e}")

        self._logger.info(
            f"Parse selesai: sampel={result.specimen.sample_id or '-'}, "
            f"{len(result.results)} hasil, {len(result.parse_errors)} error"
        )
        return result.to_dict()

    def _assemble(self, segments: list, msh_fields: list, result: ResultObject):
        msh = self._parser.parse_msh(msh_fields)
        if not msh["message_type"].startswith(EVENT_RESULT_PREFIX):
            result.parse_errors.append(f"Message type bukan hasil: {msh['message_type'] or '-'}")

        # MSH-7 satu-satunya timestamp di pesan — dipakai juga sebagai waktu sampel.
        message_time = to_iso8601(msh["datetime"])
        if message_time:
            result.message_datetime = message_time

        comments = [
            ("model", msh["model"]),
            ("serial", msh["serial"]),
            ("processing_id", msh["processing_id"]),
        ]

        pid_fields = self._parser.find_segment(segments, SEG_PID)
        sample_id = self._parser.parse_pid(pid_fields)["sample_id"] if pid_fields else ""
        if not sample_id:
            result.parse_errors.append("ID sampel kosong (PID-2/PID-3)")
        result.patient = PatientInfo(patient_id=sample_id)
        result.specimen = SpecimenInfo(sample_id=sample_id, collected_at=message_time)

        obr_fields = self._parser.find_segment(segments, SEG_OBR)
        if obr_fields:
            obr = self._parser.parse_obr(obr_fields)
            result.order = OrderInfo(panel=obr["placer_order"])
            comments += [("obr3", obr["filler_order"]), ("operator", obr["operator"])]

        orc_fields = self._parser.find_segment(segments, SEG_ORC)
        if orc_fields:
            comments.append(("orc18", self._parser.parse_orc(orc_fields)["orc18"]))

        spm_fields = self._parser.find_segment(segments, SEG_SPM)
        if spm_fields:
            spm = self._parser.parse_spm(spm_fields)
            comments += [
                ("cartridge_lot", spm["cartridge_lot"]),
                ("cartridge_expiry", to_iso8601(spm["cartridge_expiry"])),
            ]

        obx_list = self._parser.find_all(segments, SEG_OBX)
        if not obx_list:
            result.parse_errors.append("Tidak ada OBX (hasil kosong)")
        for obx_fields in obx_list:
            obx = self._parser.parse_obx(obx_fields)
            result.results.append(TestResult(
                test_code=obx["test_code"],
                test_name=obx["test_name"],
                value=obx["value"],
                unit=obx["unit"],
                reference_range=obx["reference_range"],
                flag=obx["flag"],
                status=obx["status"],
            ))

        result.comments.extend(f"{label}: {value}" for label, value in comments if value)

    # ============================================================
    # Filter & ACK — dipanggil ResultReceiver
    # ============================================================

    def should_store_result(self, result_dict: dict, raw_bytes: bytes) -> bool:
        """Hanya OUL^R24 yang layak masuk tbl_result."""
        message_type = self._parser.get_message_type(raw_bytes)
        if message_type.startswith(EVENT_RESULT_PREFIX):
            return True
        self._logger.info(f"Pesan {message_type or '(tak dikenal)'} tidak disimpan")
        return False

    def build_ack_response(self, raw_message: bytes, instrument: dict) -> bytes:
        """
        ACK^R24^ACK atas pesan alat. Dokumen tidak menyebut apakah alat
        menunggu ACK; bila tidak, balasan ini tidak berbahaya.
        """
        try:
            segments = self._parser.split_message(self._parser.unwrap_mllp(raw_message))
            msh_fields = self._parser.find_segment(segments, SEG_MSH)
        except Exception as e:
            self._logger.error(f"Gagal baca pesan untuk ACK: {e}")
            return b""

        if msh_fields is None:
            self._logger.error("Pesan tanpa MSH — ACK tidak dikirim")
            return b""

        context = self._parser.parse_msh(msh_fields)
        if not context.get("control_id"):
            self._logger.warning("MSH-10 kosong — ACK dikirim tanpa control ID")
        return self._builder.build_ack_r24(context)

    # ============================================================
    # Bidirectional — tidak didukung alat ini
    # ============================================================

    def format_order(self, order: dict, instrument: dict) -> bytes:
        self._logger.warning(
            f"AFIAS-1 tidak menerima order dari LIS — order "
            f"{(order or {}).get('order_id', '?')} tidak dikirim"
        )
        return b""

    def is_enq(self, raw_bytes: bytes) -> bool:
        return False

    def handle_enq(self, raw_bytes: bytes, instrument: dict) -> dict:
        return {"type": "unsupported", "sample_id": "", "patient_id": "",
                "raw_query": (raw_bytes or b"").hex()}

    def format_query_response(self, order: dict, instrument: dict) -> bytes:
        self._logger.warning("AFIAS-1 tidak mengirim query — tidak ada response")
        return b""

    def format_query_not_found(self, instrument: dict) -> bytes:
        self._logger.warning("AFIAS-1 tidak mengirim query — tidak ada response")
        return b""

    def handle_ack(self, raw_bytes: bytes) -> str:
        """Baca MSA-1: AA → ACK, AE/AR → NAK, selain itu UNKNOWN."""
        if not raw_bytes:
            return "UNKNOWN"
        try:
            segments = self._parser.split_message(self._parser.unwrap_mllp(raw_bytes))
        except Exception as e:
            self._logger.warning(f"Gagal decode ACK: {e}")
            return "UNKNOWN"

        msa_fields = self._parser.find_segment(segments, SEG_MSA)
        if msa_fields is None:
            return "UNKNOWN"

        code = field(msa_fields, 1).upper()
        if code == ACK_AA:
            return "ACK"
        if code in (ACK_AE, ACK_AR):
            self._logger.warning(f"Alat membalas {code}")
            return "NAK"
        return "UNKNOWN"
