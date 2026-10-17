"""
protocols/mindray_bs240pro/parser.py — Parser HL7 Mindray BS-240Pro / BS-360E / BS-240E

Turunan MindrayParser (BS-200E). Yang berbeda: model default MSH dan layout
OBR pesan QC (lihat parse_obr_qc).
"""

from lib.utils import get_logger
from protocols.mindray_bs200e.parser import MindrayParser, field
from protocols.mindray_bs240pro.constants import COMPONENT_SEP, DEFAULT_MODEL


logger = get_logger("mindray_bs240pro_parser")


def components(value: str) -> list:
    """Pecah field V1^V2^… menjadi list komponen; field kosong → []."""
    if not value:
        return []
    return [part.strip() for part in value.split(COMPONENT_SEP)]


def _nth(parts: list, index: int) -> str:
    return parts[index] if index < len(parts) else ""


class BS240ProParser(MindrayParser):
    """Parser HL7 BS-240Pro (arah alat → LIS)."""

    def parse_msh(self, fields: list) -> dict:
        """Seperti induk, tapi fallback model = BS-240Pro bila MSH-4 kosong."""
        parsed = super().parse_msh(fields)
        if not field(fields, 4):
            parsed["model"] = DEFAULT_MODEL
        return parsed

    def parse_obr_qc(self, fields: list) -> dict:
        """
        Petakan OBR hasil QC (MSH-16 = 2) versi BS-240Pro.

        Beda dengan BS-200E: OBR-2 = kode tes (sama dengan OBX-3 pasien),
        OBR-6/8 = waktu mulai/selesai, dan field 12–15 & 17–20 berformat
        V1^V2^… — satu komponen per kontrol. Jumlah kontrol mengikuti
        komponen lot (OBR-14) karena lot wajib bagi EazyLab.
        """
        numbers = components(field(fields, 12))
        names = components(field(fields, 13))
        lots = components(field(fields, 14))
        expiries = components(field(fields, 15))
        levels = components(field(fields, 17))
        means = components(field(fields, 18))
        sds = components(field(fields, 19))
        values = components(field(fields, 20))

        controls = [
            {
                "control_no": _nth(numbers, i),
                "control_name": _nth(names, i),
                "lot_number": _nth(lots, i),
                "expiration_date": _nth(expiries, i),
                "level": _nth(levels, i),
                "mean_value": _nth(means, i),
                "standard_deviation": _nth(sds, i),
                "value": _nth(values, i),
            }
            for i in range(max(len(lots), 1))
        ]

        parsed = {
            "test_code": field(fields, 2),
            "test_name": field(fields, 3),
            "sample_type": field(fields, 5),
            "start_datetime": field(fields, 6),
            "finish_datetime": field(fields, 8),
            "declared_count": field(fields, 11),
            "unit": field(fields, 21),
            "instrument_flags": field(fields, 28),
            "controls": controls,
        }
        logger.info(
            f"OBR(QC): test={parsed['test_code']}, {len(controls)} kontrol, "
            f"lot={'/'.join(c['lot_number'] for c in controls)}"
        )
        return parsed
