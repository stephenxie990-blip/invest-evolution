"""Commander status, diagnostics, and training lab summaries."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from invest_evolution.application.lab import (
    build_promotion_summary,
    build_training_evaluation_summary,
    build_training_memory_summary,
    collect_core_explainability_artifacts,
)
from invest_evolution.application.runtime_contracts import safe_read_json, safe_read_jsonl, safe_read_text
from invest_evolution.market_data.manager import MarketQueryService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DomainResponseSpec:
    domain: str
    operation: str
    runtime_tool: str
    phase: str
    runtime_method: str | None = None
    agent_kind: str | None = None
    available_tools: tuple[str, ...] = ()
    extra_phases: tuple[str, ...] = ()
    phase_stats: dict[str, Any] | None = None
    extra_policy: dict[str, Any] | None = None


@dataclass(frozen=True)
class DomainResponseBundle:
    payload: dict[str, Any]
    spec: DomainResponseSpec

def _bounded_tail(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    normalized_limit = int(limit)
    if normalized_limit <= 0:
        return []
    return rows[-normalized_limit:]


def append_event_row(path: Path, event: str, payload: dict[str, Any], *, source: str = "runtime") -> dict[str, Any]:
    normalized_payload = dict(payload or {})
    row = {
        "id": f"evt-{int(datetime.now().timestamp() * 1000)}",
        "ts": datetime.now().isoformat(),
        "event": str(event),
        "source": str(source),
        "payload": normalized_payload,
    }
    for key in ("session_key", "chat_id", "request_id", "channel", "stage"):
        value = str(normalized_payload.get(key) or "").strip()
        if value:
            row[key] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def read_event_rows(path: Path, *, limit: int = 100) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    invalid_lines = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            invalid_lines += 1
            continue
    if invalid_lines:
        logger.warning("Skipped %d invalid runtime event row(s) from %s", invalid_lines, path)
    return _bounded_tail(rows, limit)


def summarize_event_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    latest = rows[-1] if rows else None
    for row in rows:
        key = str(row.get("event") or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return {
        "count": len(rows),
        "counts": counts,
        "latest": latest,
        "window_start": rows[0].get("ts") if rows else "",
        "window_end": rows[-1].get("ts") if rows else "",
    }


def memory_brief_row(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row or {})
    ts_ms = item.get("ts_ms")
    if ts_ms:
        try:
            item["ts"] = datetime.fromtimestamp(int(ts_ms) / 1000).isoformat()
        except (TypeError, ValueError, OSError, OverflowError) as exc:
            logger.warning("Failed to normalize memory ts_ms=%r: %s", ts_ms, exc)
            item["ts"] = ""
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    if metadata:
        item["summary"] = metadata.get("summary")
        item["training_run"] = bool(metadata.get("training_run"))
    return item


def _as_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_stock_codes(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    codes: list[str] = []
    for item in values:
        code = ""
        if isinstance(item, str):
            code = item.strip()
        elif isinstance(item, dict):
            code = str(item.get("code") or item.get("ts_code") or "").strip()
        if code:
            codes.append(code)
    return codes


def _diff_params(current: Any, previous: Any) -> dict[str, Any]:
    current_dict = dict(current or {}) if isinstance(current, dict) else {}
    previous_dict = dict(previous or {}) if isinstance(previous, dict) else {}
    all_keys = sorted(set(current_dict) | set(previous_dict))
    changed = []
    for key in all_keys:
        if current_dict.get(key) != previous_dict.get(key):
            changed.append({"key": key, "current": current_dict.get(key), "previous": previous_dict.get(key)})
    return {"changed_count": len(changed), "items": changed}


def _build_strategy_compare(runtime: Any, row: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    if runtime is None:
        return {"has_previous": False}
    rows = runtime.memory.search(query="", limit=200)
    current_id = str(row.get("id") or "")
    previous_row = None
    for candidate in reversed(rows):
        if str(candidate.get("id") or "") == current_id:
            continue
        meta = candidate.get("metadata") if isinstance(candidate.get("metadata"), dict) else {}
        if meta.get("training_run"):
            previous_row = candidate
            break
    if previous_row is None:
        return {"has_previous": False}
    current_result = dict((list(metadata.get("results") or []) or [{}])[-1] or {})
    previous_meta = previous_row.get("metadata") if isinstance(previous_row.get("metadata"), dict) else {}
    previous_result = dict((list(previous_meta.get("results") or []) or [{}])[-1] or {})
    current_selected = _normalize_stock_codes(current_result.get("selected_stocks"))
    previous_selected = _normalize_stock_codes(previous_result.get("selected_stocks"))
    current_return = _as_float(current_result.get("return_pct"))
    previous_return = _as_float(previous_result.get("return_pct"))
    current_selected_count = int(current_result.get("selected_count") or len(current_selected))
    previous_selected_count = int(previous_result.get("selected_count") or len(previous_selected))
    current_trade_count = int(current_result.get("trade_count") or 0)
    previous_trade_count = int(previous_result.get("trade_count") or 0)
    current_opt_count = int(current_result.get("optimization_event_count") or len(current_result.get("optimization_events") or []))
    previous_opt_count = int(previous_result.get("optimization_event_count") or len(previous_result.get("optimization_events") or []))
    return {
        "has_previous": True,
        "previous_record": memory_brief_row(previous_row),
        "current_cycle_id": current_result.get("cycle_id"),
        "previous_cycle_id": previous_result.get("cycle_id"),
        "metrics": {
            "return_pct": {"current": current_return, "previous": previous_return, "delta": (current_return - previous_return) if current_return is not None and previous_return is not None else None},
            "selected_count": {"current": current_selected_count, "previous": previous_selected_count, "delta": current_selected_count - previous_selected_count},
            "trade_count": {"current": current_trade_count, "previous": previous_trade_count, "delta": current_trade_count - previous_trade_count},
            "optimization_event_count": {"current": current_opt_count, "previous": previous_opt_count, "delta": current_opt_count - previous_opt_count},
        },
        "flags": {
            "selection_mode": {"current": current_result.get("selection_mode"), "previous": previous_result.get("selection_mode"), "changed": current_result.get("selection_mode") != previous_result.get("selection_mode")},
            "review_applied": {"current": bool(current_result.get("review_applied", False)), "previous": bool(previous_result.get("review_applied", False)), "changed": bool(current_result.get("review_applied", False)) != bool(previous_result.get("review_applied", False))},
            "benchmark_passed": {"current": bool(current_result.get("benchmark_passed", False)), "previous": bool(previous_result.get("benchmark_passed", False)), "changed": bool(current_result.get("benchmark_passed", False)) != bool(previous_result.get("benchmark_passed", False))},
        },
        "selected_stocks": {"current": current_selected, "previous": previous_selected, "added": [code for code in current_selected if code not in previous_selected], "removed": [code for code in previous_selected if code not in current_selected], "kept": [code for code in current_selected if code in previous_selected]},
        "params": _diff_params(current_result.get("params"), previous_result.get("params")),
    }


def build_memory_detail(runtime: Any, row: dict[str, Any]) -> dict[str, Any]:
    item = memory_brief_row(row)
    metadata: dict[str, Any] = (
        dict(item.get("metadata") or {})
        if isinstance(item.get("metadata"), dict)
        else {}
    )
    results = list(metadata.get("results") or [])
    detailed_results = []
    optimization_cache: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        cycle = dict(result or {})
        artifacts = cycle.get("artifacts") if isinstance(cycle.get("artifacts"), dict) else {}
        cycle_id = cycle.get("cycle_id")
        cycle_result = safe_read_json(runtime, artifacts.get("cycle_result_path", "")) if artifacts else None
        selection_artifact = safe_read_json(runtime, artifacts.get("selection_artifact_json_path", "")) if artifacts else None
        manager_review_artifact = safe_read_json(runtime, artifacts.get("manager_review_artifact_json_path", "")) if artifacts else None
        allocation_review_artifact = safe_read_json(runtime, artifacts.get("allocation_review_artifact_json_path", "")) if artifacts else None
        config_snapshot = safe_read_json(runtime, cycle.get("config_snapshot_path", "")) if cycle.get("config_snapshot_path") else None
        optimization_path = artifacts.get("optimization_events_path", "") if artifacts else ""
        if optimization_path:
            optimization_cache.setdefault(optimization_path, safe_read_jsonl(runtime, optimization_path))
        optimization_events = optimization_cache.get(optimization_path, [])
        detailed_results.append({
            **cycle,
            "cycle_result": cycle_result,
            "selection_artifact": selection_artifact,
            "selection_artifact_markdown": safe_read_text(runtime, artifacts.get("selection_artifact_markdown_path", "")) if artifacts else "",
            "manager_review_artifact": manager_review_artifact,
            "manager_review_artifact_markdown": safe_read_text(runtime, artifacts.get("manager_review_artifact_markdown_path", "")) if artifacts else "",
            "allocation_review_artifact": allocation_review_artifact,
            "allocation_review_artifact_markdown": safe_read_text(runtime, artifacts.get("allocation_review_artifact_markdown_path", "")) if artifacts else "",
            "config_snapshot": config_snapshot,
            "optimization_events": [evt for evt in optimization_events if cycle_id is None or evt.get("cycle_id") in (None, cycle_id)],
        })
    return {
        "item": item,
        "details": {
            "summary": metadata.get("summary") or {},
            "runtime_summary": metadata.get("runtime_summary") or {},
            "results": detailed_results,
            "compare": _build_strategy_compare(runtime, row, metadata),
        },
    }


def attach_projected_domain_response(
    payload: dict[str, Any],
    *,
    spec: DomainResponseSpec,
    attach_workflow: Any,
) -> dict[str, Any]:
    return attach_workflow(
        payload,
        domain=spec.domain,
        operation=spec.operation,
        runtime_method=spec.runtime_method,
        runtime_tool=spec.runtime_tool,
        agent_kind=spec.agent_kind,
        available_tools=list(spec.available_tools) or None,
        phase=spec.phase,
        extra_phases=spec.extra_phases,
        phase_stats=dict(spec.phase_stats or {}),
        extra_policy=dict(spec.extra_policy or {}) if spec.extra_policy else None,
    )


def build_projected_domain_response_bundle(
    payload: dict[str, Any],
    *,
    spec: DomainResponseSpec,
) -> DomainResponseBundle:
    return DomainResponseBundle(payload=dict(payload or {}), spec=spec)


def _build_status_response_phase(detail_mode: str) -> tuple[str, str]:
    if detail_mode == "slow":
        return "invest_deep_status", "status_refresh"
    return "invest_quick_status", "status_read"


def _build_training_lab_summary_phase_stats(
    payload: dict[str, Any],
    *,
    limit: int,
) -> dict[str, Any]:
    return {
        "limit": int(limit),
        "plan_count": int(payload.get("plan_count", 0) or 0),
        "run_count": int(payload.get("run_count", 0) or 0),
        "evaluation_count": int(payload.get("evaluation_count", 0) or 0),
    }


def get_events_tail_response(
    runtime: Any,
    *,
    limit: int,
    attach_domain_readonly_workflow: Any,
) -> dict[str, Any]:
    bundle = build_events_tail_response_bundle(runtime, limit=limit)
    return attach_projected_domain_response(
        payload=bundle.payload,
        spec=bundle.spec,
        attach_workflow=attach_domain_readonly_workflow,
    )


def build_events_tail_response_bundle(
    runtime: Any,
    *,
    limit: int,
) -> DomainResponseBundle:
    return build_projected_domain_response_bundle(
        payload=build_events_tail_payload(runtime.cfg.runtime_events_path, limit=limit),
        spec=DomainResponseSpec(
            domain="runtime",
            operation="get_events_tail",
            runtime_tool="invest_events_tail",
            phase="events_tail_read",
            phase_stats={"limit": int(limit)},
        ),
    )


def get_events_summary_response(
    runtime: Any,
    *,
    limit: int,
    ok_status: str,
    attach_domain_readonly_workflow: Any,
) -> dict[str, Any]:
    bundle = build_events_summary_response_bundle(
        runtime,
        limit=limit,
        ok_status=ok_status,
    )
    return attach_projected_domain_response(
        payload=bundle.payload,
        spec=bundle.spec,
        attach_workflow=attach_domain_readonly_workflow,
    )


def build_events_summary_response_bundle(
    runtime: Any,
    *,
    limit: int,
    ok_status: str,
) -> DomainResponseBundle:
    return build_projected_domain_response_bundle(
        payload=build_events_summary_payload(
            runtime.cfg.runtime_events_path,
            limit=limit,
            ok_status=ok_status,
        ),
        spec=DomainResponseSpec(
            domain="runtime",
            operation="get_events_summary",
            runtime_tool="invest_events_summary",
            phase="events_summary_read",
            phase_stats={"limit": int(limit)},
        ),
    )


def get_runtime_diagnostics_response(
    runtime: Any,
    *,
    event_limit: int,
    memory_limit: int,
    attach_domain_readonly_workflow: Any,
) -> dict[str, Any]:
    bundle = build_runtime_diagnostics_response_bundle(
        runtime,
        event_limit=event_limit,
        memory_limit=memory_limit,
    )
    return attach_projected_domain_response(
        payload=bundle.payload,
        spec=bundle.spec,
        attach_workflow=attach_domain_readonly_workflow,
    )


def build_runtime_diagnostics_response_bundle(
    runtime: Any,
    *,
    event_limit: int,
    memory_limit: int,
) -> DomainResponseBundle:
    return build_projected_domain_response_bundle(
        payload=build_runtime_diagnostics(
            runtime,
            event_limit=event_limit,
            memory_limit=memory_limit,
        ),
        spec=DomainResponseSpec(
            domain="runtime",
            operation="get_runtime_diagnostics",
            runtime_tool="invest_runtime_diagnostics",
            phase="diagnostics_build",
            phase_stats={
                "event_limit": int(event_limit),
                "memory_limit": int(memory_limit),
            },
        ),
    )


def get_training_lab_summary_response(
    runtime: Any,
    *,
    limit: int,
    ok_status: str,
    attach_domain_readonly_workflow: Any,
) -> dict[str, Any]:
    bundle = build_training_lab_summary_response_bundle(
        runtime,
        limit=limit,
        ok_status=ok_status,
    )
    return attach_projected_domain_response(
        payload=bundle.payload,
        spec=bundle.spec,
        attach_workflow=attach_domain_readonly_workflow,
    )


def build_training_lab_summary_response_bundle(
    runtime: Any,
    *,
    limit: int,
    ok_status: str,
) -> DomainResponseBundle:
    payload = build_training_lab_summary_payload(
        lab_counts=runtime._lab_counts(),
        list_training_plans=runtime.list_training_plans,
        list_training_runs=runtime.list_training_runs,
        list_training_evaluations=runtime.list_training_evaluations,
        limit=limit,
        ok_status=ok_status,
    )
    return build_projected_domain_response_bundle(
        payload=payload,
        spec=DomainResponseSpec(
            domain="training",
            operation="get_training_lab_summary",
            runtime_tool="invest_training_lab_summary",
            phase="lab_summary_read",
            phase_stats=_build_training_lab_summary_phase_stats(payload, limit=limit),
        ),
    )


def get_status_response(
    runtime: Any,
    *,
    detail: str,
    attach_domain_readonly_workflow: Any,
) -> dict[str, Any]:
    bundle = build_status_response_bundle(runtime, detail=detail)
    return attach_projected_domain_response(
        payload=bundle.payload,
        spec=bundle.spec,
        attach_workflow=attach_domain_readonly_workflow,
    )


def build_status_response_bundle(
    runtime: Any,
    *,
    detail: str,
) -> DomainResponseBundle:
    snapshot = build_commander_status_snapshot(
        runtime,
        detail=detail,
        event_limit=20,
        include_recent_training_lab=True,
    )
    detail_mode = str(snapshot["detail_mode"])
    event_rows = list(snapshot["event_rows"])
    runtime_tool, phase = _build_status_response_phase(detail_mode)
    return build_projected_domain_response_bundle(
        payload=dict(snapshot["payload"]),
        spec=DomainResponseSpec(
            domain="runtime",
            operation="status",
            runtime_tool=runtime_tool,
            phase=phase,
            phase_stats={
                "detail_mode": detail_mode,
                "event_count": len(event_rows),
            },
        ),
    )


def build_runtime_diagnostics(runtime: Any, *, event_limit: int = 50, memory_limit: int = 20) -> dict[str, Any]:
    status = runtime.status(detail="fast")
    rows = read_event_rows(runtime.cfg.runtime_events_path, limit=event_limit)
    event_summary = summarize_event_rows(rows)
    memory_rows = runtime.memory.search(query="", limit=memory_limit)
    recent_training = [memory_brief_row(row) for row in memory_rows if (row.get("metadata") or {}).get("training_run")][-5:]
    diagnostics: list[str] = []
    runtime_state = status.get("runtime", {}).get("state")
    if runtime_state == "error":
        diagnostics.append("runtime_state=error")
    data = status.get("data") or {}
    quality = data.get("quality") if isinstance(data, dict) else None
    if isinstance(quality, dict) and not bool(quality.get("healthy", True)):
        diagnostics.append("data_quality_unhealthy")
    body = status.get("body") or {}
    last_result = body.get("last_result") if isinstance(body, dict) else None
    if isinstance(last_result, dict) and bool(last_result.get("degraded", False)):
        diagnostics.append("last_run_degraded")
    last_error = body.get("last_error") if isinstance(body, dict) else ""
    return {
        "status": "ok",
        "runtime": {"state": runtime_state, "current_task": status.get("runtime", {}).get("current_task"), "last_task": status.get("runtime", {}).get("last_task")},
        "event_summary": event_summary,
        "recent_events": rows,
        "recent_training_memory": recent_training,
        "last_error": last_error,
        "diagnostics": diagnostics,
        "restart_required": False,
    }


def jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value

def build_commander_promotion_summary(
    *,
    plan: dict[str, Any],
    ok_results: list[dict[str, Any]],
    avg_return_pct: float | None,
    avg_strategy_score: float | None,
    benchmark_pass_rate: float,
    leaderboard_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    baseline_manager_ids = [
        str(item)
        for item in ((plan.get("manager_scope") or {}).get("baseline_manager_ids") or [])
        if str(item).strip()
    ]
    baseline_entries = [
        entry
        for entry in list(leaderboard_entries or [])
        if str(entry.get("manager_id") or "") in baseline_manager_ids
    ]
    return build_promotion_summary(
        plan=plan,
        ok_results=ok_results,
        avg_return_pct=avg_return_pct,
        avg_strategy_score=avg_strategy_score,
        benchmark_pass_rate=benchmark_pass_rate,
        baseline_entries=baseline_entries,
    )


def build_commander_training_evaluation_summary(
    payload: dict[str, Any],
    *,
    plan: dict[str, Any],
    run_id: str,
    error: str = "",
    run_path: str,
    evaluation_path: str,
    leaderboard_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    results = list(payload.get("results") or [])
    ok_results = [item for item in results if item.get("status") == "ok"]
    returns = [float(item.get("return_pct") or 0.0) for item in ok_results]
    strategy_scores = [
        float((item.get("strategy_scores") or {}).get("overall_score", 0.0) or 0.0)
        for item in ok_results
    ]
    benchmark_passes = sum(1 for item in ok_results if bool(item.get("benchmark_passed", False)))
    avg_return_pct = round(sum(returns) / len(returns), 4) if returns else None
    avg_strategy_score = round(sum(strategy_scores) / len(strategy_scores), 4) if strategy_scores else None
    benchmark_pass_rate = round(benchmark_passes / len(ok_results), 4) if ok_results else 0.0
    promotion = build_commander_promotion_summary(
        plan=plan,
        ok_results=ok_results,
        avg_return_pct=avg_return_pct,
        avg_strategy_score=avg_strategy_score,
        benchmark_pass_rate=benchmark_pass_rate,
        leaderboard_entries=leaderboard_entries,
    )
    return build_training_evaluation_summary(
        payload=payload,
        plan=plan,
        run_id=run_id,
        error=error,
        promotion=promotion,
        run_path=run_path,
        evaluation_path=evaluation_path,
    )


def build_training_memory_entry(
    payload: dict[str, Any],
    *,
    rounds: int,
    mock: bool,
    status: str,
    error: str = "",
) -> dict[str, Any]:
    results = list(payload.get("results") or [])
    summary = build_training_memory_summary(
        payload=payload,
        rounds=rounds,
        mock=mock,
        status=status,
        error=error,
    )
    content = (
        f"训练记录 | status={status} | rounds={rounds} | mock={'true' if mock else 'false'} | "
        f"成功={summary['success_count']} | 跳过={summary['skipped_count']} | 失败={summary['error_count']}"
    )
    requested_modes = list(summary.get("requested_data_modes") or [])
    effective_modes = list(summary.get("effective_data_modes") or [])
    llm_modes = list(summary.get("llm_modes") or [])
    if summary.get("avg_return_pct") is not None:
        content += f" | 平均收益={summary['avg_return_pct']:+.2f}%"
    cycle_ids = list(summary.get("cycle_ids") or [])
    if cycle_ids:
        content += f" | 周期={','.join(str(item) for item in cycle_ids)}"
    if requested_modes:
        content += f" | 请求模式={','.join(requested_modes)}"
    if effective_modes:
        content += f" | 实际模式={','.join(effective_modes)}"
    if llm_modes:
        content += f" | LLM={','.join(llm_modes)}"
    if summary.get("degraded_count"):
        content += f" | degraded={summary['degraded_count']}"
    if error:
        content += f" | error={error}"
    return {
        "content": content,
        "metadata": {
            "training_run": True,
            "summary": jsonable(summary),
            "results": jsonable(results),
            "runtime_summary": jsonable(payload.get("summary") or {}),
            "source": "runtime.train_once",
        },
    }


def summarize_research_feedback_promotion(promotion: dict[str, Any]) -> dict[str, Any]:
    research_feedback = dict(promotion.get("research_feedback") or {})
    latest_feedback = dict(research_feedback.get("latest_feedback") or {})
    failed_checks = [dict(item) for item in list(research_feedback.get("failed_checks") or [])]
    reason_codes = [
        str(item.get("name") or "")
        for item in failed_checks
        if str(item.get("name") or "").strip()
    ]
    if not research_feedback.get("enabled", False):
        summary = "未启用 research_feedback 校准门。"
    elif research_feedback.get("passed", False):
        latest_summary = str(latest_feedback.get("summary") or "")
        summary = (
            f"research_feedback 校准门通过：{latest_summary}"
            if latest_summary
            else "research_feedback 校准门通过。"
        )
    else:
        latest_summary = str(latest_feedback.get("summary") or "")
        if not latest_feedback.get("available", False):
            summary = "未通过 research_feedback 校准门：缺少可用研究反馈样本。"
        elif latest_summary:
            summary = f"未通过 research_feedback 校准门：{latest_summary}"
        elif reason_codes:
            summary = f"未通过 research_feedback 校准门：{', '.join(reason_codes)}"
        else:
            summary = "未通过 research_feedback 校准门。"
    return {
        "enabled": bool(research_feedback.get("enabled", False)),
        "passed": bool(research_feedback.get("passed", False)),
        "summary": summary,
        "reason_codes": reason_codes,
        "latest_feedback": latest_feedback,
    }


def summarize_training_evaluation_brief(evaluation: dict[str, Any]) -> dict[str, Any]:
    promotion = dict(evaluation.get("promotion") or {})
    return {
        "verdict": str(promotion.get("verdict") or ""),
        "passed": bool(promotion.get("passed", False)),
        "research_feedback": summarize_research_feedback_promotion(promotion),
    }


def build_promotion_lineage_ops_panel(latest_result: dict[str, Any]) -> dict[str, Any]:
    result = dict(latest_result or {})
    promotion_record = dict(result.get("promotion_record") or {})
    lineage_record = dict(result.get("lineage_record") or {})
    if not promotion_record and not lineage_record:
        return {"available": False}

    active_runtime_config_ref = str(
        lineage_record.get("active_runtime_config_ref")
        or promotion_record.get("active_runtime_config_ref")
        or ""
    )
    candidate_runtime_config_ref = str(
        lineage_record.get("candidate_runtime_config_ref")
        or promotion_record.get("candidate_runtime_config_ref")
        or ""
    )
    candidate_runtime_config_meta_ref = str(
        lineage_record.get("candidate_runtime_config_meta_ref")
        or promotion_record.get("candidate_runtime_config_meta_ref")
        or ""
    )
    review_window = dict(
        lineage_record.get("review_basis_window")
        or promotion_record.get("review_basis_window")
        or {}
    )
    fitness_source_cycles = [
        int(item)
        for item in list(lineage_record.get("fitness_source_cycles") or [])
        if str(item).strip()
    ]
    basis_stage = str(
        lineage_record.get("basis_stage")
        or promotion_record.get("basis_stage")
        or dict(result.get("run_context") or {}).get("basis_stage")
        or ""
    )
    promotion_status = str(promotion_record.get("status") or "not_evaluated")
    gate_status = str(promotion_record.get("gate_status") or "not_applicable")
    lineage_status = str(lineage_record.get("lineage_status") or "unknown")
    active_candidate_drift = bool(
        active_runtime_config_ref and candidate_runtime_config_ref and active_runtime_config_ref != candidate_runtime_config_ref
    )
    ops_flags = {
        "candidate_pending": lineage_status == "candidate_pending",
        "awaiting_gate": gate_status == "awaiting_gate",
        "active_candidate_drift": active_candidate_drift,
        "has_review_window": bool(review_window),
        "has_fitness_source_cycles": bool(fitness_source_cycles),
    }
    warnings: list[str] = []
    if ops_flags["candidate_pending"]:
        warnings.append("候选配置仍待发布门确认")
    if active_candidate_drift:
        warnings.append("active 与 candidate 配置已发生漂移")
    if candidate_runtime_config_ref and not review_window:
        warnings.append("候选配置缺少 review basis window")
    if candidate_runtime_config_ref and not fitness_source_cycles:
        warnings.append("候选配置缺少 fitness source cycles")

    if ops_flags["candidate_pending"] or ops_flags["awaiting_gate"]:
        summary = "候选配置已生成，当前仍待发布门确认。"
    elif gate_status == "applied_to_active":
        summary = "候选配置已通过门控并应用到 active。"
    else:
        summary = "当前仅有 active 配置，尚未发现待发布候选。"

    return {
        "available": True,
        "summary": summary,
        "status": {
            "promotion_status": promotion_status,
            "gate_status": gate_status,
            "lineage_status": lineage_status,
            "basis_stage": basis_stage,
        },
        "refs": {
            "active_runtime_config_ref": active_runtime_config_ref,
            "candidate_runtime_config_ref": candidate_runtime_config_ref,
            "candidate_runtime_config_meta_ref": candidate_runtime_config_meta_ref,
        },
        "review_window": review_window,
        "fitness_source_cycles": fitness_source_cycles,
        "basis_stage": basis_stage,
        "ops_flags": ops_flags,
        "warnings": warnings,
        "mutation": {
            "trigger": str(
                promotion_record.get("mutation_trigger")
                or lineage_record.get("mutation_trigger")
                or ""
            ),
            "stage": str(
                promotion_record.get("mutation_stage")
                or lineage_record.get("mutation_stage")
                or ""
            ),
            "notes": str(
                promotion_record.get("mutation_notes")
                or lineage_record.get("mutation_notes")
                or ""
            ),
        },
    }


def summarize_latest_training_result(payload: dict[str, Any]) -> dict[str, Any]:
    results = [dict(item) for item in list(payload.get("results") or []) if isinstance(item, dict)]
    latest = dict(results[-1]) if results else {}
    ops_panel = build_promotion_lineage_ops_panel(latest)
    return {
        "cycle_id": latest.get("cycle_id"),
        "status": str(latest.get("status") or ""),
        "return_pct": latest.get("return_pct"),
        "benchmark_passed": bool(latest.get("benchmark_passed", False)),
        "core_artifacts": collect_core_explainability_artifacts(latest),
        "ab_comparison": dict(latest.get("ab_comparison") or {}),
        "promotion_record": dict(latest.get("promotion_record") or {}),
        "lineage_record": dict(latest.get("lineage_record") or {}),
        "review_decision": dict(latest.get("review_decision") or {}),
        "causal_diagnosis": dict(latest.get("causal_diagnosis") or {}),
        "similarity_summary": dict(latest.get("similarity_summary") or {}),
        "similar_results": [dict(item) for item in list(latest.get("similar_results") or [])],
        "ops_panel": ops_panel,
    }


def attach_training_lab_paths(payload: dict[str, Any], lab: dict[str, Any]) -> None:
    latest_result = summarize_latest_training_result(dict(lab["run"].get("payload") or {}))
    payload["training_lab"] = {
        "plan": {
            "plan_id": lab["plan"]["plan_id"],
            "path": lab["plan"]["artifacts"]["plan_path"],
            "guardrails": dict(lab["plan"].get("guardrails") or {}),
        },
        "run": {
            "run_id": lab["run"]["run_id"],
            "path": lab["evaluation"]["artifacts"]["run_path"],
            "latest_result": latest_result,
            "ops_panel": dict(latest_result.get("ops_panel") or {}),
        },
        "evaluation": {
            "run_id": lab["evaluation"]["run_id"],
            "path": lab["evaluation"]["artifacts"]["evaluation_path"],
            "promotion": summarize_training_evaluation_brief(lab["evaluation"]),
        },
    }


def normalize_status_detail(detail: str) -> str:
    detail_mode = str(detail or "fast").strip().lower()
    return detail_mode if detail_mode in {"fast", "slow"} else "fast"


def collect_data_status(detail_mode: str) -> dict[str, Any]:
    try:
        return MarketQueryService().get_status_summary(refresh=(detail_mode == "slow"))
    except Exception as exc:
        logger.warning("Failed to collect data status for detail_mode=%s: %s", detail_mode, exc)
        return {"status": "error", "error": str(exc), "detail_mode": detail_mode}


def _latest_run_summary(latest_runs: list[dict[str, Any]] | None) -> dict[str, Any]:
    latest = dict(list(latest_runs or [])[:1][0]) if list(latest_runs or []) else {}
    latest_result = dict(latest.get("latest_result") or {})
    ops_panel = build_promotion_lineage_ops_panel(latest_result) if latest_result else {}
    if not latest and not latest_result:
        return {}
    return {
        "run_id": latest.get("run_id"),
        "status": str(latest.get("status") or ""),
        "latest_result": latest_result,
        "ops_panel": ops_panel if ops_panel.get("available", False) else {},
    }


def _latest_evaluation_summary(latest_evaluations: list[dict[str, Any]] | None) -> dict[str, Any]:
    latest = dict(list(latest_evaluations or [])[:1][0]) if list(latest_evaluations or []) else {}
    if not latest:
        return {}
    return {
        "run_id": latest.get("run_id"),
        "status": str(latest.get("status") or ""),
        "assessment": dict(latest.get("assessment") or {}),
        "promotion": dict(latest.get("promotion") or {}),
    }


def _latest_governance_summary(latest_evaluations: list[dict[str, Any]] | None) -> dict[str, Any]:
    latest = dict(list(latest_evaluations or [])[:1][0]) if list(latest_evaluations or []) else {}
    governance_metrics = dict(latest.get("governance_metrics") or {})
    realism_summary = dict(latest.get("realism_summary") or {})
    if not governance_metrics and not realism_summary:
        return {}
    return {
        "run_id": latest.get("run_id"),
        "governance_metrics": governance_metrics,
        "realism_summary": realism_summary,
        "promotion": dict(latest.get("promotion") or {}),
    }


def _brain_governance_metrics(runtime: Any) -> dict[str, Any]:
    snapshot = getattr(getattr(runtime, "brain", None), "_governance_metrics_snapshot", None)
    if callable(snapshot):
        try:
            return jsonable(snapshot())
        except Exception as exc:
            logger.warning("Failed to collect brain governance metrics: %s", exc)
            return {}
    return {}


def build_training_lab_status(
    *,
    lab_counts: dict[str, Any],
    latest_plans: list[dict[str, Any]] | None,
    latest_runs: list[dict[str, Any]] | None,
    latest_evaluations: list[dict[str, Any]] | None,
    include_recent: bool,
) -> dict[str, Any]:
    payload = {**dict(lab_counts or {})}
    payload.update(
        _build_training_lab_recent_payload(
            latest_plans=latest_plans if include_recent else [],
            latest_runs=latest_runs if include_recent else [],
            latest_evaluations=latest_evaluations if include_recent else [],
        )
    )
    return payload


def _build_training_lab_recent_payload(
    *,
    latest_plans: list[dict[str, Any]] | None,
    latest_runs: list[dict[str, Any]] | None,
    latest_evaluations: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    normalized_plans = list(latest_plans or [])
    normalized_runs = list(latest_runs or [])
    normalized_evaluations = list(latest_evaluations or [])
    return {
        "latest_plans": normalized_plans,
        "latest_runs": normalized_runs,
        "latest_evaluations": normalized_evaluations,
        "latest_run_summary": _latest_run_summary(normalized_runs),
        "latest_evaluation_summary": _latest_evaluation_summary(
            normalized_evaluations
        ),
        "governance_summary": _latest_governance_summary(normalized_evaluations),
    }


def _collect_training_lab_recent_payload(
    *,
    list_training_plans: Any,
    list_training_runs: Any,
    list_training_evaluations: Any,
    limit: int,
) -> dict[str, Any]:
    return _build_training_lab_recent_payload(
        latest_plans=list(list_training_plans(limit=limit).get("items", [])),
        latest_runs=list(list_training_runs(limit=limit).get("items", [])),
        latest_evaluations=list(
            list_training_evaluations(limit=limit).get("items", [])
        ),
    )


def collect_training_lab_status(
    *,
    lab_counts: dict[str, Any],
    include_recent: bool,
    list_training_plans: Any,
    list_training_runs: Any,
    list_training_evaluations: Any,
) -> dict[str, Any]:
    recent_payload = (
        _collect_training_lab_recent_payload(
            list_training_plans=list_training_plans,
            list_training_runs=list_training_runs,
            list_training_evaluations=list_training_evaluations,
            limit=3,
        )
        if include_recent
        else _build_training_lab_recent_payload(
            latest_plans=[],
            latest_runs=[],
            latest_evaluations=[],
        )
    )
    return build_training_lab_status(
        lab_counts=lab_counts,
        latest_plans=list(recent_payload.get("latest_plans", [])),
        latest_runs=list(recent_payload.get("latest_runs", [])),
        latest_evaluations=list(recent_payload.get("latest_evaluations", [])),
        include_recent=include_recent,
    )


def build_events_tail_payload(runtime_events_path: Path, *, limit: int) -> dict[str, Any]:
    rows = read_event_rows(runtime_events_path, limit=limit)
    return {"count": len(rows), "items": rows}


def build_events_summary_payload(
    runtime_events_path: Path,
    *,
    limit: int,
    ok_status: str,
) -> dict[str, Any]:
    rows = read_event_rows(runtime_events_path, limit=limit)
    return {"status": ok_status, "summary": summarize_event_rows(rows), "items": rows}


def build_training_lab_summary_payload(
    *,
    lab_counts: dict[str, Any],
    list_training_plans: Any,
    list_training_runs: Any,
    list_training_evaluations: Any,
    limit: int,
    ok_status: str,
) -> dict[str, Any]:
    recent_payload = _collect_training_lab_recent_payload(
        list_training_plans=list_training_plans,
        list_training_runs=list_training_runs,
        list_training_evaluations=list_training_evaluations,
        limit=limit,
    )
    return {
        "status": ok_status,
        **dict(lab_counts or {}),
        **recent_payload,
    }


def build_runtime_status_payload(
    *,
    detail_mode: str,
    instance_id: str,
    workspace: str,
    playbook_dir: str,
    model: str,
    autopilot_enabled: bool,
    heartbeat_enabled: bool,
    training_interval_sec: int,
    heartbeat_interval_sec: int,
    runtime_state: str,
    started: str,
    current_task: dict[str, Any] | None,
    last_task: dict[str, Any] | None,
    runtime_lock_file: Path,
    runtime_lock_active: bool,
    training_lock_file: Path,
    training_lock_active: bool,
    brain_tool_count: int,
    brain_session_count: int,
    brain_governance_metrics: dict[str, Any],
    cron_status: dict[str, Any],
    body_snapshot: dict[str, Any],
    memory_stats: dict[str, Any],
    bridge_status: dict[str, Any],
    plugin_tool_names: set[str] | list[str],
    playbooks: list[dict[str, Any]],
    enabled_playbook_count: int,
    config_payload: dict[str, Any],
    data_status: dict[str, Any],
    event_rows: list[dict[str, Any]],
    training_lab_status: dict[str, Any],
) -> dict[str, Any]:
    rows = list(event_rows or [])
    return jsonable(
        {
            "ts": datetime.now().isoformat(),
            "detail_mode": detail_mode,
            "instance_id": instance_id,
            "workspace": workspace,
            "playbook_dir": playbook_dir,
            "model": model,
            "autopilot_enabled": autopilot_enabled,
            "heartbeat_enabled": heartbeat_enabled,
            "training_interval_sec": training_interval_sec,
            "heartbeat_interval_sec": heartbeat_interval_sec,
            "runtime": {
                "state": runtime_state,
                "started": started,
                "current_task": current_task,
                "last_task": last_task,
                "runtime_lock_file": str(runtime_lock_file),
                "runtime_lock_active": runtime_lock_active,
                "training_lock_file": str(training_lock_file),
                "training_lock_active": training_lock_active,
                "state_source": "live_runtime",
                "live_runtime": bool(started or runtime_lock_active),
            },
            "brain": {
                "tool_count": brain_tool_count,
                "session_count": brain_session_count,
                "cron": cron_status,
                "governance_metrics": brain_governance_metrics,
            },
            "body": body_snapshot,
            "memory": memory_stats,
            "bridge": bridge_status,
            "plugins": {
                "count": len(list(plugin_tool_names or [])),
                "items": sorted(str(item) for item in list(plugin_tool_names or [])),
            },
            "playbooks": {
                "total": len(list(playbooks or [])),
                "enabled": enabled_playbook_count,
                "items": list(playbooks or []),
            },
            "config": config_payload,
            "data": data_status,
            "events": summarize_event_rows(rows),
            "training_lab": training_lab_status,
        }
    )


def build_commander_status_payload(
    runtime: Any,
    *,
    detail_mode: str,
    event_rows: list[dict[str, Any]] | None = None,
    include_recent_training_lab: bool = True,
) -> dict[str, Any]:
    runtime_state, current_task, last_task = runtime._snapshot_runtime_fields()
    playbook_items = [playbook.to_dict() for playbook in runtime.playbook_registry.playbooks]
    training_lab_status = collect_training_lab_status(
        lab_counts=runtime._lab_counts(),
        include_recent=include_recent_training_lab,
        list_training_plans=runtime.list_training_plans,
        list_training_runs=runtime.list_training_runs,
        list_training_evaluations=runtime.list_training_evaluations,
    )
    return build_runtime_status_payload(
        detail_mode=detail_mode,
        instance_id=runtime.instance_id,
        workspace=str(runtime.cfg.workspace),
        playbook_dir=str(runtime.cfg.playbook_dir),
        model=runtime.cfg.model,
        autopilot_enabled=runtime.cfg.autopilot_enabled,
        heartbeat_enabled=runtime.cfg.heartbeat_enabled,
        training_interval_sec=runtime.cfg.training_interval_sec,
        heartbeat_interval_sec=runtime.cfg.heartbeat_interval_sec,
        runtime_state=runtime_state,
        started=runtime._started,
        current_task=current_task,
        last_task=last_task,
        runtime_lock_file=runtime.cfg.runtime_lock_file,
        runtime_lock_active=runtime.cfg.runtime_lock_file.exists(),
        training_lock_file=runtime.cfg.training_lock_file,
        training_lock_active=runtime.cfg.training_lock_file.exists(),
        brain_tool_count=len(runtime.brain.tools),
        brain_session_count=runtime.brain.session_count,
        brain_governance_metrics=_brain_governance_metrics(runtime),
        cron_status=runtime.cron.status(),
        body_snapshot=runtime.body.snapshot(),
        memory_stats=runtime.memory.stats(),
        bridge_status=runtime.bridge.status(),
        plugin_tool_names=runtime._plugin_tool_names,
        playbooks=playbook_items,
        enabled_playbook_count=len(runtime.playbook_registry.list_playbooks(only_enabled=True)),
        config_payload=runtime.config_service.get_masked_payload(),
        data_status=runtime._collect_data_status(detail_mode),
        event_rows=list(event_rows or []),
        training_lab_status=training_lab_status,
    )


def build_commander_status_snapshot(
    runtime: Any,
    *,
    detail: str,
    event_limit: int = 20,
    include_recent_training_lab: bool = True,
) -> dict[str, Any]:
    detail_mode = normalize_status_detail(detail)
    event_rows = read_event_rows(runtime.cfg.runtime_events_path, limit=event_limit)
    payload = build_commander_status_payload(
        runtime,
        detail_mode=detail_mode,
        event_rows=event_rows,
        include_recent_training_lab=include_recent_training_lab,
    )
    return {
        "detail_mode": detail_mode,
        "event_rows": event_rows,
        "payload": payload,
    }


def build_persisted_status_payload(runtime: Any) -> dict[str, Any]:
    return build_commander_status_payload(
        runtime,
        detail_mode=normalize_status_detail("fast"),
        event_rows=[],
        include_recent_training_lab=False,
    )
