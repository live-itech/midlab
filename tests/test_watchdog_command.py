from services.web_console.watchdog import ServiceWatchdog


def test_build_command_lis_bridge():
    w = ServiceWatchdog()
    cmd = w._build_command("lis_bridge_7", instrument_id=7)
    assert cmd is not None
    assert "services.lis_bridge.main" in cmd
    assert "--instrument-id" in cmd
    assert "7" in cmd


def test_build_command_lis_bridge_parse_id_from_name():
    w = ServiceWatchdog()
    cmd = w._build_command("lis_bridge_42")
    assert cmd is not None
    assert "42" in cmd
