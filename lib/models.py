"""
lib/models.py — Data Models untuk MidLab

Dataclass models untuk ResultObject dan OrderObject sesuai spesifikasi JSON di CLAUDE.md.
Digunakan oleh ProtocolModule untuk hasil parsing dan oleh OrderReceiverService untuk order.
Menggunakan dataclasses standar Python (tanpa dependency eksternal).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import List

from lib.utils import generate_message_id, format_datetime


# ============================================================
# Sub-models — komponen dari ResultObject dan OrderObject
# ============================================================

@dataclass
class PatientInfo:
    """Informasi pasien, dipakai di ResultObject dan OrderObject."""
    patient_id: str = ""
    name: str = ""
    dob: str = ""
    gender: str = ""
    physician: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> PatientInfo:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class OrderPatientInfo:
    """Informasi pasien khusus untuk OrderObject (tanpa physician)."""
    patient_id: str = ""
    name: str = ""
    dob: str = ""
    gender: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> OrderPatientInfo:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class SpecimenInfo:
    """Informasi spesimen/sampel untuk ResultObject."""
    sample_id: str = ""
    sample_type: str = ""
    collected_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> SpecimenInfo:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class OrderSpecimenInfo:
    """Informasi spesimen khusus untuk OrderObject (dengan priority)."""
    sample_id: str = ""
    sample_type: str = ""
    priority: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> OrderSpecimenInfo:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class OrderInfo:
    """Informasi order dalam ResultObject."""
    order_id: str = ""
    panel: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> OrderInfo:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class TestResult:
    """Satu baris hasil pemeriksaan lab."""
    test_code: str = ""
    test_name: str = ""
    value: str = ""
    unit: str = ""
    reference_range: str = ""
    flag: str = ""
    status: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> TestResult:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class TestOrder:
    """Satu item tes yang di-order."""
    test_code: str = ""
    test_name: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> TestOrder:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# ============================================================
# ResultObject — output parsing dari ProtocolModule
# ============================================================

@dataclass
class ResultObject:
    """
    Representasi hasil pemeriksaan dari alat lab.
    Disimpan di tbl_result.result_json.
    """
    mid_version: str = "1.0"
    instrument_id: int = 0
    protocol: str = ""
    message_id: str = field(default_factory=generate_message_id)
    message_datetime: str = field(default_factory=format_datetime)
    patient: PatientInfo = field(default_factory=PatientInfo)
    specimen: SpecimenInfo = field(default_factory=SpecimenInfo)
    order: OrderInfo = field(default_factory=OrderInfo)
    results: List[TestResult] = field(default_factory=list)
    comments: List[str] = field(default_factory=list)
    parse_errors: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Konversi ke dictionary untuk disimpan sebagai JSON di database."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> ResultObject:
        """Buat ResultObject dari dictionary (misal dari JSON database)."""
        d = dict(data)
        # Rekonstruksi nested objects
        if "patient" in d and isinstance(d["patient"], dict):
            d["patient"] = PatientInfo.from_dict(d["patient"])
        if "specimen" in d and isinstance(d["specimen"], dict):
            d["specimen"] = SpecimenInfo.from_dict(d["specimen"])
        if "order" in d and isinstance(d["order"], dict):
            d["order"] = OrderInfo.from_dict(d["order"])
        if "results" in d and isinstance(d["results"], list):
            d["results"] = [
                TestResult.from_dict(r) if isinstance(r, dict) else r
                for r in d["results"]
            ]
        # Filter hanya field yang valid
        valid_keys = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in valid_keys})


# ============================================================
# OrderObject — order dari LIS untuk dikirim ke alat
# ============================================================

@dataclass
class OrderObject:
    """
    Representasi order pemeriksaan dari LIS.
    Disimpan di tbl_order.order_json.
    """
    mid_version: str = "1.0"
    order_id: str = ""
    instrument_id: int = 0
    request_datetime: str = field(default_factory=format_datetime)
    patient: OrderPatientInfo = field(default_factory=OrderPatientInfo)
    specimen: OrderSpecimenInfo = field(default_factory=OrderSpecimenInfo)
    tests: List[TestOrder] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Konversi ke dictionary untuk disimpan sebagai JSON di database."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> OrderObject:
        """Buat OrderObject dari dictionary (misal dari JSON database)."""
        d = dict(data)
        # Rekonstruksi nested objects
        if "patient" in d and isinstance(d["patient"], dict):
            d["patient"] = OrderPatientInfo.from_dict(d["patient"])
        if "specimen" in d and isinstance(d["specimen"], dict):
            d["specimen"] = OrderSpecimenInfo.from_dict(d["specimen"])
        if "tests" in d and isinstance(d["tests"], list):
            d["tests"] = [
                TestOrder.from_dict(t) if isinstance(t, dict) else t
                for t in d["tests"]
            ]
        # Filter hanya field yang valid
        valid_keys = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in valid_keys})


if __name__ == "__main__":
    # Test: ResultObject.comments default is empty list
    r = ResultObject(instrument_id=1, protocol="COBAS_C111")
    assert r.comments == [], f"expected [], got {r.comments}"
    print("OK: ResultObject.comments default is []")

    # Test: comments survives to_dict / from_dict roundtrip
    r.comments.append("order: collected by night shift")
    d = r.to_dict()
    assert d["comments"] == ["order: collected by night shift"]
    r2 = ResultObject.from_dict(d)
    assert r2.comments == ["order: collected by night shift"]
    print("OK: comments roundtrip works")

    print("=== ResultObject.comments tests PASSED ===")
