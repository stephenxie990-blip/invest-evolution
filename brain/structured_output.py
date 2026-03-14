"""Schema-first normalization, validation, repair, and fallback helpers."""

from __future__ import annotations

from typing import Any


def _dict_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _list_payload(value: Any) -> list[Any]:
    if not isinstance(value, list):
        return []
    return list(value)


def _validate_type(value: Any, expected: type | tuple[type, ...], *, label: str) -> list[str]:
    if isinstance(value, expected):
        return []
    expected_name = (
        ", ".join(item.__name__ for item in expected)
        if isinstance(expected, tuple)
        else expected.__name__
    )
    return [f"{label} should be {expected_name}"]


def _validation_errors(validator: Any, payload: dict[str, Any]) -> list[str]:
    if not callable(validator):
        return []
    result = validator(payload)
    return [str(item) for item in _list_payload(result) if str(item or "")]


class StructuredOutputAdapter:
    """Produces stable payloads with validation, one repair pass, and fallback metadata."""

    def normalize_payload(
        self,
        *,
        tool_name: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        raw = _dict_payload(payload)
        normalizer = getattr(self, f"_normalize_{tool_name}", None)
        normalized = normalizer(raw) if callable(normalizer) else dict(raw)
        validator = getattr(self, f"_validate_{tool_name}", None)
        errors = _validation_errors(validator, _dict_payload(normalized))
        status = "validated"
        repair_attempted = False
        coercions = self._coercion_notes(tool_name=tool_name, raw=raw)

        if errors:
            repair_attempted = True
            repaired = self._repair_payload(tool_name=tool_name, raw=raw, normalized=_dict_payload(normalized))
            repair_errors = _validation_errors(validator, repaired)
            if not repair_errors:
                normalized = repaired
                errors = []
                status = "repaired"
            else:
                normalized = self._fallback_payload(tool_name=tool_name, raw=raw, normalized=_dict_payload(repaired))
                errors = _validation_errors(validator, _dict_payload(normalized))
                status = "fallback" if not errors else "fallback_degraded"
        elif coercions:
            status = "repaired"
            repair_attempted = True

        final_payload = _dict_payload(normalized)
        final_payload["structured_output"] = {
            "enabled": True,
            "tool_name": str(tool_name or ""),
            "status": status,
            "repair_attempted": repair_attempted,
            "coercions": list(coercions),
            "validation_errors": list(errors),
        }
        return final_payload

    def _coercion_notes(self, *, tool_name: str, raw: dict[str, Any]) -> list[str]:
        inspector = getattr(self, f"_coercions_{tool_name}", None)
        if callable(inspector):
            return [str(item) for item in _list_payload(inspector(raw)) if str(item or "")]
        return []

    def _repair_payload(
        self,
        *,
        tool_name: str,
        raw: dict[str, Any],
        normalized: dict[str, Any],
    ) -> dict[str, Any]:
        fallback = self._fallback_payload(tool_name=tool_name, raw=raw, normalized=normalized)
        merged = dict(fallback)
        merged.update(normalized)
        for key, value in fallback.items():
            if key not in merged or (
                isinstance(value, dict) and not isinstance(merged.get(key), dict)
            ):
                merged[key] = value
        return merged

    def _fallback_payload(
        self,
        *,
        tool_name: str,
        raw: dict[str, Any],
        normalized: dict[str, Any],
    ) -> dict[str, Any]:
        builder = getattr(self, f"_fallback_{tool_name}", None)
        if callable(builder):
            return _dict_payload(builder(raw=raw, normalized=normalized))
        return _dict_payload(normalized or raw)

    def _normalize_invest_ask_stock(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(payload)
        identifiers = {
            "policy_id": str(normalized.get("policy_id") or ""),
            "research_case_id": str(normalized.get("research_case_id") or ""),
            "attribution_id": str(normalized.get("attribution_id") or ""),
        }
        security = _dict_payload(normalized.get("resolved_entities")).get("security")
        if not isinstance(security, dict):
            security = _dict_payload(normalized.get("resolved_security")) or _dict_payload(normalized.get("resolved"))
        normalized["request"] = {
            "question": str(normalized.get("question") or _dict_payload(normalized.get("request")).get("question") or ""),
            "query": str(normalized.get("query") or _dict_payload(normalized.get("request")).get("query") or ""),
            "normalized_query": str(normalized.get("normalized_query") or _dict_payload(normalized.get("request")).get("normalized_query") or ""),
            "requested_as_of_date": str(normalized.get("requested_as_of_date") or _dict_payload(normalized.get("request")).get("requested_as_of_date") or ""),
            "as_of_date": str(normalized.get("as_of_date") or _dict_payload(normalized.get("request")).get("as_of_date") or ""),
        }
        normalized["identifiers"] = {
            **{key: str(_dict_payload(normalized.get("identifiers")).get(key) or "") for key in identifiers.keys()},
            **{key: value for key, value in identifiers.items() if value},
        }
        normalized["resolved_entities"] = {"security": _dict_payload(security)}
        normalized["analysis"] = {
            "tool_results": _dict_payload(_dict_payload(normalized.get("analysis")).get("tool_results")),
            "result_sequence": _list_payload(_dict_payload(normalized.get("analysis")).get("result_sequence")),
            "derived_signals": _dict_payload(_dict_payload(normalized.get("analysis")).get("derived_signals")),
            "model_bridge": _dict_payload(_dict_payload(normalized.get("analysis")).get("model_bridge")),
        }
        normalized["research"] = {
            **_dict_payload(normalized.get("research")),
            "identifiers": _dict_payload(_dict_payload(normalized.get("research")).get("identifiers"))
            or dict(normalized["identifiers"]),
        }
        normalized["dashboard"] = _dict_payload(normalized.get("dashboard"))
        return normalized

    def _validate_invest_ask_stock(self, payload: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        for key in ("request", "identifiers", "resolved_entities", "analysis", "research", "dashboard"):
            errors.extend(_validate_type(payload.get(key), dict, label=key))
        errors.extend(_validate_type(_dict_payload(payload.get("resolved_entities")).get("security"), dict, label="resolved_entities.security"))
        return errors

    def _fallback_invest_ask_stock(self, *, raw: dict[str, Any], normalized: dict[str, Any]) -> dict[str, Any]:
        return {
            "request": _dict_payload(normalized.get("request")),
            "identifiers": _dict_payload(normalized.get("identifiers")),
            "resolved_entities": {"security": _dict_payload(_dict_payload(normalized.get("resolved_entities")).get("security"))},
            "analysis": _dict_payload(normalized.get("analysis")),
            "research": _dict_payload(normalized.get("research")),
            "dashboard": _dict_payload(normalized.get("dashboard")),
            "status": str(raw.get("status") or normalized.get("status") or "ok"),
        }

    def _normalize_invest_training_plan_create(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(payload)
        normalized["status"] = str(normalized.get("status") or "planned")
        normalized["plan_id"] = str(normalized.get("plan_id") or "")
        normalized["spec"] = _dict_payload(normalized.get("spec"))
        normalized["protocol"] = _dict_payload(normalized.get("protocol"))
        normalized["dataset"] = _dict_payload(normalized.get("dataset"))
        normalized["model_scope"] = _dict_payload(normalized.get("model_scope"))
        normalized["optimization"] = _dict_payload(normalized.get("optimization"))
        normalized["guardrails"] = _dict_payload(normalized.get("guardrails"))
        normalized["llm"] = _dict_payload(normalized.get("llm"))
        normalized["objective"] = _dict_payload(normalized.get("objective"))
        normalized["artifacts"] = _dict_payload(normalized.get("artifacts"))
        return normalized

    def _validate_invest_training_plan_create(self, payload: dict[str, Any]) -> list[str]:
        errors = _validate_type(payload.get("status"), str, label="status")
        errors.extend(_validate_type(payload.get("plan_id"), str, label="plan_id"))
        for key in ("spec", "protocol", "dataset", "model_scope", "optimization", "guardrails", "llm", "objective", "artifacts"):
            errors.extend(_validate_type(payload.get(key), dict, label=key))
        return errors

    def _fallback_invest_training_plan_create(self, *, raw: dict[str, Any], normalized: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": str(raw.get("status") or normalized.get("status") or "planned"),
            "plan_id": str(raw.get("plan_id") or normalized.get("plan_id") or ""),
            "spec": _dict_payload(normalized.get("spec")),
            "protocol": _dict_payload(normalized.get("protocol")),
            "dataset": _dict_payload(normalized.get("dataset")),
            "model_scope": _dict_payload(normalized.get("model_scope")),
            "optimization": _dict_payload(normalized.get("optimization")),
            "guardrails": _dict_payload(normalized.get("guardrails")),
            "llm": _dict_payload(normalized.get("llm")),
            "objective": _dict_payload(normalized.get("objective")),
            "artifacts": _dict_payload(normalized.get("artifacts")),
        }

    def _coercions_invest_training_plan_create(self, raw: dict[str, Any]) -> list[str]:
        notes: list[str] = []
        for key in ("spec", "protocol", "dataset", "model_scope", "optimization", "guardrails", "llm", "objective", "artifacts"):
            if key in raw and not isinstance(raw.get(key), dict):
                notes.append(f"{key}_coerced_to_dict")
        if "status" in raw and not isinstance(raw.get("status"), str):
            notes.append("status_coerced_to_string")
        if "plan_id" in raw and not isinstance(raw.get("plan_id"), str):
            notes.append("plan_id_coerced_to_string")
        return notes

    def _normalize_invest_training_plan_execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(payload)
        normalized["status"] = str(payload.get("status") or "ok")
        normalized["plan_id"] = str(payload.get("plan_id") or "")
        normalized["run_id"] = str(payload.get("run_id") or "")
        results = [dict(item) for item in _list_payload(normalized.get("results")) if isinstance(item, dict)]
        latest_result = dict(results[-1]) if results else {}
        normalized["training_lab"] = _dict_payload(normalized.get("training_lab"))
        normalized["artifacts"] = _dict_payload(normalized.get("artifacts"))
        normalized["summary"] = _dict_payload(normalized.get("summary"))
        normalized["results"] = results
        normalized["result_overview"] = {
            "result_count": len(results),
            "ok_result_count": sum(1 for item in results if str(item.get("status") or "") == "ok"),
            "latest_cycle_id": latest_result.get("cycle_id"),
            "latest_result_status": str(latest_result.get("status") or ""),
        }
        normalized["latest_result"] = {
            "cycle_id": latest_result.get("cycle_id"),
            "status": str(latest_result.get("status") or ""),
            "return_pct": latest_result.get("return_pct"),
            "benchmark_passed": bool(latest_result.get("benchmark_passed", False)),
            "promotion_record": _dict_payload(latest_result.get("promotion_record")),
            "lineage_record": _dict_payload(latest_result.get("lineage_record")),
        }
        return normalized

    def _validate_invest_training_plan_execute(self, payload: dict[str, Any]) -> list[str]:
        errors = _validate_type(payload.get("status"), str, label="status")
        errors.extend(_validate_type(payload.get("plan_id"), str, label="plan_id"))
        errors.extend(_validate_type(payload.get("run_id"), str, label="run_id"))
        errors.extend(_validate_type(payload.get("results"), list, label="results"))
        for key in ("training_lab", "artifacts", "summary", "result_overview", "latest_result"):
            errors.extend(_validate_type(payload.get(key), dict, label=key))
        return errors

    def _fallback_invest_training_plan_execute(self, *, raw: dict[str, Any], normalized: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": str(raw.get("status") or normalized.get("status") or "ok"),
            "plan_id": str(raw.get("plan_id") or normalized.get("plan_id") or ""),
            "run_id": str(raw.get("run_id") or normalized.get("run_id") or ""),
            "training_lab": _dict_payload(normalized.get("training_lab")),
            "artifacts": _dict_payload(normalized.get("artifacts")),
            "summary": _dict_payload(normalized.get("summary")),
            "results": [dict(item) for item in _list_payload(normalized.get("results")) if isinstance(item, dict)],
            "result_overview": _dict_payload(normalized.get("result_overview")),
            "latest_result": _dict_payload(normalized.get("latest_result")),
        }

    def _coercions_invest_training_plan_execute(self, raw: dict[str, Any]) -> list[str]:
        notes: list[str] = []
        if "results" in raw and not isinstance(raw.get("results"), list):
            notes.append("results_coerced_to_list")
        for key in ("training_lab", "artifacts", "summary"):
            if key in raw and not isinstance(raw.get(key), dict):
                notes.append(f"{key}_coerced_to_dict")
        if "status" in raw and not isinstance(raw.get("status"), str):
            notes.append("status_coerced_to_string")
        if "plan_id" in raw and not isinstance(raw.get("plan_id"), str):
            notes.append("plan_id_coerced_to_string")
        if "run_id" in raw and not isinstance(raw.get("run_id"), str):
            notes.append("run_id_coerced_to_string")
        return notes

    def _normalize_invest_control_plane_update(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(payload)
        normalized["status"] = str(payload.get("status") or "ok")
        normalized["pending"] = _dict_payload(normalized.get("pending"))
        normalized["updated"] = _list_payload(normalized.get("updated"))
        normalized["control_plane"] = _dict_payload(normalized.get("control_plane"))
        normalized["restart_required"] = bool(normalized.get("restart_required", False))
        return normalized

    def _validate_invest_control_plane_update(self, payload: dict[str, Any]) -> list[str]:
        errors = _validate_type(payload.get("status"), str, label="status")
        errors.extend(_validate_type(payload.get("pending"), dict, label="pending"))
        errors.extend(_validate_type(payload.get("updated"), list, label="updated"))
        errors.extend(_validate_type(payload.get("control_plane"), dict, label="control_plane"))
        errors.extend(_validate_type(payload.get("restart_required"), bool, label="restart_required"))
        return errors

    def _fallback_invest_control_plane_update(self, *, raw: dict[str, Any], normalized: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": str(raw.get("status") or normalized.get("status") or "ok"),
            "pending": _dict_payload(normalized.get("pending")),
            "updated": _list_payload(normalized.get("updated")),
            "control_plane": _dict_payload(normalized.get("control_plane")),
            "restart_required": bool(normalized.get("restart_required", False)),
        }

    def _coercions_invest_control_plane_update(self, raw: dict[str, Any]) -> list[str]:
        notes: list[str] = []
        for key in ("pending", "control_plane"):
            if key in raw and not isinstance(raw.get(key), dict):
                notes.append(f"{key}_coerced_to_dict")
        if "updated" in raw and not isinstance(raw.get("updated"), list):
            notes.append("updated_coerced_to_list")
        return notes

    def _normalize_invest_runtime_paths_update(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(payload)
        normalized["status"] = str(payload.get("status") or "ok")
        normalized["pending"] = _dict_payload(normalized.get("pending"))
        normalized["updated"] = _list_payload(normalized.get("updated"))
        normalized["paths"] = _dict_payload(normalized.get("paths"))
        return normalized

    def _validate_invest_runtime_paths_update(self, payload: dict[str, Any]) -> list[str]:
        errors = _validate_type(payload.get("status"), str, label="status")
        errors.extend(_validate_type(payload.get("pending"), dict, label="pending"))
        errors.extend(_validate_type(payload.get("updated"), list, label="updated"))
        errors.extend(_validate_type(payload.get("paths"), dict, label="paths"))
        return errors

    def _fallback_invest_runtime_paths_update(self, *, raw: dict[str, Any], normalized: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": str(raw.get("status") or normalized.get("status") or "ok"),
            "pending": _dict_payload(normalized.get("pending")),
            "updated": _list_payload(normalized.get("updated")),
            "paths": _dict_payload(normalized.get("paths")),
        }

    def _coercions_invest_runtime_paths_update(self, raw: dict[str, Any]) -> list[str]:
        notes: list[str] = []
        for key in ("pending", "paths"):
            if key in raw and not isinstance(raw.get(key), dict):
                notes.append(f"{key}_coerced_to_dict")
        if "updated" in raw and not isinstance(raw.get("updated"), list):
            notes.append("updated_coerced_to_list")
        return notes

    def _normalize_invest_evolution_config_update(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(payload)
        normalized["status"] = str(payload.get("status") or "ok")
        normalized["pending"] = _dict_payload(normalized.get("pending"))
        normalized["updated"] = _list_payload(normalized.get("updated"))
        normalized["config"] = _dict_payload(normalized.get("config"))
        return normalized

    def _validate_invest_evolution_config_update(self, payload: dict[str, Any]) -> list[str]:
        errors = _validate_type(payload.get("status"), str, label="status")
        errors.extend(_validate_type(payload.get("pending"), dict, label="pending"))
        errors.extend(_validate_type(payload.get("updated"), list, label="updated"))
        errors.extend(_validate_type(payload.get("config"), dict, label="config"))
        return errors

    def _fallback_invest_evolution_config_update(self, *, raw: dict[str, Any], normalized: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": str(raw.get("status") or normalized.get("status") or "ok"),
            "pending": _dict_payload(normalized.get("pending")),
            "updated": _list_payload(normalized.get("updated")),
            "config": _dict_payload(normalized.get("config")),
        }

    def _coercions_invest_evolution_config_update(self, raw: dict[str, Any]) -> list[str]:
        notes: list[str] = []
        for key in ("pending", "config"):
            if key in raw and not isinstance(raw.get(key), dict):
                notes.append(f"{key}_coerced_to_dict")
        if "updated" in raw and not isinstance(raw.get("updated"), list):
            notes.append("updated_coerced_to_list")
        return notes


__all__ = ["StructuredOutputAdapter"]
