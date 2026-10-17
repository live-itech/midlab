"""
protocols/mindray_bs240pro/parser.py — Parser HL7 Mindray BS-240Pro / BS-360E / BS-240E

Turunan MindrayParser (BS-200E). Yang berbeda: model default MSH dan layout
OBR pesan QC (lihat parse_obr_qc).
"""

from lib.utils import get_logger
from protocols.mindray_bs200e.parser import MindrayParser, field
from protocols.mindray_bs240pro.constants import DEFAULT_MODEL


logger = get_logger("mindray_bs240pro_parser")


class BS240ProParser(MindrayParser):
    """Parser HL7 BS-240Pro (arah alat → LIS)."""

    def parse_msh(self, fields: list) -> dict:
        """Seperti induk, tapi fallback model = BS-240Pro bila MSH-4 kosong."""
        parsed = super().parse_msh(fields)
        if not field(fields, 4):
            parsed["model"] = DEFAULT_MODEL
        return parsed
