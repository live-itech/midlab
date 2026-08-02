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

PID files: /var/run/midlab/<service_name>.pid       (volatile, boleh hilang)
State file: /var/lib/midlab/watchdog_state.json     (persisten, wajib selamat reboot)
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

# Root project directory
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# Nama file state (sama di lokasi persisten maupun legacy)
STATE_FILENAME = "watchdog_state.json"


def _pick_writable_dir(preferred: str, fallback: str) -> str:
    """Pakai `preferred` kalau ada dan writable, kalau tidak jatuh ke fallback."""
    if os.path.isdir(preferred) and os.access(preferred, os.W_OK):
        return preferred
    return fallback


# Direktori PID file. /var/run = tmpfs, isinya memang boleh hilang tiap reboot.
_DEFAULT_RUN_DIR = "/var/run/midlab"
RUN_DIR = _pick_writable_dir(_DEFAULT_RUN_DIR, os.path.join(PROJECT_ROOT, "run"))

# Direktori state watchdog. HARUS persisten: di sinilah flag auto_restart
# disimpan, dan flag itu yang menentukan service alat nyala lagi setelah
# server di-reboot. Dulu ini ikut di RUN_DIR (tmpfs) sehingga tiap reboot
# semua flag hilang dan tidak ada satu pun service alat yang start otomatis.
_DEFAULT_STATE_DIR = "/var/lib/midlab"
STATE_DIR = _pick_writable_dir(_DEFAULT_STATE_DIR, RUN_DIR)
STATE_FILE = os.path.join(STATE_DIR, STATE_FILENAME)

# Path ke python interpreter
PYTHON = sys.executable or "python3"

# Interval monitor loop (detik)
MONITOR_INTERVAL = 10


