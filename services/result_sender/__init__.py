"""
services/result_sender/ — ResultSenderService untuk MidLab

Service yang poll tbl_result (status=pending) secara periodik,
kirim result_json ke LIS REST API, dan update send_status di database.
"""

from services.result_sender.service import ResultSenderService

__all__ = ["ResultSenderService"]
