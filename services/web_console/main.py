"""
services/web_console/main.py — Entry Point Web Console MidLab

Menjalankan FastAPI app via uvicorn.
Mount static files untuk frontend UI.

Konfigurasi dari config.yaml:
  web_console:
    host: "0.0.0.0"
    port: 8000
    static_dir: "static"    # relatif dari project root

Contoh:
    python3 -m services.web_console.main
    python3 services/web_console/main.py
"""

import os
import sys

# Pastikan root project ada di sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import uvicorn

from lib.config import Config
from lib.utils import get_logger
from services.web_console.api import app


def main():
    logger = get_logger("webconsole")
    config = Config()

    host = config.get("web_console.host", "0.0.0.0")
    port = config.get("web_console.port", 8000)

    webconsole_dir = os.path.dirname(os.path.abspath(__file__))
    static_path = os.path.join(webconsole_dir, "static")

    logger.info("Starting Web Console...")
    print(
        f"MidLab Web Console\n"
        f"  Host       : {host}\n"
        f"  Port       : {port}\n"
        f"  Static dir : {static_path}\n"
        f"  API docs   : http://{host}:{port}/docs\n"
    )

    uvicorn.run(
        "services.web_console.api:app",
        host=host,
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
