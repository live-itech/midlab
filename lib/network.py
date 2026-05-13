"""
lib/network.py — Helper deteksi network address lokal MidLab.

Dipakai Web Console untuk menampilkan URL endpoint Order Receiver
secara dinamis (mengikuti IP server tempat MidLab di-deploy), sehingga
tim LIS tinggal copy URL tanpa perlu tahu hostname server.
"""

import socket


def get_local_ip() -> str:
    """
    Deteksi IP address LAN aktif server ini.

    Caranya: buka UDP socket "connect" (tidak benar-benar kirim paket)
    ke 8.8.8.8:80, lalu ambil source address yang dipilih kernel sesuai
    routing table. Trick standar yang tidak butuh internet aktif —
    kernel hanya pilih interface, tidak melakukan ARP / DNS resolution.

    Fallback:
      1. socket.gethostbyname(socket.gethostname()) — kalau no routing
      2. "127.0.0.1" — kalau hostname tidak resolvable

    Returns:
        String IPv4, mis. "192.168.1.50".
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        if ip and not ip.startswith("0."):
            return ip
    except OSError:
        pass
    finally:
        s.close()

    try:
        ip = socket.gethostbyname(socket.gethostname())
        if ip and not ip.startswith("127."):
            return ip
    except OSError:
        pass

    return "127.0.0.1"


if __name__ == "__main__":
    ip = get_local_ip()
    assert isinstance(ip, str) and ip.count(".") == 3, f"unexpected: {ip!r}"
    print(f"OK: get_local_ip() → {ip}")
    print("=== lib.network tests PASSED ===")
