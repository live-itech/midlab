"""
protocols/boditech_afias1/constants.py — Konstanta HL7 Boditech AFIAS-1

Sumber: "AFIAS-1 LIS user manual" (Boditech Med, Rev.00 2025.06), slide 5 —
satu-satunya contoh pesan mode HL7:

    MSH|^~\\&||1|AFIAS-6|A60123456789|20200305171553.205+0900||OUL^R24^OUL_R24|1|T|2.6
    PID||T45|T45|||||
    OBR||T4|G||||||admin
    ORC|OK||||||||||||||||A60123456789|5
    SPM||T4PKA37F||||||||||||||||20210719
    OBX|1|TX|T4||33.26|nmol/L|||||R

Posisi field di bawah dihitung dari contoh itu. Dokumen tidak menjelaskan
artinya — driver ini best-effort dan wajib diverifikasi dengan data asli.
"""

from protocols.hl7.constants import (  # noqa: F401 — re-export
    MLLP_START_BYTE, MLLP_TRAILER,
    FIELD_SEPARATOR, COMPONENT_SEP, ENCODING_CHARACTERS, SEGMENT_TERMINATOR,
    SEG_MSH, SEG_PID, SEG_OBR, SEG_OBX, SEG_MSA,
    ACK_AA, ACK_AE, ACK_AR,
    PROC_PRODUCTION,
)


# ============================================================
# Identitas module
# ============================================================

PROTOCOL_NAME = "HL7_BODITECH_AFIAS-1"
PROTOCOL_VERSION = "1.0.0"

HL7_VERSION = "2.6"
MESSAGE_ENCODING = "utf-8"   # tidak disebut dokumen; decode dengan errors="replace"

SEG_ORC = "ORC"
SEG_SPM = "SPM"


# ============================================================
# Message type (MSH-9)
# ============================================================

EVENT_RESULT_PREFIX = "OUL^R24"   # alat → LIS: hasil
EVENT_ACK_R24 = "ACK^R24^ACK"     # LIS → alat: balasan
ACK_SENDING_APP = "MidLab"        # MSH-3 ACK


# ============================================================
# Posisi field (nomor field HL7)
# ============================================================

MSH_FIELD_MODEL = 5           # "AFIAS-6"
MSH_FIELD_SERIAL = 6          # "A60123456789"
MSH_FIELD_DATETIME = 7        # "20200305171553.205+0900"
MSH_FIELD_MESSAGE_TYPE = 9
MSH_FIELD_CONTROL_ID = 10
MSH_FIELD_PROCESSING_ID = 11  # "T" pada contoh
MSH_FIELD_VERSION = 12

PID_FIELD_PATIENT_ID = 2      # "T45"
PID_FIELD_IDENTIFIER = 3      # "T45" — didahulukan sebagai ID sampel

OBR_FIELD_PLACER_ORDER = 2    # "T4"
OBR_FIELD_FILLER_ORDER = 3    # "G" — arti tidak diketahui
OBR_FIELD_OPERATOR = 9        # "admin"

ORC_FIELD_18 = 18             # "5" — arti tidak diketahui

SPM_FIELD_CARTRIDGE_LOT = 2   # "T4PKA37F"
SPM_FIELD_CARTRIDGE_EXPIRY = 18   # "20210719"

OBX_FIELD_VALUE_TYPE = 2
OBX_FIELD_IDENTIFIER = 3
OBX_FIELD_VALUE = 5
OBX_FIELD_UNITS = 6
OBX_FIELD_REFERENCE_RANGE = 7
OBX_FIELD_ABNORMAL_FLAGS = 8
OBX_FIELD_STATUS = 11         # "R" pada contoh
