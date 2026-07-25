"""
services/tap/export.py — Export hasil tapping.

Penutup lingkaran ke SOP: hasil tap → fixture test → driver. Test driver di repo
ini semuanya berjangkar pada byte string verbatim (lihat ORU_DOC di
tests/test_aruma_ar580.py). Saat menulis driver AR580, transkripsi manual dari
PDF menghasilkan kesalahan hitung pipa di segment OBR yang baru tertangkap oleh
test — export otomatis menghapus seluruh kelas kesalahan itu.
"""


def _literal(chunk: bytes) -> str:
    """Satu potong byte → satu literal b"..." Python."""
    out = []
    for b in chunk:
        if b == 0x5C:            # backslash
            out.append("\\\\")
        elif b == 0x22:          # petik ganda
            out.append('\\"')
        elif 0x20 <= b < 0x7F:   # ASCII printable
            out.append(chr(b))
        else:
            out.append(f"\\x{b:02x}")
    return 'b"' + "".join(out) + '"'


def to_python_bytes(data: bytes, indent: str = "    ") -> str:
    """
    Format byte jadi literal Python siap tempel ke file test.

    Dipecah setelah tiap CR supaya tiap segment HL7/ASTM berada di barisnya
    sendiri — sama seperti fixture yang sudah ada di repo.

    Untuk >1 segment hasilnya dibungkus tanda kurung agar implicit string
    concatenation lintas-baris sah sebagai satu ekspresi Python (persis bentuk
    fixture ORU_DOC di tests/), sehingga hasil export bisa langsung di-eval.
    """
    if not data:
        return ""

    potongan: list[bytes] = []
    cur = bytearray()
    for b in data:
        cur.append(b)
        if b == 0x0D:            # CR — akhir segment
            potongan.append(bytes(cur))
            cur = bytearray()
    if cur:
        potongan.append(bytes(cur))

    if len(potongan) == 1:
        return f"{indent}{_literal(potongan[0])}"

    baris = "\n".join(f"{indent}{_literal(p)}" for p in potongan)
    return f"(\n{baris}\n)"


def rx_bytes(events: list[dict]) -> bytes:
    """Gabungkan seluruh byte arah RX — yang dikirim ALAT, untuk di-parse ulang."""
    out = bytearray()
    for e in events:
        if e.get("dir") == "rx":
            out.extend(bytes.fromhex(e["hex"]))
    return bytes(out)


def messages_from_events(events: list[dict]) -> list[bytes]:
    """
    Pecah aliran RX jadi pesan-pesan, berdasar penanda `message_complete`.

    Basis RAW tidak menghasilkan penanda apa pun (tidak ada framing), jadi
    fungsi ini mengembalikan list kosong — export per-pesan memang tidak
    tersedia di sana.
    """
    pesan: list[bytes] = []
    cur = bytearray()
    for e in events:
        if e.get("dir") == "rx":
            cur.extend(bytes.fromhex(e["hex"]))
        elif e.get("dir") == "meta" and e.get("event") == "message_complete":
            pesan.append(bytes(cur))
            cur = bytearray()
    return pesan
