"""Human-readable receipt helpers for the local brain runtime."""

from __future__ import annotations

from typing import Any

from brain.schema_contract import RISK_LEVEL_HIGH, RISK_LEVEL_LOW, RISK_LEVEL_MEDIUM


class BrainHumanReadablePresenter:
    """Builds human-facing summaries while keeping runtime orchestration thin."""

    @staticmethod
    def truncate_text(value: Any, *, limit: int = 120) -> str:
        text = str(value or "").strip()
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 1)].rstrip() + "…"

    @staticmethod
    def runtime_state_bullets(runtime_payload: dict[str, Any]) -> list[str]:
        state = str(runtime_payload.get("state") or "unknown")
        current_task = dict(runtime_payload.get("current_task") or {})
        last_task = dict(runtime_payload.get("last_task") or {})
        bullets = [f"运行状态：{state}"]
        if current_task.get("type"):
            bullets.append(f"当前任务：{current_task.get('type')}")
        if last_task.get("type"):
            bullets.append(
                f"最近完成：{last_task.get('type')} / {last_task.get('status', '')}".rstrip(" /")
            )
        return bullets

    @staticmethod
    def training_lab_bullets(training_lab: dict[str, Any]) -> list[str]:
        if not training_lab:
            return []
        bullets = [
            f"训练计划：{int(training_lab.get('plan_count', 0) or 0)}",
            f"训练运行：{int(training_lab.get('run_count', 0) or 0)}",
            f"训练评估：{int(training_lab.get('evaluation_count', 0) or 0)}",
        ]
        governance_summary = dict(training_lab.get("governance_summary") or {})
        governance_metrics = dict(governance_summary.get("governance_metrics") or {})
        if governance_metrics:
            bullets.append(
                f"候选待发布：{int(governance_metrics.get('candidate_pending_count', 0) or 0)}"
            )
            bullets.append(
                f"配置漂移率：{float(governance_metrics.get('active_candidate_drift_rate', 0.0) or 0.0):.2%}"
            )
        return bullets

    @staticmethod
    def runtime_governance_bullets(payload: dict[str, Any]) -> list[str]:
        brain_payload = dict(payload.get("brain") or {})
        runtime_governance = dict(
            brain_payload.get("governance_metrics")
            or dict(payload.get("governance_metrics") or {}).get("runtime")
            or {}
        )
        if not runtime_governance:
            return []
        structured = dict(runtime_governance.get("structured_output") or {})
        guardrails = dict(runtime_governance.get("guardrails") or {})
        return [
            f"guardrail 阻断：{int(guardrails.get('block_count', 0) or 0)}",
            f"结构化 fallback：{int(structured.get('fallback_count', 0) or 0)}",
        ]

    @staticmethod
    def latest_training_result_summary(payload: dict[str, Any]) -> dict[str, Any]:
        training_lab = dict(payload.get("training_lab") or {})
        run = dict(training_lab.get("run") or {})
        latest = dict(run.get("latest_result") or payload.get("latest_result") or {})
        if latest:
            return latest
        results = [dict(item) for item in list(payload.get("results") or []) if isinstance(item, dict)]
        return dict(results[-1]) if results else {}

    @staticmethod
    def is_internal_runtime_event(event_name: Any) -> bool:
        return str(event_name or "") in {
            "ask_started",
            "ask_finished",
            "task_started",
            "task_finished",
        }

    @staticmethod
    def top_event_distribution(counts: dict[str, Any], *, limit: int = 3) -> str:
        ordered = sorted(
            ((str(name), int(value or 0)) for name, value in dict(counts or {}).items()),
            key=lambda item: (-item[1], item[0]),
        )
        return "，".join(f"{name}×{count}" for name, count in ordered[:limit])

    @staticmethod
    def event_human_label(event_name: str) -> str:
        mapping = {
            "ask_started": "对话请求开始",
            "ask_finished": "对话请求完成",
            "task_started": "运行任务开始",
            "task_finished": "运行任务完成",
            "training_started": "训练开始",
            "training_finished": "训练完成",
            "routing_started": "模型路由开始",
            "regime_classified": "市场状态识别完成",
            "routing_decided": "模型路由完成",
            "model_switch_applied": "模型切换已执行",
            "model_switch_blocked": "模型切换被阻止",
            "cycle_start": "训练周期开始",
            "cycle_complete": "训练周期完成",
            "cycle_skipped": "训练周期被跳过",
            "agent_status": "Agent 状态更新",
            "agent_progress": "Agent 进度更新",
            "module_log": "模块日志更新",
            "meeting_speech": "会议发言更新",
            "data_download_triggered": "数据下载已触发",
            "runtime_paths_updated": "运行路径已更新",
            "evolution_config_updated": "训练配置已更新",
            "control_plane_updated": "控制面已更新",
            "agent_prompt_updated": "Agent Prompt 已更新",
        }
        return mapping.get(str(event_name or ""), str(event_name or "").replace("_", " "))

    @classmethod
    def event_detail_text(cls, row: dict[str, Any]) -> str:
        payload = dict(row.get("payload") or {})
        event_name = str(row.get("event") or "")
        if event_name == "ask_started":
            channel = str(payload.get("channel") or "").strip()
            message_length = payload.get("message_length")
            details = []
            if channel:
                details.append(f"来源 {channel}")
            if message_length not in (None, ""):
                details.append(f"消息长度 {message_length}")
            if details:
                return "已接收对话请求，" + "，".join(details) + "。"
            return "已接收新的对话请求。"
        if event_name == "ask_finished":
            intent = str(payload.get("intent") or "").strip()
            status = str(payload.get("status") or "").strip()
            risk_level = str(payload.get("risk_level") or "").strip()
            details = []
            if intent:
                details.append(f"意图 {intent}")
            if status:
                details.append(f"状态 {status}")
            if risk_level:
                details.append(f"风险 {risk_level}")
            if details:
                return "对话处理结束，" + "，".join(details) + "。"
            return "对话处理结束。"
        if event_name == "task_started":
            task_type = str(payload.get("type") or "").strip()
            source = str(payload.get("source") or "").strip()
            if task_type and source:
                return f"开始执行 {task_type} 任务，来源 {source}。"
            if task_type:
                return f"开始执行 {task_type} 任务。"
        if event_name == "task_finished":
            task_type = str(payload.get("type") or "").strip()
            status = str(payload.get("status") or "").strip()
            if task_type and status:
                return f"{task_type} 任务已结束，状态 {status}。"
            if status:
                return f"运行任务已结束，状态 {status}。"
        if event_name == "routing_decided":
            regime = str(payload.get("regime") or "").strip()
            selected_model = str(payload.get("selected_model") or "").strip()
            current_model = str(payload.get("current_model") or "").strip()
            if regime and selected_model:
                if (
                    bool(payload.get("switch_applied"))
                    and current_model
                    and current_model != selected_model
                ):
                    return (
                        f"识别为 {regime} 市场，主模型从 {current_model} 切换到 {selected_model}。"
                    )
                return f"识别为 {regime} 市场，当前建议主模型为 {selected_model}。"
        if event_name == "model_switch_applied":
            from_model = str(payload.get("from_model") or "").strip()
            to_model = str(payload.get("to_model") or "").strip()
            if from_model and to_model:
                return f"模型已从 {from_model} 切换到 {to_model}。"
        if event_name == "model_switch_blocked":
            hold_reason = str(payload.get("hold_reason") or "").strip()
            if hold_reason:
                return f"系统决定暂不切换模型，原因是：{hold_reason}"
            return "系统评估后决定继续保持当前模型。"
        if event_name == "cycle_start":
            cutoff_date = str(payload.get("cutoff_date") or "").strip()
            requested_mode = str(payload.get("requested_data_mode") or "").strip()
            llm_mode = str(payload.get("llm_mode") or "").strip()
            details = []
            if cutoff_date:
                details.append(f"截断日期 {cutoff_date}")
            if requested_mode:
                details.append(f"数据模式 {requested_mode}")
            if llm_mode:
                details.append(f"LLM 模式 {llm_mode}")
            if details:
                return "本轮训练已启动，" + "，".join(details) + "。"
        if event_name == "cycle_complete":
            cycle_id = payload.get("cycle_id")
            return_pct = payload.get("return_pct")
            if cycle_id is not None and return_pct not in (None, ""):
                return f"训练周期 #{cycle_id} 已完成，收益率约为 {return_pct}。"
            if cycle_id is not None:
                return f"训练周期 #{cycle_id} 已完成。"
        if event_name == "cycle_skipped":
            stage = str(payload.get("stage") or "").strip()
            reason = str(payload.get("reason") or "").strip()
            if stage and reason:
                return f"训练周期在 {stage} 阶段被跳过，原因是：{reason}"
            if reason:
                return f"训练周期被跳过，原因是：{reason}"
        if event_name == "agent_status":
            agent = str(payload.get("agent") or "").strip()
            status = str(payload.get("status") or "").strip()
            stage = str(payload.get("stage") or "").strip()
            progress_pct = payload.get("progress_pct")
            message = cls.truncate_text(payload.get("message"), limit=80)
            parts = []
            if agent:
                parts.append(agent)
            if status:
                parts.append(status)
            if stage:
                parts.append(f"阶段 {stage}")
            if progress_pct not in (None, ""):
                parts.append(f"进度 {progress_pct}%")
            if message:
                parts.append(message)
            if parts:
                return "，".join(parts) + "。"
        if event_name == "module_log":
            module = str(payload.get("module") or "").strip()
            title = str(payload.get("title") or "").strip()
            message = cls.truncate_text(payload.get("message"), limit=80)
            parts = [part for part in [module, title, message] if part]
            if parts:
                return " / ".join(parts) + "。"
        if event_name == "meeting_speech":
            speaker = str(payload.get("speaker") or "").strip()
            meeting = str(payload.get("meeting") or "").strip()
            speech = cls.truncate_text(payload.get("speech"), limit=80)
            prefix = " / ".join(part for part in [meeting, speaker] if part)
            if prefix and speech:
                return f"{prefix}：{speech}"
        if event_name == "data_download_triggered":
            status = str(payload.get("status") or "").strip()
            message = cls.truncate_text(payload.get("message"), limit=80)
            if status and message:
                return f"数据同步状态：{status}，{message}"
        if event_name in {
            "runtime_paths_updated",
            "evolution_config_updated",
            "control_plane_updated",
        }:
            updated = payload.get("updated")
            if isinstance(updated, list) and updated:
                return "更新字段：" + "，".join(str(item) for item in updated[:4])
        return ""

    @classmethod
    def event_broadcast_text(cls, row: dict[str, Any]) -> str:
        event_name = str(row.get("event") or "").strip()
        if not event_name:
            return ""
        label = cls.event_human_label(event_name)
        detail = cls.event_detail_text(row)
        source = str(row.get("source") or "").strip()
        if detail:
            return f"{label}：{detail}"
        if source:
            return f"{label}（来源 {source}）"
        return label

    @classmethod
    def event_explanation_bullets(
        cls,
        event_summary: dict[str, Any],
        *,
        recent_events: list[dict[str, Any]] | None = None,
    ) -> tuple[list[str], dict[str, Any], str]:
        summary = dict(event_summary or {})
        rows = list(recent_events or [])
        preferred_latest: dict[str, Any] = {}
        latest_internal: dict[str, Any] = {}
        for row in reversed(rows):
            event_name = str(row.get("event") or "")
            if not event_name:
                continue
            if not cls.is_internal_runtime_event(event_name):
                preferred_latest = dict(row)
                break
            if not latest_internal:
                latest_internal = dict(row)
        latest = dict(preferred_latest or latest_internal or summary.get("latest") or {})
        counts = dict(summary.get("counts") or {})
        external_counts = {
            str(name): int(value or 0)
            for name, value in counts.items()
            if not cls.is_internal_runtime_event(name)
        }
        bullets: list[str] = []
        latest_event: dict[str, Any] = {}
        explanation = ""
        if latest:
            event_name = str(latest.get("event") or "unknown")
            source = str(latest.get("source") or "").strip()
            detail_text = cls.event_detail_text(latest)
            latest_event = {
                "event": event_name,
                "source": source,
                "ts": str(latest.get("ts") or ""),
                "kind": "internal" if cls.is_internal_runtime_event(event_name) else "business",
                "label": cls.event_human_label(event_name),
                "detail": detail_text,
                "broadcast_text": cls.event_broadcast_text(latest),
            }
            if not cls.is_internal_runtime_event(event_name):
                detail = f"最近业务事件：{event_name}（{cls.event_human_label(event_name)}）"
                if source:
                    detail += f"（来源 {source}）"
                bullets.append(detail)
                if detail_text:
                    bullets.append("事件细节：" + detail_text)
        if external_counts:
            distribution = cls.top_event_distribution(external_counts)
            bullets.append("业务事件分布：" + distribution)
            if preferred_latest:
                explanation = (
                    f"最近一次业务事件是 {latest_event['event']}"
                    + (
                        f"（{latest_event.get('label')}）"
                        if latest_event.get("label")
                        else ""
                    )
                    + (
                        f"（来源 {latest_event['source']}）"
                        if latest_event.get("source")
                        else ""
                    )
                    + "。"
                )
                if latest_event.get("detail"):
                    explanation += f" {latest_event['detail']}"
                if distribution:
                    explanation += f" 当前窗口内主要业务事件分布为：{distribution}。"
        elif counts:
            distribution = cls.top_event_distribution(counts)
            bullets.append("交互事件分布：" + distribution)
            explanation = "当前窗口内主要记录的是交互与调度事件，尚未出现新的业务事件。"
            if distribution:
                explanation += f" 最近的事件分布为：{distribution}。"
        return bullets, latest_event, explanation

    @classmethod
    def event_timeline_items(
        cls,
        recent_events: list[dict[str, Any]] | None,
        *,
        limit: int = 3,
    ) -> list[str]:
        rows = list(recent_events or [])
        business_items: list[str] = []
        internal_items: list[str] = []
        for row in reversed(rows):
            event_name = str(row.get("event") or "").strip()
            if not event_name:
                continue
            broadcast_text = cls.event_broadcast_text(row)
            if not broadcast_text:
                continue
            target = (
                internal_items if cls.is_internal_runtime_event(event_name) else business_items
            )
            if broadcast_text not in target:
                target.append(broadcast_text)
        selected = business_items or internal_items
        return selected[: max(1, int(limit or 3))]

    @staticmethod
    def risk_explanations(
        diagnostics: list[Any],
        *,
        feedback: dict[str, Any],
        last_error: Any = "",
    ) -> list[str]:
        mapping = {
            "runtime_state=error": "运行态处于 error，建议优先检查最近失败任务和错误日志。",
            "data_quality_unhealthy": "数据健康异常，继续训练或问股前应先检查数据状态。",
            "last_run_degraded": "最近一次运行出现降级迹象，当前结果建议人工复核。",
        }
        items: list[str] = []
        for code in diagnostics[:3]:
            text = mapping.get(str(code), str(code).replace("_", " "))
            if text and text not in items:
                items.append(text)
        for reason_text in list(feedback.get("reason_texts") or []):
            text = str(reason_text or "").strip()
            if text and text not in items:
                items.append(text)
        error_text = BrainHumanReadablePresenter.truncate_text(last_error, limit=100)
        if error_text:
            items.append(f"最近错误：{error_text}")
        return items

    @staticmethod
    def action_items(
        next_action: dict[str, Any],
        *,
        diagnostics: list[Any],
        latest_event: dict[str, Any] | None = None,
        status: str = "ok",
    ) -> list[str]:
        items: list[str] = []
        label = str(next_action.get("label") or "").strip()
        description = str(next_action.get("description") or "").strip()
        if label:
            items.append(f"{label}：{description}" if description else label)
        if bool(next_action.get("requires_confirmation")):
            items.append("如需继续执行，请直接用自然语言明确回复“确认执行”或补充确认参数。")
        diagnostic_codes = {str(item) for item in diagnostics}
        if "runtime_state=error" in diagnostic_codes:
            items.append("先恢复运行态，再继续训练、配置修改或问股请求。")
        if "data_quality_unhealthy" in diagnostic_codes:
            items.append("先执行数据状态检查或刷新，确认数据健康后再继续下游任务。")
        latest_event_name = str((latest_event or {}).get("event") or "")
        if latest_event_name == "training_finished":
            items.append("查看最近训练结果、排行榜和生成工件，确认是否需要继续迭代。")
        elif latest_event_name == "training_started":
            items.append("继续关注事件流和运行状态，等待训练完成后再查看结果。")
        if status == "ok" and not items:
            items.append("可以继续发起更具体的自然语言任务，例如训练、问股或配置诊断。")
        deduped: list[str] = []
        for item in items:
            if item and item not in deduped:
                deduped.append(item)
        return deduped

    @staticmethod
    def risk_level_text(risk_level: str) -> str:
        mapping = {
            RISK_LEVEL_LOW: "低风险，可直接继续读取或查看结果。",
            RISK_LEVEL_MEDIUM: "中风险，建议先核对关键参数、数据状态或最近事件。",
            RISK_LEVEL_HIGH: "高风险，建议先确认操作范围与影响，再继续执行。",
        }
        return mapping.get(str(risk_level or ""), "")

    @staticmethod
    def operation_nature_text(gate: dict[str, Any]) -> str:
        writes_state = bool(gate.get("writes_state"))
        if writes_state:
            return "本次属于写操作，可能会改动系统状态、配置或运行工件。"
        return "本次属于只读分析，不会改动系统状态。"

    @staticmethod
    def confirmation_text(gate: dict[str, Any], *, status: str) -> str:
        confirmation = dict(gate.get("confirmation") or {})
        state = str(confirmation.get("state") or "")
        writes_state = bool(gate.get("writes_state"))
        requires_confirmation = bool(gate.get("requires_confirmation"))
        if requires_confirmation or state == "pending_confirmation" or status == "confirmation_required":
            return "当前仍需人工确认，系统不会直接执行写入动作。"
        if writes_state:
            return "当前写操作已确认或无需额外确认，可以按流程继续执行。"
        return "当前无需人工确认，可以直接继续查看或追问。"

    @staticmethod
    def compose_human_readable_receipt(
        *,
        title: str,
        summary: str,
        operation: str,
        facts: list[str] | None = None,
        risks: list[str] | None = None,
        suggested_actions: list[str] | None = None,
        recommended_next_step: str = "",
        risk_level: str = "",
        latest_event: dict[str, Any] | None = None,
        event_explanation: str = "",
        event_timeline: list[str] | None = None,
        operation_nature: str = "",
        risk_summary: str = "",
        confirmation_summary: str = "",
    ) -> dict[str, Any]:
        fact_items = [str(item) for item in list(facts or []) if str(item or "").strip()]
        risk_items = [str(item) for item in list(risks or []) if str(item or "").strip()]
        action_items = [
            str(item) for item in list(suggested_actions or []) if str(item or "").strip()
        ]
        timeline_items = [
            str(item) for item in list(event_timeline or []) if str(item or "").strip()
        ]
        bullets = list(fact_items)
        posture_items = [
            str(item)
            for item in [operation_nature, risk_summary, confirmation_summary]
            if str(item or "").strip()
        ]
        bullets.extend(posture_items)
        if event_explanation:
            bullets.append(f"事件解释：{event_explanation}")
        bullets.extend(f"最近事件：{item}" for item in timeline_items[:2])
        bullets.extend(f"关注项：{item}" for item in risk_items[:2])
        bullets.extend(f"建议动作：{item}" for item in action_items[:2])
        sections: list[dict[str, Any]] = [{"label": "结论", "text": summary}]
        if posture_items:
            sections.append({"label": "执行性质", "items": posture_items})
        if fact_items:
            sections.append({"label": "现状", "items": fact_items})
        if event_explanation:
            sections.append({"label": "事件解释", "text": event_explanation})
        if timeline_items:
            sections.append({"label": "最近事件", "items": timeline_items})
        if risk_items:
            sections.append({"label": "风险提示", "items": risk_items})
        if action_items:
            sections.append({"label": "建议动作", "items": action_items})
        receipt_lines = [f"结论：{summary}"]
        if operation_nature:
            receipt_lines.append(f"执行性质：{operation_nature}")
        if risk_summary:
            receipt_lines.append(f"风险等级：{risk_summary}")
        if confirmation_summary:
            receipt_lines.append(f"确认要求：{confirmation_summary}")
        if fact_items:
            receipt_lines.append("现状：" + "；".join(fact_items[:6]))
        if event_explanation:
            receipt_lines.append("事件解释：" + event_explanation)
        if timeline_items:
            receipt_lines.append("最近事件：" + "；".join(timeline_items[:2]))
        if risk_items:
            receipt_lines.append("风险提示：" + "；".join(risk_items[:2]))
        if action_items:
            receipt_lines.append("建议动作：" + "；".join(action_items[:2]))
        return {
            "title": title,
            "summary": summary,
            "bullets": bullets,
            "facts": fact_items,
            "risks": risk_items,
            "suggested_actions": action_items,
            "event_explanation": event_explanation,
            "event_timeline": timeline_items,
            "sections": sections,
            "receipt_text": "\n".join(receipt_lines),
            "recommended_next_step": recommended_next_step,
            "risk_level": risk_level,
            "latest_event": dict(latest_event or {}),
            "operation_nature": operation_nature,
            "risk_summary": risk_summary,
            "confirmation_summary": confirmation_summary,
            "operation": operation,
        }

    @classmethod
    def build_human_readable_receipt(
        cls,
        payload: dict[str, Any],
        *,
        intent: str,
        operation: str,
    ) -> dict[str, Any]:
        feedback = dict(payload.get("feedback") or {})
        next_action = dict(payload.get("next_action") or {})
        status = str(payload.get("status") or "ok")
        task_bus = dict(payload.get("task_bus") or {})
        gate = dict(task_bus.get("gate") or {})
        risk_level = str(gate.get("risk_level") or "")
        operation_nature = cls.operation_nature_text(gate)
        risk_summary = cls.risk_level_text(risk_level)
        confirmation_summary = cls.confirmation_text(gate, status=status)

        if intent in {
            "runtime_status",
            "runtime_diagnostics",
            "runtime_status_and_training",
            "config_risk_diagnostics",
        }:
            quick_status = dict(payload.get("quick_status") or {})
            runtime_payload = dict(payload.get("runtime") or quick_status.get("runtime") or {})
            plugins = dict(payload.get("plugins") or quick_status.get("plugins") or {})
            event_summary = dict(
                payload.get("event_summary")
                or payload.get("events")
                or quick_status.get("events")
                or {}
            )
            recent_events = list(payload.get("recent_events") or payload.get("items") or [])
            training_lab = dict(
                payload.get("training_lab") or quick_status.get("training_lab") or {}
            )
            diagnostics = list(payload.get("diagnostics") or [])
            facts = []
            facts.extend(cls.runtime_state_bullets(runtime_payload))
            if plugins:
                facts.append(f"插件数：{int(plugins.get('count', 0) or 0)}")
            if event_summary:
                facts.append(f"事件数：{int(event_summary.get('count', 0) or 0)}")
            event_bullets, latest_event, event_explanation = cls.event_explanation_bullets(
                event_summary,
                recent_events=recent_events,
            )
            event_timeline = cls.event_timeline_items(recent_events)
            facts.extend(event_bullets)
            facts.extend(cls.training_lab_bullets(training_lab))
            facts.extend(cls.runtime_governance_bullets(payload))
            risks = cls.risk_explanations(
                diagnostics,
                feedback=feedback,
                last_error=payload.get("last_error") or "",
            )
            actions = cls.action_items(
                next_action,
                diagnostics=diagnostics,
                latest_event=latest_event,
                status=status,
            )
            summary = str(feedback.get("summary") or "已生成运行时摘要。")
            if status == "ok" and not diagnostics:
                summary = "系统可用，已返回运行状态、事件与训练摘要。"
            elif status == "ok" and diagnostics:
                summary = f"系统仍可用，但有 {len(risks)} 项需要优先关注。"
            return cls.compose_human_readable_receipt(
                title="系统运行摘要",
                summary=summary,
                operation=operation,
                facts=facts,
                risks=risks,
                suggested_actions=actions,
                recommended_next_step=str(next_action.get("label") or ""),
                risk_level=risk_level,
                latest_event=latest_event,
                event_explanation=event_explanation,
                event_timeline=event_timeline,
                operation_nature=operation_nature,
                risk_summary=risk_summary,
                confirmation_summary=confirmation_summary,
            )

        if intent in {"training_lab_summary", "training_execution"}:
            training_lab = dict(payload.get("training_lab") or payload)
            facts = cls.training_lab_bullets(training_lab)
            facts.extend(cls.runtime_governance_bullets(payload))
            latest_result = cls.latest_training_result_summary(payload)
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
            risks = cls.risk_explanations([], feedback=feedback)
            actions = cls.action_items(next_action, diagnostics=[], status=status)
            return cls.compose_human_readable_receipt(
                title="训练实验室摘要",
                summary=str(feedback.get("summary") or "已返回训练实验室状态。"),
                operation=operation,
                facts=facts,
                risks=risks,
                suggested_actions=actions,
                recommended_next_step=str(next_action.get("label") or ""),
                risk_level=risk_level,
                event_explanation="",
                operation_nature=operation_nature,
                risk_summary=risk_summary,
                confirmation_summary=confirmation_summary,
            )

        if intent.startswith("config_") or intent in {"runtime_paths", "config_overview"}:
            control_plane = dict(payload.get("control_plane") or {})
            evolution_config = dict(payload.get("evolution_config") or {})
            facts: list[str] = []
            if control_plane:
                provider = str(
                    control_plane.get("provider") or control_plane.get("default_provider") or ""
                )
                if provider:
                    facts.append(f"控制面 Provider：{provider}")
            if evolution_config:
                model_name = str(evolution_config.get("investment_model") or "")
                if model_name:
                    facts.append(f"当前投资模型：{model_name}")
            return cls.compose_human_readable_receipt(
                title="配置摘要",
                summary=str(feedback.get("summary") or "已返回配置与控制面信息。"),
                operation=operation,
                facts=facts,
                risks=cls.risk_explanations([], feedback=feedback),
                suggested_actions=cls.action_items(next_action, diagnostics=[], status=status),
                recommended_next_step=str(next_action.get("label") or ""),
                risk_level=risk_level,
                event_explanation="",
                operation_nature=operation_nature,
                risk_summary=risk_summary,
                confirmation_summary=confirmation_summary,
            )

        return cls.compose_human_readable_receipt(
            title="执行摘要",
            summary=str(
                feedback.get("summary") or payload.get("message") or payload.get("reply") or ""
            ),
            operation=operation,
            facts=[],
            risks=cls.risk_explanations([], feedback=feedback),
            suggested_actions=cls.action_items(next_action, diagnostics=[], status=status),
            recommended_next_step=str(next_action.get("label") or ""),
            risk_level=risk_level,
            event_explanation="",
            operation_nature=operation_nature,
            risk_summary=risk_summary,
            confirmation_summary=confirmation_summary,
        )

    @classmethod
    def attach_human_readable_receipt(
        cls,
        payload: dict[str, Any],
        *,
        intent: str,
        operation: str,
    ) -> dict[str, Any]:
        enriched = dict(payload or {})
        enriched["human_readable"] = cls.build_human_readable_receipt(
            enriched,
            intent=intent,
            operation=operation,
        )
        return enriched
