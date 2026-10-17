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

from lib.utils import get_logger
# Induk diimpor lewat alias modul, BUKAN `from ... import MindrayBS200EModule`:
# load_module() memilih class pertama hasil dir() yang mewarisi
# BaseProtocolModule, dan "MindrayBS200EModule" < "MindrayBS240ProModule".
from protocols.mindray_bs200e import module as _bs200e
from protocols.mindray_bs240pro.builder import BS240ProBuilder
from protocols.mindray_bs240pro.constants import PROTOCOL_NAME, PROTOCOL_VERSION
from protocols.mindray_bs240pro.parser import BS240ProParser


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
        """Seperti induk; field `protocol` diganti nama driver ini."""
        parsed = super().parse(raw_bytes, instrument)
        parsed["protocol"] = PROTOCOL_NAME
        return parsed
