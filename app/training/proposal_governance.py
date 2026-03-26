from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from invest.shared.model_governance import (
    normalize_config_ref,
    normalize_proposal_gate_policy,
)

_TIGHTENING_DIRECTION = {
    "stop_loss_pct": "lower",
    "trailing_pct": "lower",
}


def _copy_dict(value: Any) -> dict[str, Any]:
    return deepcopy(dict(value or {}))


def _load_config_payload(controller: Any, config_ref: str | Path) -> tuple[Path, dict[str, Any]]:
    normalized_ref = normalize_config_ref(config_ref)
    model_mutator = getattr(controller, "model_mutator", None)
    if model_mutator is not None and hasattr(model_mutator, "load"):
        path, payload = model_mutator.load(normalized_ref)
        return Path(path), dict(payload or {})
    path = Path(normalized_ref)
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return path, dict(payload or {})


def _load_generation_meta(path: Path) -> dict[str, Any]:
    meta_path = path.with_suffix(".json")
    if not meta_path.exists():
        return {}
    try:
        return dict(json.loads(meta_path.read_text(encoding="utf-8")) or {})
    except Exception:
        return {}


def _resolve_baseline_config(controller: Any, config_ref: str | Path) -> tuple[Path, dict[str, Any]]:
    current_path, current_payload = _load_config_payload(controller, config_ref)
    visited = {str(current_path)}
    while True:
        meta = _load_generation_meta(current_path)
        parent_meta = dict(meta.get("parent_meta") or {})
        next_ref = normalize_config_ref(
            parent_meta.get("baseline_config_ref")
            or meta.get("parent_config")
            or ""
        )
        if not next_ref:
            return current_path, current_payload
        next_path, next_payload = _load_config_payload(controller, next_ref)
        if str(next_path) in visited:
            return current_path, current_payload
        visited.add(str(next_path))
        current_path, current_payload = next_path, next_payload


def _resolve_current_runtime_params(
    controller: Any,
    proposal_bundle: dict[str, Any],
) -> dict[str, Any]:
    snapshot = _copy_dict(proposal_bundle.get("execution_snapshot") or {})
    runtime_params = _copy_dict(snapshot.get("runtime_overrides") or {})
    if runtime_params:
        return runtime_params
    proposals = list(proposal_bundle.get("proposals") or [])
    for proposal in proposals:
        active_snapshot = _copy_dict(dict(proposal or {}).get("active_params_snapshot") or {})
        if active_snapshot:
            return active_snapshot
    return {}


def _baseline_param_lookup(config_payload: dict[str, Any], key: str) -> Any:
    params = dict(config_payload.get("params") or {})
    if key in params:
        return params.get(key)
    risk = dict(config_payload.get("risk") or {})
    if key in risk:
        return risk.get(key)
    return None


def _change_ratio(current: Any, candidate: Any, baseline: Any) -> float | None:
    try:
        current_float = float(current)
        candidate_float = float(candidate)
        baseline_float = float(baseline)
    except (TypeError, ValueError):
        return None
    if abs(baseline_float) < 1e-9:
        return None if abs(candidate_float - current_float) < 1e-9 else float("inf")
    return abs(candidate_float - current_float) / abs(baseline_float)


def _drift_ratio(candidate: Any, baseline: Any) -> float | None:
    try:
        candidate_float = float(candidate)
        baseline_float = float(baseline)
    except (TypeError, ValueError):
        return None
    if abs(baseline_float) < 1e-9:
        return None if abs(candidate_float) < 1e-9 else float("inf")
    return abs(candidate_float - baseline_float) / abs(baseline_float)


def _config_section(config_payload: dict[str, Any], *section_names: str) -> dict[str, Any]:
    for section_name in section_names:
        section = config_payload.get(section_name)
        if isinstance(section, dict):
            return _copy_dict(section)
    return {}


