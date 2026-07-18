"""
protocols/aruma_ar580/constants.py — Konstanta HL7 ARUMA AR580

Referensi: "LIS communication protocol instruction" (Genrui, 2019).

Dokumen acuan tertulis untuk Genrui KT-6610; AR580 diperlakukan sebagai rebrand
OEM-nya. Karena string MSH-3 yang dikirim alat di lapangan belum dipastikan,
parser TIDAK memvalidasi sending application — lihat parser.parse().

AR580 memakai HL7 v2.3.1 di atas MLLP. Byte kontrol MLLP dan delimiter HL7
identik dengan modul HL7 generic, jadi di-reuse dari protocols.hl7.constants.
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

PROTOCOL_NAME = "HL7_ARUMA_AR580"
PROTOCOL_VERSION = "1.0.0"

HL7_VERSION = "2.3.1"       # MSH-12 — dikunci dokumen bab 1.1
CHARACTER_SET = "UTF-8"     # MSH-18 — dokumen bab 1.4
COUNTRY_CODE = "CHA"        # MSH-17 — nilai contoh dokumen

# MSH-3/MSH-4 pada pesan ACK (LIS → alat).
LIS_SENDING_APP = "MidLab"
LIS_SENDING_FACILITY = ""

# Fallback MSH-5/MSH-6 ACK bila pesan alat tidak menyertakan MSH-3/MSH-4.
DEFAULT_INSTRUMENT_APP = "Genrui"
DEFAULT_INSTRUMENT_MODEL = "KT-6610"


# ============================================================
# Message Types (MSH-9)
# ============================================================

EVENT_ORU_R01 = "ORU^R01"   # Alat → LIS: hasil sampel
EVENT_ACK_R01 = "ACK^R01"   # LIS → Alat: response ORU

# Disebut di bab 2.2 dokumen tapi TIDAK didokumentasikan sama sekali — tidak ada
# definisi field, contoh, maupun grammar segment. Karena itu di luar lingkup
# driver ini. Lihat docs/superpowers/specs/2026-07-17-aruma-ar580-design.md.
EVENT_ORM_O01 = "ORM^O01"   # Alat → LIS: permintaan info sampel (TIDAK diimplementasi)
EVENT_ORR_O02 = "ORR^O02"   # LIS → Alat: response ORM (TIDAK diimplementasi)


# ============================================================
# Kode acknowledgment MSA-1 (dokumen tabel 12)
# ============================================================

ACK_CA = "CA"   # Original: Application Error — Enhanced: Accept acknowledgment
ACK_CE = "CE"   # Enhanced: Accept acknowledgment: Commit Error
ACK_CR = "CR"   # Enhanced: Accept acknowledgment: Commit Reject

# Hanya AA yang berarti diterima; sisanya diperlakukan sebagai penolakan.
ACK_CODES_POSITIF = frozenset({ACK_AA})
ACK_CODES_NEGATIF = frozenset({ACK_AE, ACK_AR, ACK_CA, ACK_CE, ACK_CR})


# ============================================================
# Value type OBX-2
# ============================================================

VT_NUMERIC = "NM"      # Pengukuran
VT_CODED = "IS"        # String berkode
VT_ENCAPSULATED = "ED" # Bitmap histogram/scattergram — dilewati


# ============================================================
# Observation identifier (OBX-3 komponen 2) yang BUKAN pengukuran
# ============================================================
#
# Routing memakai nama identifier, bukan value type OBX-2. Alasannya: ESR ada di
# tabel custom OBX dokumen (tabel 10) bersama metadata, padahal ia pengukuran
# sungguhan. Memfilter berdasar tipe "IS" akan salah membuang ESR.

META_BLOOD_MODE = "Blood Mode"   # whole blood / pre-dilution / peripheral whole blood
META_TEST_MODE = "Test Mode"     # CBC+DIFF / CBC
META_REF_GROUP = "Ref Group"     # Parameter group
META_AGE = "Age"
META_REMARKS = "Remarks"
META_BLOOD_TYPE = "Blood Type"

METADATA_IDENTIFIERS = frozenset({
    META_BLOOD_MODE, META_TEST_MODE, META_REF_GROUP,
    META_AGE, META_REMARKS, META_BLOOD_TYPE,
})


# ============================================================
# Status edit OBX-13 (field custom vendor, dokumen tabel 9)
# ============================================================

EDIT_UNEDITED = ""
EDIT_EXPIRED_REAGENT = "O"
EDIT_ACTIVE = "E"
EDIT_PASSIVE = "e"

EDIT_LABELS = {
    EDIT_EXPIRED_REAGENT: "O",
    EDIT_ACTIVE: "E",
    EDIT_PASSIVE: "e",
}


# ============================================================
# Escape sequence (dokumen tabel 2)
# ============================================================
#
# Urutan penting: \E\ (escape char) harus didecode TERAKHIR agar backslash hasil
# decode-nya tidak ikut ditafsirkan sebagai awal sequence lain.

ESCAPE_SEQUENCES = (
    ("\\F\\", FIELD_SEPARATOR),
    ("\\S\\", COMPONENT_SEP),
    ("\\T\\", SUBCOMPONENT_SEP),
    ("\\R\\", REPEAT_SEP),
    ("\\.br\\", "\r"),
    ("\\E\\", ESCAPE_CHAR),
)
