"""Unit test sidik jari isi klinis hasil pemeriksaan — murni, tanpa DB."""
from lib.fingerprint import result_fingerprint


def _payload(sample_id="ABC123", results=None, **extra):
    """Payload minimal berbentuk ResultObject.to_dict()."""
    base = {
        "mid_version": "1.0",
        "instrument_id": 1,
        "protocol": "HL7_MINDRAY_BC5150",
        "message_id": "msg-0001",
        "message_datetime": "2026-08-10T10:30:00+07:00",
        "patient": {"patient_id": "P1", "name": "BUDI"},
        "specimen": {"sample_id": sample_id},
        "order": {"order_id": "", "panel": ""},
        "results": results if results is not None else [
            {"test_code": "WBC", "value": "5.2", "unit": "10^3/uL", "flag": "N",
             "reference_range": "4.0-10.0", "status": "F"},
            {"test_code": "RBC", "value": "4.1", "unit": "10^6/uL", "flag": "N",
             "reference_range": "3.5-5.5", "status": "F"},
        ],
        "comments": [],
        "parse_errors": [],
    }
    base.update(extra)
    return base


def test_payload_identik_menghasilkan_sidik_jari_sama():
    assert result_fingerprint(_payload()) == result_fingerprint(_payload())


def test_urutan_tes_dibalik_tetap_sama():
    a = _payload()
    b = _payload(results=list(reversed(a["results"])))
    assert result_fingerprint(a) == result_fingerprint(b)


def test_metadata_transmisi_diabaikan():
    a = _payload()
    b = _payload(message_id="msg-9999",
                 message_datetime="2026-08-10T10:35:00+07:00")
    assert result_fingerprint(a) == result_fingerprint(b)


def test_reference_range_dan_status_diabaikan():
    a = _payload()
    b = _payload()
    b["results"][0]["reference_range"] = "4.5-11.0"
    b["results"][0]["status"] = "P"
    assert result_fingerprint(a) == result_fingerprint(b)


def test_nilai_tes_berubah_menghasilkan_sidik_jari_berbeda():
    a = _payload()
    b = _payload()
    b["results"][0]["value"] = "6.0"
    assert result_fingerprint(a) != result_fingerprint(b)


def test_flag_berubah_menghasilkan_sidik_jari_berbeda():
    a = _payload()
    b = _payload()
    b["results"][0]["flag"] = "H"
    assert result_fingerprint(a) != result_fingerprint(b)


def test_sample_id_berbeda_menghasilkan_sidik_jari_berbeda():
    assert result_fingerprint(_payload(sample_id="ABC123")) != \
        result_fingerprint(_payload(sample_id="XYZ999"))


def test_sample_id_case_sensitive():
    """Huruf besar/kecil tidak diseragamkan — sebagian alat pakai ID case-sensitive."""
    assert result_fingerprint(_payload(sample_id="abc123")) != \
        result_fingerprint(_payload(sample_id="ABC123"))


def test_spasi_berlebih_diabaikan():
    a = _payload()
    b = _payload(sample_id="  ABC123 ")
    b["results"][0]["value"] = " 5.2 "
    assert result_fingerprint(a) == result_fingerprint(b)


def test_input_cacat_tidak_melempar_exception():
    for bad in (None, {}, {"specimen": None}, {"results": None},
                {"results": "bukan list"}, "bukan dict", 42):
        fp = result_fingerprint(bad)
        assert len(fp) == 64
        assert fp == fp.lower()


def test_hasil_tanpa_sample_id_dan_tanpa_tes_stabil():
    assert result_fingerprint({}) == result_fingerprint(None)


def test_nilai_numerik_dikonversi_lewat_str():
    """Driver bisa mengirim angka sebagai float; str(5.2) == '5.2'."""
    a = _payload()
    b = _payload()
    b["results"][0]["value"] = 5.2
    assert result_fingerprint(a) == result_fingerprint(b)
