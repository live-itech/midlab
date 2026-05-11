"""
services/web_console/watchdog.py — ServiceWatchdog untuk MidLab

Mengelola lifecycle semua service MidLab:
- Start/stop/restart service via subprocess
- Tracking PID per service
- Monitor loop: auto-restart service yang mati (jika enabled)
- Persist state auto_restart ke file JSON

Service yang dikelola:
- result_sender  → python3 -m services.result_sender.main
- order_receiver → python3 -m services.order_receiver.main
- tcp_<id>       → python3 -m services.tcp_socket.main --instrument-id <id>

PID files: /var/run/midlab/<service_name>.pid
State file: /var/run/midlab/watchdog_state.json
"""

import asyncio
import json
import os
import signal
import subprocess
import sys
import time

from lib.config import Config
from lib.utils import get_logger

# Direktori untuk PID files dan state
# Gunakan /var/run/midlab jika tersedia, fallback ke PROJECT_ROOT/run
_DEFAULT_RUN_DIR = "/var/run/midlab"
RUN_DIR = _DEFAULT_RUN_DIR if os.path.isdir(_DEFAULT_RUN_DIR) and os.access(_DEFAULT_RUN_DIR, os.W_OK) else os.path.join(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")), "run"
)
STATE_FILE = os.path.join(RUN_DIR, "watchdog_state.json")

# Path ke python interpreter
PYTHON = sys.executable or "python3"

# Root project directory
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# Interval monitor loop (detik)
MONITOR_INTERVAL = 10


