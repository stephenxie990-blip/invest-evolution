"""Commander runtime state, event stream, and control services."""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import logging
import queue
import uuid
from collections.abc import Coroutine
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from importlib import import_module
from pathlib import Path
from typing import Any, Awaitable, Callable, cast

from invest_evolution.agent_runtime.runtime import BrainRuntime

logger = logging.getLogger(__name__)

def _load_module(name: str) -> Any:
    return import_module(name)


def _call_module_attr(
    loader: Callable[[], Any],
    attr: str,
    /,
    *args: Any,
    **kwargs: Any,
) -> Any:
    return getattr(loader(), attr)(*args, **kwargs)


def _lazy_module_function(loader: Callable[[], Any], attr: str) -> Callable[..., Any]:
    def _proxy(*args: Any, **kwargs: Any) -> Any:
        return _call_module_attr(loader, attr, *args, **kwargs)

    _proxy.__name__ = attr
    _proxy.__qualname__ = attr
    return _proxy


@lru_cache(maxsize=None)
def _commander_bootstrap_module() -> Any:
    return _load_module("invest_evolution.application.commander.bootstrap")


@lru_cache(maxsize=None)
def _commander_workflow_module() -> Any:
    return _load_module("invest_evolution.application.commander.workflow")


@lru_cache(maxsize=None)
def _commander_status_module() -> Any:
    return _load_module("invest_evolution.application.commander.status")


def _bootstrap_proxy(attr: str) -> Callable[..., Any]:
    return _lazy_module_function(_commander_bootstrap_module, attr)


def _workflow_proxy(attr: str) -> Callable[..., Any]:
    return _lazy_module_function(_commander_workflow_module, attr)


def _status_proxy(attr: str) -> Callable[..., Any]:
    return _lazy_module_function(_commander_status_module, attr)


def copy_runtime_task(task: dict[str, Any] | None) -> dict[str, Any] | None:
    if task is None:
        return None
    return deepcopy(task)


def build_started_task(task_type: str, source: str, **metadata: Any) -> dict[str, Any]:
    return {
        "type": task_type,
        "source": source,
        "started_at": datetime.now().isoformat(),
        **metadata,
    }


def build_finished_task(
    current_task: dict[str, Any],
    *,
    status: str,
    copy_task: Callable[[dict[str, Any] | None], dict[str, Any] | None],
    **metadata: Any,
) -> dict[str, Any]:
    return {
        **(copy_task(current_task) or {}),
        "finished_at": datetime.now().isoformat(),
        "status": status,
        **metadata,
    }


apply_restored_body_state = cast(Callable[..., None], _bootstrap_proxy("apply_restored_body_state"))
read_runtime_lock_payload = cast(
    Callable[..., dict[str, Any]],
    _bootstrap_proxy("read_runtime_lock_payload"),
)
is_pid_alive = cast(Callable[..., bool], _bootstrap_proxy("is_pid_alive"))
acquire_runtime_lock = cast(Callable[..., None], _bootstrap_proxy("acquire_runtime_lock"))
release_runtime_lock = cast(Callable[..., None], _bootstrap_proxy("release_runtime_lock"))

STATUS_CONFIRMATION_REQUIRED = "confirmation_required"

EVENT_TASK_STARTED = "task_started"

EVENT_TASK_FINISHED = "task_finished"

EVENT_TRAINING_STARTED = "training_started"

EVENT_TRAINING_FINISHED = "training_finished"

EVENT_ASK_STARTED = "ask_started"

EVENT_ASK_FINISHED = "ask_finished"

_REQUEST_EVENT_CONTEXT: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "commander_request_event_context",
    default={},
)

@dataclass(slots=True)
class RuntimeEventSubscription:
    subscription_id: str
    event_queue: queue.Queue
    session_key: str = ""
    chat_id: str = ""
    request_id: str = ""
    emitted_count: int = 0
    suppressed_count: int = 0
    last_display_by_key: dict[str, str] = field(default_factory=dict)
    last_progress_bucket_by_stage: dict[str, int] = field(default_factory=dict)
    last_module_title_by_scope: dict[str, str] = field(default_factory=dict)
    seen_meeting_updates: set[str] = field(default_factory=set)
    governance_decision_emitted: bool = False
    collected_packets: list[dict[str, Any]] = field(default_factory=list)


