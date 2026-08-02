"""
Autostart service alat setelah server reboot.

Bug: state watchdog disimpan di /run/midlab (tmpfs) sehingga hilang tiap
reboot. Akibatnya flag auto_restart semua service ikut hilang, monitor loop
melewati semuanya, dan service tcp_<id>/lis_bridge_<id> tidak pernah nyala
lagi sampai dipencet Start manual di UI.

Yang diuji:
- state file ditulis ke direktori persisten, bukan RUN_DIR
- state lama di RUN_DIR ikut termigrasi supaya flag existing tidak hilang
- service alat default auto_restart=True, tapi state persisten tetap menang
- autostart menyalakan yang mati dan tidak menduplikasi yang sudah jalan
"""
import json
import os

from services.web_console.watchdog import ServiceWatchdog


def _watchdog(tmp_path):
    """Watchdog dengan run/state dir terisolasi per-test."""
    return ServiceWatchdog(
        run_dir=str(tmp_path / "run"),
        state_dir=str(tmp_path / "state"),
    )


# ============================================================
# Persistensi state
# ============================================================

def test_state_file_terpisah_dari_run_dir(tmp_path):
    """State harus di state_dir (persisten), bukan run_dir (tmpfs)."""
    w = _watchdog(tmp_path)

    assert w._state_file == str(tmp_path / "state" / "watchdog_state.json")
    assert not w._state_file.startswith(str(tmp_path / "run"))


def test_auto_restart_bertahan_setelah_watchdog_dibuat_ulang(tmp_path):
    """Simulasi reboot: instance baru harus tetap lihat auto_restart=True."""
    w1 = _watchdog(tmp_path)
    w1._services.clear()
    w1.register_service("tcp_1", instrument_id=1)
    w1.set_auto_restart("tcp_1", True)

    w2 = _watchdog(tmp_path)

    assert w2._services["tcp_1"]["auto_restart"] is True
    assert w2._services["tcp_1"]["instrument_id"] == 1


def test_state_lama_di_run_dir_dimigrasi(tmp_path):
    """Deployment existing: state di RUN_DIR harus ikut terbaca + dipindah."""
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    (run_dir / "watchdog_state.json").write_text(
        json.dumps({"tcp_7": {"auto_restart": True, "instrument_id": 7}})
    )

    w = _watchdog(tmp_path)

    assert w._services["tcp_7"]["auto_restart"] is True
    # sudah dipindah ke lokasi persisten
    assert os.path.exists(w._state_file)


def test_state_baru_menang_atas_state_lama(tmp_path):
    """Kalau dua-duanya ada, yang persisten yang dipakai."""
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    (run_dir / "watchdog_state.json").write_text(
        json.dumps({"tcp_1": {"auto_restart": False, "instrument_id": 1}})
    )
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    (state_dir / "watchdog_state.json").write_text(
        json.dumps({"tcp_1": {"auto_restart": True, "instrument_id": 1}})
    )

    w = _watchdog(tmp_path)

    assert w._services["tcp_1"]["auto_restart"] is True


# ============================================================
# Default auto_restart untuk service alat
# ============================================================

def test_service_alat_default_auto_restart_true(tmp_path):
    """Alat aktif di DB harus nyala sendiri tanpa perlu toggle manual."""
    w = _watchdog(tmp_path)
    w._services.clear()
    w.register_instrument_services([1, 2], lis_bridge_ids=[2])

    assert w._services["tcp_1"]["auto_restart"] is True
    assert w._services["tcp_2"]["auto_restart"] is True
    assert w._services["lis_bridge_2"]["auto_restart"] is True


def test_core_service_legacy_tetap_default_false(tmp_path):
    """result_sender/order_receiver legacy tidak boleh ikut nyala sendiri."""
    w = _watchdog(tmp_path)
    w._services.clear()
    w.ensure_core_services()

    assert w._services["result_sender"]["auto_restart"] is False
    assert w._services["order_receiver"]["auto_restart"] is False


