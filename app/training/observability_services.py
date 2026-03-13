from __future__ import annotations

from datetime import datetime
from typing import Any, Callable


class TrainingObservabilityService:
    """Owns controller-side event shaping and progress adaptation."""

    @staticmethod
    def thinking_excerpt(reasoning: Any, limit: int = 200) -> str:
        if not reasoning:
            return ""
        if isinstance(reasoning, dict):
            candidate = (
                reasoning.get("reasoning")
                or reasoning.get("summary")
                or reasoning.get("regime")
                or ""
            )
            return str(candidate)[:limit]
        if isinstance(reasoning, (list, tuple)):
            return "；".join(str(item) for item in reasoning[:5])[:limit]
        return str(reasoning)[:limit]

    def event_context(self, controller: Any, cycle_id: int | None = None) -> dict[str, Any]:
        meta = dict(controller.last_cycle_meta or {})
        context: dict[str, Any] = {"timestamp": datetime.now().isoformat()}
        if cycle_id is not None:
            context["cycle_id"] = cycle_id
        elif meta.get("cycle_id") is not None:
            context["cycle_id"] = meta.get("cycle_id")
        if meta.get("cutoff_date"):
            context["cutoff_date"] = meta.get("cutoff_date")
        return context

    def emit_agent_status(
        self,
        controller: Any,
        *,
        event_emitter: Callable[[str, dict[str, Any]], None],
        agent: str,
        status: str,
        message: str,
        cycle_id: int | None = None,
        stage: str = "",
        progress_pct: int | None = None,
        step: int | None = None,
        total_steps: int | None = None,
        thinking: str = "",
        selected_stocks: list[str] | None = None,
        details: Any = None,
        **extra: Any,
    ) -> None:
        payload = {
            **self.event_context(controller, cycle_id),
            "agent": agent,
            "status": status,
            "message": message,
        }
        if stage:
            payload["stage"] = stage
        if progress_pct is not None:
            payload["progress_pct"] = int(progress_pct)
        if step is not None:
            payload["step"] = int(step)
        if total_steps is not None:
            payload["total_steps"] = int(total_steps)
        if thinking:
            payload["thinking"] = thinking
        if selected_stocks:
            payload["selected_stocks"] = list(selected_stocks)
        if details is not None:
            payload["details"] = details
        payload.update(extra)
        event_emitter("agent_status", payload)
        event_emitter("agent_progress", dict(payload))

    def emit_module_log(
        self,
        controller: Any,
        *,
        event_emitter: Callable[[str, dict[str, Any]], None],
        module: str,
        title: str,
        message: str = "",
        cycle_id: int | None = None,
        kind: str = "log",
        level: str = "info",
        details: Any = None,
        metrics: dict[str, Any] | None = None,
        **extra: Any,
    ) -> None:
        payload = {
            **self.event_context(controller, cycle_id),
            "module": module,
            "title": title,
            "message": message,
            "kind": kind,
            "level": level,
        }
        if details is not None:
            payload["details"] = details
        if metrics:
            payload["metrics"] = metrics
        payload.update(extra)
        event_emitter("module_log", payload)

    def emit_meeting_speech(
        self,
        controller: Any,
        *,
        event_emitter: Callable[[str, dict[str, Any]], None],
        meeting: str,
        speaker: str,
        speech: str,
        cycle_id: int | None = None,
        role: str = "",
        picks: list[dict[str, Any]] | list[str] | None = None,
        suggestions: list[str] | None = None,
        decision: dict[str, Any] | None = None,
        confidence: Any = None,
        **extra: Any,
    ) -> None:
        payload = {
            **self.event_context(controller, cycle_id),
            "meeting": meeting,
            "speaker": speaker,
            "speech": str(speech or "").strip(),
        }
        if role:
            payload["role"] = role
        if picks:
            payload["picks"] = picks
        if suggestions:
            payload["suggestions"] = suggestions
        if decision:
            payload["decision"] = decision
        if confidence is not None:
            payload["confidence"] = confidence
        payload.update(extra)
        event_emitter("meeting_speech", payload)

    def handle_selection_progress(self, controller: Any, payload: dict[str, Any]) -> None:
        agent = str(payload.get("agent") or "SelectionMeeting")
        status = str(payload.get("status") or "running")
        stage = str(payload.get("stage") or "selection")
        progress_pct = payload.get("progress_pct")
        if progress_pct is None:
            progress_pct = {
                "TrendHunter": 38,
                "Contrarian": 46,
                "SelectionMeeting": 54,
            }.get(agent, 40)
            if status == "completed":
                progress_pct = min(100, int(progress_pct) + 8)
            elif status == "error":
                progress_pct = int(progress_pct)
        controller._emit_agent_status(
            agent,
            status,
            str(payload.get("message") or ""),
            stage=stage,
            progress_pct=int(progress_pct),
            step=payload.get("step"),
            total_steps=payload.get("total_steps"),
            thinking=controller._thinking_excerpt(
                payload.get("speech") or payload.get("reasoning") or payload.get("overall_view")
            ),
            details=payload.get("details"),
            picks=payload.get("picks"),
        )
        speech = str(payload.get("speech") or payload.get("overall_view") or "").strip()
        if speech:
            controller._emit_meeting_speech(
                "selection",
                agent,
                speech,
                role="selector",
                picks=payload.get("picks"),
                confidence=payload.get("confidence"),
            )
        picks = payload.get("picks") or []
        if picks:
            controller._emit_module_log(
                "selection",
                f"{agent} 输出候选",
                f"推荐 {len(picks)} 只候选股票",
                kind="selection_candidates",
                details=picks[:10],
                metrics={"candidate_count": len(picks)},
            )

    def handle_review_progress(self, controller: Any, payload: dict[str, Any]) -> None:
        agent = str(payload.get("agent") or "ReviewMeeting")
        status = str(payload.get("status") or "running")
        stage = str(payload.get("stage") or "review")
        progress_pct = payload.get("progress_pct")
        if progress_pct is None:
            progress_pct = {
                "Strategist": 82,
                "EvoJudge": 88,
                "ReviewDecision": 92,
                "ReviewMeeting": 95,
            }.get(agent, 85)
        controller._emit_agent_status(
            agent,
            status,
            str(payload.get("message") or ""),
            stage=stage,
            progress_pct=int(progress_pct),
            thinking=controller._thinking_excerpt(payload.get("speech") or payload.get("reasoning")),
            details=payload.get("details"),
        )
        speech = str(payload.get("speech") or payload.get("reasoning") or "").strip()
        if speech:
            controller._emit_meeting_speech(
                "review",
                agent,
                speech,
                role="reviewer",
                suggestions=payload.get("suggestions"),
                decision=payload.get("decision"),
                confidence=payload.get("confidence"),
            )
        suggestions = payload.get("suggestions") or []
        if suggestions or payload.get("decision"):
            controller._emit_module_log(
                "review",
                f"{agent} 复盘输出",
                str(payload.get("message") or ""),
                kind="review_update",
                details=suggestions or payload.get("decision"),
            )

    def mark_cycle_skipped(
        self,
        controller: Any,
        *,
        event_emitter: Callable[[str, dict[str, Any]], None],
        cycle_id: int,
        cutoff_date: str,
        stage: str,
        reason: str,
        **extra: Any,
    ) -> None:
        meta = {
            "status": "no_data",
            "cycle_id": cycle_id,
            "cutoff_date": cutoff_date,
            "stage": stage,
            "reason": reason,
            "timestamp": datetime.now().isoformat(),
            **extra,
        }
        controller.last_cycle_meta = meta
        controller._emit_module_log(
            stage,
            f"周期 #{cycle_id} 已跳过",
            reason,
            cycle_id=cycle_id,
            kind="cycle_skipped",
            level="warn",
            details=extra or None,
        )
        event_emitter("cycle_skipped", meta)
