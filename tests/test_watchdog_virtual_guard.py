"""
Regression: virtual service (__comm) tidak boleh dikontrol oleh watchdog,
dan tidak boleh bikin orphan entry di _services lewat _cleanup_service.
"""
from services.web_console.watchdog import ServiceWatchdog


def test_is_virtual_service_detects_comm_suffix():
    assert ServiceWatchdog._is_virtual_service("tcp_1__comm") is True
    assert ServiceWatchdog._is_virtual_service("tcp_42__comm") is True


def test_is_virtual_service_passes_real_services():
    assert ServiceWatchdog._is_virtual_service("tcp_1") is False
    assert ServiceWatchdog._is_virtual_service("result_sender") is False
    assert ServiceWatchdog._is_virtual_service("lis_bridge_3") is False
    assert ServiceWatchdog._is_virtual_service("order_receiver") is False
