"""Meeting service facades for the invest domain."""

from __future__ import annotations

from typing import Any, Protocol

from invest.meetings import ReviewMeeting, SelectionMeeting


class SelectionMeetingLike(Protocol):
    def run(
        self,
        regime: dict[str, Any],
        stock_summaries: list[dict[str, Any]],
        top_n: int = 5,
    ) -> dict[str, Any]:
        ...

    def run_with_model_output(self, model_output: Any) -> dict[str, Any]:
        ...

    def run_with_context(self, signal_packet: Any, agent_context: Any) -> dict[str, Any]:
        ...

    def update_weights(self, weight_adjustments: dict[str, float]) -> None:
        ...


class ReviewMeetingLike(Protocol):
    def run_with_eval_report(
        self,
        eval_report: Any,
        *,
        agent_accuracy: dict[str, Any],
        current_params: dict[str, Any],
        regime_history: list[str] | None = None,
    ) -> dict[str, Any]:
        ...

    def set_policy(self, policy: dict[str, Any] | None = None) -> None:
        ...


class SelectionMeetingService:
    """Thin facade that gives the selection meeting an explicit service boundary."""

    def __init__(self, meeting: SelectionMeetingLike | None = None, **meeting_kwargs: Any):
        self.meeting = meeting or SelectionMeeting(**meeting_kwargs)

    def run(self, regime: dict[str, Any], stock_summaries: list[dict[str, Any]], top_n: int = 5) -> dict[str, Any]:
        return self.meeting.run(regime, stock_summaries, top_n=top_n)

    def run_with_model_output(self, model_output: Any) -> dict[str, Any]:
        return self.meeting.run_with_model_output(model_output)

    def run_with_context(self, signal_packet: Any, agent_context: Any) -> dict[str, Any]:
        return self.meeting.run_with_context(signal_packet, agent_context)

    def update_weights(self, weight_adjustments: dict[str, float]) -> None:
        self.meeting.update_weights(weight_adjustments)


class ReviewMeetingService:
    """Thin facade that gives the review meeting an explicit service boundary."""

    def __init__(self, meeting: ReviewMeetingLike | None = None, **meeting_kwargs: Any):
        self.meeting = meeting or ReviewMeeting(**meeting_kwargs)

    def run_with_eval_report(
        self,
        eval_report: Any,
        agent_accuracy: dict[str, Any],
        current_params: dict[str, Any],
        regime_history: list[str] | None = None,
    ) -> dict[str, Any]:
        return self.meeting.run_with_eval_report(
            eval_report,
            agent_accuracy=agent_accuracy,
            current_params=current_params,
            regime_history=regime_history,
        )

    def set_policy(self, policy: dict[str, Any] | None = None) -> None:
        self.meeting.set_policy(policy)
