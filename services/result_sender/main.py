"""
services/result_sender/main.py — Entry Point ResultSenderService

Menjalankan ResultSenderService sebagai asyncio loop.

Contoh:
    python3 -m services.result_sender.main
    python3 services/result_sender/main.py
"""

import asyncio
import sys
import os

# Pastikan root project ada di sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from lib import timeutil
from lib.config import Config
from lib.utils import get_logger
from services.result_sender.service import ResultSenderService

# Zona waktu proses = zona lab, dipasang sebelum logger/DB dipakai.
# Tanpa ini service yang dijalankan systemd dengan environment bersih
# jatuh ke UTC dan seluruh timestamp meleset 7 jam.
timeutil.install_process_timezone()


async def run():
    logger = get_logger("result_sender")
    config = Config()

    lis_url = config.get("lis.api_url", "(belum diset)")
    poll_interval = config.get("result_sender.poll_interval", 5)
    retry_max = config.get("result_sender.retry_max", 3)

    logger.info("Starting ResultSenderService...")
    print(
        f"MidLab ResultSenderService\n"
        f"  LIS URL       : {lis_url}\n"
        f"  Poll interval  : {poll_interval}s\n"
        f"  Retry max      : {retry_max}\n"
    )

    service = ResultSenderService()
    await service.start()


def main():
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\nShutdown by keyboard interrupt.")


if __name__ == "__main__":
    main()
