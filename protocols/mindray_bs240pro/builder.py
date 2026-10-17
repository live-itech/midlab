"""
protocols/mindray_bs240pro/builder.py — Builder HL7 Mindray BS-240Pro / BS-360E / BS-240E

Turunan MindrayBuilder (BS-200E). Yang berbeda:
- model default (BS-240Pro) di MSH-6/QRF-1
- MSH-15 diisi dari context `accept_ack_type` (broadcast DSR^Q03: "P")
- DSP-26 (jenis sampel) huruf awal kapital
"""

from protocols.mindray_bs200e.builder import MindrayBuilder
from protocols.mindray_bs240pro.constants import (
    DEFAULT_MODEL, FIELD_SEPARATOR, MSH15_INDEX,
    DSP_SAMPLE_TYPE, SAMPLE_TYPE_LABELS,
)


class BS240ProBuilder(MindrayBuilder):
    """Builder pesan HL7 BS-240Pro (arah LIS → alat)."""

    def _with_model(self, context: dict) -> dict:
        """
        Salinan context dengan `model` terisi. Builder induk jatuh ke model
        BS-200E bila context tidak membawa model (mis. broadcast).
        """
        ctx = dict(context or {})
        if not ctx.get("model"):
            ctx["model"] = DEFAULT_MODEL
        return ctx

    def _build_msh(self, event: str, control_id: str, context: dict,
                   result_type: str = "") -> str:
        """
        MSH induk + model default BS-240Pro. Bila context membawa
        `accept_ack_type`, nilainya ditulis ke MSH-15 (broadcast: "P").
        """
        ctx = self._with_model(context)
        msh = super()._build_msh(event, control_id, ctx, result_type)
        accept = ctx.get("accept_ack_type")
        if accept:
            fields = msh.split(FIELD_SEPARATOR)
            fields[MSH15_INDEX] = accept
            msh = FIELD_SEPARATOR.join(fields)
        return msh

    def _build_qrf(self, context: dict) -> str:
        return super()._build_qrf(self._with_model(context))

    def _build_dsp_lines(self, order: dict) -> list:
        """
        DSP induk, dengan DSP-26 (jenis sampel) berhuruf awal kapital —
        format yang terbukti diterima BS-360E di log. Jenis di luar tabel
        dikirim apa adanya (kamus jenis sampel lokal alat).
        """
        segments = super()._build_dsp_lines(order)
        raw = ((order.get("specimen") or {}).get("sample_type") or "").strip()
        label = SAMPLE_TYPE_LABELS.get(raw.lower(), raw)
        segments[DSP_SAMPLE_TYPE - 1] = self._build_dsp(DSP_SAMPLE_TYPE, label)
        return segments
