"""
services/lis_bridge/main.py — Entry point LisBridgeService per-alat.

Usage:
    python3 -m services.lis_bridge.main --instrument-id 1
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from lib import timeutil
from lib.utils import get_logger
from services.lis_bridge.service import LisBridgeService

# Zona waktu proses = zona lab, dipasang sebelum logger/DB dipakai.
# Tanpa ini service yang dijalankan systemd dengan environment bersih
# jatuh ke UTC dan seluruh timestamp meleset 7 jam.
timeutil.install_process_timezone()


def parse_args():
    p = argparse.ArgumentParser(description="MidLab LisBridgeService")
    p.add_argument("--instrument-id", type=int, required=True)
    return p.parse_args()


async def run(instrument_id: int):
    logger = get_logger(f"lis_bridge_{instrument_id}")
    logger.info(f"Starting LisBridgeService for instrument_id={instrument_id}")
    svc = LisBridgeService(instrument_id=instrument_id)
    await svc.start()


def main():
    args = parse_args()
    try:
        asyncio.run(run(args.instrument_id))
    except KeyboardInterrupt:
        print("\nShutdown by keyboard interrupt.")


if __name__ == "__main__":
    main()