def test_auto_restart_false_persisten_tidak_ditimpa_default(tmp_path):
    """Operator sengaja matikan auto-restart → jangan dinyalakan lagi."""
    w1 = _watchdog(tmp_path)
    w1._services.clear()
    w1.register_instrument_services([1])
    w1.set_auto_restart("tcp_1", False)

    w2 = _watchdog(tmp_path)
    w2.register_instrument_services([1])

    assert w2._services["tcp_1"]["auto_restart"] is False


def test_register_service_melengkapi_instrument_id_kosong(tmp_path):
    """State lama menyimpan instrument_id=null; registrasi harus mengisinya."""
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    (state_dir / "watchdog_state.json").write_text(
        json.dumps({"lis_bridge_3": {"auto_restart": True, "instrument_id": None}})
    )

    w = _watchdog(tmp_path)
    w.register_instrument_services([3], lis_bridge_ids=[3])

    assert w._services["lis_bridge_3"]["instrument_id"] == 3


# ============================================================
# Autostart
# ============================================================

def test_autostart_menyalakan_service_yang_auto_restart(tmp_path):
    w = _watchdog(tmp_path)
    w._services.clear()
    w.register_service("tcp_1", instrument_id=1, auto_restart=True)
    w.register_service("tcp_2", instrument_id=2, auto_restart=False)

    started = []

    def fake_start(name, instrument_id=None):
        started.append(name)
        return {"success": True, "pid": 4242, "message": "Started (PID 4242)"}

    w.start_service = fake_start
    w.autostart_enabled_services()

    assert started == ["tcp_1"]


def test_autostart_melewati_virtual_service(tmp_path):
    w = _watchdog(tmp_path)
    w._services.clear()
    w._services["tcp_1__comm"] = {
        "process": None, "pid": None, "start_time": None,
        "auto_restart": True, "instrument_id": 1, "log_file": None,
    }

    started = []
    w.start_service = lambda name, instrument_id=None: started.append(name)
    w.autostart_enabled_services()

    assert started == []


def test_adopt_proses_yang_masih_hidup_dari_pid_file(tmp_path):
    """
    Web console restart sendiri (Restart=always) sementara service alat masih
    hidup — harus di-adopt, bukan di-spawn ulang jadi dobel.
    """
    w = _watchdog(tmp_path)
    w._services.clear()
    w.register_service("tcp_1", instrument_id=1, auto_restart=True)
    w._write_pid_file("tcp_1", os.getpid())
    w._read_proc_cmdline = lambda pid: (
        "/opt/midlab/.venv/bin/python -m services.tcp_socket.main --instrument-id 1"
    )

    started = []
    w.start_service = lambda name, instrument_id=None: started.append(name)
    w.autostart_enabled_services()

    assert started == []
    assert w._services["tcp_1"]["pid"] == os.getpid()
    assert w.get_status("tcp_1")["running"] is True


def test_adopt_tolak_pid_yang_sudah_dipakai_proses_lain(tmp_path):
    """PID reuse setelah reboot: cmdline tidak cocok → jangan di-adopt."""
    w = _watchdog(tmp_path)
    w._services.clear()
    w.register_service("tcp_1", instrument_id=1, auto_restart=True)
    w._write_pid_file("tcp_1", os.getpid())
    w._read_proc_cmdline = lambda pid: "/usr/sbin/sshd -D"

    started = []
    w.start_service = lambda name, instrument_id=None: started.append(name)
    w.autostart_enabled_services()

    assert started == ["tcp_1"]


def test_adopt_tidak_tertukar_antar_instrument(tmp_path):
    """--instrument-id 1 tidak boleh match proses --instrument-id 11."""
    w = _watchdog(tmp_path)
    w._services.clear()
    w.register_service("tcp_1", instrument_id=1, auto_restart=True)
    w._write_pid_file("tcp_1", os.getpid())
    w._read_proc_cmdline = lambda pid: (
        "/opt/midlab/.venv/bin/python -m services.tcp_socket.main --instrument-id 11"
    )

    assert w._adopt_running_process("tcp_1") is False
