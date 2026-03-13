"""Shared training summary helpers for commander runtime flows."""

from __future__ import annotations

from typing import Any

from app.commander_workflow_support import jsonable
from app.lab.evaluation import (
    build_promotion_summary,
    build_training_evaluation_summary,
    build_training_memory_summary,
)


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


def attach_training_lab_paths(payload: dict[str, Any], lab: dict[str, Any]) -> None:
    payload["training_lab"] = {
        "plan": {
            "plan_id": lab["plan"]["plan_id"],
            "path": lab["plan"]["artifacts"]["plan_path"],
            "guardrails": dict(lab["plan"].get("guardrails") or {}),
        },
        "run": {
            "run_id": lab["run"]["run_id"],
            "path": lab["evaluation"]["artifacts"]["run_path"],
        },
        "evaluation": {
            "run_id": lab["evaluation"]["run_id"],
            "path": lab["evaluation"]["artifacts"]["evaluation_path"],
            "promotion": summarize_training_evaluation_brief(lab["evaluation"]),
        },
    }
