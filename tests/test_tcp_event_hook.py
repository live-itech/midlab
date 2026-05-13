"""Smoke-level: hook is wired."""
def test_enqueue_imported_in_tcp_service():
    from services.tcp_socket import service as tcp_svc
    assert hasattr(tcp_svc, "enqueue_lis_event"), (
        "TCPSocketService harus import enqueue_lis_event setelah Task 13"
    )