def _flatten_patch_leaves(patch: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    leaves: dict[str, Any] = {}
    for key, value in dict(patch or {}).items():
        key_name = str(key)
        path = f"{prefix}.{key_name}" if prefix else key_name
        if isinstance(value, dict):
            nested = _flatten_patch_leaves(value, path)
            if nested:
                leaves.update(nested)
            else:
                leaves[path] = value
        else:
            leaves[path] = value
    return leaves


def _nested_lookup(payload: dict[str, Any], dotted_path: str) -> Any:
    current: Any = payload
    for key in str(dotted_path or "").split("."):
        if not isinstance(current, dict):
            return None
        if key not in current:
            return None
        current = current.get(key)
    return current


def _nested_assign(payload: dict[str, Any], dotted_path: str, value: Any) -> None:
    current = payload
    keys = [segment for segment in str(dotted_path or "").split(".") if segment]
    if not keys:
        return
    for key in keys[:-1]:
        child = current.get(key)
        if not isinstance(child, dict):
            child = {}
            current[key] = child
        current = child
    current[keys[-1]] = deepcopy(value)


def _proposal_patch_keys(proposal_kind: str, patch: dict[str, Any]) -> list[str]:
    if proposal_kind == "scoring_adjustment":
        return sorted(_flatten_patch_leaves(patch).keys())
    return sorted(str(key) for key in dict(patch or {}).keys())


def _resolve_scope_threshold(
    section: dict[str, Any],
    *,
    nested_key: str,
    flat_keys: list[str],
    default: float,
) -> float:
    nested = dict(section.get(nested_key) or {})
    candidates = [nested.get("max_single_step_ratio_vs_baseline"), nested.get("max_ratio_vs_baseline")]
    for key in flat_keys:
        candidates.append(section.get(key))
    for value in candidates:
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return float(default)


def _scope_drift_thresholds(policy: dict[str, Any], scope_name: str) -> tuple[float, float]:
    identity_policy = dict(policy.get("identity_protection") or {})
    cumulative_policy = dict(policy.get("cumulative_drift") or {})
    if scope_name == "params":
        max_single_step_ratio = float(
            identity_policy.get("max_single_step_ratio_vs_baseline", 0.30) or 0.30
        )
        max_cumulative_ratio = float(
            cumulative_policy.get("max_param_ratio_vs_baseline", 0.50) or 0.50
        )
        return max_single_step_ratio, max_cumulative_ratio
    if scope_name == "scoring":
        return (
            _resolve_scope_threshold(
                identity_policy,
                nested_key="scoring",
                flat_keys=["max_scoring_single_step_ratio_vs_baseline"],
                default=0.30,
            ),
            _resolve_scope_threshold(
                cumulative_policy,
                nested_key="scoring",
                flat_keys=["max_scoring_ratio_vs_baseline"],
                default=0.50,
            ),
        )
    if scope_name == "agent_weights":
        return (
            _resolve_scope_threshold(
                identity_policy,
                nested_key="agent_weights",
                flat_keys=["max_agent_weight_single_step_ratio_vs_baseline"],
                default=0.30,
            ),
            _resolve_scope_threshold(
                cumulative_policy,
                nested_key="agent_weights",
                flat_keys=["max_agent_weight_ratio_vs_baseline"],
                default=0.50,
            ),
        )
    return 0.30, 0.50


def _scope_drift_reason(scope_name: str, reason: str) -> str:
    if scope_name == "params":
        return reason
    if reason == "single_step_identity_drift_exceeded":
        return f"single_step_{scope_name}_identity_drift_exceeded"
    if reason == "cumulative_identity_drift_exceeded":
        return f"cumulative_{scope_name}_identity_drift_exceeded"
    if reason == "cumulative_identity_drift_worsened":
        return f"cumulative_{scope_name}_identity_drift_worsened"
    return f"{scope_name}_{reason}"


def _evaluate_identity_drift(
    *,
    scope_name: str,
    current_value: Any,
    candidate_value: Any,
    baseline_value: Any,
    max_single_step_ratio: float,
    max_cumulative_ratio: float,
) -> tuple[dict[str, Any], str]:
    effective_current_value = baseline_value if current_value is None else current_value
    metric = {
        "baseline_value": baseline_value,
        "current_value": effective_current_value,
        "candidate_value": candidate_value,
    }
    if baseline_value is None or effective_current_value is None:
        return metric, ""
    single_step_ratio = _change_ratio(effective_current_value, candidate_value, baseline_value)
    current_drift_ratio = _drift_ratio(effective_current_value, baseline_value)
    candidate_drift_ratio = _drift_ratio(candidate_value, baseline_value)
    metric.update(
        {
            "single_step_ratio_vs_baseline": single_step_ratio,
            "current_drift_ratio_vs_baseline": current_drift_ratio,
            "candidate_drift_ratio_vs_baseline": candidate_drift_ratio,
        }
    )
    if single_step_ratio is not None and single_step_ratio > max_single_step_ratio:
        return metric, _scope_drift_reason(scope_name, "single_step_identity_drift_exceeded")
    if candidate_drift_ratio is not None and current_drift_ratio is not None:
        if current_drift_ratio <= max_cumulative_ratio < candidate_drift_ratio:
            return metric, _scope_drift_reason(scope_name, "cumulative_identity_drift_exceeded")
        if (
            current_drift_ratio > max_cumulative_ratio
            and candidate_drift_ratio > current_drift_ratio
        ):
            return metric, _scope_drift_reason(scope_name, "cumulative_identity_drift_worsened")
    return metric, ""


def _is_tightening_param_change(key: str, current: Any, candidate: Any) -> bool:
    direction = _TIGHTENING_DIRECTION.get(str(key))
    try:
        current_float = float(current)
        candidate_float = float(candidate)
    except (TypeError, ValueError):
        return False
    if direction == "lower":
        return candidate_float < current_float
    return False


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = _copy_dict(base)
    for key, value in dict(patch or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(dict(merged.get(key) or {}), value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _proposal_kind(proposal: dict[str, Any]) -> str:
    metadata = dict(proposal.get("metadata") or {})
    return str(metadata.get("proposal_kind") or "")


def _append_reason_count(counter: dict[str, int], reason: str) -> None:
    label = str(reason or "unknown").strip() or "unknown"
    counter[label] = counter.get(label, 0) + 1


def evaluate_candidate_proposal_gate(
    controller: Any,
    *,
    cycle_id: int,
    proposal_bundle: dict[str, Any],
) -> dict[str, Any]:
    policy = normalize_proposal_gate_policy(
        dict(getattr(controller, "proposal_gate_policy", {}) or {})
    )
    proposals = [
        dict(item or {})
        for item in list(proposal_bundle.get("proposals") or [])
        if str(dict(item or {}).get("target_scope") or "candidate") == "candidate"
    ]
    if not bool(policy.get("enabled", True)):
        requested_source_summary: dict[str, int] = {}
        requested_refs: list[str] = []
        filtered_params: dict[str, Any] = {}
        filtered_scoring: dict[str, Any] = {}
        filtered_agent_weights: dict[str, Any] = {}
        for proposal in proposals:
            proposal_id = str(proposal.get("proposal_id") or "")
            if proposal_id:
                requested_refs.append(proposal_id)
            source = str(proposal.get("source") or "unknown")
            requested_source_summary[source] = requested_source_summary.get(source, 0) + 1
            patch = dict(proposal.get("patch") or {})
            kind = _proposal_kind(proposal)
            if kind in {"runtime_param_adjustment", "param_adjustment"}:
                filtered_params.update(patch)
            elif kind == "scoring_adjustment":
                filtered_scoring = _deep_merge(filtered_scoring, patch)
            elif kind == "agent_weight_adjustment":
                filtered_agent_weights.update(patch)
        return {
            "approved": bool(filtered_params or filtered_scoring or filtered_agent_weights),
            "cycle_id": int(cycle_id),
            "policy": policy,
            "profit_context": {
                "is_profit": False,
                "return_pct": None,
                "benchmark_passed": False,
            },
            "baseline": {
                "config_ref": normalize_config_ref(
                    proposal_bundle.get("active_config_ref")
                    or getattr(controller, "model_config_path", "")
                    or ""
                ),
                "model_kind": "",
                "active_config_ref": normalize_config_ref(
                    proposal_bundle.get("active_config_ref")
                    or getattr(controller, "model_config_path", "")
                    or ""
                ),
            },
            "filtered_adjustments": {
                "params": filtered_params,
                "scoring": filtered_scoring,
                "agent_weights": filtered_agent_weights,
                "proposal_refs": requested_refs,
                "proposal_source_summary": requested_source_summary,
            },
            "blocked_adjustments": {
                "params": {},
                "scoring": {},
                "agent_weights": {},
            },
            "violations": [],
            "drift_summary": {
                "approved_params": {},
                "blocked_params": {},
                "approved_scoring": {},
                "blocked_scoring": {},
                "approved_agent_weights": {},
                "blocked_agent_weights": {},
                "max_single_step_ratio_vs_baseline": None,
                "max_param_drift_ratio_vs_baseline": None,
                "max_scoring_single_step_ratio_vs_baseline": None,
                "max_scoring_drift_ratio_vs_baseline": None,
                "max_agent_weight_single_step_ratio_vs_baseline": None,
                "max_agent_weight_drift_ratio_vs_baseline": None,
            },
            "approved_proposals": [
                {
                    "proposal_id": str(item.get("proposal_id") or ""),
                    "source": str(item.get("source") or "unknown"),
                    "proposal_kind": _proposal_kind(item),
                    "requested_keys": _proposal_patch_keys(
                        _proposal_kind(item),
                        dict(item.get("patch") or {}),
                    ),
                    "approved_keys": _proposal_patch_keys(
                        _proposal_kind(item),
                        dict(item.get("patch") or {}),
                    ),
                    "blocked_keys": [],
                    "status": "approved",
                    "block_reasons": [],
                }
                for item in proposals
            ],
            "blocked_proposals": [],
            "proposal_summary": {
                "requested_proposal_count": len(proposals),
                "approved_proposal_count": len(proposals),
                "blocked_proposal_count": 0,
                "partially_blocked_proposal_count": 0,
                "requested_proposal_refs": requested_refs,
                "approved_proposal_refs": requested_refs,
                "blocked_proposal_refs": [],
                "requested_source_summary": requested_source_summary,
                "approved_source_summary": requested_source_summary,
                "blocked_source_summary": {},
                "block_reason_counts": {},
                "top_block_reasons": [],
            },
        }
    filtered_params: dict[str, Any] = {}
    filtered_scoring: dict[str, Any] = {}
    filtered_agent_weights: dict[str, Any] = {}
    blocked_params: dict[str, Any] = {}
    blocked_scoring: dict[str, Any] = {}
    blocked_agent_weights: dict[str, Any] = {}
    violations: list[dict[str, Any]] = []

    active_config_ref = normalize_config_ref(
        proposal_bundle.get("active_config_ref")
        or getattr(controller, "model_config_path", "")
        or ""
    )
    current_path, current_payload = _load_config_payload(controller, active_config_ref)
    baseline_path, baseline_payload = _resolve_baseline_config(controller, active_config_ref)
    current_params = _resolve_current_runtime_params(controller, proposal_bundle)
    effective_params = _copy_dict(current_params)
    current_scoring = _config_section(current_payload, "summary_scoring", "scoring")
    baseline_scoring = _config_section(baseline_payload, "summary_scoring", "scoring")
    effective_scoring = _copy_dict(current_scoring)
    current_agent_weights = _config_section(current_payload, "agent_weights")
    baseline_agent_weights = _config_section(baseline_payload, "agent_weights")
    effective_agent_weights = _copy_dict(current_agent_weights)
    protected_params = {
        str(item)
        for item in list(policy.get("protected_params") or [])
        if str(item).strip()
    }
    profitable_cycle_policy = dict(policy.get("profitable_cycle") or {})
    allowed_safety_tightening_params = {
        str(item)
        for item in list(
            profitable_cycle_policy.get("allowed_safety_tightening_params") or []
        )
        if str(item).strip()
    }
    execution_snapshot = _copy_dict(proposal_bundle.get("execution_snapshot") or {})
    return_pct = execution_snapshot.get("return_pct")
    is_profit = bool(execution_snapshot.get("is_profit", False))
    if not is_profit:
        try:
            is_profit = float(return_pct or 0.0) > 0.0
        except (TypeError, ValueError):
            is_profit = False

    approved_param_metrics: dict[str, Any] = {}
    blocked_param_metrics: dict[str, Any] = {}
    approved_scoring_metrics: dict[str, Any] = {}
    blocked_scoring_metrics: dict[str, Any] = {}
    approved_agent_weight_metrics: dict[str, Any] = {}
    blocked_agent_weight_metrics: dict[str, Any] = {}
    max_single_step_ratio, max_cumulative_ratio = _scope_drift_thresholds(policy, "params")
    scoring_max_single_step_ratio, scoring_max_cumulative_ratio = _scope_drift_thresholds(
        policy,
        "scoring",
    )
    agent_weight_max_single_step_ratio, agent_weight_max_cumulative_ratio = _scope_drift_thresholds(
        policy,
        "agent_weights",
    )
    approved_proposals: list[dict[str, Any]] = []
    blocked_proposals: list[dict[str, Any]] = []
    requested_source_summary: dict[str, int] = {}
    approved_source_summary: dict[str, int] = {}
    blocked_source_summary: dict[str, int] = {}
    block_reason_counts: dict[str, int] = {}
    approved_proposal_refs: list[str] = []
    blocked_proposal_refs: list[str] = []
    partial_blocked_count = 0

    for proposal in proposals:
        proposal_id = str(proposal.get("proposal_id") or "")
        source = str(proposal.get("source") or "unknown")
        proposal_kind = _proposal_kind(proposal)
        patch = dict(proposal.get("patch") or {})
        if not patch:
            continue
        requested_source_summary[source] = requested_source_summary.get(source, 0) + 1
        requested_keys = _proposal_patch_keys(proposal_kind, patch)
        approved_patch: dict[str, Any] = {}
        blocked_patch: dict[str, Any] = {}
        proposal_block_reasons: list[str] = []

        if proposal_kind in {"runtime_param_adjustment", "param_adjustment"}:
            for key, candidate_value in patch.items():
                current_value = effective_params.get(key)
                baseline_value = _baseline_param_lookup(baseline_payload, key)
                metric = {
                    "baseline_value": baseline_value,
                    "current_value": current_value,
                    "candidate_value": candidate_value,
                }
                block_reason = ""
                if is_profit:
                    if key in allowed_safety_tightening_params:
                        if not _is_tightening_param_change(key, current_value, candidate_value):
                            block_reason = "profitable_cycle_requires_safety_tightening"
                    elif bool(profitable_cycle_policy.get("freeze_behavior_params", True)):
                        block_reason = "profitable_cycle_behavior_frozen"
                if (
                    not block_reason
                    and key in protected_params
                    and baseline_value is not None
                    and current_value is not None
                ):
                    single_step_ratio = _change_ratio(current_value, candidate_value, baseline_value)
                    current_drift_ratio = _drift_ratio(current_value, baseline_value)
                    candidate_drift_ratio = _drift_ratio(candidate_value, baseline_value)
                    metric.update(
                        {
                            "single_step_ratio_vs_baseline": single_step_ratio,
                            "current_drift_ratio_vs_baseline": current_drift_ratio,
                            "candidate_drift_ratio_vs_baseline": candidate_drift_ratio,
                        }
                    )
                    if (
                        single_step_ratio is not None
                        and single_step_ratio > max_single_step_ratio
                    ):
                        block_reason = "single_step_identity_drift_exceeded"
                    elif candidate_drift_ratio is not None and current_drift_ratio is not None:
                        if current_drift_ratio <= max_cumulative_ratio < candidate_drift_ratio:
                            block_reason = "cumulative_identity_drift_exceeded"
                        elif (
                            current_drift_ratio > max_cumulative_ratio
                            and candidate_drift_ratio > current_drift_ratio
                        ):
                            block_reason = "cumulative_identity_drift_worsened"
                if block_reason:
                    blocked_patch[key] = candidate_value
                    blocked_params[key] = candidate_value
                    blocked_param_metrics[key] = dict(metric, block_reason=block_reason)
                    proposal_block_reasons.append(block_reason)
                    _append_reason_count(block_reason_counts, block_reason)
                    violations.append(
                        {
                            "type": block_reason,
                            "proposal_id": proposal_id,
                            "source": source,
                            "param": key,
                            "current_value": current_value,
                            "candidate_value": candidate_value,
                        }
                    )
                    continue
                approved_patch[key] = candidate_value
                filtered_params[key] = candidate_value
                effective_params[key] = candidate_value
                approved_param_metrics[key] = metric
        elif proposal_kind == "scoring_adjustment":
            if bool(profitable_cycle_policy.get("block_scoring_adjustments", True)) and is_profit:
                blocked_patch = _deep_merge(blocked_patch, patch)
                blocked_scoring = _deep_merge(blocked_scoring, patch)
                proposal_block_reasons.append("profitable_cycle_scoring_frozen")
                _append_reason_count(block_reason_counts, "profitable_cycle_scoring_frozen")
                violations.append(
                    {
                        "type": "profitable_cycle_scoring_frozen",
                        "proposal_id": proposal_id,
                        "source": source,
                        "keys": requested_keys,
                    }
                )
            else:
                for key_path, candidate_value in _flatten_patch_leaves(patch).items():
                    metric, block_reason = _evaluate_identity_drift(
                        scope_name="scoring",
                        current_value=_nested_lookup(effective_scoring, key_path),
                        candidate_value=candidate_value,
                        baseline_value=_nested_lookup(baseline_scoring, key_path),
                        max_single_step_ratio=scoring_max_single_step_ratio,
                        max_cumulative_ratio=scoring_max_cumulative_ratio,
                    )
                    if block_reason:
                        _nested_assign(blocked_patch, key_path, candidate_value)
                        _nested_assign(blocked_scoring, key_path, candidate_value)
                        blocked_scoring_metrics[key_path] = dict(metric, block_reason=block_reason)
                        proposal_block_reasons.append(block_reason)
                        _append_reason_count(block_reason_counts, block_reason)
                        violations.append(
                            {
                                "type": block_reason,
                                "proposal_id": proposal_id,
                                "source": source,
                                "scoring_key": key_path,
                                "current_value": metric.get("current_value"),
                                "candidate_value": candidate_value,
                            }
                        )
                        continue
                    _nested_assign(approved_patch, key_path, candidate_value)
                    _nested_assign(filtered_scoring, key_path, candidate_value)
                    _nested_assign(effective_scoring, key_path, candidate_value)
                    approved_scoring_metrics[key_path] = metric
        elif proposal_kind == "agent_weight_adjustment":
            if bool(profitable_cycle_policy.get("block_agent_weight_adjustments", True)) and is_profit:
                blocked_patch.update(patch)
                blocked_agent_weights.update(patch)
                proposal_block_reasons.append("profitable_cycle_agent_weights_frozen")
                _append_reason_count(block_reason_counts, "profitable_cycle_agent_weights_frozen")
                violations.append(
                    {
                        "type": "profitable_cycle_agent_weights_frozen",
                        "proposal_id": proposal_id,
                        "source": source,
                        "keys": requested_keys,
                    }
                )
            else:
                for agent_name, candidate_value in patch.items():
                    metric, block_reason = _evaluate_identity_drift(
                        scope_name="agent_weights",
                        current_value=effective_agent_weights.get(agent_name),
                        candidate_value=candidate_value,
                        baseline_value=baseline_agent_weights.get(agent_name),
                        max_single_step_ratio=agent_weight_max_single_step_ratio,
                        max_cumulative_ratio=agent_weight_max_cumulative_ratio,
                    )
                    if block_reason:
                        blocked_patch[agent_name] = candidate_value
                        blocked_agent_weights[agent_name] = candidate_value
                        blocked_agent_weight_metrics[agent_name] = dict(metric, block_reason=block_reason)
                        proposal_block_reasons.append(block_reason)
                        _append_reason_count(block_reason_counts, block_reason)
                        violations.append(
                            {
                                "type": block_reason,
                                "proposal_id": proposal_id,
                                "source": source,
                                "agent": agent_name,
                                "current_value": metric.get("current_value"),
                                "candidate_value": candidate_value,
                            }
                        )
                        continue
                    approved_patch[agent_name] = candidate_value
                    filtered_agent_weights[agent_name] = candidate_value
                    effective_agent_weights[agent_name] = candidate_value
                    approved_agent_weight_metrics[agent_name] = metric
        else:
            blocked_patch.update(patch)
            proposal_block_reasons.append("unsupported_proposal_kind")
            _append_reason_count(block_reason_counts, "unsupported_proposal_kind")
            violations.append(
                {
                    "type": "unsupported_proposal_kind",
                    "proposal_id": proposal_id,
                    "source": source,
                    "proposal_kind": proposal_kind,
                }
            )

        proposal_record = {
            "proposal_id": proposal_id,
            "source": source,
            "proposal_kind": proposal_kind,
            "requested_keys": requested_keys,
            "approved_keys": _proposal_patch_keys(proposal_kind, approved_patch),
            "blocked_keys": _proposal_patch_keys(proposal_kind, blocked_patch),
            "status": "approved",
            "block_reasons": sorted(set(proposal_block_reasons)),
        }
        if blocked_patch and approved_patch:
            proposal_record["status"] = "partially_blocked"
            partial_blocked_count += 1
        elif blocked_patch:
            proposal_record["status"] = "blocked"
        if approved_patch:
            approved_proposals.append(deepcopy(proposal_record))
            approved_source_summary[source] = approved_source_summary.get(source, 0) + 1
            if proposal_id:
                approved_proposal_refs.append(proposal_id)
        if blocked_patch:
            blocked_proposals.append(deepcopy(proposal_record))
            blocked_source_summary[source] = blocked_source_summary.get(source, 0) + 1
            if proposal_id:
                blocked_proposal_refs.append(proposal_id)

    approved = bool(filtered_params or filtered_scoring or filtered_agent_weights)
    top_block_reasons = [
        reason
        for reason, _count in sorted(
            block_reason_counts.items(),
            key=lambda item: (-int(item[1]), str(item[0])),
        )[:3]
    ]
    return {
        "approved": approved,
        "cycle_id": int(cycle_id),
        "policy": policy,
        "profit_context": {
            "is_profit": is_profit,
            "return_pct": return_pct,
            "benchmark_passed": bool(execution_snapshot.get("benchmark_passed", False)),
        },
        "baseline": {
            "config_ref": str(baseline_path),
            "model_kind": str(baseline_payload.get("kind") or ""),
            "active_config_ref": str(current_path),
        },
        "filtered_adjustments": {
            "params": filtered_params,
            "scoring": filtered_scoring,
            "agent_weights": filtered_agent_weights,
            "proposal_refs": approved_proposal_refs,
            "proposal_source_summary": approved_source_summary,
        },
        "blocked_adjustments": {
            "params": blocked_params,
            "scoring": blocked_scoring,
            "agent_weights": blocked_agent_weights,
        },
        "violations": violations,
        "drift_summary": {
            "approved_params": approved_param_metrics,
            "blocked_params": blocked_param_metrics,
            "approved_scoring": approved_scoring_metrics,
            "blocked_scoring": blocked_scoring_metrics,
            "approved_agent_weights": approved_agent_weight_metrics,
            "blocked_agent_weights": blocked_agent_weight_metrics,
            "max_single_step_ratio_vs_baseline": max_single_step_ratio,
            "max_param_drift_ratio_vs_baseline": max_cumulative_ratio,
            "max_scoring_single_step_ratio_vs_baseline": scoring_max_single_step_ratio,
            "max_scoring_drift_ratio_vs_baseline": scoring_max_cumulative_ratio,
            "max_agent_weight_single_step_ratio_vs_baseline": agent_weight_max_single_step_ratio,
            "max_agent_weight_drift_ratio_vs_baseline": agent_weight_max_cumulative_ratio,
        },
        "approved_proposals": approved_proposals,
        "blocked_proposals": blocked_proposals,
        "proposal_summary": {
            "requested_proposal_count": len(proposals),
            "approved_proposal_count": len(approved_proposals),
            "blocked_proposal_count": len(blocked_proposals),
            "partially_blocked_proposal_count": partial_blocked_count,
            "requested_proposal_refs": [
                str(item.get("proposal_id") or "")
                for item in proposals
                if str(item.get("proposal_id") or "")
            ],
            "approved_proposal_refs": approved_proposal_refs,
            "blocked_proposal_refs": blocked_proposal_refs,
            "requested_source_summary": requested_source_summary,
            "approved_source_summary": approved_source_summary,
            "blocked_source_summary": blocked_source_summary,
            "block_reason_counts": block_reason_counts,
            "top_block_reasons": top_block_reasons,
        },
    }
