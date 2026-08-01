"""
protocols/mindray_bc5150/constants.py — Konstanta HL7 Mindray BC-5150

Dua sumber kebenaran, dan penting untuk membedakannya:

- "(log)"  — diambil persis dari byte log komunikasi riil alat BC-5150
             (unidirectional, 2019-05-04). Balasan MidLab dibuat identik
             dengan driver lama yang sudah terbukti diterima alat.
- "(dok)"  — dari manual vendor *BC-5000 & BC-5150 HL7 Communication
             Protocol V2.0 EN*. Dipakai untuk jalur yang tidak pernah muncul
             di log, terutama arah bidirectional (ORR^O02 berisi worklist).

Bila keduanya berbeda, log yang menang untuk jalur unidirectional — itu bentuk
yang sudah terbukti diterima alat di lapangan.

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
# Processing ID (MSH-11) — dok §4.3.1
#
# "P" = pesan hasil sampel & pencarian worklist
# "Q" = pesan hasil QC
#
# Pada balasan, MSH-11 harus mengikuti pesan yang dibalas ("In Ack messages,
# it is consistent with the previously received message") — karena itu builder
# memantulkan nilai ini, bukan selalu menulis "P".
# ============================================================

PROC_QC = "Q"


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
# ORC — Common Order
#
# Perhatikan: posisi sample ID BERBEDA antara ORM (alat → LIS) dan ORR
# (LIS → alat). Dok Tabel 4-8:
#
#   ORM^O01 dari alat : ORC|RF||<sample id>||IP     → ORC-3 = sample ID
#   ORR^O02 dari LIS  : ORC|AF|<sample id>          → ORC-2 = sample ID
#
# Menukar keduanya membuat alat tidak mengenali sampel yang dijawab.
# ============================================================

ORC_FIELD_ORDER_CONTROL = 1   # "RF" (log)
ORC_FIELD_SAMPLE_ID = 3       # ORC-3 — sample ID pada ORM dari alat (log)
ORC_FIELD_ORDER_STATUS = 5    # "IP" (log)

ORC_FIELD_PLACER_ORDER = 2    # ORC-2 — sample ID pada ORR dari LIS (dok)

ORC_REQUEST = "RF"            # ORC-1 pada ORM dari alat (log)
ORC_AFFIRM = "AF"             # ORC-1 pada ORR dari LIS — "affirm the re-filled
                              # order request" (dok Tabel 4-8)
ORC_STATUS_IN_PROCESS = "IP"  # ORC-5 pada ORM (log)


# ============================================================
# OBR — Observation Request (pada ORU^R01)
# ============================================================

OBR_FIELD_SAMPLE_ID = 3           # OBR-3 filler — sample ID pada ORU (log)
OBR_FIELD_PLACER_SAMPLE_ID = 2    # OBR-2 placer — sample ID pada ORR (dok §5.5)
OBR_FIELD_SERVICE_ID = 4          # `00001^Automated Count^99MRC` (log)
OBR_FIELD_DRAW_TIME = 6           # OBR-6 waktu pengambilan darah (dok)
OBR_FIELD_OBSERVATION_TIME = 7    # OBR-7 waktu periksa `20190504060725` (log)
OBR_FIELD_SENDER = 10             # OBR-10 pengirim sampel (dok)
OBR_FIELD_CLINICAL_INFO = 13      # OBR-13 info klinis / diagnosa (dok)
OBR_FIELD_RECEIVED_TIME = 14      # OBR-14 waktu order dibuat (dok)
OBR_FIELD_SERVICE_SECTION = 24    # `HM` = Hematology (log)
OBR_FIELD_INTERPRETER = 32        # `Service` (log)

# Jumlah field OBR pada ORR^O02 — dok §5.5 mengisi sampai OBR-32, dan contoh
# di dok mempertahankan seluruh field kosong di antaranya.
OBR_FIELD_COUNT = 32

DIAGNOSTIC_SERV_HEMATOLOGY = "HM"


# ============================================================
# OBR-4 — jenis analisis (dok §5.7 Tabel 4-10)
# ============================================================

SERVICE_AUTOMATED_COUNT = "00001^Automated Count^99MRC"   # hasil sampel (log)
SERVICE_QC_LJ = "00003^LJ QCR^99MRC"                      # hasil QC (dok)


# ============================================================
# OBX — Observation/Result
# ============================================================

VALUE_TYPE_NUMERIC = "NM"   # hasil terukur
VALUE_TYPE_CODED = "IS"     # mode run & alarm morfologi

OBSERVATION_STATUS_FINAL = "F"   # OBX-11

# Sistem koding pada komponen ke-3 OBX-3
CODING_LOINC = "LN"        # parameter standar (WBC, HGB, PLT, ...)
CODING_MINDRAY = "99MRC"   # parameter & alarm khas Mindray (PCT, PLCC, ...)

VALUE_TYPE_STRING = "ST"    # remark
VALUE_TYPE_ENCAPSULATED = "ED"   # histogram / scattergram Base64 (dok §6.2)

# OBX bertipe IS dengan kode di bawah adalah METADATA RUN, bukan hasil
# pemeriksaan — dipindahkan ke `comments`, tidak masuk `results`.
RUN_MODE_CODES = {
    "08001": "take_mode",    # O = Open tube
    "08002": "blood_mode",   # W = Whole blood
    "08003": "test_mode",    # CBC+DIFF
    "01002": "ref_group",    # General
}


# ============================================================
# OBX yang dikirim MidLab di dalam ORR^O02 (worklist) — dok §5.5 & §5.8
#
# Alat hematologi tidak menerima "daftar tes" seperti alat kimia: yang bisa
# diperintahkan lewat worklist adalah *cara* sampel dijalankan (mode darah,
# mode pemeriksaan) plus konteks pasien (umur, catatan). Itulah OBX yang
# muncul pada contoh ORR di dok §5.5.
# ============================================================

OBX_CODE_BLOOD_MODE = "08002^Blood Mode^99MRC"
OBX_CODE_TEST_MODE = "08003^Test Mode^99MRC"
OBX_CODE_AGE = "30525-0^Age^LN"
OBX_CODE_REMARK = "01001^Remark^99MRC"


# ============================================================
# Enumerasi nilai OBX (dok §6)
# ============================================================

# Blood Mode (OBX 08002)
BLOOD_MODE_WHOLE = "W"        # whole blood
BLOOD_MODE_PREDILUTE = "P"    # predilute

# Peta sample_type OrderObject → Blood Mode alat. Kunci di-lowercase.
# Istilah Bahasa Indonesia ikut dipetakan karena itulah yang dikirim EazyApp
# di lapangan ("Darah Vena"). Sampel yang tidak jelas cara jalannya — mis.
# "darah kapiler", yang bisa whole blood maupun predilute tergantung apakah
# lab mengencerkannya — sengaja tidak dipetakan: alat memakai setelan sendiri,
# lebih aman daripada memaksa mode yang salah.
BLOOD_MODE_BY_SAMPLE_TYPE = {
    "whole blood": BLOOD_MODE_WHOLE,
    "wholeblood": BLOOD_MODE_WHOLE,
    "darah": BLOOD_MODE_WHOLE,
    "darah vena": BLOOD_MODE_WHOLE,
    "darah lengkap": BLOOD_MODE_WHOLE,
    "darah edta": BLOOD_MODE_WHOLE,
    "vena": BLOOD_MODE_WHOLE,
    "venous": BLOOD_MODE_WHOLE,
    "venous blood": BLOOD_MODE_WHOLE,
    "edta": BLOOD_MODE_WHOLE,
    "w": BLOOD_MODE_WHOLE,
    "predilute": BLOOD_MODE_PREDILUTE,
    "prediluted": BLOOD_MODE_PREDILUTE,
    "predilusi": BLOOD_MODE_PREDILUTE,
    "darah predilusi": BLOOD_MODE_PREDILUTE,
    "p": BLOOD_MODE_PREDILUTE,
}

# Test Mode (OBX 08003) — dok §6 hanya mengenumerasi dua nilai ini, tapi log
# alat di lapangan mengirim "CBC+DIFF", jadi bentuk itu ikut diterima.
TEST_MODE_CBC = "CBC"
TEST_MODE_CBC_5DIFF = "CBC+5DIFF"
TEST_MODE_CBC_DIFF = "CBC+DIFF"       # (log) varian firmware

VALID_TEST_MODES = {TEST_MODE_CBC, TEST_MODE_CBC_5DIFF, TEST_MODE_CBC_DIFF}

# Satuan umur pada OBX 30525-0 (dok §5.9)
AGE_UNIT_YEAR = "yr"

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
# Escape sequence (dok §3)
#
# Delimiter yang muncul di dalam teks bebas (remark, diagnosa, nama) harus
# diganti escape sequence saat encoding dan dipulihkan saat decoding. Tanpa
# ini, satu tanda `|` pada remark akan memecah segment jadi field baru dan
# menggeser seluruh pemetaan field sesudahnya.
#
# Urutan penting: `\E\` (escape char) harus diproses lebih dulu saat encode
# dan paling akhir saat decode, kalau tidak backslash hasil substitusi ikut
# ter-escape dua kali.
# ============================================================

ESCAPE_SEQUENCES = [
    ("\\", "\\E\\"),               # ESC — harus pertama saat encode
    (FIELD_SEPARATOR, "\\F\\"),    # |
    (COMPONENT_SEP, "\\S\\"),      # ^
    (SUBCOMPONENT_SEP, "\\T\\"),   # &
    (REPEAT_SEP, "\\R\\"),         # ~
    (SEGMENT_TERMINATOR, "\\.br\\"),  # <CR>
]


# ============================================================
# Timeout (detik)
# ============================================================

TIMEOUT_ACK_RESPONSE = 15
