"""
protocols/mindray_bc5150/constants.py — Konstanta HL7 Mindray BC-5150

Sumber kebenaran modul ini adalah **log komunikasi riil** alat BC-5150
(unidirectional, 2019-05-04), bukan manual vendor. Setiap konstanta di bawah
yang ditandai "(log)" diambil persis dari byte yang terekam, sehingga balasan
MidLab identik dengan driver lama yang sudah terbukti diterima alat.

BC-5150 memakai HL7 v2.3.1 di atas MLLP — sama seperti BS-200E — jadi byte
kontrol MLLP dan delimiter HL7 di-reuse dari protocols.hl7.constants. Yang
berbeda dari BS-200E:

- alur pakai ORM^O01/ORR^O02 (bukan QRY^Q02/QCK^Q02)
- MSH-18 = UNICODE (BS-200E: ASCII) → decode/encode UTF-8
- OBX-3 = `code^name^codingsystem` (BS-200E: test No. polos di OBX-3)
- OBX-8 bisa berisi dua repeat, mis. `L~A` (BS-200E: satu nilai)
- MSH-16 tidak dipakai (tidak ada tipe hasil QC/kalibrasi di jalur ini)
"""

from protocols.hl7.constants import (  # noqa: F401 — re-export untuk parser/builder
    MLLP_START, MLLP_END, MLLP_CR,
    MLLP_START_BYTE, MLLP_END_BYTE, MLLP_TRAILER,
    FIELD_SEPARATOR, COMPONENT_SEP, REPEAT_SEP, ESCAPE_CHAR, SUBCOMPONENT_SEP,
    ENCODING_CHARACTERS, SEGMENT_TERMINATOR,
    SEG_MSH, SEG_PID, SEG_PV1, SEG_OBR, SEG_OBX, SEG_MSA,
    ACK_AA, ACK_AE, ACK_AR,
    PROC_PRODUCTION,
)


# ============================================================
# Identitas module
# ============================================================

PROTOCOL_NAME = "HL7_MINDRAY_BC5150"
PROTOCOL_VERSION = "1.0.0"

HL7_VERSION = "2.3.1"       # MSH-12 (log)
CHARACTER_SET = "UNICODE"   # MSH-18 (log) — karena itu payload di-decode UTF-8

# Encoding untuk decode pesan alat & encode balasan. MSH-18 menyebut UNICODE
# dan isi log seluruhnya ASCII; UTF-8 aman untuk keduanya (ASCII adalah subset),
# sekaligus tidak merusak nama pasien non-ASCII bila suatu saat dikirim alat.
MESSAGE_ENCODING = "utf-8"


# ============================================================
# Segment tambahan — dipakai BC-5150, tidak ada di HL7 generic MidLab
# ============================================================

SEG_ORC = "ORC"   # Common Order — pembawa sample ID pada ORM^O01


# ============================================================
# Message Types (MSH-9)
# ============================================================

EVENT_ORM_O01 = "ORM^O01"   # Alat → LIS: minta order untuk satu sampel (log)
EVENT_ORR_O02 = "ORR^O02"   # LIS → Alat: balasan ORM (log)
EVENT_ORU_R01 = "ORU^R01"   # Alat → LIS: hasil sampel (log)
EVENT_ACK_R01 = "ACK^R01"   # LIS → Alat: balasan ORU (log)

# Message type yang memicu QueryHandler bila alat dipakai bidirectional.
QUERY_EVENTS = {EVENT_ORM_O01}

# Message type yang membawa hasil untuk disimpan ke tbl_result.
RESULT_EVENTS = {EVENT_ORU_R01}


# ============================================================
# MSH-3 / MSH-4 pada balasan LIS → alat
#
# Nilai ini persis seperti driver lama di log. Alat tidak memvalidasinya, tapi
# menyamakannya membuat capture baru bisa dibandingkan langsung dengan log lama.
# Catatan: "Chemistry Analyzer" memang janggal untuk alat hematologi — itu
# string bawaan driver lama, dipertahankan demi kompatibilitas byte-level.
# ============================================================

ORR_SENDING_APP = "LIS"                  # MSH-3 pada ORR^O02 (log)
ORR_SENDING_FACILITY = ""                # MSH-4 pada ORR^O02 (log: kosong)

