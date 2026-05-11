"""
protocols/hl7/constants.py — Konstanta Protokol HL7 v2.x + MLLP Transport

Mendefinisikan byte kontrol MLLP, message types, segment names, ACK codes,
dan field/component delimiters standar HL7.

Referensi: HL7 v2.5.1, MLLP (Minimal Lower Layer Protocol)
"""


# ============================================================
# MLLP Transport — Minimal Lower Layer Protocol
# ============================================================

MLLP_START = 0x0B   # Vertical Tab — awal MLLP envelope
MLLP_END   = 0x1C   # File Separator — akhir MLLP envelope
MLLP_CR    = 0x0D   # Carriage Return — setelah MLLP_END

# Byte versions untuk perbandingan langsung
MLLP_START_BYTE = bytes([MLLP_START])
MLLP_END_BYTE   = bytes([MLLP_END])
MLLP_CR_BYTE    = bytes([MLLP_CR])
MLLP_TRAILER    = bytes([MLLP_END, MLLP_CR])  # <FS><CR> di akhir message


# ============================================================
# HL7 Delimiters — standar encoding characters
# ============================================================

FIELD_SEPARATOR    = "|"    # Pemisah antar field (MSH-1)
COMPONENT_SEP      = "^"    # Pemisah komponen dalam field
REPEAT_SEP         = "~"    # Pemisah repeat field
ESCAPE_CHAR        = "\\"   # Karakter escape
SUBCOMPONENT_SEP   = "&"    # Pemisah sub-komponen

# Encoding characters string (MSH-2): ^~\&
ENCODING_CHARACTERS = COMPONENT_SEP + REPEAT_SEP + ESCAPE_CHAR + SUBCOMPONENT_SEP

# Segment terminator
SEGMENT_TERMINATOR = "\r"   # CR memisahkan antar segment dalam HL7 message


# ============================================================
# Message Types — HL7 v2.x
# ============================================================

# Message type (MSH-9 component 1)
MSG_ADT = "ADT"   # Admit/Discharge/Transfer
MSG_ORM = "ORM"   # Order Message (pharmacy/treatment, general)
MSG_ORU = "ORU"   # Observation Result (unsolicited)
MSG_QBP = "QBP"   # Query By Parameter
MSG_RSP = "RSP"   # Response to Query
MSG_ACK = "ACK"   # General Acknowledgement
MSG_QRY = "QRY"   # Query (legacy)

# Trigger events umum
EVENT_ORU_R01 = "ORU^R01"   # Unsolicited observation result
EVENT_ORM_O01 = "ORM^O01"   # General order message
EVENT_QBP_Q22 = "QBP^Q22"   # Query by parameter (find candidates)
EVENT_RSP_K22 = "RSP^K22"   # Response to QBP^Q22
EVENT_ACK_R01 = "ACK^R01"   # ACK untuk ORU^R01
EVENT_ACK_O01 = "ACK^O01"   # ACK untuk ORM^O01
EVENT_QRY_Q01 = "QRY^Q01"   # Legacy query

# Set message types yang merupakan query (trigger bidirectional)
QUERY_MESSAGE_TYPES = {
    MSG_QBP, MSG_QRY,
}

QUERY_EVENTS = {
    EVENT_QBP_Q22, EVENT_QRY_Q01,
}


# ============================================================
# Segment Names — HL7 v2.x
# ============================================================

SEG_MSH = "MSH"   # Message Header
SEG_PID = "PID"   # Patient Identification
SEG_PV1 = "PV1"   # Patient Visit
SEG_OBR = "OBR"   # Observation Request
SEG_OBX = "OBX"   # Observation/Result
SEG_NTE = "NTE"   # Notes and Comments
SEG_MSA = "MSA"   # Message Acknowledgement
SEG_QAK = "QAK"   # Query Acknowledgement
SEG_QPD = "QPD"   # Query Parameter Definition
SEG_ERR = "ERR"   # Error

