"""Presentation helpers for human-readable commander responses."""

from __future__ import annotations

from typing import Any


def _fallback_sections(body: dict[str, Any], summary: str) -> tuple[list[dict[str, Any]], list[str], str]:
    feedback = dict(body.get("feedback") or {})
    next_action = dict(body.get("next_action") or {})
    reasons = [str(item) for item in list(feedback.get("reason_texts") or []) if str(item or "").strip()]
    facts: list[str] = []

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

    action_label = str(next_action.get("label") or "").strip()
    action_description = str(next_action.get("description") or "").strip()
    actions = []
    if action_label:
        actions.append(f"{action_label}：{action_description}" if action_description else action_label)

    sections: list[dict[str, Any]] = [{"label": "结论", "text": summary}]
    lines = [f"结论：{summary}"]
    if facts:
        sections.append({"label": "现状", "items": facts})
        lines.append("现状：" + "；".join(facts[:4]))
    if reasons:
        sections.append({"label": "风险提示", "items": reasons})
        lines.append("风险提示：" + "；".join(reasons[:2]))
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
