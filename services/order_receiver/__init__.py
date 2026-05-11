"""
services/order_receiver/ — OrderReceiverService untuk MidLab

FastAPI service yang menerima order dari LIS via REST API,
validasi sesuai OrderObject schema, dan simpan ke tbl_order.
"""

from services.order_receiver.api import app

__all__ = ["app"]
