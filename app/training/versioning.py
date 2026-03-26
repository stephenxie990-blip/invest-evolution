from __future__ import annotations

from copy import deepcopy
from typing import Any

from invest.research.contracts import stable_hash
from invest.shared.model_governance import normalize_config_ref


def _copy_dict(value: Any) -> dict[str, Any]:
    return deepcopy(dict(value or {}))


def _resolve_agent_weights(controller: Any) -> dict[str, Any]:
    model = getattr(controller, "investment_model", None)
    if model is None:
        return {}
    config_section = getattr(model, "config_section", None)
    if callable(config_section):
        try:
            return _copy_dict(config_section("agent_weights", {}) or {})
        except Exception:
            return {}
    return _copy_dict(getattr(model, "agent_weights", {}) or {})


def build_runtime_identity(
    *,
    model_name: str,
    config_ref: str,
    runtime_params: dict[str, Any] | None = None,
    agent_weights: dict[str, Any] | None = None,
) -> dict[str, Any]:
    signature = {
        "model_name": str(model_name or ""),
        "config_ref": normalize_config_ref(config_ref),
        "runtime_params": _copy_dict(runtime_params or {}),
        "agent_weights": _copy_dict(agent_weights or {}),
    }
    runtime_fingerprint = stable_hash(signature)
    return {
        "version_id": f"version_{runtime_fingerprint[:16]}",
        "runtime_fingerprint": runtime_fingerprint,
        "signature": signature,
    }


def resolve_active_runtime_identity(
    controller: Any,
    *,
    model_name: str,
    config_ref: str,
    runtime_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return build_runtime_identity(
        model_name=model_name,
        config_ref=config_ref,
        runtime_params=runtime_params,
        agent_weights=_resolve_agent_weights(controller),
    )


def build_candidate_identity(
    *,
    config_ref: str,
    config_payload: dict[str, Any] | None = None,
    model_name: str = "",
) -> dict[str, Any]:
    payload = _copy_dict(config_payload or {})
    signature = {
        "model_name": str(model_name or payload.get("kind") or payload.get("name") or ""),
        "config_ref": normalize_config_ref(config_ref),
        "params": _copy_dict(payload.get("params") or {}),
        "risk": _copy_dict(payload.get("risk") or {}),
        "summary_scoring": _copy_dict(
            payload.get("summary_scoring")
            or payload.get("scoring")
            or {}
        ),
        "agent_weights": _copy_dict(payload.get("agent_weights") or {}),
    }
    runtime_fingerprint = stable_hash(signature)
    return {
        "version_id": f"version_{runtime_fingerprint[:16]}",
        "runtime_fingerprint": runtime_fingerprint,
        "signature": signature,
    }

