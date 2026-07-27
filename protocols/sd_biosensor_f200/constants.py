"""
protocols/sd_biosensor_f200/constants.py — Konstanta IHE PCD-01 SD Biosensor STANDARD F

Berlaku untuk lini STANDARD F (FLine) SD Biosensor — F200 dan F2400. Leaflet
resmi pabrikan mencantumkan baris "LIS/HIS connectivity" yang identik untuk
kedua model (`HL7 v2.6(PCD-01)` + `POCT1-A`), dan SD Biosensor menerbitkan satu
dokumen integrasi untuk seluruh "FLine analyzers". Log yang dipakai menyusun
modul ini berasal dari **F2400** (2026-07-13); alat mengidentifikasi dirinya
`Kind=FLine1` pada segment NTE.

Berbeda jauh dari keluarga Mindray di repo ini:

    Mindray BC-5150            SD Biosensor STANDARD F
    HL7 v2.3.1                 HL7 v2.6, profil IHE PCD-01
    MSH-9 = ORU^R01            MSH-9 = ORU^R01^ORU_R01
    control ID angka (389)     control ID GUID dalam kurung kurawal
    OBX-4 kosong               OBX-4 = containment tree (`1.0.0.7`)
    range `4.00-10.00`         range `[4.00;15.00]`
    waktu tanpa offset         waktu ber-offset zona (`...-0500`)
    charset ASCII/UNICODE      `UNICODE UTF-8`

Konstanta bertanda "(log)" diambil persis dari byte yang terekam.
"""

from protocols.hl7.constants import (  # noqa: F401 — re-export untuk parser/builder
    MLLP_START, MLLP_END, MLLP_CR,
    MLLP_START_BYTE, MLLP_END_BYTE, MLLP_TRAILER,
    FIELD_SEPARATOR, COMPONENT_SEP, REPEAT_SEP, ESCAPE_CHAR, SUBCOMPONENT_SEP,
    ENCODING_CHARACTERS, SEGMENT_TERMINATOR,
    SEG_MSH, SEG_PID, SEG_PV1, SEG_OBR, SEG_OBX, SEG_NTE, SEG_MSA,
    ACK_AA, ACK_AE, ACK_AR,
    PROC_PRODUCTION,
)


# ============================================================
# Identitas module
# ============================================================

PROTOCOL_NAME = "HL7_SD_BIOSENSOR_F200"
PROTOCOL_VERSION = "1.0.0"

DEVICE_HL7_VERSION = "2.6"          # MSH-12 pesan alat (log)
MESSAGE_ENCODING = "utf-8"          # MSH-18 = "UNICODE UTF-8" (log)

# Profil IHE yang dideklarasikan alat di MSH-21 (log)
IHE_PROFILE_ID = "IHE_PCD_ORU_R01"
IHE_PROFILE_OID = "1.3.6.1.4.1.19376.1.6.4.1"


# ============================================================
# Message Types (MSH-9)
# ============================================================

EVENT_ORU_R01 = "ORU^R01^ORU_R01"   # Alat → LIS: hasil (log)
EVENT_ACK_R01 = "ACK^R01"           # LIS → Alat: balasan (log)

# MSH-9 dibandingkan setelah dinormalkan ke dua komponen pertama, karena
# sebagian firmware mengirim `ORU^R01` tanpa komponen struktur ketiga.
RESULT_EVENT_PREFIX = "ORU^R01"


# ============================================================
# MSH-15 / MSH-16 — kebijakan acknowledgment alat (log)
#
# AL = Always: alat SELALU menunggu accept-ACK. Tanpa balasan, alat
#      menganggap pengiriman gagal.
# NE = Never: alat tidak menunggu application-ACK terpisah.
# ============================================================

ACCEPT_ACK_ALWAYS = "AL"
APP_ACK_NEVER = "NE"


# ============================================================
# MSH-3 / MSH-4 pada balasan LIS → alat
#
# Nilai ini persis seperti driver lama di log, termasuk versi HL7 2.3.1 pada
# balasan meskipun alat berbicara 2.6 — alat menerimanya tanpa keluhan dan
# terus mengirim hasil berikutnya. Dipertahankan supaya capture baru bisa
# dibandingkan langsung dengan log lama. Lihat catatan di builder.
# ============================================================