# Segment yang wajib ada di setiap message
REQUIRED_SEGMENTS = {SEG_MSH}


# ============================================================
# ACK Codes — MSA-1 Acknowledgement Code
# ============================================================

ACK_AA = "AA"   # Application Accept — message diterima dan diproses
ACK_AE = "AE"   # Application Error — message diterima tapi ada error
ACK_AR = "AR"   # Application Reject — message ditolak

# Mapping ke string deskriptif
ACK_DESCRIPTIONS = {
    ACK_AA: "Application Accept",
    ACK_AE: "Application Error",
    ACK_AR: "Application Reject",
}


# ============================================================
# Processing ID — MSH-11
# ============================================================

PROC_PRODUCTION  = "P"   # Production
PROC_TRAINING    = "T"   # Training
PROC_DEBUGGING   = "D"   # Debugging


# ============================================================
# HL7 Version
# ============================================================

HL7_VERSION = "2.5.1"


# ============================================================
# Timeout Constants (dalam detik)
# ============================================================

TIMEOUT_ACK_RESPONSE = 15   # Waktu tunggu ACK setelah kirim message
TIMEOUT_QUERY_RESPONSE = 30 # Waktu tunggu response setelah kirim query
TIMEOUT_IDLE = 60            # Waktu idle sebelum timeout koneksi


# ============================================================
# Unit Test
# ============================================================

if __name__ == "__main__":
    print("=== Test HL7 Constants ===\n")

    # Test MLLP bytes
    assert MLLP_START == 0x0B, "MLLP_START harus 0x0B"
    assert MLLP_END == 0x1C, "MLLP_END harus 0x1C"
    assert MLLP_CR == 0x0D, "MLLP_CR harus 0x0D"
    assert MLLP_START_BYTE == b"\x0b"
    assert MLLP_END_BYTE == b"\x1c"
    assert MLLP_TRAILER == b"\x1c\x0d"
    print("OK: MLLP bytes benar")

    # Test delimiters
    assert FIELD_SEPARATOR == "|"
    assert COMPONENT_SEP == "^"
    assert REPEAT_SEP == "~"
    assert ESCAPE_CHAR == "\\"
    assert SUBCOMPONENT_SEP == "&"
    assert ENCODING_CHARACTERS == "^~\\&"
    print("OK: Delimiters benar")

    # Test message types
    assert MSG_ORU == "ORU"
    assert MSG_QBP == "QBP"
    assert MSG_RSP == "RSP"
    assert MSG_ACK == "ACK"
    assert EVENT_ORU_R01 == "ORU^R01"
    assert EVENT_QBP_Q22 == "QBP^Q22"
    assert EVENT_RSP_K22 == "RSP^K22"
    print(f"OK: Message types terdefinisi")

    # Test query types
    assert MSG_QBP in QUERY_MESSAGE_TYPES
    assert MSG_QRY in QUERY_MESSAGE_TYPES
    assert MSG_ORU not in QUERY_MESSAGE_TYPES
    assert EVENT_QBP_Q22 in QUERY_EVENTS
    print("OK: Query message types benar")

    # Test segments
    assert SEG_MSH == "MSH"
    assert SEG_PID == "PID"
    assert SEG_OBR == "OBR"
    assert SEG_OBX == "OBX"
    assert SEG_MSA == "MSA"
    assert SEG_QAK == "QAK"
    assert SEG_QPD == "QPD"
    print("OK: Segment names benar")

    # Test ACK codes
    assert ACK_AA == "AA"
    assert ACK_AE == "AE"
    assert ACK_AR == "AR"
    assert len(ACK_DESCRIPTIONS) == 3
    print("OK: ACK codes benar")

    # Test segment terminator
    assert SEGMENT_TERMINATOR == "\r"
    print("OK: Segment terminator benar")

    # Test HL7 version
    assert HL7_VERSION == "2.5.1"
    print("OK: HL7 version benar")

    print("\n=== Semua test HL7 Constants PASSED ===")
