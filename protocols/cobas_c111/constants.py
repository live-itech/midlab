"""
protocols/cobas_c111/constants.py — Konstanta protokol ASTM untuk Cobas c-111.

Sumber: Roche Host Interface Manual v2.2 untuk Cobas c 111 SW v3.0+
        Chapter 7 (ASTM Protocol).

Modul ini STANDALONE — tidak import dari protocols/astm/.
"""

# ============================================================
# ASTM E1381 lower-layer control characters (manual 7.1.3 Table 7)
# ============================================================
STX = 0x02   # Start of Text
ETX = 0x03   # End of Text (last frame)
ETB = 0x17   # End of Transmission Block (intermediate frame)
EOT = 0x04   # End of Transmission (session end)
ENQ = 0x05   # Enquiry (session start)
ACK = 0x06   # Acknowledge
NAK = 0x15   # Negative Acknowledge
CR  = 0x0D   # Carriage Return (record separator within frame text)
LF  = 0x0A   # Line Feed (frame trailing)

STX_BYTE = bytes([STX])
ETX_BYTE = bytes([ETX])
ETB_BYTE = bytes([ETB])
EOT_BYTE = bytes([EOT])
ENQ_BYTE = bytes([ENQ])
ACK_BYTE = bytes([ACK])
NAK_BYTE = bytes([NAK])
CR_BYTE  = bytes([CR])

# ============================================================
# Delimiters (manual 7.1.4.3 — recommended defaults, declared in H record)
# ============================================================
FIELD_DELIM     = "|"
REPEAT_DELIM    = "\\"
COMPONENT_DELIM = "^"
ESCAPE_DELIM    = "&"

# Escape sequences (manual 7.1.4.3.5)
# Note: these are SUB-strings that appear in text; decode after field split.
ESC_FIELD     = "&F&"  # → "|"
ESC_COMPONENT = "&S&"  # → "^"
ESC_REPEAT    = "&R&"  # → "\\"
ESC_ESCAPE    = "&E&"  # → "&"

ESCAPE_MAP = {
    ESC_FIELD:     FIELD_DELIM,
    ESC_COMPONENT: COMPONENT_DELIM,
    ESC_REPEAT:    REPEAT_DELIM,
    ESC_ESCAPE:    ESCAPE_DELIM,
}

# ============================================================
# Record type identifiers (manual 7.2.1 Tables 8 & 9)
# ============================================================
REC_HEADER      = "H"
REC_PATIENT     = "P"
REC_ORDER       = "O"
REC_RESULT      = "R"
REC_COMMENT     = "C"
REC_TERMINATOR  = "L"
REC_QUERY       = "Q"
REC_MANUFACTURER = "M"

VALID_RECORDS = {
    REC_HEADER, REC_PATIENT, REC_ORDER, REC_RESULT,
    REC_COMMENT, REC_TERMINATOR, REC_QUERY, REC_MANUFACTURER,
}

# Roche Manufacturer Specific Record subtypes (manual 7.2 Table 9, 7.2.2.10, 7.2.2.11)
M_SUBTYPE_CR = "CR"   # Photometric / ISE Calibration Result
M_SUBTYPE_RR = "RR"   # Photometric Absorbance raw data

# Protocol identity
PROTOCOL_NAME = "COBAS_C111"
PROTOCOL_VERSION = "1.0.0"


if __name__ == "__main__":
    # Sanity checks
    assert STX == 0x02 and ETX == 0x03 and ETB == 0x17
    assert EOT == 0x04 and ENQ == 0x05 and ACK == 0x06
    assert ESCAPE_MAP["&F&"] == "|"
    assert ESCAPE_MAP["&S&"] == "^"
    assert ESCAPE_MAP["&R&"] == "\\"
    assert ESCAPE_MAP["&E&"] == "&"
    assert PROTOCOL_NAME == "COBAS_C111"
    assert {REC_HEADER, REC_RESULT, REC_TERMINATOR}.issubset(VALID_RECORDS)
    print("=== cobas_c111.constants tests PASSED ===")
