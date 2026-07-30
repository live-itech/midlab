"""
protocols/sd_biosensor_f2400/module.py — Protocol Module SD Biosensor STANDARD F2400

Implementasi BaseProtocolModule untuk SD Biosensor STANDARD F2400 Fluorescent
Immunoassay Analyzer, memakai profil **IHE PCD-01 DEC** di atas HL7 v2.6 + MLLP.

## Asal driver ini

Disusun dari log komunikasi riil (2026-07-13) yang diambil langsung dari
**F2400** — alat inilah sumber kebenaran driver ini.

Driver sempat dinamai F200 saat pertama dibuat; nama itu keliru. Log yang
dipakai menyusunnya selalu berasal dari F2400, jadi sejak commit rename ini
nama modul mengikuti alat yang benar-benar terekam.

## Kaitan dengan model FLine lain (mis. F200)

Driver ini **tidak meng-hardcode apa pun yang model-spesifik** — identitas alat
dibaca dari MSH-3 tiap pesan dan daftar tes tidak dibatasi. Karena itu model
FLine lain kemungkinan besar ikut jalan tanpa perubahan:

- Leaflet resmi SD Biosensor mencantumkan baris "LIS/HIS connectivity" yang
  identik untuk F200 dan F2400: `HL7 v2.6(PCD-01)` + `POCT1-A`
- SD Biosensor menerbitkan satu dokumen integrasi untuk seluruh "FLine
  analyzers", bukan per model
- Alat di log mengidentifikasi dirinya `Kind=FLine1` pada NTE — penanda lini,
  bukan model
- PCD-01 adalah profil terstandar IHE; kerangka pesannya ditentukan standar

Tetap **belum diverifikasi** terhadap unit F200 fisik. Bila nanti dipasang di
F200 dan ternyata berbeda, buat driver terpisah — jangan longgarkan driver ini.

## Alur (unidirectional)

    alat  → ORU^R01^ORU_R01   PID + OBR + NTE(info alat) + OBX + NTE(turunan)
    MidLab→ ACK^R01           MSA|AA|<GUID>||||0|

Alat memasang `MSH-15 = AL`, jadi ACK wajib. `MSH-16 = NE` — tidak ada
application-ACK terpisah.

## Yang perlu diverifikasi saat dipasang di model FLine lain

1. Format `MSH-3` (serial + EUI-64) — dibaca dinamis, tidak masalah bila beda
2. `Kind=` pada NTE info alat — mungkin bukan `FLine1`
3. Menu tes: log hanya memuat HbA1c (`4548-4^Hemoglobin A1c^LN`). Parameter
   lain (CRP, PCT, TnI, D-dimer, dst.) mengikuti pola OBX yang sama, tapi
   NTE nilai turunan seperti `eAG`/`IFCC` khas HbA1c
4. Apakah model itu juga menerima ACK versi 2.3.1 — lihat catatan di builder

Di-load dynamic via protocols.base.load_module("HL7_SD_BIOSENSOR_F2400").
"""

from lib.utils import get_logger
from lib.models import (
    ResultObject, PatientInfo, SpecimenInfo, OrderInfo, TestResult,
)
from protocols.base import BaseProtocolModule
from protocols.sd_biosensor_f2400.constants import (
    PROTOCOL_NAME, PROTOCOL_VERSION,
    SEG_MSH, SEG_PID, SEG_MSA,
    ACK_AA, ACK_AE, ACK_AR,
    RESULT_EVENT_PREFIX,
    DERIVED_VALUE_STATUS,
)
from protocols.sd_biosensor_f2400.parser import (
    SDBiosensorF2400Parser, field, parse_device_info, parse_derived_values,
)
from protocols.sd_biosensor_f2400.builder import SDBiosensorF2400Builder


