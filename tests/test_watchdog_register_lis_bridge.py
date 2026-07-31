"""
Registrasi lis_bridge_<id> ke watchdog.

Tanpa ini service lis_bridge tidak pernah muncul di GET /api/services,
jadi halaman Services tidak punya tombol Start/Stop untuk bridge dan
result menumpuk di tbl_result dengan send_status=pending.
"""
from services.web_console.watchdog import ServiceWatchdog


def test_register_instrument_services_registers_lis_bridge():
    w = ServiceWatchdog()
    w._services.clear()
    w.register_instrument_services([1, 2], lis_bridge_ids=[1, 2])

    assert "lis_bridge_1" in w._services
    assert "lis_bridge_2" in w._services
    assert w._services["lis_bridge_1"]["instrument_id"] == 1
    assert w._services["lis_bridge_2"]["instrument_id"] == 2


def test_register_instrument_services_still_registers_tcp():
    w = ServiceWatchdog()
    w._services.clear()
    w.register_instrument_services([3], lis_bridge_ids=[3])

    assert "tcp_3" in w._services
    assert w._services["tcp_3"]["instrument_id"] == 3


def test_lis_bridge_only_for_enabled_instruments():
    """Alat dengan lis_bridge_enabled=0 tidak boleh dapat entry bridge."""
    w = ServiceWatchdog()
    w._services.clear()
    # instrument 4 dan 5 aktif, tapi cuma 4 yang bridge-nya enabled
    w.register_instrument_services([4, 5], lis_bridge_ids=[4])

    assert "tcp_4" in w._services
    assert "tcp_5" in w._services
    assert "lis_bridge_4" in w._services
    assert "lis_bridge_5" not in w._services


def test_lis_bridge_ids_defaults_to_none():
    """Backward compat: pemanggilan lama tanpa lis_bridge_ids tetap jalan."""
    w = ServiceWatchdog()
    w._services.clear()
    w.register_instrument_services([6])

    assert "tcp_6" in w._services
    assert "lis_bridge_6" not in w._services
