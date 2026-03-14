"""Shared training summary helpers for commander runtime flows."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.commander_support.workflow import jsonable
from app.lab.evaluation import (
    build_promotion_summary,
    build_training_evaluation_summary,
    build_training_memory_summary,
)
from market_data import DataSourceUnavailableError


def build_commander_promotion_summary(
    *,
    plan: dict[str, Any],
    ok_results: list[dict[str, Any]],
    avg_return_pct: float | None,
    avg_strategy_score: float | None,
    benchmark_pass_rate: float,
    leaderboard_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    baseline_models = [
        str(item)
        for item in ((plan.get("model_scope") or {}).get("baseline_models") or [])
        if str(item).strip()
    ]
    baseline_entries = [
        entry
        for entry in list(leaderboard_entries or [])
        if str(entry.get("model_name") or "") in baseline_models
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

    active_config_ref = str(
        lineage_record.get("active_config_ref")
        or promotion_record.get("active_config_ref")
        or ""
    )
    candidate_config_ref = str(
        lineage_record.get("candidate_config_ref")
        or promotion_record.get("candidate_config_ref")
        or ""
    )
    candidate_meta_ref = str(
        lineage_record.get("candidate_meta_ref")
        or promotion_record.get("candidate_meta_ref")
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
        active_config_ref and candidate_config_ref and active_config_ref != candidate_config_ref
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
    if candidate_config_ref and not review_window:
        warnings.append("候选配置缺少 review basis window")
    if candidate_config_ref and not fitness_source_cycles:
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
            "active_config_ref": active_config_ref,
            "candidate_config_ref": candidate_config_ref,
            "candidate_meta_ref": candidate_meta_ref,
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


def load_leaderboard_snapshot(training_output_dir: str | Path) -> dict[str, Any]:
    path = Path(training_output_dir).parent / "leaderboard.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def record_training_lab_artifacts(
    *,
    training_lab: Any,
    build_training_evaluation_summary: Any,
    new_run_id: Any,
    plan: dict[str, Any],
    payload: dict[str, Any],
    status: str,
    error: str = "",
) -> dict[str, Any]:
    run_id = new_run_id()
    eval_payload = build_training_evaluation_summary(payload, plan=plan, run_id=run_id, error=error)
    return training_lab.record_training_lab_artifacts(
        payload=payload,
        plan=plan,
        status=status,
        eval_payload=eval_payload,
        run_id=run_id,
        error=error,
    )


def append_training_memory(
    memory: Any,
    payload: dict[str, Any],
    *,
    rounds: int,
    mock: bool,
    status: str,
    error: str = "",
) -> None:
    entry = build_training_memory_entry(
        payload,
        rounds=rounds,
        mock=mock,
        status=status,
        error=error,
    )
    memory.append(
        kind="training_run",
        session_key="runtime:train",
        content=str(entry.get("content") or ""),
        metadata=dict(entry.get("metadata") or {}),
    )


def mark_training_plan_running(
    *,
    plan: dict[str, Any],
    plan_path: Path,
    write_json_artifact: Any,
    begin_task: Any,
    set_runtime_state: Any,
    memory: Any,
    rounds: int,
    mock: bool,
    plan_id: str,
    training_state: str,
) -> None:
    plan["status"] = "running"
    plan["started_at"] = datetime.now().isoformat()
    write_json_artifact(plan_path, plan)
    begin_task(
        "train_plan",
        str(plan.get("source", "manual")),
        rounds=rounds,
        mock=mock,
        plan_id=plan_id,
    )
    set_runtime_state(training_state)
    memory.append_audit(
        "train_requested",
        "runtime:train",
        {"rounds": rounds, "mock": mock, "plan_id": plan_id},
    )


def finalize_training_execution(
    *,
    plan: dict[str, Any],
    payload: dict[str, Any],
    status: str,
    rounds: int,
    mock: bool,
    plan_id: str,
    record_training_lab_artifacts_impl: Any,
    attach_training_lab_paths_impl: Any,
    append_training_memory_impl: Any,
    complete_runtime_task: Any,
    idle_state: str,
    busy_state: str,
    error_state: str,
    error: str = "",
    wrap_training_execution_payload: Any | None = None,
) -> dict[str, Any]:
    lab = record_training_lab_artifacts_impl(
        plan=plan,
        payload=payload,
        status=status,
        error=error,
    )
    attach_training_lab_paths_impl(payload, lab)
    append_training_memory_impl(
        payload,
        rounds=rounds,
        mock=mock,
        status=status,
        error=error,
    )
    state = error_state if status == error_state else (busy_state if status == busy_state else idle_state)
    complete_runtime_task(
        state=state,
        status=status,
        rounds=rounds,
        mock=mock,
        plan_id=plan_id,
    )
    if wrap_training_execution_payload is None:
        return payload
    return wrap_training_execution_payload(
        payload,
        plan_id=str(plan_id),
        rounds=rounds,
        mock=mock,
    )


async def execute_training_plan_flow(
    *,
    plan_path: Path,
    plan: dict[str, Any],
    experiment_spec: dict[str, Any],
    rounds: int,
    mock: bool,
    plan_id: str,
    body: Any,
    body_snapshot: Any,
    build_run_cycles_kwargs: Any,
    write_json_artifact: Any,
    begin_task: Any,
    set_runtime_state: Any,
    memory: Any,
    record_training_lab_artifacts_impl: Any,
    attach_training_lab_paths_impl: Any,
    append_training_memory_impl: Any,
    complete_runtime_task: Any,
    wrap_training_execution_payload: Any,
    ok_status: str,
    busy_state: str,
    idle_state: str,
    training_state: str,
    error_state: str,
) -> dict[str, Any]:
    mark_training_plan_running(
        plan=plan,
        plan_path=plan_path,
        write_json_artifact=write_json_artifact,
        begin_task=begin_task,
        set_runtime_state=set_runtime_state,
        memory=memory,
        rounds=rounds,
        mock=mock,
        plan_id=plan_id,
        training_state=training_state,
    )
    try:
        run_cycles_kwargs = build_run_cycles_kwargs(
            plan=plan,
            rounds=rounds,
            mock=mock,
            experiment_spec=experiment_spec,
        )
        payload = await body.run_cycles(**run_cycles_kwargs)
        if data_error := body._extract_data_source_error(payload):
            raise DataSourceUnavailableError.from_payload(data_error)
        status = str(payload.get("status", ok_status))
        return finalize_training_execution(
            plan=plan,
            payload=payload,
            status=status,
            rounds=rounds,
            mock=mock,
            plan_id=plan_id,
            record_training_lab_artifacts_impl=record_training_lab_artifacts_impl,
            attach_training_lab_paths_impl=attach_training_lab_paths_impl,
            append_training_memory_impl=append_training_memory_impl,
            complete_runtime_task=complete_runtime_task,
            idle_state=idle_state,
            busy_state=busy_state,
            error_state=error_state,
            wrap_training_execution_payload=wrap_training_execution_payload,
        )
    except Exception as exc:
        error_payload = {"results": [], "summary": body_snapshot()}
        finalize_training_execution(
            plan=plan,
            payload=error_payload,
            status=error_state,
            rounds=rounds,
            mock=mock,
            plan_id=plan_id,
            record_training_lab_artifacts_impl=record_training_lab_artifacts_impl,
            attach_training_lab_paths_impl=attach_training_lab_paths_impl,
            append_training_memory_impl=append_training_memory_impl,
            complete_runtime_task=complete_runtime_task,
            idle_state=idle_state,
            busy_state=busy_state,
            error_state=error_state,
            error=str(exc),
        )
        raise