class CommanderRuntimeEventStreamMixin:
    """Mixin that encapsulates runtime event-stream assembly and summarization."""

    # Typed on the mixin so extracted helpers can still be checked against the
    # CommanderRuntime host object without reintroducing the original monolith.
    body: Any
    _stream_lock: Any
    _event_subscriptions: dict[str, RuntimeEventSubscription]

    def _current_request_event_context(self) -> dict[str, Any]:
        return dict(_REQUEST_EVENT_CONTEXT.get() or {})

    @contextlib.contextmanager
    def _request_event_context(
        self,
        *,
        session_key: str = "",
        chat_id: str = "",
        request_id: str = "",
        channel: str = "",
    ):
        base = self._current_request_event_context()
        for key, value in {
            "session_key": session_key,
            "chat_id": chat_id,
            "request_id": request_id,
            "channel": channel,
        }.items():
            normalized = str(value or "").strip()
            if normalized:
                base[key] = normalized
        token = _REQUEST_EVENT_CONTEXT.set(base)
        try:
            yield dict(base)
        finally:
            _REQUEST_EVENT_CONTEXT.reset(token)

    def _normalize_event_payload(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        normalized = dict(payload or {})
        context = self._current_request_event_context()
        for key in ("session_key", "chat_id", "request_id", "channel"):
            value = str(normalized.get(key) or context.get(key) or "").strip()
            if value:
                normalized[key] = value
        return normalized

    @staticmethod
    def _event_context_from_row(row: dict[str, Any]) -> dict[str, str]:
        payload = dict(row.get("payload") or {})
        resolved: dict[str, str] = {}
        for key in ("session_key", "chat_id", "request_id", "channel"):
            value = str(row.get(key) or payload.get(key) or "").strip()
            if value:
                resolved[key] = value
        return resolved

    @staticmethod
    def _stream_stage_for_event(row: dict[str, Any]) -> str:
        payload = dict(row.get("payload") or {})
        stage = str(payload.get("stage") or "").strip()
        if stage:
            return stage
        mapping = {
            EVENT_ASK_STARTED: "request_received",
            EVENT_ASK_FINISHED: "request_completed",
            EVENT_TASK_STARTED: "task_started",
            EVENT_TASK_FINISHED: "task_completed",
            EVENT_TRAINING_STARTED: "training",
            EVENT_TRAINING_FINISHED: "training",
            "governance_started": "governance",
            "regime_classified": "governance",
            "manager_activation_decided": "governance",
            "governance_applied": "governance",
            "governance_blocked": "governance",
            "agent_status": "agent",
            "agent_progress": "agent",
            "module_log": "module",
            "meeting_speech": "meeting",
            "cycle_start": "training",
            "cycle_complete": "training",
            "cycle_skipped": "training",
            "data_download_triggered": "data",
        }
        return mapping.get(str(row.get("event") or ""), "")

    @staticmethod
    def _stream_phase_label(stage: str) -> str:
        mapping = {
            "request_received": "收到请求",
            "request_completed": "请求完成",
            "task_started": "任务开始",
            "task_completed": "任务结束",
            "training": "训练执行",
            "governance": "组合治理",
            "agent": "Agent 执行",
            "module": "模块处理",
            "artifact": "工件播报",
            "data": "数据处理",
            "selection_artifact": "选股工件",
            "manager_review_artifact": "经理复盘工件",
            "allocation_review_artifact": "组合复盘工件",
            "simulation": "模拟交易",
            "data_loading": "数据加载",
        }
        return mapping.get(str(stage or ""), str(stage or "").replace("_", " "))

    @staticmethod
    def _stream_kind_for_event(row: dict[str, Any]) -> str:
        event_name = str(row.get("event") or "")
        payload = dict(row.get("payload") or {})
        if payload.get("requires_confirmation") or str(payload.get("confirmation_state") or "").strip():
            return "confirmation_update"
        if str(payload.get("risk_level") or "").strip():
            return "risk_update"
        if event_name in {"cycle_complete", "cycle_skipped"}:
            return "artifact_update"
        if event_name in {"module_log"}:
            return "module_update"
        if event_name in {"meeting_speech"}:
            return "meeting_update"
        if event_name in {"agent_status", "agent_progress"}:
            return "agent_update"
        if event_name in {"governance_started", "regime_classified", "manager_activation_decided", "governance_applied", "governance_blocked"}:
            return "governance_update"
        return "stage_update"

    @staticmethod
    def _stream_tags_for_event(row: dict[str, Any]) -> list[str]:
        event_name = str(row.get("event") or "")
        payload = dict(row.get("payload") or {})
        tags: list[str] = []
        stream_kind = CommanderRuntimeEventStreamMixin._stream_kind_for_event(row)
        if stream_kind:
            tags.append(stream_kind)
        if str(payload.get("risk_level") or "").strip():
            tags.append("risk_update")
        if payload.get("requires_confirmation") or str(payload.get("confirmation_state") or "").strip():
            tags.append("confirmation_update")
        if event_name in {"cycle_complete", "cycle_skipped"}:
            tags.append("artifact_update")
        if event_name in {"module_log"}:
            tags.append("module_update")
        if event_name in {"meeting_speech"}:
            tags.append("meeting_update")
        if event_name in {"agent_status", "agent_progress"}:
            tags.append("agent_update")
        deduped: list[str] = []
        for tag in tags:
            if tag and tag not in deduped:
                deduped.append(tag)
        return deduped

    @staticmethod
    def _stream_risk_summary(risk_level: str) -> str:
        mapping = {
            "low": "低风险，可继续观察。",
            "medium": "中风险，建议先核对关键参数或数据状态。",
            "high": "高风险，建议先人工确认再继续。",
        }
        return mapping.get(str(risk_level or "").strip(), "")

    @staticmethod
    def _stream_confirmation_summary(*, requires_confirmation: bool, confirmation_state: str, status: str) -> str:
        if requires_confirmation or confirmation_state == "pending_confirmation" or status == STATUS_CONFIRMATION_REQUIRED:
            return "当前仍需人工确认，系统尚未执行最终写入。"
        if confirmation_state in {"confirmed", "approved"}:
            return "确认已完成，系统可继续执行后续动作。"
        return ""

    def _stream_artifacts_for_event(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = dict(row.get("payload") or {})
        artifacts = dict(payload.get("artifacts") or {}) if isinstance(payload.get("artifacts"), dict) else {}
        cycle_id = payload.get("cycle_id")
        if cycle_id not in (None, ""):
            try:
                artifacts.update(self.body._artifact_paths_for_cycle(int(cycle_id)))  # pylint: disable=protected-access
            except Exception as exc:
                logger.warning("Failed to resolve artifact paths for cycle %s: %s", cycle_id, exc)
        config_snapshot_path = str(payload.get("config_snapshot_path") or "").strip()
        if config_snapshot_path:
            artifacts.setdefault("config_snapshot_path", config_snapshot_path)
        return artifacts

    @staticmethod
    def _stream_artifact_summary(artifacts: dict[str, Any]) -> str:
        labels = {
            "cycle_result_path": "周期结果",
            "selection_artifact_json_path": "选股工件(JSON)",
            "selection_artifact_markdown_path": "选股工件(Markdown)",
            "manager_review_artifact_json_path": "经理复盘工件(JSON)",
            "manager_review_artifact_markdown_path": "经理复盘工件(Markdown)",
            "allocation_review_artifact_json_path": "组合复盘工件(JSON)",
            "allocation_review_artifact_markdown_path": "组合复盘工件(Markdown)",
            "config_snapshot_path": "配置快照",
            "optimization_events_path": "优化事件",
        }
        items: list[str] = []
        for key, label in labels.items():
            value = str((artifacts or {}).get(key) or "").strip()
            if not value:
                continue
            items.append(f"{label}：{Path(value).name}")
            if len(items) >= 3:
                break
        return "；".join(items)

    @staticmethod
    def _stream_display_priority(stream_kind: str) -> int:
        mapping = {
            "confirmation_update": 100,
            "risk_update": 90,
            "artifact_update": 80,
            "meeting_update": 70,
            "governance_update": 65,
            "agent_update": 60,
            "module_update": 55,
            "stage_update": 50,
        }
        return int(mapping.get(str(stream_kind or ""), 40))

    @staticmethod
    def _format_confidence_value(value: Any) -> str:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return ""
        if numeric <= 1:
            return f"{numeric:.0%}"
        return f"{numeric:.2f}"

    @staticmethod
    def _stream_governance_decision_text(packet: dict[str, Any]) -> str:
        event_name = str(packet.get("event") or "").strip()
        regime = str(packet.get("regime") or "").strip()
        dominant_manager_id = str(packet.get("dominant_manager_id") or "").strip()
        active_manager_ids = [
            str(item).strip()
            for item in list(packet.get("active_manager_ids") or [])
            if str(item).strip()
        ]
        manager_budget_weights = {
            str(key): float(value)
            for key, value in dict(packet.get("manager_budget_weights") or {}).items()
            if str(key).strip()
        }
        decision_source = str(packet.get("decision_source") or "").strip()
        reasoning = BrainRuntime._truncate_text(packet.get("reasoning"), limit=120)
        regime_confidence = CommanderRuntimeEventStreamMixin._format_confidence_value(packet.get("regime_confidence"))
        decision_confidence = CommanderRuntimeEventStreamMixin._format_confidence_value(packet.get("decision_confidence"))
        cash_reserve_hint = packet.get("cash_reserve_hint")
        portfolio_constraints = dict(packet.get("portfolio_constraints") or {})

        parts: list[str] = ["组合治理决策"]
        if regime:
            regime_text = f"市场状态 {regime}"
            if regime_confidence:
                regime_text += f"（置信度 {regime_confidence}）"
            parts.append(regime_text)

        if active_manager_ids:
            parts.append(f"激活经理 {', '.join(active_manager_ids)}")
        if dominant_manager_id:
            parts.append(f"主导经理 {dominant_manager_id}")
        if manager_budget_weights:
            weight_parts = [
                f"{manager_id}:{CommanderRuntimeEventStreamMixin._format_confidence_value(weight) or f'{weight:.2f}'}"
                for manager_id, weight in manager_budget_weights.items()
            ]
            parts.append(f"预算分配 {' / '.join(weight_parts)}")

        if decision_source:
            source_text = f"决策来源 {decision_source}"
            if decision_confidence:
                source_text += f"（置信度 {decision_confidence}）"
            parts.append(source_text)
        elif decision_confidence:
            parts.append(f"决策置信度 {decision_confidence}")

        try:
            if cash_reserve_hint is not None:
                parts.append(f"现金保留 {CommanderRuntimeEventStreamMixin._format_confidence_value(cash_reserve_hint)}")
        except Exception:
            pass
        if portfolio_constraints:
            constraint_parts = []
            if portfolio_constraints.get("top_n") not in (None, ""):
                constraint_parts.append(f"top_n={portfolio_constraints.get('top_n')}")
            if constraint_parts:
                parts.append(f"组合约束 {' / '.join(constraint_parts)}")
        if event_name == "governance_blocked":
            parts.append("本次治理调整被 guardrail 阻断")
            hold_reason = str(packet.get("hold_reason") or "").strip()
            if hold_reason:
                parts.append(f"阻断原因 {hold_reason}")

        if reasoning:
            parts.append(f"依据：{reasoning}")
        return "；".join(part for part in parts if part) + "。"

    def _stream_display_text(self, packet: dict[str, Any]) -> str:
        stream_kind = str(packet.get("stream_kind") or "").strip()
        phase_label = str(packet.get("phase_label") or "").strip()
        base = str(
            packet.get("human_reply")
            or packet.get("broadcast_text")
            or packet.get("label")
            or packet.get("event")
            or ""
        ).strip()
        risk_summary = str(packet.get("risk_summary") or "").strip()
        confirmation_summary = str(packet.get("confirmation_summary") or "").strip()
        artifacts = dict(packet.get("artifacts") or {})
        artifact_summary = self._stream_artifact_summary(artifacts)

        if stream_kind == "confirmation_update":
            parts = [base or phase_label]
            if risk_summary:
                parts.append(f"风险提示：{risk_summary}")
            if confirmation_summary:
                parts.append(f"确认要求：{confirmation_summary}")
            return "；".join(part for part in parts if part)
        if stream_kind == "risk_update":
            parts = [base or phase_label]
            if risk_summary:
                parts.append(f"风险提示：{risk_summary}")
            return "；".join(part for part in parts if part)
        if stream_kind == "artifact_update":
            parts = [base or phase_label]
            if artifact_summary:
                parts.append(f"相关产物：{artifact_summary}")
            return "；".join(part for part in parts if part)
        if stream_kind == "governance_update":
            return self._stream_governance_decision_text(packet)
        if stream_kind in {"governance_update", "meeting_update", "agent_update", "module_update"}:
            if phase_label and base and not base.startswith(f"{phase_label}："):
                return f"{phase_label}：{base}"
        if phase_label and base and phase_label not in {"", base} and not base.startswith(f"{phase_label}："):
            return f"{phase_label}：{base}"
        return base

    def _build_stream_event_packet(self, row: dict[str, Any]) -> dict[str, Any]:
        event_name = str(row.get("event") or "")
        payload = dict(row.get("payload") or {})
        context = self._event_context_from_row(row)
        status = str(payload.get("status") or "").strip()
        risk_level = str(payload.get("risk_level") or "").strip()
        confirmation_state = str(payload.get("confirmation_state") or "").strip()
        requires_confirmation = bool(payload.get("requires_confirmation")) or status == STATUS_CONFIRMATION_REQUIRED
        stage = self._stream_stage_for_event(row)
        artifacts = self._stream_artifacts_for_event(row)
        packet = {
            "type": "runtime_event",
            "id": str(row.get("id") or ""),
            "ts": str(row.get("ts") or ""),
            "event": event_name,
            "source": str(row.get("source") or ""),
            "kind": "internal" if BrainRuntime._is_internal_runtime_event(event_name) else "business",
            "stream_kind": self._stream_kind_for_event(row),
            "stream_tags": self._stream_tags_for_event(row),
            "stage": stage,
            "phase_label": self._stream_phase_label(stage),
            "label": BrainRuntime._event_human_label(event_name),
            "detail": BrainRuntime._event_detail_text(row),
            "broadcast_text": BrainRuntime._event_broadcast_text(row),
            "human_reply": BrainRuntime._event_broadcast_text(row) or BrainRuntime._event_human_label(event_name),
            "status": status,
            "risk_level": risk_level,
            "risk_summary": self._stream_risk_summary(risk_level),
            "requires_confirmation": requires_confirmation,
            "confirmation_state": confirmation_state,
            "confirmation_summary": self._stream_confirmation_summary(
                requires_confirmation=requires_confirmation,
                confirmation_state=confirmation_state,
                status=status,
            ),
            "session_key": context.get("session_key", ""),
            "chat_id": context.get("chat_id", ""),
            "request_id": context.get("request_id", ""),
            "channel": context.get("channel", ""),
            "agent": str(payload.get("agent") or "").strip(),
            "module": str(payload.get("module") or "").strip(),
            "module_title": str(payload.get("title") or "").strip(),
            "meeting": str(payload.get("meeting") or "").strip(),
            "speaker": str(payload.get("speaker") or "").strip(),
            "artifacts": artifacts,
            "has_decision": bool(payload.get("decision")),
            "suggestion_count": len(list(payload.get("suggestions") or [])) if isinstance(payload.get("suggestions"), list) else 0,
            "pick_count": len(list(payload.get("picks") or [])) if isinstance(payload.get("picks"), list) else 0,
            "dominant_manager_id": str(payload.get("dominant_manager_id") or "").strip(),
            "active_manager_ids": [str(item).strip() for item in list(payload.get("active_manager_ids") or []) if str(item).strip()],
            "manager_budget_weights": {
                str(key): float(value)
                for key, value in dict(payload.get("manager_budget_weights") or {}).items()
                if str(key).strip()
            },
            "regime": str(payload.get("regime") or "").strip(),
            "decision_source": str(payload.get("decision_source") or "").strip(),
            "cash_reserve_hint": payload.get("cash_reserve_hint"),
            "portfolio_constraints": dict(payload.get("portfolio_constraints") or {}),
            "hold_reason": str(payload.get("hold_reason") or "").strip(),
            "reasoning": str(payload.get("reasoning") or "").strip(),
            "regime_confidence": payload.get("regime_confidence"),
            "decision_confidence": payload.get("decision_confidence"),
        }
        if payload.get("progress_pct") not in (None, ""):
            packet["progress_pct"] = payload.get("progress_pct")
        packet["display_priority"] = self._stream_display_priority(str(packet.get("stream_kind") or ""))
        packet["display_text"] = self._stream_display_text(packet)
        return packet

    def subscribe_event_stream(
        self,
        *,
        session_key: str = "",
        chat_id: str = "",
        request_id: str = "",
    ) -> tuple[str, queue.Queue]:
        subscription_id = f"sub:{uuid.uuid4().hex[:16]}"
        subscription = RuntimeEventSubscription(
            subscription_id=subscription_id,
            event_queue=queue.Queue(maxsize=256),
            session_key=str(session_key or "").strip(),
            chat_id=str(chat_id or "").strip(),
            request_id=str(request_id or "").strip(),
        )
        with self._stream_lock:
            self._event_subscriptions[subscription_id] = subscription
        return subscription_id, subscription.event_queue

    @staticmethod
    def _stream_packet_key(packet: dict[str, Any]) -> str:
        return "|".join(
            [
                str(packet.get("stream_kind") or ""),
                str(packet.get("stage") or ""),
                str(packet.get("agent") or ""),
                str(packet.get("module") or ""),
                str(packet.get("meeting") or ""),
                str(packet.get("event") or ""),
            ]
        )

    @staticmethod
    def _stream_text_has_terminal_signal(text: str) -> bool:
        content = str(text or "").strip()
        if not content:
            return False
        keywords = (
            "完成",
            "已完成",
            "成功",
            "失败",
            "异常",
            "结论",
            "决议",
            "决定",
            "最终",
            "切换",
            "阻止",
            "确认",
            "产物",
        )
        return any(keyword in content for keyword in keywords)

    @staticmethod
    def _should_suppress_governance_update(packet: dict[str, Any]) -> bool:
        event_name = str(packet.get("event") or "").strip()
        return event_name in {"governance_started", "regime_classified"}

    @staticmethod
    def _meeting_packet_is_material(packet: dict[str, Any]) -> bool:
        if bool(packet.get("has_decision")):
            return True
        if int(packet.get("suggestion_count") or 0) > 0:
            return True
        if int(packet.get("pick_count") or 0) > 0:
            return True
        return CommanderRuntimeEventStreamMixin._stream_text_has_terminal_signal(str(packet.get("display_text") or ""))

    def _should_emit_stream_packet(
        self,
        subscription: RuntimeEventSubscription,
        packet: dict[str, Any],
    ) -> bool:
        stream_kind = str(packet.get("stream_kind") or "").strip()
        display_text = str(packet.get("display_text") or "").strip()
        packet_key = self._stream_packet_key(packet)
        event_name = str(packet.get("event") or "").strip()
        if display_text and subscription.last_display_by_key.get(packet_key) == display_text:
            subscription.suppressed_count += 1
            return False

        if stream_kind == "governance_update" and self._should_suppress_governance_update(packet):
            subscription.suppressed_count += 1
            return False
        if stream_kind == "governance_update":
            if event_name == "manager_activation_decided":
                subscription.governance_decision_emitted = True
            elif subscription.governance_decision_emitted and event_name in {"governance_applied", "governance_blocked"}:
                subscription.suppressed_count += 1
                return False

        if stream_kind == "agent_update" and packet.get("progress_pct") not in (None, ""):
            try:
                progress_pct = packet.get("progress_pct")
                bucket = int(progress_pct) // 10 if progress_pct is not None else -1
            except (TypeError, ValueError):
                bucket = -1
            stage = str(packet.get("stage") or "agent").strip() or "agent"
            previous_bucket = subscription.last_progress_bucket_by_stage.get(stage)
            if previous_bucket is not None and bucket >= 0 and bucket <= previous_bucket:
                subscription.suppressed_count += 1
                return False
            if bucket >= 0:
                subscription.last_progress_bucket_by_stage[stage] = bucket

        if stream_kind == "module_update":
            stage = str(packet.get("stage") or "module").strip() or "module"
            module = str(packet.get("module") or "").strip() or "module"
            title = str(packet.get("module_title") or "").strip() or str(packet.get("label") or "").strip()
            scope_key = f"{stage}|{module}"
            previous_title = subscription.last_module_title_by_scope.get(scope_key)
            if previous_title and previous_title == title and not self._stream_text_has_terminal_signal(display_text):
                subscription.suppressed_count += 1
                return False
            if title:
                subscription.last_module_title_by_scope[scope_key] = title

        if stream_kind == "meeting_update":
            meeting = str(packet.get("meeting") or "").strip() or str(packet.get("stage") or "meeting").strip() or "meeting"
            if meeting in subscription.seen_meeting_updates and not self._meeting_packet_is_material(packet):
                subscription.suppressed_count += 1
                return False
            subscription.seen_meeting_updates.add(meeting)

        if display_text:
            subscription.last_display_by_key[packet_key] = display_text
        return True

    def _record_stream_packet(
        self,
        subscription: RuntimeEventSubscription,
        packet: dict[str, Any],
    ) -> None:
        subscription.emitted_count += 1
        subscription.collected_packets.append(dict(packet))
        if len(subscription.collected_packets) > 64:
            subscription.collected_packets = subscription.collected_packets[-64:]

    @staticmethod
    def _risk_rank(value: str) -> int:
        return {"low": 1, "medium": 2, "high": 3}.get(str(value or "").strip(), 0)

    def build_stream_summary_packet(self, subscription_id: str) -> dict[str, Any]:
        with self._stream_lock:
            subscription = self._event_subscriptions.get(str(subscription_id or ""))
            if subscription is None:
                return {
                    "type": "runtime_summary",
                    "stream_kind": "summary",
                    "display_priority": 110,
                    "display_text": "本次会话流式播报已结束。",
                }
            packets = list(subscription.collected_packets)
            emitted_count = int(subscription.emitted_count)
            suppressed_count = int(subscription.suppressed_count)

        phase_labels: list[str] = []
        highest_risk = ""
        highest_risk_summary = ""
        requires_confirmation = False
        confirmation_summary = ""
        artifact_names: list[str] = []
        last_display_text = ""
        for packet in packets:
            phase_label = str(packet.get("phase_label") or "").strip()
            if phase_label and phase_label not in phase_labels:
                phase_labels.append(phase_label)
            risk_level = str(packet.get("risk_level") or "").strip()
            if self._risk_rank(risk_level) > self._risk_rank(highest_risk):
                highest_risk = risk_level
                highest_risk_summary = self._stream_risk_summary(risk_level)
            if bool(packet.get("requires_confirmation")):
                requires_confirmation = True
            confirmation_text = str(packet.get("confirmation_summary") or "").strip()
            if confirmation_text:
                confirmation_summary = confirmation_text
            last_display_text = str(packet.get("display_text") or last_display_text or "").strip()
            artifacts = dict(packet.get("artifacts") or {})
            for value in artifacts.values():
                name = Path(str(value)).name if str(value or "").strip() else ""
                if name and name not in artifact_names:
                    artifact_names.append(name)
                if len(artifact_names) >= 3:
                    break
            if len(artifact_names) >= 3:
                break

        parts = [f"本次共播报 {emitted_count} 条事件"]
        if suppressed_count:
            parts.append(f"已合并/抑制 {suppressed_count} 条高频更新")
        if phase_labels:
            parts.append("主要阶段：" + " → ".join(phase_labels[:5]))
        if highest_risk:
            parts.append("最高风险：" + highest_risk_summary)
        if requires_confirmation and confirmation_summary:
            parts.append("确认状态：" + confirmation_summary)
        if artifact_names:
            parts.append("关键产物：" + "、".join(artifact_names[:3]))
        if last_display_text:
            parts.append("最后播报：" + last_display_text)

        display_text = "；".join(part for part in parts if part) + "。"
        return {
            "type": "runtime_summary",
            "stream_kind": "summary",
            "stream_tags": ["summary"],
            "display_priority": 110,
            "display_text": display_text,
            "human_reply": display_text,
            "session_key": subscription.session_key,
            "chat_id": subscription.chat_id,
            "request_id": subscription.request_id,
            "event_count": emitted_count,
            "suppressed_count": suppressed_count,
            "phase_labels": phase_labels,
            "highest_risk_level": highest_risk,
            "highest_risk_summary": highest_risk_summary,
            "requires_confirmation": requires_confirmation,
            "confirmation_summary": confirmation_summary,
            "artifact_names": artifact_names[:3],
            "last_display_text": last_display_text,
        }

    @staticmethod
    def _upsert_human_section(
        sections: list[dict[str, Any]],
        section: dict[str, Any],
        *,
        after_labels: tuple[str, ...] = ("执行性质", "结论"),
    ) -> list[dict[str, Any]]:
        label = str(section.get("label") or "").strip()
        if not label:
            return list(sections or [])

        normalized: list[dict[str, Any]] = []
        insert_index: int | None = None
        for item in list(sections or []):
            if not isinstance(item, dict):
                continue
            item_label = str(item.get("label") or "").strip()
            if item_label == label:
                continue
            normalized.append(item)
            if item_label in after_labels:
                insert_index = len(normalized)

        if insert_index is None:
            normalized.append(section)
            return normalized

        normalized.insert(insert_index, section)
        return normalized

    @staticmethod
    def _append_unique_text(target: list[str], value: str) -> None:
        text = str(value or "").strip()
        if text and text not in target:
            target.append(text)

    @staticmethod
    def _stream_summary_sections(summary: dict[str, Any]) -> list[dict[str, Any]]:
        summary_text = str(summary.get("display_text") or "").strip()
        event_count = int(summary.get("event_count") or 0)
        suppressed_count = int(summary.get("suppressed_count") or 0)
        phase_labels = [str(item) for item in list(summary.get("phase_labels") or []) if str(item or "").strip()]
        artifact_names = [str(item) for item in list(summary.get("artifact_names") or []) if str(item or "").strip()]
        highest_risk_summary = str(summary.get("highest_risk_summary") or "").strip()
        confirmation_summary = str(summary.get("confirmation_summary") or "").strip()
        last_display_text = str(summary.get("last_display_text") or "").strip()

        sections: list[dict[str, Any]] = []
        stream_items: list[str] = []
        if event_count:
            stream_items.append(f"本次共播报 {event_count} 条事件")
        if suppressed_count:
            stream_items.append(f"已合并/抑制 {suppressed_count} 条高频更新")
        if last_display_text:
            stream_items.append(f"最后播报：{last_display_text}")
        if stream_items:
            sections.append({"label": "流式过程", "items": stream_items, "text": summary_text})
        elif summary_text:
            sections.append({"label": "流式过程", "text": summary_text})

        if phase_labels:
            sections.append({"label": "主要阶段", "items": phase_labels})

        risk_items: list[str] = []
        if highest_risk_summary:
            risk_items.append(f"最高风险：{highest_risk_summary}")
        if confirmation_summary:
            risk_items.append(f"确认状态：{confirmation_summary}")
        if risk_items:
            sections.append({"label": "流式风险与确认", "items": risk_items})

        if artifact_names:
            sections.append({"label": "关键产物", "items": artifact_names})
        return sections

    @staticmethod
    def merge_stream_summary_into_reply_payload(
        payload: dict[str, Any] | None,
        summary_packet: dict[str, Any] | None,
    ) -> dict[str, Any]:
        body = dict(payload or {})
        summary = dict(summary_packet or {})
        summary_text = str(summary.get("display_text") or "").strip()
        if not summary_text:
            return body

        body["stream_summary"] = summary
        human = dict(body.get("human_readable") or {})
        if not human:
            human = {
                "summary": str(body.get("message") or body.get("reply") or "").strip(),
                "receipt_text": str(body.get("message") or body.get("reply") or "").strip(),
                "sections": [],
                "bullets": [],
                "facts": [],
                "risks": [],
                "suggested_actions": [],
            }

        sections = list(human.get("sections") or [])
        for section in CommanderRuntimeEventStreamMixin._stream_summary_sections(summary):
            sections = CommanderRuntimeEventStreamMixin._upsert_human_section(sections, section)
        if not any(str(section.get("label") or "") == "流式过程摘要" for section in sections if isinstance(section, dict)):
            sections = CommanderRuntimeEventStreamMixin._upsert_human_section(
                sections,
                {"label": "流式过程摘要", "text": summary_text},
                after_labels=("关键产物", "流式风险与确认", "主要阶段", "流式过程", "执行性质", "结论"),
            )
        human["sections"] = sections

        bullets = [str(item) for item in list(human.get("bullets") or []) if str(item or "").strip()]
        stream_bullet = f"流式过程摘要：{summary_text}"
        CommanderRuntimeEventStreamMixin._append_unique_text(bullets, stream_bullet)
        phase_labels = [str(item) for item in list(summary.get("phase_labels") or []) if str(item or "").strip()]
        if phase_labels:
            CommanderRuntimeEventStreamMixin._append_unique_text(bullets, "主要阶段：" + " → ".join(phase_labels[:5]))
        human["bullets"] = bullets

        facts = [str(item) for item in list(human.get("facts") or []) if str(item or "").strip()]
        CommanderRuntimeEventStreamMixin._append_unique_text(facts, stream_bullet)
        artifact_names = [str(item) for item in list(summary.get("artifact_names") or []) if str(item or "").strip()]
        if artifact_names:
            CommanderRuntimeEventStreamMixin._append_unique_text(facts, "关键产物：" + "、".join(artifact_names[:3]))
        if phase_labels:
            CommanderRuntimeEventStreamMixin._append_unique_text(facts, "主要阶段：" + " → ".join(phase_labels[:5]))
        human["facts"] = facts

        risks = [str(item) for item in list(human.get("risks") or []) if str(item or "").strip()]
        highest_risk_summary = str(summary.get("highest_risk_summary") or "").strip()
        if highest_risk_summary:
            CommanderRuntimeEventStreamMixin._append_unique_text(risks, "最高风险：" + highest_risk_summary)
        confirmation_summary = str(summary.get("confirmation_summary") or "").strip()
        if confirmation_summary:
            CommanderRuntimeEventStreamMixin._append_unique_text(risks, "确认状态：" + confirmation_summary)
        if risks:
            human["risks"] = risks

        suggested_actions = [str(item) for item in list(human.get("suggested_actions") or []) if str(item or "").strip()]
        if bool(summary.get("requires_confirmation")):
            CommanderRuntimeEventStreamMixin._append_unique_text(suggested_actions, "如需继续执行，请先人工确认后再继续。")
        human["suggested_actions"] = suggested_actions

        receipt_text = str(human.get("receipt_text") or "").strip()
        receipt_lines = [line for line in receipt_text.splitlines() if str(line or "").strip()]
        for line in [
            "流式过程：" + summary_text,
            ("主要阶段：" + " → ".join(phase_labels[:5])) if phase_labels else "",
            ("关键产物：" + "、".join(artifact_names[:3])) if artifact_names else "",
            ("流式风险：" + highest_risk_summary) if highest_risk_summary else "",
            ("流式确认：" + confirmation_summary) if confirmation_summary else "",
            "流式过程摘要：" + summary_text,
        ]:
            if line and line not in receipt_lines:
                receipt_lines.append(line)
        receipt_text = "\n".join(receipt_lines) if receipt_lines else ("流式过程摘要：" + summary_text)
        human["receipt_text"] = receipt_text
        human["stream_summary"] = summary
        body["human_readable"] = human
        return body

    def unsubscribe_event_stream(self, subscription_id: str) -> None:
        with self._stream_lock:
            self._event_subscriptions.pop(str(subscription_id or ""), None)

    @staticmethod
    def _event_matches_subscription(row: dict[str, Any], subscription: RuntimeEventSubscription) -> bool:
        context = CommanderRuntimeEventStreamMixin._event_context_from_row(row)
        if subscription.request_id:
            return context.get("request_id", "") == subscription.request_id
        if subscription.session_key and context.get("session_key", "") != subscription.session_key:
            return False
        if subscription.chat_id and context.get("chat_id", "") != subscription.chat_id:
            return False
        return bool(subscription.session_key or subscription.chat_id)

    def _publish_stream_event(self, row: dict[str, Any]) -> None:
        packet = self._build_stream_event_packet(row)
        with self._stream_lock:
            subscriptions = list(self._event_subscriptions.values())
        for subscription in subscriptions:
            if not self._event_matches_subscription(row, subscription):
                continue
            if not self._should_emit_stream_packet(subscription, packet):
                continue
            self._record_stream_packet(subscription, packet)
            try:
                subscription.event_queue.put_nowait(packet)
            except queue.Full:
                try:
                    subscription.event_queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    subscription.event_queue.put_nowait(packet)
                except queue.Full:
                    continue


def setup_cron_callback(
    *,
    cron: Any,
    ask: Callable[..., Awaitable[str]],
    notifications: asyncio.Queue[str],
) -> None:
    async def on_cron_job(job: Any) -> str | None:
        response = await ask(job.message, session_key=f"cron:{job.id}")
        if job.deliver:
            notify = f"[cron][{job.channel}:{job.to}] {response or ''}"
            await notifications.put(notify)
        return response

    cron.on_job = on_cron_job


async def drain_runtime_notifications(
    notifications: asyncio.Queue[str],
    *,
    logger: Any,
) -> None:
    while True:
        try:
            msg = await asyncio.wait_for(notifications.get(), timeout=1.0)
        except asyncio.TimeoutError:
            continue
        except asyncio.CancelledError:
            return

        preview = msg.strip() if isinstance(msg, str) else str(msg)
        if preview:
            logger.info("%s", preview[:300])


def _emit_background_task_failure(
    *,
    task_name: str,
    exc: BaseException,
    logger: Any,
    task_error_handler: Callable[[str, BaseException], None] | None = None,
) -> None:
    logger.error(
        "Commander runtime background task failed: task=%s error_type=%s error=%s",
        task_name,
        type(exc).__name__,
        exc,
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    if task_error_handler is None:
        return
    try:
        task_error_handler(task_name, exc)
    except Exception:
        logger.exception(
            "Commander runtime background task error hook failed: task=%s",
            task_name,
        )


def create_supervised_task(
    coro: Coroutine[Any, Any, None],
    *,
    task_name: str,
    logger: Any,
    task_error_handler: Callable[[str, BaseException], None] | None = None,
) -> asyncio.Task[None]:
    task = asyncio.create_task(coro, name=task_name)

    def _on_done(done_task: asyncio.Task[None]) -> None:
        if done_task.cancelled():
            return
        try:
            exc = done_task.exception()
        except asyncio.CancelledError:
            return
        if exc is None:
            return
        _emit_background_task_failure(
            task_name=task_name,
            exc=exc,
            logger=logger,
            task_error_handler=task_error_handler,
        )

    task.add_done_callback(_on_done)
    return task


async def _await_cancelled_task(
    task: asyncio.Task[None],
    *,
    task_name: str,
    logger: Any,
) -> None:
    task.cancel()
    results = await asyncio.gather(task, return_exceptions=True)
    if not results:
        return
    result = results[0]
    if isinstance(result, BaseException) and not isinstance(
        result, asyncio.CancelledError
    ):
        logger.error(
            "Commander runtime background task stopped with error: task=%s error_type=%s error=%s",
            task_name,
            type(result).__name__,
            result,
            exc_info=(type(result), result, result.__traceback__),
        )


ensure_runtime_storage = cast(Callable[..., None], _bootstrap_proxy("ensure_runtime_storage"))
persist_runtime_state = cast(Callable[..., None], _bootstrap_proxy("persist_runtime_state_payload"))
load_persisted_runtime_state = cast(
    Callable[..., dict[str, Any] | None],
    _bootstrap_proxy("load_persisted_runtime_state"),
)
restore_runtime_from_persisted_state = cast(
    Callable[..., None],
    _bootstrap_proxy("restore_runtime_from_persisted_state"),
)
persist_runtime_snapshot = cast(Callable[..., None], _bootstrap_proxy("persist_runtime_snapshot"))
write_commander_identity_artifacts = cast(
    Callable[..., None],
    _bootstrap_proxy("write_commander_identity_artifacts"),
)
write_runtime_identity = cast(Callable[..., None], _bootstrap_proxy("write_runtime_identity"))


async def start_runtime_background_services(
    *,
    cron: Any,
    heartbeat: Any,
    bridge: Any,
    heartbeat_enabled: bool,
    bridge_enabled: bool,
    drain_notifications: Callable[[], Coroutine[Any, Any, None]],
    autopilot_enabled: bool,
    autopilot_loop: Callable[[int], Coroutine[Any, Any, None]],
    training_interval_sec: int,
    logger: Any,
    task_error_handler: Callable[[str, BaseException], None] | None = None,
) -> tuple[asyncio.Task[None], asyncio.Task[None] | None]:
    await cron.start()
    if heartbeat_enabled:
        await heartbeat.start()
    if bridge_enabled:
        await bridge.start()

    notify_task = create_supervised_task(
        drain_notifications(),
        task_name="runtime-notifications",
        logger=logger,
        task_error_handler=task_error_handler,
    )
    autopilot_task: asyncio.Task[None] | None = None
    if autopilot_enabled:
        autopilot_task = create_supervised_task(
            autopilot_loop(training_interval_sec),
            task_name="runtime-autopilot",
            logger=logger,
            task_error_handler=task_error_handler,
        )
    return notify_task, autopilot_task


async def start_runtime_flow(
    *,
    is_started: bool,
    logger: Any,
    ensure_runtime_storage: Callable[[], None],
    begin_task: Callable[..., None],
    set_runtime_state: Callable[[str], None],
    acquire_runtime_lock: Callable[[], None],
    ensure_default_playbooks: Callable[[], None],
    reload_playbooks: Callable[[], Any],
    load_plugins: Callable[..., dict[str, Any]],
    write_commander_identity: Callable[[], None],
    start_background_services: Callable[[], Awaitable[tuple[asyncio.Task[None], asyncio.Task[None] | None]]],
    mark_started: Callable[[bool], None],
    set_background_tasks: Callable[[asyncio.Task[None] | None, asyncio.Task[None] | None], None],
    complete_runtime_task: Callable[..., None],
    end_task: Callable[..., None],
    release_runtime_lock: Callable[[], None],
    persist_state: Callable[[], None],
    starting_state: str,
    idle_state: str,
    error_state: str,
    ok_status: str,
) -> None:
    if is_started:
        return
    ensure_runtime_storage()
    begin_task("start", "system")
    set_runtime_state(starting_state)
    try:
        acquire_runtime_lock()
        ensure_default_playbooks()
        reload_playbooks()
        load_plugins(persist=False)
        write_commander_identity()
        notify_task, autopilot_task = await start_background_services()
        set_background_tasks(notify_task, autopilot_task)
        mark_started(True)
        complete_runtime_task(state=idle_state, status=ok_status)
    except Exception:
        logger.exception("Commander runtime start failed during bootstrap sequence")
        set_runtime_state(error_state)
        end_task(error_state)
        release_runtime_lock()
        persist_state()
        raise


async def stop_runtime_background_services(
    *,
    body: Any,
    autopilot_task: asyncio.Task[None] | None,
    notify_task: asyncio.Task[None] | None,
    bridge: Any,
    heartbeat: Any,
    cron: Any,
    brain: Any,
    logger: Any,
) -> tuple[None, None]:
    body.stop()
    if autopilot_task:
        await _await_cancelled_task(
            autopilot_task,
            task_name="runtime-autopilot",
            logger=logger,
        )
    if notify_task:
        await _await_cancelled_task(
            notify_task,
            task_name="runtime-notifications",
            logger=logger,
        )

    bridge.stop()
    heartbeat.stop()
    cron.stop()
    await brain.close()
    return None, None


async def stop_runtime_flow(
    *,
    is_started: bool,
    logger: Any | None = None,
    begin_task: Callable[..., None],
    set_runtime_state: Callable[[str], None],
    stop_background_services: Callable[[], Awaitable[tuple[None, None]]],
    mark_started: Callable[[bool], None],
    release_runtime_lock: Callable[[], None],
    complete_runtime_task: Callable[..., None],
    persist_state: Callable[[], None] | None = None,
    stopping_state: str,
    stopped_state: str,
    error_state: str = "error",
    ok_status: str,
    error_status: str = "error",
) -> None:
    if not is_started:
        return
    begin_task("stop", "system")
    set_runtime_state(stopping_state)
    try:
        await stop_background_services()
    except Exception:
        if logger is not None:
            logger.exception("Commander runtime stop failed during shutdown sequence")
        mark_started(False)
        set_runtime_state(error_state)
        release_runtime_lock()
        complete_runtime_task(state=error_state, status=error_status)
        if persist_state is not None:
            persist_state()
        raise
    mark_started(False)
    release_runtime_lock()
    complete_runtime_task(state=stopped_state, status=ok_status)


reload_playbooks_response = cast(Callable[..., dict[str, Any]], _workflow_proxy("reload_playbooks_response"))
add_cron_job_response = cast(Callable[..., dict[str, Any]], _workflow_proxy("add_cron_job_response"))
list_cron_jobs_response = cast(Callable[..., dict[str, Any]], _workflow_proxy("list_cron_jobs_response"))
remove_cron_job_response = cast(Callable[..., dict[str, Any]], _workflow_proxy("remove_cron_job_response"))
reload_plugins_response = cast(Callable[..., dict[str, Any]], _workflow_proxy("reload_plugins_response"))


async def serve_forever_loop(
    *,
    start_runtime: Callable[[], Awaitable[None]],
    ask_runtime: Callable[..., Awaitable[str]],
    interactive: bool,
    input_func: Callable[[str], str] = input,
    print_func: Callable[..., None] = print,
    sleep_func: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    await start_runtime()

    if interactive:
        print_func("Commander interactive mode. Type 'exit' to quit.")
        while True:
            line = await asyncio.to_thread(input_func, "commander> ")
            cmd = line.strip()
            if not cmd:
                continue
            if cmd.lower() in {"exit", "quit", "/exit", ":q"}:
                break
            reply = await ask_runtime(cmd, session_key="cli:commander", channel="cli", chat_id="commander")
            print_func(reply)
        return

    while True:
        await sleep_func(1)


build_events_tail_response_bundle = cast(
    Callable[..., Any],
    _status_proxy("build_events_tail_response_bundle"),
)
build_events_summary_response_bundle = cast(
    Callable[..., Any],
    _status_proxy("build_events_summary_response_bundle"),
)
build_runtime_diagnostics_response_bundle = cast(
    Callable[..., Any],
    _status_proxy("build_runtime_diagnostics_response_bundle"),
)
build_training_lab_summary_response_bundle = cast(
    Callable[..., Any],
    _status_proxy("build_training_lab_summary_response_bundle"),
)
build_status_response_bundle = cast(
    Callable[..., Any],
    _status_proxy("build_status_response_bundle"),
)
get_events_tail_response = cast(Callable[..., dict[str, Any]], _status_proxy("get_events_tail_response"))
get_events_summary_response = cast(Callable[..., dict[str, Any]], _status_proxy("get_events_summary_response"))
get_runtime_diagnostics_response = cast(
    Callable[..., dict[str, Any]],
    _status_proxy("get_runtime_diagnostics_response"),
)
get_training_lab_summary_response = cast(
    Callable[..., dict[str, Any]],
    _status_proxy("get_training_lab_summary_response"),
)
get_status_response = cast(Callable[..., dict[str, Any]], _status_proxy("get_status_response"))


def register_fusion_tools(
    runtime: Any,
    *,
    build_tools: Callable[[Any], list[Any]],
    load_plugins: Callable[..., dict[str, Any]],
) -> None:
    for tool in build_tools(runtime):
        runtime.brain.tools.register(tool)
    load_plugins(persist=False)


def load_plugin_tools(
    *,
    brain_tools: Any,
    plugin_loader: Any,
    plugin_tool_names: set[str],
    plugin_dir: Path,
    persist: bool,
    persist_state: Callable[[], None],
    logger: Any | None = None,
) -> dict[str, Any]:
    for name in list(plugin_tool_names):
        brain_tools.unregister(name)
    plugin_tool_names.clear()

    loaded: list[str] = []
    skipped_conflicts: list[str] = []
    for tool in plugin_loader.load_tools():
        if brain_tools.get(tool.name) is not None:
            skipped_conflicts.append(tool.name)
            if logger is not None:
                logger.warning(
                    "Skipping plugin tool %s because it conflicts with an existing runtime tool",
                    tool.name,
                )
            continue
        brain_tools.register(tool)
        plugin_tool_names.add(tool.name)
        loaded.append(tool.name)

    if persist:
        persist_state()
    return {
        "count": len(loaded),
        "tools": loaded,
        "plugin_dir": str(plugin_dir),
        "skipped_conflicts": skipped_conflicts,
    }
