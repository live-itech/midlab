"""
protocols/astm/constants.py — Konstanta Protokol ASTM E1381/E1394

Mendefinisikan semua byte kontrol, kode record type, dan delimiter default
yang digunakan dalam komunikasi ASTM antara alat lab dan middleware.

Referensi: ASTM E1381 (Transport) dan ASTM E1394 (Data Format)
"""


# ============================================================
# Control Characters — ASTM E1381 Transport Layer
# ============================================================

ENQ = 0x05    # Enquiry — memulai sesi komunikasi
ACK = 0x06    # Acknowledge — konfirmasi frame diterima
NAK = 0x15    # Negative Acknowledge — frame ditolak / error
EOT = 0x04    # End of Transmission — akhiri sesi komunikasi
STX = 0x02    # Start of Text — awal data frame
ETX = 0x03    # End of Text — akhir frame terakhir (single frame / frame terakhir)
ETB = 0x17    # End of Transmission Block — akhir frame intermediate (multi-frame)
CR  = 0x0D    # Carriage Return
LF  = 0x0A    # Line Feed

# Byte versions untuk perbandingan langsung
ENQ_BYTE = bytes([ENQ])
ACK_BYTE = bytes([ACK])
NAK_BYTE = bytes([NAK])
EOT_BYTE = bytes([EOT])
STX_BYTE = bytes([STX])
ETX_BYTE = bytes([ETX])
ETB_BYTE = bytes([ETB])
CR_BYTE  = bytes([CR])
LF_BYTE  = bytes([LF])
CRLF     = bytes([CR, LF])


# ============================================================
# Record Type Codes — ASTM E1394 Data Format
# ============================================================

RECORD_HEADER      = "H"   # Header record — info pengirim/penerima
RECORD_PATIENT     = "P"   # Patient record — data pasien
RECORD_ORDER       = "O"   # Order record — info order/pemeriksaan
RECORD_RESULT      = "R"   # Result record — hasil pemeriksaan
RECORD_TERMINATOR  = "L"   # Message terminator record
RECORD_QUERY       = "Q"   # Query record — permintaan data (bidirectional)
RECORD_COMMENT     = "C"   # Comment record — komentar tambahan
RECORD_SCIENTIFIC  = "S"   # Scientific record (jarang dipakai)
RECORD_MANUFACTURER = "M"  # Manufacturer-specific record

# Set semua record type yang valid
VALID_RECORD_TYPES = {
    RECORD_HEADER, RECORD_PATIENT, RECORD_ORDER, RECORD_RESULT,
    RECORD_TERMINATOR, RECORD_QUERY, RECORD_COMMENT,
    RECORD_SCIENTIFIC, RECORD_MANUFACTURER,
}


# ============================================================
# Delimiter Defaults — ASTM E1394
# ============================================================

FIELD_DELIMITER     = "|"   # Pemisah antar field
REPEAT_DELIMITER    = "\\"  # Pemisah repeat field
COMPONENT_DELIMITER = "^"   # Pemisah komponen dalam field
ESCAPE_DELIMITER    = "&"   # Karakter escape

# String delimiter lengkap yang biasa muncul di H record field ke-2
DEFAULT_DELIMITERS = FIELD_DELIMITER + REPEAT_DELIMITER + COMPONENT_DELIMITER + ESCAPE_DELIMITER
# Contoh: "|\\^&"


# ============================================================
# Frame Constants
# ============================================================

MAX_FRAME_SIZE = 63993      # Max data dalam 1 frame sebelum dipecah (64K - overhead)
FRAME_NUMBER_RANGE = range(0, 8)  # Frame number 0-7, kemudian wrap ke 0


# ============================================================
# Timeout Constants (dalam detik)
# ============================================================

TIMEOUT_ENQ_RESPONSE = 15   # Waktu tunggu response setelah kirim ENQ
TIMEOUT_ACK_RESPONSE = 15   # Waktu tunggu ACK setelah kirim frame
TIMEOUT_IDLE = 30           # Waktu idle sebelum timeout sesi


# ============================================================
# Unit Test
# ============================================================

if __name__ == "__main__":
    print("=== Test ASTM Constants ===\n")

    # Test control characters
    assert ENQ == 0x05, "ENQ harus 0x05"
    assert ACK == 0x06, "ACK harus 0x06"
    assert NAK == 0x15, "NAK harus 0x15"
    assert EOT == 0x04, "EOT harus 0x04"
    assert STX == 0x02, "STX harus 0x02"
    assert ETX == 0x03, "ETX harus 0x03"
    assert ETB == 0x17, "ETB harus 0x17"
    print("OK: Semua control characters benar")

    # Test byte versions
    assert ENQ_BYTE == b"\x05"
    assert ACK_BYTE == b"\x06"
    assert NAK_BYTE == b"\x15"
    assert EOT_BYTE == b"\x04"
    print("OK: Byte versions benar")

    # Test record types
    assert RECORD_HEADER == "H"
    assert RECORD_RESULT == "R"
    assert RECORD_QUERY == "Q"
    assert len(VALID_RECORD_TYPES) == 9
    print(f"OK: {len(VALID_RECORD_TYPES)} record types terdefinisi")

    # Test delimiters
    assert FIELD_DELIMITER == "|"
    assert REPEAT_DELIMITER == "\\"
    assert COMPONENT_DELIMITER == "^"
    assert ESCAPE_DELIMITER == "&"
    assert DEFAULT_DELIMITERS == "|\\^&"
    print("OK: Delimiters benar")

    # Test CRLF
    assert CRLF == b"\r\n"
    print("OK: CRLF benar")

    print("\n=== Semua test ASTM Constants PASSED ===")
