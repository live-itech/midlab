"""
protocols/mindray_bs240pro/constants.py — Konstanta HL7 Mindray BS-240Pro / BS-360E / BS-240E

Referensi: "BS-360E & BS-240Pro & BS-240E LIS Interface Manual V1.0" bab 1
(HL7 v2.3.1 + MLLP). Bab HL7 manual ini sama dengan Host Interface Manual v6.0
milik BS-200E, jadi seluruh konstanta BS-200E di-re-export; di sini hanya yang
berbeda.
"""

import re

from protocols.mindray_bs200e.constants import *  # noqa: F401,F403 — re-export


# ============================================================
# Identitas module
# ============================================================

PROTOCOL_NAME = "HL7_MINDRAY_BS240PRO_360E_240E"
PROTOCOL_VERSION = "1.0.0"

# MSH-6 / QRF-1 bila alat tidak mengisi MSH-4 (log BS-360E: kosong).
DEFAULT_MODEL = "BS-240Pro"


# ============================================================
# MSH-15 — Accept Acknowledgment Type
#
# Manual: "When LIS sends sample request information to the chemistry
# analyzer, the 15 field should be set to P." Dipakai DSR^Q03 broadcast
# (tanpa QRY dari alat); balasan query membiarkannya kosong.
# ============================================================

MSH15_LIS_DOWNLOAD = "P"
MSH15_INDEX = 14    # index list hasil split("|") segmen MSH = MSH-15


# ============================================================
# QC (MSH-16 = 2) — level kontrol
#
# Di lapangan OBR-17 selalu "M" walau kontrol bernama "CTRL L1"/"CTRL L2",
# jadi level diambil dari nama kontrol lebih dulu.
# ============================================================

QC_LEVEL_FROM_NAME = re.compile(r"\b(?:L|LV|LVL|LEVEL)\s*[-_]?\s*([123])\b", re.IGNORECASE)

# OBR-17 alat → nomor level EazyLab (sama dengan _QC_LEVEL_MAP ResultPusher)
INSTRUMENT_LEVEL_TO_NUMBER = {"L": "1", "M": "2", "H": "3"}

# Kunci internal di dict hasil parse(): daftar kontrol untuk split_results().
# Tidak pernah disimpan ke tbl_result (dibuang split_results).
QC_CONTROLS_KEY = "_qc_controls"


# ============================================================
# DSP-26 — jenis sampel; format yang terbukti diterima BS-360E di log
# ============================================================

SAMPLE_TYPE_LABELS = {"serum": "Serum", "plasma": "Plasma", "urine": "Urine"}
