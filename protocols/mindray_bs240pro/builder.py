"""
protocols/mindray_bs240pro/builder.py — Builder HL7 Mindray BS-240Pro / BS-360E / BS-240E

Turunan MindrayBuilder (BS-200E). Yang berbeda: model default di MSH-6/QRF-1.
"""

from protocols.mindray_bs200e.builder import MindrayBuilder
from protocols.mindray_bs240pro.constants import DEFAULT_MODEL


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
        return super()._build_msh(event, control_id, self._with_model(context), result_type)

    def _build_qrf(self, context: dict) -> str:
        return super()._build_qrf(self._with_model(context))
