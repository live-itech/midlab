"""
services/tcp_socket/main.py — Entry Point TCPSocketService

Menjalankan satu instance TCPSocketService untuk instrument tertentu.
Instrument ID diambil dari argument CLI.

Contoh:
    python -m services.tcp_socket.main --instrument-id 1
    python services/tcp_socket/main.py --instrument-id 1
"""

import argparse
import asyncio
import sys
import os

# Pastikan root project ada di sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from lib.utils import get_logger
from services.tcp_socket.config import load_instrument_config
from services.tcp_socket.service import TCPSocketService


def parse_args():
    parser = argparse.ArgumentParser(
        description="MidLab TCPSocketService — koneksi TCP per alat lab"
    )
    parser.add_argument(
        "--instrument-id",
        type=int,
        required=True,
        help="ID instrument di tbl_instrument",
    )
    return parser.parse_args()


async def run(instrument_id: int):
    logger = get_logger("tcp_socket", instrument_id)

    # Load konfigurasi instrument dari database
    logger.info(f"Loading config untuk instrument ID {instrument_id}...")
    config = load_instrument_config(instrument_id)

    if config is None:
        logger.error(f"Instrument ID {instrument_id} tidak ditemukan di database")
        print(f"ERROR: Instrument ID {instrument_id} tidak ditemukan.", file=sys.stderr)
        sys.exit(1)

    if not config.is_active:
        logger.warning(f"Instrument {config.name} (ID {instrument_id}) tidak aktif")
        print(
            f"WARNING: Instrument {config.name} tidak aktif (is_active=False).",
            file=sys.stderr,
        )
        sys.exit(1)

    # Tampilkan info startup
    logger.info(
        f"Starting TCPSocketService untuk {config.name} "
        f"({config.protocol} @ {config.ip_address}:{config.port}) "
        f"mode={config.mode} bidir={config.bidir_mode} conn={config.connection}"
    )
    print(
        f"MidLab TCPSocketService\n"
        f"  Instrument : {config.name} (ID {config.id})\n"
        f"  Protocol   : {config.protocol}\n"
        f"  Address    : {config.ip_address}:{config.port}\n"
        f"  Mode       : {config.mode}"
        + (f" ({config.bidir_mode})" if config.bidir_mode else "")
        + f"\n  Connection : {config.connection}\n"
    )

    # Buat dan jalankan service
    service = TCPSocketService(config)
    await service.start()


def main():
    args = parse_args()
    try:
        asyncio.run(run(args.instrument_id))
    except KeyboardInterrupt:
        print("\nShutdown by keyboard interrupt.")


if __name__ == "__main__":
    main()
