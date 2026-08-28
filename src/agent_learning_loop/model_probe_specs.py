"""Load the fixed M7C-A task, model, and generation registrations."""

from __future__ import annotations

from importlib.resources import files

from pydantic import ValidationError

from agent_learning_loop.model_probe_schemas import LocalModelSpec, ProbeContractSpec


class ModelProbeSpecError(ValueError):
    """The packaged M7C-A registration is absent or does not match code truth."""


CANONICAL_PROBE_CONTRACT_FINGERPRINT = (
    "c5ef7121a32b73f47d05af72f189de765bb8366de00a2b92d10a7f574425ee38"
)


def load_probe_contract() -> ProbeContractSpec:
    """Load the only allowed M7C-A contract and reject a jointly edited resource."""
    try:
        resource = files("agent_learning_loop").joinpath(
            "model_probe_specs", "m7ca-v1.json"
        )
        contract = ProbeContractSpec.model_validate_json(
            resource.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, ValidationError) as exc:
        raise ModelProbeSpecError("invalid_model_probe_spec") from exc
    if contract.contract_fingerprint != CANONICAL_PROBE_CONTRACT_FINGERPRINT:
        raise ModelProbeSpecError("model_probe_spec_identity")
    return contract


def select_model_spec(model_id: str) -> LocalModelSpec:
    contract = load_probe_contract()
    selected = next((item for item in contract.models if item.model_id == model_id), None)
    if selected is None:
        raise ModelProbeSpecError("unknown_model_spec")
    return selected
