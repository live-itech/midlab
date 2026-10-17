"""
protocols/mindray_bs240pro/module.py — Protocol Module Mindray BS-240Pro / BS-360E / BS-240E

Implementasi BaseProtocolModule untuk chemistry analyzer Mindray BS-240Pro,
BS-360E, dan BS-240E (HL7 v2.3.1 + MLLP), sesuai "BS-360E & BS-240Pro &
BS-240E LIS Interface Manual V1.0" bab 1.

Turunan driver BS-200E — MLLP, hasil pasien, query (QRY^Q02 → QCK^Q02 +
DSR^Q03), group download, dan ACK dipakai ulang. Yang berbeda:

- QC: OBR-2 sudah kode tes, satu pesan bisa membawa beberapa kontrol
  (V1^V2^…), level diambil dari nama kontrol lebih dulu
- broadcast resmi: DSR^Q03 dengan MSH-15 = P
- model default BS-240Pro di MSH-6/QRF-1, DSP-26 huruf awal kapital

Di-load dynamic via protocols.base.load_module("HL7_MINDRAY_BS240PRO_360E_240E").
"""

import copy

from lib.models import SpecimenInfo, TestResult
from lib.utils import generate_message_id, get_logger
# Induk diimpor lewat alias modul, BUKAN `from ... import MindrayBS200EModule`:
# load_module() memilih class pertama hasil dir() yang mewarisi
# BaseProtocolModule, dan "MindrayBS200EModule" < "MindrayBS240ProModule".
from protocols.mindray_bs200e import module as _bs200e
from protocols.mindray_bs240pro.builder import BS240ProBuilder
from protocols.mindray_bs240pro.constants import (
    PROTOCOL_NAME, PROTOCOL_VERSION, SEG_OBR,
    QC_LEVEL_FROM_NAME, INSTRUMENT_LEVEL_TO_NUMBER, QC_CONTROLS_KEY,
    MSH15_LIS_DOWNLOAD,
)
from protocols.mindray_bs240pro.parser import BS240ProParser


def resolve_qc_level(control_name: str, instrument_level: str) -> tuple:
    """
    Tentukan level kontrol QC.

    Nama kontrol ("CTRL L1", "Level 2", "LV3") didahulukan karena di lapangan
    OBR-17 tidak bisa dipercaya (selalu "M"). Tanpa pola di nama, OBR-17
    dipakai apa adanya (L/M/H → dipetakan ResultPusher).

    Returns:
        (level, konflik) — konflik True bila nama dan OBR-17 sama-sama ada
        tapi menunjuk level berbeda.
    """
    raw = (instrument_level or "").strip().upper()
    match = QC_LEVEL_FROM_NAME.search(control_name or "")
    if not match:
        return raw, False
    from_name = match.group(1)
    from_instrument = INSTRUMENT_LEVEL_TO_NUMBER.get(raw, raw)
    return from_name, bool(raw) and from_instrument != from_name


