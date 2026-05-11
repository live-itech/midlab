"""
lib/config.py — Configuration Loader untuk MidLab

Singleton class yang memuat konfigurasi dari /etc/midlab/config.yaml.
Mendukung akses nested key dengan dot notation, misal: Config.get("database.host")
"""

import os
import yaml
import threading


# Path default config file
DEFAULT_CONFIG_PATH = "/etc/midlab/config.yaml"


class Config:
    """
    Singleton config loader.

    Contoh penggunaan:
        config = Config()
        db_host = config.get("database.host")
        poll_interval = config.get("result_sender.poll_interval", default=5)
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, config_path: str = None):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self, config_path: str = None):
        if self._initialized:
            return
        self._config_path = config_path or os.environ.get(
            "MIDLAB_CONFIG", DEFAULT_CONFIG_PATH
        )
        self._data = {}
        self._load()
        self._initialized = True

    def _load(self):
        """Baca dan parse file YAML konfigurasi."""
        try:
            with open(self._config_path, "r") as f:
                self._data = yaml.safe_load(f) or {}
        except FileNotFoundError:
            # Development-friendly default: jika config tidak ada, gunakan default values
            self._data = self._default_config()
        except yaml.YAMLError as e:
            raise ValueError(f"Config YAML tidak valid: {e}")

    def _default_config(self) -> dict:
        """
        Return default configuration untuk development/testing.

        Ini digunakan saat config file tidak ditemukan (misal /etc/midlab/config.yaml
        belum ada), sehingga logger dan service masih bisa berjalan.
        """
        return {
            "logging": {
                "level": "INFO",
                "max_bytes": 10485760,
                "backup_count": 5,
            },
            "database": {
                "host": "127.0.0.1",
                "port": 3306,
                "user": "midlab",
                "password": "midlab",
                "database": "midlab_db",
                "pool_size": 10,
                "pool_recycle": 3600,
            },
            "server": {
                "host": "0.0.0.0",
                "debug": False,
            },
            "result_sender": {
                "poll_interval": 5,
                "lis_api_url": "http://localhost:8080/api/results",
                "lis_api_key": "",
                "retry_max": 3,
            },
            "order_receiver": {
                "port": 8001,
            },
            "web_console": {
                "port": 8000,
            },
        }

    def get(self, key: str, default=None):
        """
        Akses config value dengan dot notation.

        Args:
            key: Dot-separated key, misal "database.host"
            default: Nilai default jika key tidak ditemukan

        Returns:
            Nilai konfigurasi atau default
        """
        keys = key.split(".")
        value = self._data
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
            else:
                return default
            if value is None:
                return default
        return value

    def reload(self):
        """Reload konfigurasi dari file (berguna saat runtime)."""
        self._load()

    @property
    def data(self) -> dict:
        """Akses langsung ke seluruh dictionary konfigurasi."""
        return self._data

    @classmethod
    def reset(cls):
        """Reset singleton instance (untuk testing)."""
        with cls._lock:
            cls._instance = None