class ServiceWatchdog:
    """
    Mengelola semua service MidLab via subprocess.

    Setiap service dijalankan sebagai child process terpisah.
    Watchdog melacak PID, uptime, dan status masing-masing.
    Auto-restart bisa di-toggle per service.
    """

    def __init__(self, run_dir: str | None = None, state_dir: str | None = None):
        self._logger = get_logger("webconsole")
        self._config = Config()

        # Registry: service_name → info dict
        # {process, pid, start_time, auto_restart, instrument_id}
        self._services: dict[str, dict] = {}

        # run_dir = PID file (volatile), state_dir = state watchdog (persisten)
        self._run_dir = run_dir or RUN_DIR
        self._state_dir = state_dir or STATE_DIR
        self._state_file = os.path.join(self._state_dir, STATE_FILENAME)
        # Lokasi lama, sebelum state dipisah dari RUN_DIR
        self._legacy_state_file = os.path.join(self._run_dir, STATE_FILENAME)

        os.makedirs(self._run_dir, exist_ok=True)
        os.makedirs(self._state_dir, exist_ok=True)

        # Load persisted state (auto_restart flags)
        self._load_state()

        # Monitor task handle
        self._monitor_task: asyncio.Task | None = None
        self._running = False

    # ============================================================
    # Service Commands
    # ============================================================

    @staticmethod
    def _is_virtual_service(service_name: str) -> bool:
        """
        Virtual service = entry yang muncul di list_services tapi bukan proses
        nyata (mis. tcp_<id>__comm → pointer ke file .comm.log).
        Tidak boleh distart/stop/restart oleh watchdog.
        """
        return "__comm" in service_name

    def start_service(self, service_name: str, instrument_id: int = None) -> dict:
        """
        Start sebuah service sebagai subprocess.

        Args:
            service_name: 'result_sender', 'order_receiver', atau 'tcp_<id>'
            instrument_id: ID instrument (hanya untuk tcp_socket service)

        Returns:
            Dict {success, pid, message}
        """
        if self._is_virtual_service(service_name):
            return {
                "success": False,
                "pid": None,
                "message": f"{service_name} adalah virtual service (log-only), tidak bisa di-start",
            }

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
        if self._is_virtual_service(service_name):
            return {
                "success": False,
                "message": f"{service_name} adalah virtual service, tidak bisa di-stop",
            }

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
        if self._is_virtual_service(service_name):
            return {
                "success": False,
                "message": f"{service_name} adalah virtual service, tidak bisa di-restart",
            }

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
        if self._is_virtual_service(service_name):
            return {
                "success": False,
                "auto_restart": False,
                "message": f"{service_name} adalah virtual service, tidak punya auto-restart",
            }

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
                        # Proses bisa saja masih hidup tapi lepas dari registry
                        # (web console yang restart, bukan service-nya).
                        if self._adopt_running_process(name):
                            continue

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

        if service_name.startswith("lis_bridge_"):
            iid = instrument_id
            if iid is None:
                try:
                    iid = int(service_name.split("_", 2)[2])
                except (IndexError, ValueError):
                    return None
            return [
                PYTHON, "-m", "services.lis_bridge.main",
                "--instrument-id", str(iid),
            ]

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

    @staticmethod
    def _read_proc_cmdline(pid: int) -> str:
        """Baca /proc/<pid>/cmdline sebagai string dipisah spasi."""
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                return f.read().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            return ""

    def _cmdline_matches(
        self, service_name: str, pid: int, instrument_id: int = None
    ) -> bool:
        """
        Cek PID benar-benar milik service ini, bukan proses lain yang kebetulan
        mewarisi nomor PID yang sama (sangat mungkin setelah reboot).

        Dicocokkan per-token, bukan substring: argumen `--instrument-id 1`
        tidak boleh dianggap cocok dengan proses `--instrument-id 11`.
        """
        cmd = self._build_command(service_name, instrument_id)
        if not cmd:
            return False

        # Path interpreter diabaikan (.venv/bin/python vs python3 bisa beda)
        needle = cmd[1:]
        tokens = self._read_proc_cmdline(pid).split()
        if len(tokens) < len(needle):
            return False

        return any(
            tokens[i:i + len(needle)] == needle
            for i in range(len(tokens) - len(needle) + 1)
        )

    def _adopt_running_process(self, service_name: str) -> bool:
        """
        Ambil alih proses yang masih hidup berdasarkan PID file.

        Dipakai saat web console sendiri di-restart (systemd Restart=always)
        sementara service alat tetap jalan — proses anak sengaja dilepas dengan
        start_new_session=True. Tanpa adopsi, watchdog menganggapnya mati lalu
        spawn proses kedua untuk alat yang sama.
        """
        if self._is_virtual_service(service_name):
            return False

        info = self._services.get(service_name)
        if info is None:
            return False

        pid = self._read_pid_file(service_name)
        if not pid or not self._pid_exists(pid):
            return False

        if not self._cmdline_matches(service_name, pid, info.get("instrument_id")):
            self._logger.info(
                f"PID {pid} untuk {service_name} sudah dipakai proses lain, "
                f"PID file diabaikan"
            )
            return False

        info["process"] = None
        info["pid"] = pid
        # Perkiraan uptime: PID file ditulis tepat setelah proses dispawn.
        try:
            info["start_time"] = os.path.getmtime(self._pid_path(service_name))
        except OSError:
            info["start_time"] = time.time()

        self._logger.info(f"Adopsi {service_name} yang masih running: PID={pid}")
        return True

    def adopt_running_services(self) -> list[str]:
        """Adopsi semua service terdaftar yang prosesnya masih hidup."""
        adopted = []
        for name in list(self._services):
            if self._is_process_alive(name):
                continue
            if self._adopt_running_process(name):
                adopted.append(name)
        return adopted

    def autostart_enabled_services(self) -> dict:
        """
        Nyalakan service yang auto_restart=True tapi belum jalan.

        Dipanggil sekali saat web console startup. Ini yang bikin service alat
        hidup lagi sendiri setelah server reboot, tanpa nunggu satu siklus
        monitor loop dan tanpa perlu klik Start manual di UI.
        """
        adopted = self.adopt_running_services()
        started, failed = [], []

        for name, info in list(self._services.items()):
            if self._is_virtual_service(name):
                continue
            if not info.get("auto_restart", False):
                continue
            if self._is_process_alive(name):
                continue

            result = self.start_service(name, info.get("instrument_id"))
            if isinstance(result, dict) and result.get("success"):
                started.append(name)
            else:
                failed.append(name)

        if adopted or started or failed:
            self._logger.info(
                f"Autostart: started={started or '-'} "
                f"adopted={adopted or '-'} failed={failed or '-'}"
            )
        return {"started": started, "adopted": adopted, "failed": failed}

    def _cleanup_service(self, service_name: str):
        """
        Bersihkan state service setelah stop.

        Penting: kalau service_name tidak ada di _services, JANGAN bikin entry
        baru. Bug lama: virtual service (mis. tcp_<id>__comm) atau nama asing
        akan ter-resolve ke entry baru lewat fungsi ini, lalu muncul dobel di
        list_services (sekali dari watchdog status, sekali dari virtual loop).
        """
        # Hapus PID file (selalu, terlepas dari entry registry)
        pid_path = self._pid_path(service_name)
        try:
            os.remove(pid_path)
        except FileNotFoundError:
            pass

        info = self._services.get(service_name)
        if info is None:
            # Service tidak terdaftar — no-op, jangan bikin entry baru.
            return

        # Tutup log file handle
        log_file = info.get("log_file")
        if log_file:
            try:
                log_file.close()
            except Exception:
                pass

        # Reset info tapi pertahankan auto_restart dan instrument_id
        self._services[service_name] = {
            "process": None,
            "pid": None,
            "start_time": None,
            "auto_restart": info.get("auto_restart", False),
            "instrument_id": info.get("instrument_id"),
            "log_file": None,
        }

    def _pid_path(self, service_name: str) -> str:
        return os.path.join(self._run_dir, f"{service_name}.pid")

    def _write_pid_file(self, service_name: str, pid: int):
        """Tulis PID ke file."""
        with open(self._pid_path(service_name), "w") as f:
            f.write(str(pid))

    def _read_pid_file(self, service_name: str) -> int | None:
        """Baca PID dari file."""
        pid_path = self._pid_path(service_name)
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
            with open(self._state_file, "w") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            self._logger.warning(f"Gagal simpan watchdog state: {e}")

    def _read_state_file(self) -> dict | None:
        """
        Baca state dari lokasi persisten; kalau belum ada, coba lokasi legacy
        (RUN_DIR) supaya deployment existing tidak kehilangan flag auto_restart
        saat upgrade. Return None kalau dua-duanya tidak ada.
        """
        for path in (self._state_file, self._legacy_state_file):
            try:
                with open(path, "r") as f:
                    state = json.load(f)
            except FileNotFoundError:
                continue
            if path != self._state_file:
                self._logger.info(
                    f"Migrasi watchdog state {path} → {self._state_file}"
                )
            return state
        return None

    def _load_state(self):
        """Load persisted state dari file JSON."""
        try:
            state = self._read_state_file()
            if state is None:
                return
            purged = 0
            for name, data in state.items():
                # Self-heal: skip entry virtual (orphan dari bug lama).
                if self._is_virtual_service(name):
                    purged += 1
                    continue
                self._services[name] = {
                    "process": None,
                    "pid": None,
                    "start_time": None,
                    "auto_restart": data.get("auto_restart", False),
                    "instrument_id": data.get("instrument_id"),
                    "log_file": None,
                }
            self._logger.info(
                f"Loaded watchdog state: {len(self._services)} services"
                + (f" (purged {purged} virtual orphan)" if purged else "")
            )
            if purged or not os.path.exists(self._state_file):
                # Tulis ulang tanpa entri virtual, sekaligus menuntaskan
                # migrasi dari lokasi legacy ke lokasi persisten.
                self._save_state()
        except Exception as e:
            self._logger.warning(f"Gagal load watchdog state: {e}")

    # ============================================================
    # Registration Helpers
    # ============================================================

    def register_service(
        self,
        service_name: str,
        instrument_id: int = None,
        auto_restart: bool = False,
    ):
        """
        Register service ke watchdog (tanpa start).

        `auto_restart` hanya dipakai sebagai nilai default untuk entry baru.
        Entry yang sudah ada — termasuk hasil load state persisten — tidak
        ditimpa, supaya operator yang sengaja mematikan auto-restart tidak
        dinyalakan ulang tiap web console restart.
        """
        existing = self._services.get(service_name)
        if existing is None:
            self._services[service_name] = {
                "process": None,
                "pid": None,
                "start_time": None,
                "auto_restart": auto_restart,
                "instrument_id": instrument_id,
                "log_file": None,
            }
            return

        # Lengkapi instrument_id kalau state lama menyimpannya sebagai null.
        if existing.get("instrument_id") is None and instrument_id is not None:
            existing["instrument_id"] = instrument_id

    def register_instrument_services(
        self,
        instrument_ids: list[int],
        lis_bridge_ids: list[int] | None = None,
    ):
        """
        Register service per-alat berdasarkan list instrument IDs.

        - tcp_<id>        → untuk semua alat aktif
        - lis_bridge_<id> → hanya untuk alat di lis_bridge_ids (yaitu yang
          lis_bridge_enabled=1). Tanpa registrasi ini, bridge tidak muncul
          di GET /api/services sehingga UI tidak punya tombol Start-nya.

        Default auto_restart=True: alat yang is_active=1 di DB memang
        diharapkan selalu tersambung, jadi service-nya harus nyala sendiri
        setelah reboot maupun saat alat baru ditambahkan.
        """
        for iid in instrument_ids:
            self.register_service(f"tcp_{iid}", instrument_id=iid, auto_restart=True)

        for iid in lis_bridge_ids or []:
            self.register_service(
                f"lis_bridge_{iid}", instrument_id=iid, auto_restart=True
            )

    def ensure_core_services(self):
        """Pastikan core services (result_sender, order_receiver) terdaftar."""
        self.register_service("result_sender")
        self.register_service("order_receiver")