ACK_SENDING_APP = "Factory"              # MSH-3 pada ACK^R01 (log)
ACK_SENDING_FACILITY = "Chemistry Analyzer"   # MSH-4 pada ACK^R01 (log)


# ============================================================
# MSA — acknowledgment
# ============================================================

STATUS_CODE_OK = "0"    # MSA-6 pada ACK^R01 (log)

# Balasan atas ORM^O01 saat MidLab tidak punya order untuk sampel tersebut.
# Log memakai AR (Application Reject) — alat menerimanya dan tetap lanjut
# mengirim hasil ~60 detik kemudian.
ORR_ACK_NOT_FOUND = ACK_AR
ORR_ACK_FOUND = ACK_AA


# ============================================================
# ORC — Common Order (pada ORM^O01 dari alat)
# ============================================================

ORC_FIELD_ORDER_CONTROL = 1   # "RF" (log)
ORC_FIELD_SAMPLE_ID = 3       # sample ID / barcode (log)
ORC_FIELD_ORDER_STATUS = 5    # "IP" (log)

ORC_REQUEST = "RF"            # ORC-1 dari alat (log)
ORC_NEW_ORDER = "NW"          # ORC-1 saat MidLab mengirim order
ORC_STATUS_IN_PROCESS = "IP"  # ORC-5 (log)


# ============================================================
# OBR — Observation Request (pada ORU^R01)
# ============================================================

OBR_FIELD_SAMPLE_ID = 3           # sample ID (log)
OBR_FIELD_SERVICE_ID = 4          # `00001^Automated Count^99MRC` (log)
OBR_FIELD_OBSERVATION_TIME = 7    # `20190504060725` (log)
OBR_FIELD_SERVICE_SECTION = 24    # `HM` = Hematology (log)
OBR_FIELD_INTERPRETER = 32        # `Service` (log)

DIAGNOSTIC_SERV_HEMATOLOGY = "HM"


# ============================================================
# OBX — Observation/Result
# ============================================================

VALUE_TYPE_NUMERIC = "NM"   # hasil terukur
VALUE_TYPE_CODED = "IS"     # mode run & alarm morfologi

OBSERVATION_STATUS_FINAL = "F"   # OBX-11

# Sistem koding pada komponen ke-3 OBX-3
CODING_LOINC = "LN"        # parameter standar (WBC, HGB, PLT, ...)
CODING_MINDRAY = "99MRC"   # parameter & alarm khas Mindray (PCT, PLCC, ...)

# OBX bertipe IS dengan kode di bawah adalah METADATA RUN, bukan hasil
# pemeriksaan — dipindahkan ke `comments`, tidak masuk `results`.
RUN_MODE_CODES = {
    "08001": "take_mode",    # O = Open tube
    "08002": "blood_mode",   # W = Whole blood
    "08003": "test_mode",    # CBC+DIFF
    "01002": "ref_group",    # General
}

# Nilai OBX-5 pada alarm morfologi bertipe IS
CODED_TRUE = "T"

# Reference range yang berarti "tidak ada"
EMPTY_REFERENCE_RANGE = {"", "-", "--"}


# ============================================================
# OBX-8 — abnormal flag
#
# Alat mengirim satu atau dua repeat dipisah `~`, contoh dari log:
#   `N`     in-range, tidak ada alarm
#   `A`     in-range, tapi kanal ini kena alarm/suspect
#   `L~N`   di bawah range, tanpa alarm
#   `H~A`   di atas range, dengan alarm
#
# Repeat pertama (bila ada) = indikator abnormal HL7 tabel 0078.
# Repeat terakhir = penanda suspect: N = normal, A = suspect.
# ============================================================

FLAG_LOW = "L"
FLAG_HIGH = "H"
FLAG_NORMAL = "N"
FLAG_SUSPECT = "A"

# Nilai yang dianggap indikator abnormal pada repeat pertama
ABNORMAL_INDICATORS = {"L", "H", "LL", "HH", "<", ">"}


# ============================================================
# Timeout (detik)
# ============================================================

TIMEOUT_ACK_RESPONSE = 15