class ServiceWatchdog:
    """
    Mengelola semua service MidLab via subprocess.

    Setiap service dijalankan sebagai child process terpisah.
    Watchdog melacak PID, uptime, dan status masing-masing.
    Auto-restart bisa di-toggle per service.
    """

    def __init__(self):
        self._logger = get_logger("webconsole")
        self._config = Config()

        # Registry: service_name → info dict
        # {process, pid, start_time, auto_restart, instrument_id}
        self._services: dict[str, dict] = {}

        # Pastikan direktori run ada
        os.makedirs(RUN_DIR, exist_ok=True)

        # Load persisted state (auto_restart flags)
        self._load_state()

        # Monitor task handle
        self._monitor_task: asyncio.Task | None = None
        self._running = False

    # ============================================================
    # Service Commands
    # ============================================================

    def start_service(self, service_name: str, instrument_id: int = None) -> dict:
        """
        Start sebuah service sebagai subprocess.

        Args:
            service_name: 'result_sender', 'order_receiver', atau 'tcp_<id>'
            instrument_id: ID instrument (hanya untuk tcp_socket service)

        Returns:
            Dict {success, pid, message}
        """
        # Cek apakah sudah running
        if self._is_process_alive(service_name):
            info = self._services.get(service_name, {})
            return {
                "success": False,
                "pid": info.get("pid"),
                "message": f"Service {service_name} sudah running (PID {info.get('pid')})",
            }

        # Tentukan command
        cmd = self._build_command(service_name, instrument_id)
        if cmd is None:
            return {
                "success": False,
                "pid": None,
                "message": f"Service {service_name} tidak dikenali",
            }

        try:
            # Start subprocess
            log_path = self._get_log_path(service_name)
            log_file = open(log_path, "a")

            process = subprocess.Popen(
                cmd,
                cwd=PROJECT_ROOT,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,  # Agar tidak mati saat parent exit
            )

            pid = process.pid

            # Simpan info
            self._services[service_name] = {
                "process": process,
                "pid": pid,
                "start_time": time.time(),
                "auto_restart": self._services.get(service_name, {}).get(
                    "auto_restart", False
                ),
                "instrument_id": instrument_id,
                "log_file": log_file,
            }

            # Tulis PID file
            self._write_pid_file(service_name, pid)
            self._save_state()

            self._logger.info(
                f"Service {service_name} started: PID={pid}"
            )
            return {"success": True, "pid": pid, "message": f"Started (PID {pid})"}

        except Exception as e:
            self._logger.error(f"Gagal start {service_name}: {e}")
            return {"success": False, "pid": None, "message": str(e)}

    def stop_service(self, service_name: str) -> dict:
        """
        Stop service via SIGTERM ke PID.

        Returns:
            Dict {success, message}
        """
        info = self._services.get(service_name)

        if not info or not self._is_process_alive(service_name):
            # Coba baca PID file sebagai fallback
            pid = self._read_pid_file(service_name)
            if pid and self._pid_exists(pid):
                try:
                    os.kill(pid, signal.SIGTERM)
                    self._cleanup_service(service_name)
                    self._logger.info(
                        f"Service {service_name} stopped via PID file: PID={pid}"
                    )
                    return {"success": True, "message": f"Stopped (PID {pid})"}
                except ProcessLookupError:
                    pass

            self._cleanup_service(service_name)
            return {
                "success": False,
                "message": f"Service {service_name} tidak running",
            }

        pid = info["pid"]
        process = info.get("process")

        try:
            # Kirim SIGTERM
            if process and process.poll() is None:
                process.terminate()
                # Tunggu max 10 detik
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    # Force kill jika tidak mau mati
                    process.kill()
                    process.wait(timeout=5)
            else:
                # Process object tidak ada, kirim SIGTERM langsung
                os.kill(pid, signal.SIGTERM)

            self._cleanup_service(service_name)
            self._logger.info(f"Service {service_name} stopped: PID={pid}")
            return {"success": True, "message": f"Stopped (PID {pid})"}

        except Exception as e:
            self._logger.error(f"Gagal stop {service_name}: {e}")
            self._cleanup_service(service_name)
            return {"success": False, "message": str(e)}

    def restart_service(self, service_name: str) -> dict:
        """Stop lalu start ulang service."""
        info = self._services.get(service_name, {})
        instrument_id = info.get("instrument_id")

        stop_result = self.stop_service(service_name)
        self._logger.info(
            f"Restart {service_name}: stop={stop_result['message']}"
        )

        # Tunggu sebentar agar port/resource dilepas
        time.sleep(1)

        start_result = self.start_service(service_name, instrument_id)
        self._logger.info(
            f"Restart {service_name}: start={start_result['message']}"
        )

        return {
            "success": start_result["success"],
            "pid": start_result.get("pid"),
            "message": f"Restart: {start_result['message']}",
        }

    def set_auto_restart(self, service_name: str, enabled: bool) -> dict:
        """Toggle auto-restart untuk service."""
        if service_name not in self._services:
            self._services[service_name] = {
                "process": None,
                "pid": None,
                "start_time": None,
                "auto_restart": enabled,
                "instrument_id": None,
                "log_file": None,
            }
        else:
            self._services[service_name]["auto_restart"] = enabled

        self._save_state()
        self._logger.info(
            f"Auto-restart {service_name}: {'enabled' if enabled else 'disabled'}"
        )
        return {
            "success": True,
            "auto_restart": enabled,
            "message": f"Auto-restart {'enabled' if enabled else 'disabled'}",
        }

    # ============================================================
    # Status
    # ============================================================

    def get_status(self, service_name: str) -> dict:
        """
        Ambil status satu service.

        Returns:
            {name, running, pid, uptime, auto_restart}
        """
        info = self._services.get(service_name, {})
        running = self._is_process_alive(service_name)
        pid = info.get("pid")
        start_time = info.get("start_time")

        uptime = None
        if running and start_time:
            uptime = int(time.time() - start_time)

        return {
            "name": service_name,
            "running": running,
            "pid": pid if running else None,
            "uptime": uptime,
            "auto_restart": info.get("auto_restart", False),
            "instrument_id": info.get("instrument_id"),
        }

    def get_all_status(self) -> dict:
        """
        Status semua service yang terdaftar.

        Returns:
            Dict {service_name: status_dict}
        """
        result = {}
        for name in self._services:
            result[name] = self.get_status(name)
        return result

    # ============================================================
    # Monitor Loop — auto-restart
    # ============================================================

    async def start_monitor(self):
        """Start monitor loop sebagai asyncio task."""
        if self._running:
            return
        self._running = True
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        self._logger.info(
            f"Watchdog monitor started (interval={MONITOR_INTERVAL}s)"
        )

    async def stop_monitor(self):
        """Stop monitor loop."""
        self._running = False
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        self._logger.info("Watchdog monitor stopped")

    async def _monitor_loop(self):
        """Loop utama: cek tiap service, restart jika mati dan auto_restart=True."""
        while self._running:
            try:
                for name, info in list(self._services.items()):
                    if not info.get("auto_restart", False):
                        continue

                    if not self._is_process_alive(name):
                        instrument_id = info.get("instrument_id")
                        self._logger.warning(
                            f"Service {name} mati, auto-restarting..."
                        )
                        # Cleanup dulu
                        self._cleanup_service(name)
                        # Preserve auto_restart flag
                        auto_restart = info.get("auto_restart", False)
                        result = self.start_service(name, instrument_id)
                        if name in self._services:
                            self._services[name]["auto_restart"] = auto_restart
                            self._save_state()
                        self._logger.info(
                            f"Auto-restart {name}: {result['message']}"
                        )

            except Exception as e:
                self._logger.error(f"Monitor loop error: {e}")

            await asyncio.sleep(MONITOR_INTERVAL)

    # ============================================================
    # Internal Helpers
    # ============================================================

    def _build_command(self, service_name: str, instrument_id: int = None) -> list | None:
        """Build command list untuk subprocess."""
        if service_name == "result_sender":
            return [PYTHON, "-m", "services.result_sender.main"]

        if service_name == "order_receiver":
            return [PYTHON, "-m", "services.order_receiver.main"]

        if service_name.startswith("tcp_"):
            # tcp_<instrument_id>
            iid = instrument_id
            if iid is None:
                try:
                    iid = int(service_name.split("_", 1)[1])
                except (IndexError, ValueError):
                    return None
            return [
                PYTHON, "-m", "services.tcp_socket.main",
                "--instrument-id", str(iid),
            ]

        return None

    def _get_log_path(self, service_name: str) -> str:
        """Path log file untuk subprocess stdout redirect."""
        log_dir = "/var/log/midlab"
        os.makedirs(log_dir, exist_ok=True)
        return os.path.join(log_dir, f"{service_name}.log")

    def _is_process_alive(self, service_name: str) -> bool:
        """Cek apakah process masih hidup."""
        info = self._services.get(service_name)
        if not info:
            return False

        process = info.get("process")
        pid = info.get("pid")

        # Cek via Popen object
        if process is not None:
            return process.poll() is None

        # Fallback: cek PID existence
        if pid:
            return self._pid_exists(pid)

        return False

    @staticmethod
    def _pid_exists(pid: int) -> bool:
        """Cek apakah PID masih ada di OS."""
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False

    def _cleanup_service(self, service_name: str):
        """Bersihkan state service setelah stop."""
        info = self._services.get(service_name, {})

        # Tutup log file handle
        log_file = info.get("log_file")
        if log_file:
            try:
                log_file.close()
            except Exception:
                pass

        # Hapus PID file
        pid_path = os.path.join(RUN_DIR, f"{service_name}.pid")
        try:
            os.remove(pid_path)
        except FileNotFoundError:
            pass

        # Reset info tapi pertahankan auto_restart dan instrument_id
        auto_restart = info.get("auto_restart", False)
        instrument_id = info.get("instrument_id")
        self._services[service_name] = {
            "process": None,
            "pid": None,
            "start_time": None,
            "auto_restart": auto_restart,
            "instrument_id": instrument_id,
            "log_file": None,
        }

    def _write_pid_file(self, service_name: str, pid: int):
        """Tulis PID ke file."""
        pid_path = os.path.join(RUN_DIR, f"{service_name}.pid")
        with open(pid_path, "w") as f:
            f.write(str(pid))

    def _read_pid_file(self, service_name: str) -> int | None:
        """Baca PID dari file."""
        pid_path = os.path.join(RUN_DIR, f"{service_name}.pid")
        try:
            with open(pid_path, "r") as f:
                return int(f.read().strip())
        except (FileNotFoundError, ValueError):
            return None

    # ============================================================
    # State Persistence
    # ============================================================

    def _save_state(self):
        """Simpan auto_restart state ke file JSON."""
        state = {}
        for name, info in self._services.items():
            state[name] = {
                "auto_restart": info.get("auto_restart", False),
                "instrument_id": info.get("instrument_id"),
            }
        try:
            with open(STATE_FILE, "w") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            self._logger.warning(f"Gagal simpan watchdog state: {e}")

    def _load_state(self):
        """Load persisted state dari file JSON."""
        try:
            with open(STATE_FILE, "r") as f:
                state = json.load(f)
            for name, data in state.items():
                self._services[name] = {
                    "process": None,
                    "pid": None,
                    "start_time": None,
                    "auto_restart": data.get("auto_restart", False),
                    "instrument_id": data.get("instrument_id"),
                    "log_file": None,
                }
            self._logger.info(
                f"Loaded watchdog state: {len(state)} services"
            )
        except FileNotFoundError:
            pass
        except Exception as e:
            self._logger.warning(f"Gagal load watchdog state: {e}")

    # ============================================================
    # Registration Helpers
    # ============================================================

    def register_service(self, service_name: str, instrument_id: int = None):
        """Register service ke watchdog (tanpa start)."""
        if service_name not in self._services:
            self._services[service_name] = {
                "process": None,
                "pid": None,
                "start_time": None,
                "auto_restart": False,
                "instrument_id": instrument_id,
                "log_file": None,
            }

    def register_instrument_services(self, instrument_ids: list[int]):
        """Register semua tcp_<id> service berdasarkan list instrument IDs."""
        for iid in instrument_ids:
            self.register_service(f"tcp_{iid}", instrument_id=iid)

    def ensure_core_services(self):
        """Pastikan core services (result_sender, order_receiver) terdaftar."""
        self.register_service("result_sender")
        self.register_service("order_receiver")
