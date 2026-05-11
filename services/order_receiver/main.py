"""
services/order_receiver/main.py — Entry Point OrderReceiverService

Menjalankan FastAPI app via uvicorn.

Konfigurasi dari config.yaml:
  order_receiver:
    host: "0.0.0.0"      # bind address
    port: 8080            # port
    api_key: "..."        # API key

Contoh:
    python3 -m services.order_receiver.main
    python3 services/order_receiver/main.py
"""

import sys
import os

# Pastikan root project ada di sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import uvicorn

from lib.config import Config
from lib.utils import get_logger


def main():
    logger = get_logger("order_receiver")
    config = Config()

    host = config.get("order_receiver.host", "0.0.0.0")
    port = config.get("order_receiver.port", 8080)
    api_key_set = bool(config.get("order_receiver.api_key", ""))

    logger.info("Starting OrderReceiverService...")
    print(
        f"MidLab OrderReceiverService\n"
        f"  Host      : {host}\n"
        f"  Port      : {port}\n"
        f"  API Key   : {'configured' if api_key_set else 'NOT SET (no auth)'}\n"
    )

    uvicorn.run(
        "services.order_receiver.api:app",
        host=host,
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