ACK_SENDING_APP = "Factory"                    # MSH-3 (log)
ACK_SENDING_FACILITY = "Chemistry Analyzer"    # MSH-4 (log)
ACK_HL7_VERSION = "2.3.1"                      # MSH-12 balasan (log)
ACK_CHARACTER_SET = "UNICODE"                  # MSH-18 balasan (log)

STATUS_CODE_OK = "0"                           # MSA-6 (log)


# ============================================================
# Posisi field — diverifikasi dari log
# ============================================================

# MSH
MSH_FIELD_DEVICE_ID = 3        # `FA24E01AA0173^70b3d57372500457^EUI-64`
MSH_FIELD_DATETIME = 7
MSH_FIELD_MESSAGE_TYPE = 9
MSH_FIELD_CONTROL_ID = 10      # GUID dalam kurung kurawal
MSH_FIELD_VERSION = 12
MSH_FIELD_ACCEPT_ACK = 15
MSH_FIELD_APP_ACK = 16
MSH_FIELD_CHARSET = 18
MSH_FIELD_PROFILE = 21

# PID — alat tidak mengisi nama; PID-3 berisi nomor sampel (YYMMDD + urut)
PID_FIELD_ID = 3
PID_FIELD_NAME = 5

# OBR
OBR_FIELD_PLACER_ORDER = 2     # GUID^deviceId^EUI-64^GUID
OBR_FIELD_FILLER_ORDER = 3     # sama dengan OBR-2 pada log
OBR_FIELD_SERVICE_ID = 4       # `4548-4^Hemoglobin A1c^LN`
OBR_FIELD_OBSERVATION_TIME = 7
OBR_FIELD_OBSERVATION_END = 8

# OBX
OBX_FIELD_VALUE_TYPE = 2
OBX_FIELD_IDENTIFIER = 3       # `4548-4^Hemoglobin A1c^LN`
OBX_FIELD_SUB_ID = 4           # containment tree PCD-01, mis. `1.0.0.7`
OBX_FIELD_VALUE = 5
OBX_FIELD_UNITS = 6            # `%^Percent^NGSP`
OBX_FIELD_REFERENCE_RANGE = 7  # `[4.00;15.00]`
OBX_FIELD_ABNORMAL_FLAGS = 8
OBX_FIELD_STATUS = 11
OBX_FIELD_OBSERVATION_TIME = 14
OBX_FIELD_OBSERVER = 16        # `admin`
OBX_FIELD_ANALYSIS_TIME = 19

# NTE
NTE_FIELD_COMMENT = 3


# ============================================================
# NTE — dua jenis, dibedakan menurut segment induknya
#
#   setelah OBR : `Device Information,Date of manufacture=...,LotNo=...,
#                  SerialNo=...,Kind=FLine1`
#   setelah OBX : `eAG = 134.11 mg/dL, IFCC = 45.36 %`
# ============================================================

NTE_DEVICE_INFO_PREFIX = "Device Information"

# Kunci yang dikenali di NTE device information (log)
DEVICE_INFO_KEYS = {
    "Date of manufacture": "manufactured",
    "LotNo": "lot_no",
    "SerialNo": "serial_no",
    "Kind": "device_kind",
}

# Nilai turunan pada NTE setelah OBX. Alat menghitungnya dari HbA1c NGSP:
#   eAG  (mg/dL)     = 28.7 x A1c - 46.7
#   IFCC (mmol/mol)  = (A1c - 2.15) x 10.929
#
# ⚠️ Alat menuliskan satuan IFCC sebagai `%` padahal nilainya mmol/mol
# (contoh log: A1c 6.3% → IFCC 45.36, yang benar 45.36 mmol/mol). Driver
# meneruskan satuan APA ADANYA — mengoreksi diam-diam berbahaya, dan
# pemetaan satuan adalah keputusan lab, bukan driver.
DERIVED_VALUE_STATUS = "F"


# ============================================================
# Reference range
#
# Alat memakai `[low;high]`, bukan `low-high` seperti HL7 umumnya.
# ============================================================

RANGE_OPEN = "["
RANGE_CLOSE = "]"
RANGE_SEPARATOR = ";"

EMPTY_REFERENCE_RANGE = {"", "-", "--", "[]"}


# ============================================================
# Timeout (detik)
# ============================================================

TIMEOUT_ACK_RESPONSE = 15
