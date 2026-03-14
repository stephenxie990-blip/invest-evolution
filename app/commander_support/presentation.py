"""Presentation helpers for human-readable commander responses."""

from __future__ import annotations

from typing import Any

from app.commander_support.training import build_promotion_lineage_ops_panel


def _latest_training_result_summary(body: dict[str, Any]) -> dict[str, Any]:
    training_lab = dict(body.get("training_lab") or {})
    run = dict(training_lab.get("run") or {})
    latest = dict(run.get("latest_result") or body.get("latest_result") or {})
    if latest:
        return latest
    nested_payload = dict(body.get("payload") or {})
    results = [
        dict(item)
        for item in list(nested_payload.get("results") or body.get("results") or [])
        if isinstance(item, dict)
    ]
    return dict(results[-1]) if results else {}


def _fallback_sections(body: dict[str, Any], summary: str) -> tuple[list[dict[str, Any]], list[str], str]:
    feedback = dict(body.get("feedback") or {})
    next_action = dict(body.get("next_action") or {})
    reasons = [str(item) for item in list(feedback.get("reason_texts") or []) if str(item or "").strip()]
    facts: list[str] = []
    lines = [f"结论：{summary}"]

    runtime = dict(body.get("runtime") or {})
    if runtime:
        state = str(runtime.get("state") or "").strip()
        if state:
            facts.append(f"运行状态：{state}")
    training_lab = dict(body.get("training_lab") or {})
    if training_lab:
        if training_lab.get("plan_count") is not None:
            facts.append(f"训练计划：{int(training_lab.get('plan_count', 0) or 0)}")
        if training_lab.get("run_count") is not None:
            facts.append(f"训练运行：{int(training_lab.get('run_count', 0) or 0)}")
    pending = dict(body.get("pending") or {})
    if pending:
        if pending.get("rounds") is not None:
            facts.append(f"待确认轮数：{int(pending.get('rounds', 0) or 0)}")
        if "mock" in pending:
            facts.append("执行模式：" + ("mock" if bool(pending.get("mock")) else "real"))
    if body.get("count") is not None:
        try:
            facts.append(f"条目数：{int(body.get('count', 0) or 0)}")
        except (TypeError, ValueError):
            pass
    item = dict(body.get("item") or {})
    if item:
        item_id = str(item.get("id") or "").strip()
        if item_id:
            facts.append(f"记录 ID：{item_id}")
        item_kind = str(item.get("kind") or "").strip()
        if item_kind:
            facts.append(f"记录类型：{item_kind}")
        item_summary = item.get("summary")
        if isinstance(item_summary, dict):
            status = str(item_summary.get("status") or "").strip()
            if status:
                facts.append(f"记录摘要状态：{status}")
    details = dict(body.get("details") or {})
    if details:
        results = details.get("results")
        if isinstance(results, list):
            facts.append(f"详细结果数：{len(results)}")
    plan_id = str(body.get("plan_id") or "").strip()
    if plan_id:
        facts.append(f"训练计划：{plan_id}")
    run_id = str(body.get("run_id") or "").strip()
    if run_id:
        facts.append(f"训练运行：{run_id}")
    status_value = str(body.get("status") or "").strip()
    if status_value and status_value not in {"ok", "error"}:
        facts.append(f"状态：{status_value}")
    spec = dict(body.get("spec") or {})
    if spec:
        if spec.get("rounds") is not None:
            facts.append(f"计划轮数：{int(spec.get('rounds', 0) or 0)}")
        if "mock" in spec:
            facts.append("计划模式：" + ("mock" if bool(spec.get("mock")) else "real"))
    config = dict(body.get("config") or {})
    if config:
        llm = dict(config.get("llm") or {})
        provider = str(llm.get("default_provider") or llm.get("provider") or "").strip()
        if provider:
            facts.append(f"默认模型提供方：{provider}")
        investment_model = str(config.get("investment_model") or body.get("active_model") or "").strip()
        if investment_model:
            facts.append(f"当前投资模型：{investment_model}")
        if "training_output_dir" in config:
            facts.append(f"训练输出目录已配置：{str(config.get('training_output_dir') or '').strip()}")
    items = body.get("items")
    if isinstance(items, list):
        facts.append(f"条目数：{len(items)}")
    entries = body.get("entries")
    if isinstance(entries, list):
        facts.append(f"排行榜条目：{len(entries)}")
    best_model = body.get("best_model")
    if isinstance(best_model, dict):
        best_name = str(best_model.get("model_name") or best_model.get("config_name") or "").strip()
        if best_name:
            facts.append(f"当前最佳模型：{best_name}")
    latest_result = _latest_training_result_summary(body)
    ops_panel_section: dict[str, Any] | None = None
    ops_panel_warnings: list[str] = []
    causal_section: dict[str, Any] | None = None
    similar_section: dict[str, Any] | None = None
    realism_section: dict[str, Any] | None = None
    if latest_result:
        cycle_id = latest_result.get("cycle_id")
        if cycle_id is not None:
            facts.append(f"最新训练周期：{int(cycle_id)}")
        if latest_result.get("return_pct") is not None:
            facts.append(f"最新收益：{float(latest_result.get('return_pct') or 0.0):+.2f}%")
        promotion_record = dict(latest_result.get("promotion_record") or {})
        if promotion_record:
            facts.append(
                "晋升状态："
                + str(promotion_record.get("status") or "unknown")
                + " / "
                + str(promotion_record.get("gate_status") or "unknown")
            )
        lineage_record = dict(latest_result.get("lineage_record") or {})
        if lineage_record:
            facts.append("lineage：" + str(lineage_record.get("lineage_status") or "unknown"))
        training_lab = dict(body.get("training_lab") or {})
        run = dict(training_lab.get("run") or {})
        ops_panel = dict(
            run.get("ops_panel")
            or latest_result.get("ops_panel")
            or build_promotion_lineage_ops_panel(latest_result)
            or {}
        )
        if ops_panel.get("available", False):
            refs = dict(ops_panel.get("refs") or {})
            review_window = dict(ops_panel.get("review_window") or {})
            fitness_source_cycles = list(ops_panel.get("fitness_source_cycles") or [])
            ops_items: list[str] = []
            if refs.get("active_config_ref"):
                ops_items.append(f"活动配置：{refs.get('active_config_ref')}")
            if refs.get("candidate_config_ref"):
                ops_items.append(f"候选配置：{refs.get('candidate_config_ref')}")
            if refs.get("candidate_meta_ref"):
                ops_items.append(f"候选元数据：{refs.get('candidate_meta_ref')}")
            if review_window:
                ops_items.append(
                    f"review 窗口：{review_window.get('mode', 'unknown')} / {int(review_window.get('size', 0) or 0)}"
                )
            if ops_panel.get("basis_stage"):
                ops_items.append(f"审计基准：{ops_panel.get('basis_stage')}")
            if fitness_source_cycles:
                ops_items.append(
                    "fitness 来源周期：" + ",".join(str(item) for item in fitness_source_cycles)
                )
            if ops_items:
                ops_panel_section = {"label": "运营面板", "items": ops_items}
                lines.append("运营面板：" + "；".join(ops_items))
            ops_panel_warnings = [
                str(item)
                for item in list(ops_panel.get("warnings") or [])
                if str(item or "").strip()
            ]
            if ops_panel_warnings:
                lines.append("运营关注：" + "；".join(ops_panel_warnings[:2]))
        causal_diagnosis = dict(
            latest_result.get("causal_diagnosis")
            or dict(latest_result.get("review_decision") or {}).get("causal_diagnosis")
            or {}
        )
        if causal_diagnosis:
            drivers = [dict(item) for item in list(causal_diagnosis.get("drivers") or [])]
            causal_items = [
                f"首要驱动：{causal_diagnosis.get('primary_driver', 'unknown')}",
            ]
            if causal_diagnosis.get("summary"):
                causal_items.append(f"诊断摘要：{causal_diagnosis.get('summary')}")
            if drivers:
                causal_items.append(
                    "证据周期："
                    + ",".join(str(item) for item in list(drivers[0].get("evidence_cycle_ids") or []))
                )
            causal_section = {"label": "因果诊断", "items": causal_items}
            lines.append("因果诊断：" + str(causal_diagnosis.get("primary_driver") or "unknown"))
        similarity_summary = dict(
            latest_result.get("similarity_summary")
            or dict(latest_result.get("review_decision") or {}).get("similarity_summary")
            or {}
        )
        similar_results = [
            dict(item)
            for item in list(
                latest_result.get("similar_results")
                or dict(latest_result.get("review_decision") or {}).get("similar_results")
                or []
            )
        ]
        if similarity_summary or similar_results:
            similar_items: list[str] = []
            matched_cycle_ids = list(similarity_summary.get("matched_cycle_ids") or [])
            if matched_cycle_ids:
                similar_items.append(
                    "命中周期：" + ",".join(str(item) for item in matched_cycle_ids)
                )
            if similarity_summary.get("dominant_regime"):
                similar_items.append(
                    f"主导市场状态：{similarity_summary.get('dominant_regime')}"
                )
            if similar_results:
                top = dict(similar_results[0])
                similar_items.append(
                    f"最近相似样本：cycle {top.get('cycle_id')} / {float(top.get('return_pct', 0.0) or 0.0):+.2f}%"
                )
            if similar_items:
                similar_section = {"label": "相似样本", "items": similar_items}
                lines.append("相似样本：" + "；".join(similar_items[:2]))
        realism_metrics = dict(latest_result.get("realism_metrics") or {})
        if realism_metrics:
            realism_items = [
                f"平均成交额：{float(realism_metrics.get('avg_trade_amount', 0.0) or 0.0):.2f}",
                f"平均换手率：{float(realism_metrics.get('avg_turnover_rate', 0.0) or 0.0):.4f}",
                f"平均持有天数：{float(realism_metrics.get('avg_holding_days', 0.0) or 0.0):.2f}",
            ]
            realism_section = {"label": "执行现实性", "items": realism_items}
            lines.append("执行现实性：" + "；".join(realism_items[:2]))

    action_label = str(next_action.get("label") or "").strip()
    action_description = str(next_action.get("description") or "").strip()
    actions = []
    if action_label:
        actions.append(f"{action_label}：{action_description}" if action_description else action_label)

    sections: list[dict[str, Any]] = [{"label": "结论", "text": summary}]
    if facts:
        sections.append({"label": "现状", "items": facts})
        lines.append("现状：" + "；".join(facts[:6]))
    if ops_panel_section:
        sections.append(ops_panel_section)
    if causal_section:
        sections.append(causal_section)
    if similar_section:
        sections.append(similar_section)
    if realism_section:
        sections.append(realism_section)
    if reasons:
        sections.append({"label": "风险提示", "items": reasons})
        lines.append("风险提示：" + "；".join(reasons[:2]))
    elif ops_panel_warnings:
        sections.append({"label": "风险提示", "items": ops_panel_warnings})
    if actions:
        sections.append({"label": "建议动作", "items": actions})
        lines.append("建议动作：" + "；".join(actions[:2]))
    return sections, actions, "\n".join(lines)


