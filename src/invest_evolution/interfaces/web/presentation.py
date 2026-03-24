"""Web response helpers and display presentation adapters."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, TypeVar

from flask import Response, jsonify, request
from werkzeug.exceptions import BadRequest

from invest_evolution.agent_runtime.presentation import (
    project_runtime_governance_display_payload,
    read_latest_training_result,
)
from invest_evolution.application.commander.presentation import build_human_display
from invest_evolution.application.commander.status import build_promotion_lineage_ops_panel

ResponseValue = Any
T = TypeVar("T")


def is_route_error_response(value: Any) -> bool:
    return isinstance(value, tuple)


def build_json_payload_response(payload: Any, status_code: int = 200) -> ResponseValue:
    response = jsonify(payload)
    response.status_code = status_code
    return response


def build_json_error_response(
    error_message: str,
    status_code: int,
    **extra: Any,
) -> ResponseValue:
    payload: dict[str, Any] = {"error": str(error_message)}
    payload.update(extra)
    return build_json_payload_response(payload, status_code=status_code)


def build_json_status_error_response(
    error_message: str,
    status_code: int,
    **extra: Any,
) -> ResponseValue:
    payload: dict[str, Any] = {"status": "error", "error": str(error_message)}
    payload.update(extra)
    return build_json_payload_response(payload, status_code=status_code)


def build_data_source_unavailable_response(exc: Any) -> ResponseValue:
    return build_json_payload_response(exc.to_dict(), status_code=503)


def build_not_found_response(error: str | Exception, **extra: Any) -> ResponseValue:
    return build_json_error_response(str(error), 404, **extra)


def parse_value_or_400(loader: Callable[[], T]) -> T | ResponseValue:
    try:
        return loader()
    except (ValueError, BadRequest) as exc:
        return build_json_error_response(str(getattr(exc, "description", exc)), 400)


def parse_json_object_or_400(*, force: bool = False, silent: bool = False) -> dict[str, Any] | ResponseValue:
    def _load() -> dict[str, Any]:
        payload = request.get_json(force=force, silent=silent)
        if payload is None:
            return {}
        if not isinstance(payload, dict):
            raise ValueError(f"request body must be a JSON object, got {type(payload).__name__}")
        return payload

    return parse_value_or_400(_load)


def read_str_field(
    data: Mapping[str, Any],
    field_name: str,
    *,
    default: str = "",
    strip: bool = False,
) -> str:
    raw_value = data.get(field_name, default)
    text = str(raw_value or default)
    return text.strip() if strip else text


def parse_required_str_field_or_400(
    data: Mapping[str, Any],
    field_name: str,
    *,
    error_message: str | None = None,
    strip: bool = True,
) -> str | ResponseValue:
    text = read_str_field(data, field_name, strip=strip)
    if text:
        return text
    return build_json_error_response(error_message or f"{field_name} is required", 400)


def parse_bool_field_or_400(
    data: Mapping[str, Any],
    field_name: str,
    parse_bool: Callable[[Any, str], bool],
    *,
    default: bool = False,
) -> bool | ResponseValue:
    return parse_value_or_400(lambda: parse_bool(data.get(field_name, default), field_name))


def parse_int_field_or_400(
    data: Mapping[str, Any],
    field_name: str,
    *,
    default: int,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int | ResponseValue:
    def _load() -> int:
        try:
            value = int(data.get(field_name, default))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field_name} must be an integer") from exc
        if minimum is not None:
            value = max(minimum, value)
        if maximum is not None:
            value = min(maximum, value)
        return value

    return parse_value_or_400(_load)


def parse_str_list_field_or_400(
    data: Mapping[str, Any],
    field_name: str,
    *,
    default: Any | None = None,
) -> list[str] | ResponseValue:
    raw_value = data.get(field_name, [] if default is None else default)
    if raw_value is None:
        return []
    if isinstance(raw_value, str):
        return [part.strip() for part in raw_value.split(",") if part.strip()]
    if isinstance(raw_value, list):
        return [str(part).strip() for part in raw_value if str(part).strip()]
    return build_json_error_response(
        f"{field_name} must be a list of strings or a comma-separated string",
        400,
    )


def read_object_field(data: Mapping[str, Any], field_name: str) -> dict[str, Any] | None:
    value = data.get(field_name)
    return value if isinstance(value, dict) else None


def read_query_str(field_name: str, *, default: str = "", empty_as_none: bool = False) -> str | None:
    value = str(request.args.get(field_name, default) or "").strip()
    if empty_as_none and not value:
        return None
    return value


def read_query_str_list(field_name: str) -> list[str]:
    values: list[str] = []
    for raw_value in request.args.getlist(field_name):
        for part in str(raw_value).split(","):
            normalized = part.strip()
            if normalized:
                values.append(normalized)
    return values


def parse_optional_query_int_or_400(field_name: str) -> int | None | ResponseValue:
    def _load() -> int | None:
        raw_value = request.args.get(field_name)
        if raw_value in (None, ""):
            return None
        try:
            return int(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field_name} must be an integer") from exc

    return parse_value_or_400(_load)


def parse_query_int_or_400(
    field_name: str,
    parse_int: Callable[..., int],
    *,
    default: int,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int | ResponseValue:
    return parse_value_or_400(
        lambda: parse_int(
            request.args.get(field_name, default),
            field_name,
            minimum=minimum,
            maximum=maximum,
        )
    )


def parse_query_bool_or_400(
    field_name: str,
    parse_bool: Callable[[Any, str], bool],
    *,
    default: bool = False,
) -> bool | ResponseValue:
    return parse_value_or_400(lambda: parse_bool(request.args.get(field_name, default), field_name))


def parse_view_limit_or_400(
    request_view_arg: Callable[[], str],
    parse_limit_arg: Callable[..., int],
    *,
    default_limit: int = 20,
    maximum_limit: int = 200,
) -> tuple[str, int] | ResponseValue:
    view = parse_view_or_400(request_view_arg)
    if not isinstance(view, str):
        return view
    limit = parse_limit_or_400(parse_limit_arg, default=default_limit, maximum=maximum_limit)
    if not isinstance(limit, int):
        return limit
    return view, limit


def parse_view_or_400(request_view_arg: Callable[[], str]) -> str | ResponseValue:
    return parse_value_or_400(request_view_arg)


def parse_limit_or_400(
    parse_limit_arg: Callable[..., int],
    *,
    default: int = 20,
    maximum: int = 200,
) -> int | ResponseValue:
    return parse_value_or_400(lambda: parse_limit_arg(default=default, maximum=maximum))


def parse_detail_or_400(
    parse_detail_mode: Callable[..., str],
    *,
    raw_value: Any,
    default: str = "fast",
    field_name: str = "detail",
) -> str | ResponseValue:
    return parse_value_or_400(
        lambda: parse_detail_mode(
            raw_value,
            default=default,
            field_name=field_name,
            strict=True,
        )
    )


def parsed_request_response_or_400(
    *,
    parse_request: Callable[[], dict[str, Any] | ResponseValue],
    respond: Callable[[dict[str, Any]], ResponseValue],
) -> ResponseValue:
    parsed_request = parse_request()
    if not isinstance(parsed_request, dict):
        return parsed_request
    return respond(parsed_request)


def _respond_with_view_or_400(
    *,
    request_view_arg: Callable[[], str],
    respond_with_display: Callable[..., ResponseValue],
    load_payload: Callable[[], Any],
) -> ResponseValue:
    view = parse_view_or_400(request_view_arg)
    if not isinstance(view, str):
        return view
    return respond_with_display(load_payload(), view=view)


def _respond_with_view_limit_or_400(
    *,
    request_view_arg: Callable[[], str],
    parse_limit_arg: Callable[..., int],
    respond_with_display: Callable[..., ResponseValue],
    load_payload: Callable[[int], Any],
    default_limit: int = 20,
    maximum_limit: int = 200,
) -> ResponseValue:
    parsed_display = parse_view_limit_or_400(
        request_view_arg,
        parse_limit_arg,
        default_limit=default_limit,
        maximum_limit=maximum_limit,
    )
    if not isinstance(parsed_display, tuple):
        return parsed_display
    view, limit = parsed_display
    return respond_with_display(load_payload(limit), view=view)


def _with_loaded_runtime_or_error(
    *,
    load_runtime: Callable[[], Any],
    handle_runtime: Callable[[Any], ResponseValue],
) -> ResponseValue:
    runtime = load_runtime()
    if is_route_error_response(runtime):
        return runtime
    return handle_runtime(runtime)


def _build_counted_items_payload(items: list[Any]) -> dict[str, Any]:
    return {"count": len(items), "items": items}


def _respond_optional_payload_or_404(
    *,
    request_view_arg: Callable[[], str],
    respond_with_display: Callable[..., ResponseValue],
    payload: Any | None,
    not_found_message: str,
) -> ResponseValue:
    if payload is None:
        return build_json_error_response(not_found_message, 404)
    return _respond_with_view_or_400(
        request_view_arg=request_view_arg,
        respond_with_display=respond_with_display,
        load_payload=lambda: payload,
    )


def display_response_or_400(
    *,
    request_view_arg: Callable[[], str],
    respond_with_display: Callable[..., ResponseValue],
    fetch: Callable[[], Any],
) -> ResponseValue:
    return _respond_with_view_or_400(
        request_view_arg=request_view_arg,
        respond_with_display=respond_with_display,
        load_payload=fetch,
    )


def display_response_or_404(
    *,
    request_view_arg: Callable[[], str],
    respond_with_display: Callable[..., ResponseValue],
    fetch: Callable[[], Any],
) -> ResponseValue:
    try:
        return display_response_or_400(
            request_view_arg=request_view_arg,
            respond_with_display=respond_with_display,
            fetch=fetch,
        )
    except FileNotFoundError as exc:
        return build_not_found_response(exc)


def display_limit_response_or_400(
    *,
    request_view_arg: Callable[[], str],
    parse_limit_arg: Callable[..., int],
    respond_with_display: Callable[..., ResponseValue],
    fetch: Callable[[int], Any],
    default_limit: int = 20,
    maximum_limit: int = 200,
) -> ResponseValue:
    return _respond_with_view_limit_or_400(
        request_view_arg=request_view_arg,
        parse_limit_arg=parse_limit_arg,
        respond_with_display=respond_with_display,
        load_payload=fetch,
        default_limit=default_limit,
        maximum_limit=maximum_limit,
    )


def display_list_response_or_400(
    *,
    request_view_arg: Callable[[], str],
    parse_limit_arg: Callable[..., int],
    respond_with_display: Callable[..., ResponseValue],
    fetch: Callable[[int], Any],
    default_limit: int = 20,
    maximum_limit: int = 200,
) -> ResponseValue:
    return display_limit_response_or_400(
        request_view_arg=request_view_arg,
        parse_limit_arg=parse_limit_arg,
        respond_with_display=respond_with_display,
        fetch=fetch,
        default_limit=default_limit,
        maximum_limit=maximum_limit,
    )


def runtime_display_response_or_400(
    *,
    load_runtime: Callable[[], Any],
    request_view_arg: Callable[[], str],
    respond_with_display: Callable[..., ResponseValue],
    fetch: Callable[[Any], Any],
) -> ResponseValue:
    return _with_loaded_runtime_or_error(
        load_runtime=load_runtime,
        handle_runtime=lambda runtime: _respond_with_view_or_400(
            request_view_arg=request_view_arg,
            respond_with_display=respond_with_display,
            load_payload=lambda: fetch(runtime),
        ),
    )


def runtime_items_response_or_400(
    *,
    load_runtime: Callable[[], Any],
    request_view_arg: Callable[[], str],
    respond_with_display: Callable[..., ResponseValue],
    fetch_items: Callable[[Any], list[Any]],
) -> ResponseValue:
    return runtime_display_response_or_400(
        load_runtime=load_runtime,
        request_view_arg=request_view_arg,
        respond_with_display=respond_with_display,
        fetch=lambda runtime: _build_counted_items_payload(fetch_items(runtime)),
    )


def runtime_optional_detail_response_or_404(
    *,
    load_runtime: Callable[[], Any],
    request_view_arg: Callable[[], str],
    respond_with_display: Callable[..., ResponseValue],
    fetch: Callable[[Any], Any | None],
    not_found_message: str,
) -> ResponseValue:
    return _with_loaded_runtime_or_error(
        load_runtime=load_runtime,
        handle_runtime=lambda runtime: _respond_optional_payload_or_404(
            request_view_arg=request_view_arg,
            respond_with_display=respond_with_display,
            payload=fetch(runtime),
            not_found_message=not_found_message,
        ),
    )


def runtime_or_fallback_display_response_or_400(
    *,
    get_runtime: Callable[[], Any | None],
    request_view_arg: Callable[[], str],
    respond_with_display: Callable[..., ResponseValue],
    runtime_fetch: Callable[[Any], Any],
    fallback_fetch: Callable[[], Any],
) -> ResponseValue:
    return _respond_with_view_or_400(
        request_view_arg=request_view_arg,
        respond_with_display=respond_with_display,
        load_payload=lambda: _resolve_runtime_or_fallback_payload(
            get_runtime=get_runtime,
            runtime_fetch=runtime_fetch,
            fallback_fetch=fallback_fetch,
        ),
    )


def runtime_or_fallback_payload_response(
    *,
    get_runtime: Callable[[], Any | None],
    build_contract_payload_response: Callable[..., ResponseValue],
    runtime_fetch: Callable[[Any], Any],
    fallback_fetch: Callable[[], Any],
) -> ResponseValue:
    payload = _resolve_runtime_or_fallback_payload(
        get_runtime=get_runtime,
        runtime_fetch=runtime_fetch,
        fallback_fetch=fallback_fetch,
    )
    return build_contract_payload_response(payload)


def _build_display_row(label: str, value: Any) -> dict[str, str]:
    return {"label": str(label), "value": str(value)}


def _build_training_display_cards(body: dict[str, Any]) -> list[dict[str, Any]]:
    latest = read_latest_training_result(body)
    if not latest and not body.get("training_lab"):
        return []

    cards: list[dict[str, Any]] = []
    ops_panel = dict(
        dict(dict(body.get("training_lab") or {}).get("run") or {}).get("ops_panel")
        or latest.get("ops_panel")
        or build_promotion_lineage_ops_panel(latest)
        or {}
    )
    if ops_panel.get("available", False):
        refs = dict(ops_panel.get("refs") or {})
        status = dict(ops_panel.get("status") or {})
        review_window = dict(ops_panel.get("review_window") or {})
        rows = [
            _build_display_row("promotion", status.get("promotion_status") or "unknown"),
            _build_display_row("gate", status.get("gate_status") or "unknown"),
            _build_display_row("lineage", status.get("lineage_status") or "unknown"),
        ]
        if status.get("basis_stage"):
            rows.append(_build_display_row("basis_stage", status.get("basis_stage")))
        if refs.get("active_runtime_config_ref"):
            rows.append(_build_display_row("active", refs.get("active_runtime_config_ref")))
        if refs.get("candidate_runtime_config_ref"):
            rows.append(_build_display_row("candidate", refs.get("candidate_runtime_config_ref")))
        if review_window:
            rows.append(
                _build_display_row(
                    "review_window",
                    f"{review_window.get('mode', 'unknown')} / {int(review_window.get('size', 0) or 0)}",
                )
            )
        cards.append(
            {
                "id": "training_ops_panel",
                "title": "Promotion / Lineage",
                "tone": "warning" if list(ops_panel.get("warnings") or []) else "neutral",
                "summary": str(ops_panel.get("summary") or ""),
                "rows": rows,
                "badges": [
                    str(item)
                    for item in [
                        status.get("promotion_status"),
                        status.get("gate_status"),
                        status.get("lineage_status"),
                    ]
                    if str(item or "").strip()
                ],
                "warnings": [str(item) for item in list(ops_panel.get("warnings") or []) if str(item or "").strip()],
            }
        )

    causal_diagnosis = dict(
        latest.get("causal_diagnosis")
        or dict(latest.get("review_decision") or {}).get("causal_diagnosis")
        or {}
    )
    if causal_diagnosis:
        drivers = [dict(item) for item in list(causal_diagnosis.get("drivers") or [])]
        rows = [
            _build_display_row("primary_driver", causal_diagnosis.get("primary_driver") or "unknown"),
            _build_display_row("summary", causal_diagnosis.get("summary") or ""),
        ]
        if drivers:
            top = drivers[0]
            rows.append(_build_display_row("top_evidence", ",".join(str(item) for item in list(top.get("evidence_cycle_ids") or []))))
            rows.append(_build_display_row("top_score", top.get("score") or ""))
        cards.append(
            {
                "id": "causal_diagnosis",
                "title": "Causal Diagnosis",
                "tone": "warning",
                "summary": str(causal_diagnosis.get("summary") or ""),
                "rows": rows,
                "badges": [str(causal_diagnosis.get("primary_driver") or "unknown")],
            }
        )

    similarity_summary = dict(
        latest.get("similarity_summary")
        or dict(latest.get("review_decision") or {}).get("similarity_summary")
        or {}
    )
    similar_results = [
        dict(item)
        for item in list(
            latest.get("similar_results")
            or dict(latest.get("review_decision") or {}).get("similar_results")
            or []
        )
    ]
    if similarity_summary or similar_results:
        rows = []
        matched_cycle_ids = list(similarity_summary.get("matched_cycle_ids") or [])
        if matched_cycle_ids:
            rows.append(_build_display_row("matched_cycles", ",".join(str(item) for item in matched_cycle_ids)))
        if similarity_summary.get("dominant_regime"):
            rows.append(_build_display_row("dominant_regime", similarity_summary.get("dominant_regime")))
        if similar_results:
            top = dict(similar_results[0])
            rows.append(
                _build_display_row(
                    "top_match",
                    f"cycle {top.get('cycle_id')} / {float(top.get('return_pct', 0.0) or 0.0):+.2f}%",
                )
            )
        cards.append(
            {
                "id": "similar_samples",
                "title": "Similar Samples",
                "tone": "neutral",
                "summary": f"命中 {len(matched_cycle_ids or similar_results)} 个历史相似样本",
                "rows": rows,
                "badges": [str(similarity_summary.get("dominant_regime") or "").strip()] if str(similarity_summary.get("dominant_regime") or "").strip() else [],
            }
        )
    realism_metrics = dict(latest.get("realism_metrics") or {})
    if realism_metrics:
        rows = [
            _build_display_row("avg_trade_amount", realism_metrics.get("avg_trade_amount") or ""),
            _build_display_row("avg_turnover_rate", realism_metrics.get("avg_turnover_rate") or ""),
            _build_display_row("avg_holding_days", realism_metrics.get("avg_holding_days") or ""),
        ]
        cards.append(
            {
                "id": "execution_realism",
                "title": "Execution Realism",
                "tone": "neutral",
                "summary": f"{int(realism_metrics.get('trade_record_count', 0) or 0)} 条交易记录",
                "rows": rows,
                "badges": [str(realism_metrics.get("selection_mode") or "").strip()] if str(realism_metrics.get("selection_mode") or "").strip() else [],
            }
        )
    governance_summary = dict(dict(body.get("training_lab") or {}).get("governance_summary") or {})
    governance_metrics = dict(governance_summary.get("governance_metrics") or {})
    realism_summary = dict(governance_summary.get("realism_summary") or {})
    if governance_metrics or realism_summary:
        rows = []
        if governance_metrics:
            rows.extend(
                [
                    _build_display_row("candidate_pending", governance_metrics.get("candidate_pending_count") or 0),
                    _build_display_row("awaiting_gate", governance_metrics.get("promotion_awaiting_gate_count") or 0),
                    _build_display_row("drift_rate", f"{float(governance_metrics.get('active_candidate_drift_rate', 0.0) or 0.0):.2%}"),
                ]
            )
        if realism_summary:
            rows.extend(
                [
                    _build_display_row("avg_holding_days", realism_summary.get("avg_holding_days") or ""),
                    _build_display_row("high_turnover", realism_summary.get("high_turnover_trade_count") or 0),
                ]
            )
        cards.append(
            {
                "id": "training_governance",
                "title": "Training Governance",
                "tone": "warning" if int(governance_metrics.get("candidate_pending_count", 0) or 0) else "neutral",
                "summary": "最近一次评估已沉淀治理与现实性摘要",
                "rows": rows,
                "badges": [
                    str(item)
                    for item in [
                        governance_summary.get("run_id"),
                        dict(governance_summary.get("promotion") or {}).get("verdict"),
                    ]
                    if str(item or "").strip()
                ],
            }
        )
    runtime_governance_display = project_runtime_governance_display_payload(body)
    if runtime_governance_display.get("available", False):
        cards.append(
            {
                "id": "runtime_governance",
                "title": "Runtime Governance",
                "tone": "warning" if int(runtime_governance_display.get("guardrail_blocks", 0) or 0) else "neutral",
                "summary": "当前运行时的 structured output 与 guardrail 统计",
                "rows": [
                    _build_display_row("guardrail_blocks", runtime_governance_display.get("guardrail_blocks") or 0),
                    _build_display_row("validated", runtime_governance_display.get("validated_count") or 0),
                    _build_display_row("repaired", runtime_governance_display.get("repaired_count") or 0),
                    _build_display_row("fallback", runtime_governance_display.get("fallback_count") or 0),
                ],
                "badges": [
                    str(item)
                    for item in list(runtime_governance_display.get("reason_codes") or [])[:2]
                    if str(item or "").strip()
                ],
            }
        )
    return cards


def _read_contract_root(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, dict):
        if isinstance(payload.get("protocol"), dict) or isinstance(payload.get("task_bus"), dict):
            return payload
        return _read_legacy_snapshot_root(payload)
    return None


def _set_response_header_if_present(
    response: Response,
    *,
    header_name: str,
    value: Any,
) -> None:
    if value:
        response.headers[header_name] = str(value)


def _contract_header_pairs(root: Mapping[str, Any]) -> tuple[tuple[str, Any], ...]:
    protocol = dict(root.get("protocol") or {})
    task_bus = dict(root.get("task_bus") or {})
    coverage = dict(root.get("coverage") or {})
    artifact_taxonomy = dict(root.get("artifact_taxonomy") or {})
    task_bus_schema = protocol.get("task_bus_schema_version") or task_bus.get("schema_version")
    return (
        ("X-Bounded-Workflow-Schema", protocol.get("schema_version")),
        ("X-Task-Bus-Schema", task_bus_schema),
        ("X-Coverage-Schema", coverage.get("schema_version")),
        ("X-Artifact-Taxonomy-Schema", artifact_taxonomy.get("schema_version")),
        ("X-Commander-Domain", protocol.get("domain")),
        ("X-Commander-Operation", protocol.get("operation")),
    )


def _resolve_runtime_or_fallback_payload(
    *,
    get_runtime: Callable[[], Any | None],
    runtime_fetch: Callable[[Any], Any],
    fallback_fetch: Callable[[], Any],
) -> Any:
    runtime = get_runtime()
    return runtime_fetch(runtime) if runtime is not None else fallback_fetch()


def _read_legacy_snapshot_root(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    snapshot = payload.get("snapshot")
    if isinstance(snapshot, dict) and (
        isinstance(snapshot.get("protocol"), dict)
        or isinstance(snapshot.get("task_bus"), dict)
    ):
        return dict(snapshot)
    return None


def _canonical_display_body(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"reply": str(payload)}

    body = dict(payload)
    snapshot_root = _read_legacy_snapshot_root(body)
    if snapshot_root is None:
        return body

    canonical = dict(snapshot_root)
    for key, value in body.items():
        if key != "snapshot":
            canonical[key] = value
    return canonical


def build_contract_payload_response(payload: Any, *, status_code: int = 200):
    response = jsonify(payload)
    response.status_code = int(status_code)
    root = _read_contract_root(payload)
    if not root:
        return response

    for header_name, value in _contract_header_pairs(root):
        _set_response_header_if_present(
            response,
            header_name=header_name,
            value=value,
        )
    return response


def jsonify_contract_payload(payload: Any, status_code: int = 200):
    return build_contract_payload_response(payload, status_code=status_code)


def build_display_payload(payload: Any) -> dict[str, Any]:
    body = _canonical_display_body(payload)
    display = build_human_display(body)
    body.setdefault(
        "human_reply",
        str(display.get("text") or body.get("reply") or body.get("message") or ""),
    )
    body.setdefault(
        "display",
        {
            "available": bool(display.get("available")),
            "title": str(display.get("title") or ""),
            "summary": str(display.get("summary") or ""),
            "text": str(display.get("text") or ""),
            "sections": list(display.get("sections") or []),
            "cards": _build_training_display_cards(body),
            "suggested_actions": list(display.get("suggested_actions") or []),
            "recommended_next_step": str(display.get("recommended_next_step") or ""),
            "risk_level": str(display.get("risk_level") or ""),
            "synthesized": bool(display.get("synthesized")),
        },
    )
    return body


def respond_with_display(payload: Any, *, status_code: int = 200, view: str = "json"):
    enriched = build_display_payload(payload)
    if view == "human":
        return Response(
            str(enriched.get("human_reply") or ""),
            status=int(status_code),
            mimetype="text/plain; charset=utf-8",
        )
    return build_contract_payload_response(enriched, status_code=status_code)
