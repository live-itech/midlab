"""
services/result_sender/service.py — ResultSenderService untuk MidLab

Poll tbl_result WHERE send_status='pending' secara periodik,
POST result_json ke LIS REST API, dan update status di database.

Konfigurasi dari config.yaml:
  result_sender:
    poll_interval: 5        # detik antar poll cycle
    retry_max: 3            # max retry sebelum skip
    batch_size: 50          # jumlah result per poll
    timeout: 30             # HTTP request timeout (detik)
  lis:
    api_url: "http://..."   # LIS REST API base URL
    api_key: "..."          # API key untuk autentikasi
"""

import asyncio
import signal
import sys

import aiohttp

from lib.config import Config
from lib.db import get_pending_results, update_result_status
from lib.utils import get_logger


class ResultSenderService:
    """
    Service yang mengirim hasil pemeriksaan ke LIS via REST API.

    Lifecycle:
    1. __init__  — load config (poll_interval, LIS URL, API key, retry_max)
    2. start()   — masuk poll loop, setup signal handler
    3. stop()    — set flag stop, tunggu cycle selesai

    Setiap poll cycle:
    - Ambil batch result pending dari tbl_result
    - Untuk tiap result: POST ke LIS API
    - Response 2xx → update send_status='sent'
    - Response non-2xx / exception → update send_status='failed', retry_count+=1
    - Jika retry_count >= retry_max → skip, log WARNING
    """

    def __init__(self):
        config = Config()
        self._poll_interval = config.get("result_sender.poll_interval", 5)
        self._retry_max = config.get("result_sender.retry_max", 3)
        self._batch_size = config.get("result_sender.batch_size", 50)
        self._timeout = config.get("result_sender.timeout", 30)

        self._lis_url = config.get("lis.api_url", "")
        self._lis_api_key = config.get("lis.api_key", "")

        self._logger = get_logger("result_sender")
        self._running = False
        self._shutdown_event = asyncio.Event()

        # Statistik
        self._total_sent = 0
        self._total_failed = 0
        self._total_skipped = 0

    # ============================================================
    # Lifecycle
    # ============================================================

    async def start(self):
        """Start service: setup signal, masuk poll loop."""
        self._running = True

        self._logger.info(
            f"ResultSenderService starting — "
            f"poll_interval={self._poll_interval}s, "
            f"retry_max={self._retry_max}, "
            f"lis_url={self._lis_url}"
        )

        if not self._lis_url:
            self._logger.error("lis.api_url tidak dikonfigurasi di config.yaml")
            print("ERROR: lis.api_url belum diset di config.yaml", file=sys.stderr)
            return

        # Setup signal handlers
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._signal_handler, sig)

        try:
            await self._poll_loop()
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()

    async def stop(self):
        """Stop service gracefully."""
        if not self._running:
            return

        self._running = False
        self._shutdown_event.set()
        self._logger.info(
            f"ResultSenderService stopped — "
            f"sent={self._total_sent}, failed={self._total_failed}, "
            f"skipped={self._total_skipped}"
        )

    def _signal_handler(self, sig):
        sig_name = signal.Signals(sig).name
        self._logger.info(f"{sig_name} diterima, shutting down...")
        self._running = False
        self._shutdown_event.set()

    # ============================================================
    # Poll Loop
    # ============================================================

    async def _poll_loop(self):
        """Loop utama: poll DB → kirim → sleep → repeat."""
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self._timeout),
        ) as session:
            while self._running:
                try:
                    await self._poll_and_send(session)
                except Exception as e:
                    self._logger.error(f"Error dalam poll cycle: {e}")

                # Tunggu poll_interval atau shutdown
                try:
                    await asyncio.wait_for(
                        self._shutdown_event.wait(),
                        timeout=self._poll_interval,
                    )
                    # shutdown_event di-set → keluar
                    break
                except asyncio.TimeoutError:
                    # Timeout = interval selesai, lanjut poll
                    pass

    async def _poll_and_send(self, session: aiohttp.ClientSession):
        """Satu cycle: ambil pending results, kirim satu per satu."""
        results = await asyncio.get_event_loop().run_in_executor(
            None, get_pending_results, self._batch_size
        )

        if not results:
            return

        self._logger.info(f"Ditemukan {len(results)} result pending")

        for result in results:
            if not self._running:
                break

            # Skip jika sudah melebihi retry_max
            retry_count = result.retry_count or 0
            if retry_count >= self._retry_max:
                self._total_skipped += 1
                self._logger.warning(
                    f"result_id={result.id} status=skipped "
                    f"retry_count={retry_count} >= retry_max={self._retry_max}"
                )
                continue

            await self._send_result(session, result)

    # ============================================================
    # Send to LIS
    # ============================================================

    async def _send_result(self, session: aiohttp.ClientSession, result):
        """
        POST satu result ke LIS REST API.

        Args:
            session: aiohttp ClientSession
            result: TblResult object dari database
        """
        result_id = result.id
        result_json = result.result_json

        if not result_json:
            self._logger.warning(
                f"result_id={result_id} status=skipped reason=empty_result_json"
            )
            await asyncio.get_event_loop().run_in_executor(
                None,
                update_result_status,
                result_id,
                "failed",
                "result_json kosong",
            )
            self._total_failed += 1
            return

        headers = {"Content-Type": "application/json"}
        if self._lis_api_key:
            headers["X-API-Key"] = self._lis_api_key

        try:
            async with session.post(
                self._lis_url,
                json=result_json,
                headers=headers,
            ) as resp:
                status_code = resp.status

                if 200 <= status_code < 300:
                    # Sukses → update sent
                    await asyncio.get_event_loop().run_in_executor(
                        None,
                        update_result_status,
                        result_id,
                        "sent",
                        None,
                    )
                    self._total_sent += 1
                    self._logger.info(
                        f"result_id={result_id} status=sent "
                        f"http_status={status_code}"
                    )
                else:
                    # Non-2xx → failed
                    body = await resp.text()
                    error_msg = f"HTTP {status_code}: {body[:500]}"
                    await asyncio.get_event_loop().run_in_executor(
                        None,
                        update_result_status,
                        result_id,
                        "failed",
                        error_msg,
                    )
                    self._total_failed += 1
                    self._logger.error(
                        f"result_id={result_id} status=failed "
                        f"http_status={status_code} error={error_msg[:200]}"
                    )

        except aiohttp.ClientError as e:
            error_msg = f"Connection error: {e}"
            await asyncio.get_event_loop().run_in_executor(
                None,
                update_result_status,
                result_id,
                "failed",
                error_msg,
            )
            self._total_failed += 1
            self._logger.error(
                f"result_id={result_id} status=failed error={error_msg}"
            )

        except asyncio.TimeoutError:
            error_msg = f"Request timeout ({self._timeout}s)"
            await asyncio.get_event_loop().run_in_executor(
                None,
                update_result_status,
                result_id,
                "failed",
                error_msg,
            )
            self._total_failed += 1
            self._logger.error(
                f"result_id={result_id} status=failed error={error_msg}"
            )

    # ============================================================
    # Properties
    # ============================================================

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def stats(self) -> dict:
        return {
            "running": self._running,
            "total_sent": self._total_sent,
            "total_failed": self._total_failed,
            "total_skipped": self._total_skipped,
            "poll_interval": self._poll_interval,
            "retry_max": self._retry_max,
        }