def build_human_display(payload: Any) -> dict[str, Any]:
    body = dict(payload or {}) if isinstance(payload, dict) else {}
    human = dict(body.get("human_readable") or {})
    feedback = dict(body.get("feedback") or {})
    receipt_text = str(human.get("receipt_text") or "").strip()
    summary = str(human.get("summary") or feedback.get("summary") or body.get("message") or body.get("reply") or "").strip()
    if not summary:
        count = body.get("count")
        items = body.get("items")
        entries = body.get("entries")
        if isinstance(count, int):
            summary = f"已返回 {count} 条记录。"
        elif isinstance(items, list):
            summary = f"已返回 {len(items)} 条记录。"
        elif isinstance(entries, list):
            summary = f"已返回 {len(entries)} 条结果。"
        elif body.get("plan_id"):
            summary = f"已返回训练计划 {body.get('plan_id')}。"
        elif body.get("run_id"):
            summary = f"已返回训练运行 {body.get('run_id')}。"
        elif isinstance(body.get("item"), dict):
            summary = f"已返回记忆记录 {dict(body.get('item') or {}).get('id', '')}。".strip()
        elif body.get("status"):
            summary = f"当前状态：{body.get('status')}。"
    title = str(human.get("title") or "").strip()
    sections = list(human.get("sections") or [])
    suggested_actions = [str(item) for item in list(human.get("suggested_actions") or []) if str(item or "").strip()]
    synthesized = False

    if summary and not receipt_text:
        fallback_sections, fallback_actions, fallback_text = _fallback_sections(body, summary)
        if not sections:
            sections = fallback_sections
        if not suggested_actions:
            suggested_actions = fallback_actions
        receipt_text = fallback_text
        synthesized = True

    text = receipt_text or summary
    return {
        "available": bool(text),
        "title": title,
        "summary": summary,
        "text": text,
        "sections": sections,
        "suggested_actions": suggested_actions,
        "recommended_next_step": str(human.get("recommended_next_step") or ""),
        "risk_level": str(human.get("risk_level") or ""),
        "synthesized": synthesized,
    }
