from lib.log_resolver import resolve_log_path


def test_resolve_app_log():
    assert resolve_log_path("tcp_3").endswith("tcp_3.log")


def test_resolve_comm_log():
    assert resolve_log_path("tcp_3__comm").endswith("tcp_3.comm.log")


def test_resolve_other_service():
    assert resolve_log_path("result_sender").endswith("result_sender.log")