class MindrayBS240ProModule(_bs200e.MindrayBS200EModule):
    """Protocol module Mindray BS-240Pro / BS-360E / BS-240E."""

    def __init__(self):
        super().__init__()
        self._parser = BS240ProParser()
        self._builder = BS240ProBuilder()
        self._logger = get_logger("mindray_bs240pro")

    @property
    def PROTOCOL_NAME(self) -> str:
        return PROTOCOL_NAME

    @property
    def VERSION(self) -> str:
        return PROTOCOL_VERSION

    def parse(self, raw_bytes: bytes, instrument: dict) -> dict:
        """
        Seperti induk; field `protocol` diganti nama driver ini, dan untuk
        pesan QC daftar seluruh kontrol dipindah dari blok `qc` ke kunci
        `_qc_controls` (dikonsumsi split_results()).
        """
        parsed = super().parse(raw_bytes, instrument)
        parsed["protocol"] = PROTOCOL_NAME
        qc = parsed.get("qc")
        if isinstance(qc, dict) and QC_CONTROLS_KEY in qc:
            parsed[QC_CONTROLS_KEY] = qc.pop(QC_CONTROLS_KEY)
        return parsed

    def _assemble_qc(self, segments: list, result) -> dict | None:
        """
        Rakit ResultObject QC dari OBR (tanpa PID/OBX).

        ResultObject dan blok `qc` diisi kontrol pertama; seluruh kontrol
        dibawa di `qc["_qc_controls"]` agar parse() memindahkannya keluar.
        """
        obr_fields = self._parser.find_segment(segments, SEG_OBR)
        if obr_fields is None:
            result.parse_errors.append("Pesan QC tanpa segment OBR")
            return None

        qc = self._parser.parse_obr_qc(obr_fields)
        controls = qc["controls"]
        declared = qc["declared_count"]
        if declared.isdigit() and int(declared) != len(controls):
            result.parse_errors.append(
                f"OBR-11 menyebut {declared} kontrol, data berisi {len(controls)}"
            )

        # OBR-8 = selesai per tes (waktu ukur sebenarnya); OBR-6 = mulai run.
        run_at = qc["finish_datetime"] or qc["start_datetime"]
        common = [
            f"{label}: {value}"
            for label, value in (
                ("sample_type", qc["sample_type"]),
                ("instrument_flags", qc["instrument_flags"]),
            )
            if value
        ]
        entries = [self._qc_entry(qc, control, run_at, common) for control in controls]

        first = entries[0]
        result.specimen = SpecimenInfo(sample_type="qc", collected_at=run_at)
        result.results.append(TestResult(**first["result"]))
        result.comments.extend(first["comments"])

        block = dict(first["qc"])
        block[QC_CONTROLS_KEY] = entries
        return block

    def _qc_entry(self, qc: dict, control: dict, run_at: str, common: list) -> dict:
        """Satu kontrol → {result, comments, qc} untuk satu baris tbl_result."""
        code = qc["test_code"]
        level, conflict = resolve_qc_level(control["control_name"], control["level"])
        if conflict:
            self._logger.warning(
                f'QC {code} kontrol "{control["control_name"]}": level dari nama = '
                f'{level}, dari alat = {control["level"]} — pakai {level}; '
                f"periksa setting level kontrol di alat"
            )

        comments = list(common)
        for label, value in (
            ("control_no", control["control_no"]),
            ("control_name", control["control_name"]),
            ("lot_number", control["lot_number"]),
            ("expiration_date", control["expiration_date"]),
            ("mean_value", control["mean_value"]),
            ("standard_deviation", control["standard_deviation"]),
        ):
            if value:
                comments.append(f"{label}: {value}")

        return {
            "result": TestResult(
                test_code=code,
                test_name=qc["test_name"],
                value=control["value"],
                unit=qc["unit"],
                flag="",            # flag Westgard alat hanya di komentar
                status="qc",
            ).to_dict(),
            "comments": comments,
            "qc": {
                "lot_number": _bs200e._strip_lot_prefix(control["lot_number"]),
                "level": level,
                "control_name": control["control_name"],
                "expiry_date": _bs200e._hl7_date(control["expiration_date"]),
                "run_at": run_at,
                "targets": {
                    code: {
                        "mean": control["mean_value"],
                        "sd": control["standard_deviation"],
                    },
                },
            },
        }

    def split_results(self, result_dict: dict) -> list:
        """
        Hook TCPSocketService: pecah hasil parse() menjadi baris tbl_result.

        Pesan QC → satu baris per kontrol (blok `qc`, hasil, dan komentar
        masing-masing; message_id baru mulai kontrol kedua). Pesan lain →
        [result_dict] apa adanya. Kunci `_qc_controls` tidak pernah ikut
        tersimpan.
        """
        controls = result_dict.get(QC_CONTROLS_KEY)
        if not controls:
            return [result_dict]

        base = {k: v for k, v in result_dict.items() if k != QC_CONTROLS_KEY}
        rows = []
        for index, entry in enumerate(controls):
            row = copy.deepcopy(base)
            if index > 0:
                row["message_id"] = generate_message_id()
            row["results"] = [dict(entry["result"])]
            row["comments"] = list(entry["comments"])
            row["qc"] = copy.deepcopy(entry["qc"])
            rows.append(row)
        return rows

    def format_order(self, order: dict, instrument: dict) -> bytes:
        """
        DSR^Q03 untuk broadcast (MidLab mendorong order tanpa QRY dari alat).

        Resmi di manual BS-240Pro: MSH-15 = P menandai info sampel yang
        dikirim LIS atas inisiatif sendiri. Alat membalas ACK^Q03.
        """
        self._logger.info(
            f"Broadcast DSR^Q03 order {order.get('order_id', '?')} / barcode "
            f"{(order.get('specimen') or {}).get('sample_id', '?')}"
        )
        return self._builder.build_dsr_q03(order, {"accept_ack_type": MSH15_LIS_DOWNLOAD})
