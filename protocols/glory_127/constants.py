"""
Konstanta driver CUSTOM_GLORY_127.

Format wire (satu pesan = SATU analit, bukan satu sampel):

    <<<SGPT,510,,2000-01-01 00:06:57,-0.007,0.000,11.039,U/L,0.0000,40.0000,7.9000,500.0000,01,00,GLORY 127,>>>

Indeks field di bawah terkonfirmasi dengan mencocokkan capture serial ke foto
layar Result List alat untuk record yang sama — lihat
docs/superpowers/specs/2026-08-04-glory-127-design.md
"""

PROTOCOL_NAME = "CUSTOM_GLORY_127"
PROTOCOL_VERSION = "1.0.0"

# Delimiter pesan. Diumumkan ke ResultReceiver lewat atribut module dengan nama
# yang sama, supaya receiver bisa memotong frame sebelum memanggil parse().
FRAME_START = b"<<<"
FRAME_END = b">>>"

# Alat mengirim ASCII; latin-1 dipilih supaya byte apa pun tetap terbaca
# tanpa melempar exception (satuan kadang memuat karakter mikro).
ENCODING = "latin-1"

FIELD_SEPARATOR = ","

# --- Indeks field ---------------------------------------------------
# Yang UNKNOWN tidak muncul di layar mana pun dan tidak ada dokumen vendor.
# Sengaja TIDAK ditebak semantiknya — disimpan mentah di instrument_meta.
FIELD_PROGRAM_NAME = 0   # layar: "Program Name"
FIELD_NR = 1             # layar: "Nr"        → sample_id
FIELD_REF = 2            # layar: "Ref"       → kosong di lapangan
FIELD_DATETIME = 3       # layar: "Time"      → jam alat (bisa tertinggal)
FIELD_RATE_OD = 4        # layar: "RATE" (kinetik) / "OD" (endpoint)
FIELD_UNKNOWN_5 = 5
FIELD_CONC = 6           # layar: "CONC"      → nilai hasil
FIELD_UNIT = 7           # satuan
FIELD_NORMAL_LOW = 8     # layar: "Normal Low"
FIELD_NORMAL_HIGH = 9    # layar: "Normal High"
FIELD_UNKNOWN_10 = 10
FIELD_UNKNOWN_11 = 11
FIELD_MODE = 12          # `01` saat layar menulis RATE, `00` saat OD.
                         # Dugaan jenis assay — TIDAK dijadikan logika,
                         # dua sampel tidak cukup untuk mengunci itu.
FIELD_UNKNOWN_13 = 13
FIELD_DEVICE_NAME = 14   # "GLORY 127"

EXPECTED_FIELD_COUNT = 16

# Field yang belum diketahui artinya, dikumpulkan apa adanya untuk analisis
# nanti kalau dokumen vendor ketemu.
UNKNOWN_FIELDS = {
    "f5": FIELD_UNKNOWN_5,
    "f10": FIELD_UNKNOWN_10,
    "f11": FIELD_UNKNOWN_11,
    "f13": FIELD_UNKNOWN_13,
}

# --- Nilai hasil ----------------------------------------------------
# Alat kuantitatif murni: CONC selalu numerik. Nilai non-numerik ('----',
# '***') berarti tes gagal, bukan hasil kualitatif — lihat should_store_result().
STATUS_FINAL = "F"

FLAG_HIGH = "H"
FLAG_LOW = "L"
FLAG_NORMAL = "N"
FLAG_NONE = ""

# Potongan isi frame di log, supaya stream sampah tidak membanjiri berkas log.
LOG_PREVIEW_LIMIT = 500