class SDBiosensorF2400Module(BaseProtocolModule):
    """Protocol module SD Biosensor STANDARD F2400 — IHE PCD-01 / HL7 v2.6."""

    # Alat tidak pernah meminta order (PCD-01 DEC hanya pelaporan hasil).
    ACK_EXPECTED_ON_NOT_FOUND = False

    def __init__(self):
        self._parser = SDBiosensorF2400Parser()
        self._builder = SDBiosensorF2400Builder()
        self._logger = get_logger("sd_biosensor_f2400")

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
        """Parse pesan ORU^R01^ORU_R01 dari alat menjadi ResultObject dict."""
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

        from protocols.sd_biosensor_f2400.parser import to_iso8601
        if msh.get("datetime"):
            result.message_datetime = to_iso8601(msh["datetime"])

        message_type = msh.get("message_type", "")
        if message_type and not message_type.startswith(RESULT_EVENT_PREFIX):
            result.parse_errors.append(f"Message type bukan hasil: {message_type}")
            self._logger.warning(f"parse() dipanggil untuk message type {message_type}")

        if msh.get("device_serial"):
            result.comments.append(f"device: {msh['device_serial']}")

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
        """Rakit ResultObject dari PID + grup OBR/OBX/NTE."""
        sample_id = ""

        pid_fields = self._parser.find_segment(segments, SEG_PID)
        if pid_fields:
            pid = self._parser.parse_pid(pid_fields)
            sample_id = pid["patient_id"]
            result.patient = PatientInfo(
                patient_id=pid["patient_id"],
                name=pid["name"],
            )
        else:
            result.parse_errors.append("Segment PID tidak ditemukan")

        groups = self._parser.iter_groups(segments)
        if not groups:
            result.parse_errors.append("Tidak ada segment OBR/OBX (hasil kosong)")
            return

        if len(groups) > 1:
            # Belum pernah terlihat di log, tapi PCD-01 mengizinkannya. Semua
            # hasil tetap dikumpulkan; identitas sampel diambil dari grup pertama.
            self._logger.warning(f"{len(groups)} grup OBR dalam satu pesan")
            result.comments.append(f"pesan berisi {len(groups)} grup OBR")

        first = groups[0]
        obr = first.get("obr")
        if obr:
            # PID-3 (nomor sampel) lebih berguna untuk mencocokkan hasil dengan
            # order di LIS daripada GUID di OBR-2/OBR-3, yang unik per
            # pemeriksaan dan tidak dikenal sistem lain.
            result.specimen = SpecimenInfo(
                sample_id=sample_id,
                collected_at=obr["observation_datetime"],
            )
            result.order = OrderInfo(
                order_id=sample_id,
                panel=obr["test_name"],
            )
            if obr["order_guid"]:
                result.comments.append(f"order_guid: {obr['order_guid']}")
        else:
            result.specimen = SpecimenInfo(sample_id=sample_id)
            result.order = OrderInfo(order_id=sample_id)
            result.parse_errors.append("Segment OBR tidak ditemukan")

        if not sample_id:
            result.parse_errors.append("Nomor sampel (PID-3) kosong")
            self._logger.warning("PID-3 kosong — hasil tanpa identitas sampel")

        obx_found = False

        for group in groups:
            # NTE tepat setelah OBR = informasi alat (lot, serial, kind).
            # Ini jejak traceability reagen yang biasanya diminta saat audit.
            for note in group.get("obr_notes", []):
                if note["is_device_info"]:
                    info = parse_device_info(note["comment"])
                    for key, value in info.items():
                        result.comments.append(f"{key}: {value}")
                elif note["comment"]:
                    result.comments.append(note["comment"])

            for obs in group.get("observations", []):
                obx = obs["obx"]
                obx_found = True

                result.results.append(TestResult(
                    test_code=obx["test_code"],
                    test_name=obx["test_name"],
                    value=obx["value"],
                    unit=obx["unit"],
                    reference_range=obx["reference_range"],
                    flag=obx["flag"],
                    status=obx["status"],
                ))

                # NTE setelah OBX = nilai turunan yang dihitung alat.
                # Untuk HbA1c: eAG (mg/dL) dan IFCC. Nilai ini dilaporkan
                # rutin bersama HbA1c, jadi diangkat menjadi TestResult
                # tersendiri — bukan sekadar komentar — supaya bisa dipetakan
                # LIS. Satuan diteruskan APA ADANYA; lihat catatan IFCC di
                # constants.py.
                for note in obs.get("notes", []):
                    derived = parse_derived_values(note["comment"])
                    if not derived:
                        if note["comment"]:
                            result.comments.append(note["comment"])
                        continue
                    for item in derived:
                        result.results.append(TestResult(
                            test_code=item["name"],
                            test_name=item["name"],
                            value=item["value"],
                            unit=item["unit"],
                            reference_range="",
                            flag="",
                            status=obx["status"] or DERIVED_VALUE_STATUS,
                        ))
                    # Teks aslinya tetap disimpan supaya tidak ada yang hilang
                    # bila format turunan berubah di firmware lain.
                    result.comments.append(f"derived: {note['comment']}")

        if not obx_found:
            result.parse_errors.append("Tidak ada segment OBX (hasil kosong)")

    # ============================================================
    # Hook opsional untuk ResultReceiver
    # ============================================================

    def should_store_result(self, result_dict: dict, raw_bytes: bytes) -> bool:
        """Hanya ORU^R01 yang layak masuk tbl_result."""
        message_type = self._parser.get_message_type(raw_bytes)
        if message_type.startswith(RESULT_EVENT_PREFIX):
            return True
        self._logger.info(f"Pesan {message_type or '(tak dikenal)'} tidak disimpan")
        return False

    # ============================================================
    # build_ack_response() — dipanggil ResultReceiver
    # ============================================================

    def build_ack_response(self, raw_message: bytes, instrument: dict) -> bytes:
        """
        Bangun ACK^R01 atas pesan alat.

        Wajib: alat memasang MSH-15 = AL (accept ACK always). Tanpa balasan,
        alat menganggap pengiriman gagal.
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

        if not context.get("control_id"):
            self._logger.warning("MSH-10 kosong — ACK tetap dikirim tanpa control ID")

        return self._builder.build_ack_r01(context)

    # ============================================================
    # Bidirectional — tidak didukung alat ini
    # ============================================================

    def is_enq(self, raw_bytes: bytes) -> bool:
        """
        Alat tidak pernah meminta order.

        PCD-01 DEC (Device Enterprise Communication) adalah profil **pelaporan
        hasil satu arah**: alat melapor, LIS mengakui. Pengunduhan order bukan
        bagian dari transaksi ini, dan tidak ada satu pun pesan query di log.
        """
        return False

    def handle_enq(self, raw_bytes: bytes, instrument: dict) -> dict:
        """Tidak berlaku — alat tidak mengirim query."""
        return {
            "type": "unsupported",
            "sample_id": "",
            "patient_id": "",
            "raw_query": "",
        }

    def format_query_response(self, order: dict, instrument: dict) -> bytes:
        """Tidak berlaku — lihat is_enq()."""
        self._logger.error(
            "format_query_response() dipanggil: SD Biosensor STANDARD F memakai "
            "PCD-01 DEC yang hanya melaporkan hasil; alat tidak menerima order"
        )
        return b""

    def format_query_not_found(self, instrument: dict) -> bytes:
        """Tidak berlaku — lihat is_enq()."""
        return b""

    def format_order(self, order: dict, instrument: dict) -> bytes:
        """
        Tidak berlaku — alat tidak menerima order.

        Sengaja mengembalikan bytes kosong alih-alih mengarang format: PCD-01
        DEC tidak mendefinisikan jalur order, dan mengirim pesan karangan ke
        alat klinis lebih berbahaya daripada gagal terang-terangan.
        BroadcastWorker akan menandai order `failed` — itu memang jawaban yang
        benar untuk alat ini. Pakai `mode=unidirectional`.
        """
        self._logger.error(
            f"format_order() dipanggil untuk order {order.get('order_id', '?')}: "
            f"SD Biosensor STANDARD F tidak menerima order lewat PCD-01. "
            f"Set alat ini ke mode unidirectional."
        )
        return b""

    # ============================================================
    # handle_ack()
    # ============================================================

    def handle_ack(self, raw_bytes: bytes) -> str:
        """
        Identifikasi balasan alat via segment MSA.

        Alat tidak pernah membalas MidLab pada alur PCD-01 DEC — method ini ada
        untuk memenuhi kontrak BaseProtocolModule.
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
