"""
protocols/mindray_bs200e/constants.py — Konstanta HL7 Mindray BS-200E

Referensi: Mindray Host Interface Manual v6.0 (P/N BA20-20-75337), berlaku untuk
BS-120/130/180/190/200/220/200E/220E/330/350/330E/350E Chemistry Analyzer.

Mindray memakai HL7 v2.3.1 di atas MLLP. Byte kontrol MLLP dan delimiter HL7
identik dengan modul HL7 generic, jadi di-reuse dari protocols.hl7.constants.
Yang berbeda: versi HL7, set message type (QRY/QCK/DSR bukan QBP/RSP), dan
posisi field yang dipakai Mindray.
"""

from protocols.hl7.constants import (  # noqa: F401 — re-export untuk parser/builder
    MLLP_START, MLLP_END, MLLP_CR,
    MLLP_START_BYTE, MLLP_END_BYTE, MLLP_TRAILER,
    FIELD_SEPARATOR, COMPONENT_SEP, REPEAT_SEP, ESCAPE_CHAR, SUBCOMPONENT_SEP,
    ENCODING_CHARACTERS, SEGMENT_TERMINATOR,
    SEG_MSH, SEG_PID, SEG_OBR, SEG_OBX, SEG_MSA, SEG_QAK, SEG_ERR,
    ACK_AA, ACK_AE, ACK_AR,
    PROC_PRODUCTION,
)


# ============================================================
# Identitas module
# ============================================================

PROTOCOL_NAME = "HL7_MINDRAY_BS200E"
PROTOCOL_VERSION = "1.0.0"

HL7_VERSION = "2.3.1"       # MSH-12 — dikunci oleh manual Mindray
CHARACTER_SET = "ASCII"     # MSH-18

# MSH-5 / MSH-6 pada pesan LIS → alat. Manual menyebutnya "Manufacturer" dan
# "Model"; nilai riil diambil dari MSH-3/MSH-4 pesan alat bila tersedia,
# nilai di bawah hanya fallback.
DEFAULT_MANUFACTURER = "Mindray"
DEFAULT_MODEL = "BS-200E"


# ============================================================
# Segment tambahan — dipakai Mindray, tidak ada di HL7 generic MidLab
# ============================================================

SEG_QRD = "QRD"   # Query Definition
SEG_QRF = "QRF"   # Query Filter
SEG_DSP = "DSP"   # Display Data — satu baris info sampel
SEG_DSC = "DSC"   # Continuation Pointer


# ============================================================
# Message Types (MSH-9)
# ============================================================

EVENT_ORU_R01 = "ORU^R01"   # Alat → LIS: hasil sampel / QC / kalibrasi
EVENT_ACK_R01 = "ACK^R01"   # LIS → Alat: response ORU
EVENT_QRY_Q02 = "QRY^Q02"   # Alat → LIS: query order (per barcode / group)
EVENT_QCK_Q02 = "QCK^Q02"   # LIS → Alat: response QRY (ketemu / tidak)
EVENT_DSR_Q03 = "DSR^Q03"   # LIS → Alat: data order hasil query
EVENT_ACK_Q03 = "ACK^Q03"   # Alat → LIS: response DSR

# Message type yang memicu QueryHandler
QUERY_EVENTS = {EVENT_QRY_Q02}


# ============================================================
# MSH-16 — tipe hasil pada pesan ORU
# ============================================================

RESULT_TYPE_SAMPLE = "0"
RESULT_TYPE_CALIBRATION = "1"
RESULT_TYPE_QC = "2"


# ============================================================
# MSA — acknowledgment
# ============================================================

MSA_TEXT_ACCEPTED = "Message accepted"   # MSA-3
STATUS_CODE_OK = "0"                     # MSA-6 (0 = succeed)
ERR_CODE_OK = "0"                        # ERR-1


# ============================================================
# QAK — query acknowledgment
# ============================================================

QUERY_TAG = "SR"    # QAK-1 — "sample request information"

QAK_OK = "OK"       # Data ditemukan
QAK_NF = "NF"       # Data tidak ditemukan
QAK_AE = "AE"       # Application error
QAK_AR = "AR"       # Application reject


# ============================================================
# QRD — query definition
# ============================================================

QRD_FORMAT_CODE = "R"          # QRD-2 — record-oriented
QRD_PRIORITY = "D"             # QRD-3 — deferred
QRD_QUANTITY_LIMITED = "RD"    # QRD-7 — records
QRD_FILTER_QUERY = "OTH"       # QRD-9 — query
QRD_FILTER_CANCEL = "CAN"      # QRD-9 — batalkan group download
QRD_RESULTS_LEVEL = "T"        # QRD-12 — full results

# QRF — query filter
QRF_DATETIME_QUALIFIER = "RCT"   # QRF-6 — specimen receipt date/time
QRF_STATUS_QUALIFIER = "COR"     # QRF-7
QRF_SELECTION_QUALIFIER = "ALL"  # QRF-8


# ============================================================
# DSP — urutan baris data sampel (manual bab 2, segmen DSP)
#
# Baris 1-28 fixed; baris 29 dan seterusnya berisi satu tes per baris dengan
# format TestNo^TestName^Unit^NormalRange. Barcode (21) dan minimal satu tes
# adalah field wajib — sisanya opsional.
# ============================================================

DSP_ADMISSION_NUMBER = 1
DSP_BED_NUMBER = 2
DSP_PATIENT_NAME = 3
DSP_DATE_OF_BIRTH = 4
DSP_SEX = 5
DSP_PATIENT_ALIAS = 6
DSP_BAR_CODE = 21
DSP_SAMPLE_ID = 22
DSP_SAMPLE_TIME = 23
DSP_STAT = 24
DSP_COLLECTION_VOLUME = 25
DSP_SAMPLE_TYPE = 26
DSP_FETCH_DOCTOR = 27
DSP_FETCH_DEPARTMENT = 28

DSP_FIXED_LINE_COUNT = 28   # Baris tes mulai dari DSP set-ID 29
DSP_FIRST_TEST_LINE = 29


# ============================================================
# Nilai enumerasi
# ============================================================

STAT_YES = "Y"
STAT_NO = "N"

# Priority OrderObject yang diperlakukan sebagai STAT/cito
STAT_PRIORITY_VALUES = {"S", "STAT", "Y", "U", "URGENT", "CITO", "EMERGENCY"}

# Sample type yang dikenali alat (DSP baris 26)
SAMPLE_TYPES = {"serum", "plasma", "urine"}

# OBX-2 — value type
VALUE_TYPE_NUMERIC = "NM"
VALUE_TYPE_STRING = "ST"

OBSERVATION_STATUS_FINAL = "F"   # OBX-11

# Reference range yang berarti "tidak ada" pada output Mindray
EMPTY_REFERENCE_RANGE = {"", "-", "--"}


# ============================================================
# Timeout (detik)
# ============================================================

TIMEOUT_ACK_RESPONSE = 15
