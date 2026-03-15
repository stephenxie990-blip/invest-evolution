import logging
from typing import Any, Dict, List, Optional, Callable

from invest.agents.reviewers import EvoJudgeAgent, ReviewDecisionAgent, StrategistAgent
from invest.shared.llm import LLMCaller
from invest.shared.tracking import AgentTracker
from invest.contracts import EvalReport, StrategyAdvice
from invest.foundation.risk import sanitize_risk_params

try:
    from invest.debate import RiskDebateOrchestrator
    _HAS_DEBATE = True
except ImportError:
    RiskDebateOrchestrator = None
    _HAS_DEBATE = False

logger = logging.getLogger(__name__)


def _normalize_agent_weight_adjustments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if not isinstance(raw, list):
        return {}
    normalized: dict[str, Any] = {}
    for item in raw:
        if isinstance(item, dict):
            agent = str(item.get("agent") or item.get("name") or item.get("agent_name") or "").strip()
            if not agent:
                continue
            weight = item.get("weight")
            if weight is None:
                weight = item.get("value")
            if weight is None:
                weight = item.get("adjustment")
            normalized[agent] = weight
            continue
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            agent = str(item[0] or "").strip()
            if agent:
                normalized[agent] = item[1]
    return normalized


def _string_items(raw: Any, *, limit: int) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()][:limit]


def _dict_payload(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    return dict(raw)


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default



def _normalize_param_value(key: str, value: float) -> float:
    percent_like = {"stop_loss_pct", "take_profit_pct", "position_size", "cash_reserve", "trailing_pct"}
    if key in percent_like and value > 1.0 and value <= 100.0:
        value = value / 100.0
    return value

_REVIEW_STRATEGIST_SYSTEM = """你是投资复盘会议中的策略分析师，负责基于已给出的交易事实总结“策略层面”的问题与改进建议。

任务目标：
1. 只基于输入事实做诊断，不编造不存在的市场事件。
2. problems 聚焦策略缺陷、风格偏差、风险控制问题。
3. suggestions 聚焦可执行改进，而不是空泛口号。

稳定性约束：
- 只输出一个 JSON 对象，不要输出 Markdown、代码块或额外说明。
- problems 与 suggestions 各保留 0-4 条。
- confidence 必须是 0 到 1 的数字。

少样本示例：
示例1（合格）
输出：{"problems":["胜率偏低且回撤集中在追涨型交易"],"suggestions":["收紧趋势确认阈值","降低高波动标的仓位"],"confidence":0.76}

负例约束：
- 错误示例1：输出长篇复盘文章。
- 错误示例2：直接替 Commander 做最终采纳决策。
- 错误示例3：只说“注意风险”，却没有具体问题。

严格输出：{"problems":["问题1"],"suggestions":["建议1"],"confidence":0.0}"""

_REVIEW_EVO_JUDGE_SYSTEM = """你是复盘会议中的进化裁判，负责根据策略问题和近期表现，提出参数层面的调整方向。

任务目标：
1. 给出 param_adjustments。
2. 给出 evolution_direction，只能是 aggressive / conservative / maintain。
3. 给出简洁 suggestions 与 reasoning。

稳定性约束：
- 只输出一个 JSON 对象。
- 不要输出未定义字段、Markdown 或解释性前缀。
- 若没有足够证据，不要过度激进调参。

少样本示例：
示例1（合格）
输出：{"param_adjustments":{"stop_loss_pct":0.04,"position_size":0.15},"evolution_direction":"conservative","suggestions":["先收紧止损与仓位"],"confidence":0.73,"reasoning":"连续亏损说明当前应先降风险暴露。"}

负例约束：
- 错误示例1：输出 none/unknown 等未定义方向。
- 错误示例2：给出离谱参数，如仓位 0.9。
- 错误示例3：没有 param_adjustments 却强行给 aggressive。

严格输出：{"param_adjustments":{"stop_loss_pct":null,"take_profit_pct":null,"position_size":null},"evolution_direction":"maintain","suggestions":["建议1"],"confidence":0.0,"reasoning":"一句话说明依据"}"""

_REVIEW_DECISION_SYSTEM = """你是复盘决策综合员，负责综合策略分析与进化评估，输出下一轮的采纳建议。

任务目标：
1. 产出 strategy_suggestions。
2. 决定 param_adjustments 是否采纳。
3. 调整 agent_weight_adjustments。
4. reasoning 解释采纳依据。

稳定性约束：
- 只输出一个 JSON 对象。
- 不要编造不存在的 agent 名称。
- agent_weight_adjustments 只对输入里已有的 agent 调整。
- 若证据偏弱，可维持权重接近 1.0，而不是极端调整。

少样本示例：
示例1（合格）
输出：{"strategy_suggestions":["减少追高型交易"],"param_adjustments":{"position_size":0.15},"agent_weight_adjustments":{"trend_hunter":0.9,"contrarian":1.1},"reasoning":"逆向侧近期相对稳定，应小幅提高其权重。"}

负例约束：
- 错误示例1：输出 Markdown、代码块或会议纪要。
- 错误示例2：给不存在的 agent 分配权重。
- 错误示例3：把所有权重都拉到 2.0 以上。

严格输出：{"strategy_suggestions":["建议1"],"param_adjustments":{"key":0.1},"agent_weight_adjustments":{"trend_hunter":1.0,"contrarian":1.0},"reasoning":"一句话说明依据"}"""

class ReviewMeeting:
    """
    复盘会议编排器

    流程：
    1. 事实对账（算法）：Agent 预测 vs 实际结果
    2. Strategist 分析（LLM）：策略层面问题
    3. EvoJudge 评估（LLM）：参数层面调整建议
    4. ReviewDecision 决策（LLM）：最终采纳

    触发条件：连续亏损 ≥ 3 / 每 N 个 cycle / 重大偏差
    """

    def __init__(
        self,
        llm_caller: Optional[LLMCaller] = None,
        agent_tracker: Optional[AgentTracker] = None,
        strategist: Optional[Any] = None,
        evo_judge: Optional[Any] = None,
        commander: Optional[Any] = None,
        enable_risk_debate: bool = True,
        max_risk_discuss_rounds: int = 1,
        deep_llm_caller: Optional[LLMCaller] = None,
        aggressive_llm_caller: Optional[LLMCaller] = None,
        conservative_llm_caller: Optional[LLMCaller] = None,
        neutral_llm_caller: Optional[LLMCaller] = None,
        risk_judge_llm_caller: Optional[LLMCaller] = None,
        progress_callback: Optional[Callable[[dict], None]] = None,
    ):
        self.llm = llm_caller
        self.deep_llm = deep_llm_caller or llm_caller
        self.aggressive_llm = aggressive_llm_caller or llm_caller
        self.conservative_llm = conservative_llm_caller or llm_caller
        self.neutral_llm = neutral_llm_caller or llm_caller
        self.risk_judge_llm = risk_judge_llm_caller or self.deep_llm
        self.tracker = agent_tracker
        self.strategist = strategist or StrategistAgent()
        self.evo_judge = evo_judge or EvoJudgeAgent()
        self.review_decision_agent = commander or ReviewDecisionAgent()
        self.commander = self.review_decision_agent
        self.review_count = 0
        self.progress_callback = progress_callback
        self.review_policy: dict[str, Any] = {}
        self.last_facts: Dict[str, Any] = {}

        self._risk_debate: Optional[Any] = None
        if _HAS_DEBATE and RiskDebateOrchestrator is not None and enable_risk_debate and llm_caller is not None:
            deep = self.deep_llm or llm_caller
            aggressive = self.aggressive_llm or llm_caller
            conservative = self.conservative_llm or llm_caller
            neutral = self.neutral_llm or llm_caller
            judge = self.risk_judge_llm or deep
            self._risk_debate = RiskDebateOrchestrator(
                fast_llm=llm_caller,
                deep_llm=deep,
                max_rounds=max_risk_discuss_rounds,
                aggressive_llm=aggressive,
                conservative_llm=conservative,
                neutral_llm=neutral,
                judge_llm=judge,
            )
            logger.info("ReviewMeeting: risk debate enabled (max_rounds=%d)", max_risk_discuss_rounds)

    def _notify_progress(self, payload: dict) -> None:
        if not self.progress_callback:
            return
        try:
            self.progress_callback(dict(payload))
        except Exception as exc:
            logger.warning(
                "Review progress callback failed for keys=%s: %s",
                sorted(payload.keys()),
                exc,
                exc_info=exc,
            )

    def _run_review(
        self,
        recent_results: List[dict],
        agent_accuracy: dict,
        current_params: dict,
        regime_history: List[str] | None = None,
        review_basis_window: dict[str, Any] | None = None,
        similar_results: List[dict] | None = None,
        similarity_summary: dict[str, Any] | None = None,
        causal_diagnosis: dict[str, Any] | None = None,
    ) -> dict:
        """
        运行复盘会议

        Args:
            recent_results: 最近 N 个 cycle 结果
            agent_accuracy: AgentTracker.compute_accuracy() 的输出
            current_params: 当前策略参数
            regime_history: 最近的市场状态列表

        Returns:
            {"strategy_suggestions", "param_adjustments",
             "agent_weight_adjustments", "reasoning"}
        """
        self.review_count += 1
        logger.info(f"\n{'='*50}")
        logger.info(f"📋 复盘会议 #{self.review_count} 开始")

        facts = self._compile_facts(
            recent_results,
            agent_accuracy,
            similar_results=similar_results,
            similarity_summary=similarity_summary,
            causal_diagnosis=causal_diagnosis,
        )
        if isinstance(review_basis_window, dict) and review_basis_window:
            facts["review_basis_window"] = dict(review_basis_window)
        self.last_facts = dict(facts)
        self._log_facts(facts)
        self._notify_progress({
            "agent": "ReviewMeeting",
            "status": "running",
            "stage": "review",
            "progress_pct": 84,
            "message": f"复盘会议 #{self.review_count} 启动，正在汇总近 {facts.get('total_cycles', 0)} 轮结果",
            "details": facts,
        })

        strategy_analysis = self._strategist_analysis(facts, current_params)
        self._notify_progress({
            "agent": "Strategist",
            "status": "completed",
            "stage": "review",
            "progress_pct": 88,
            "message": f"策略分析完成，识别 {len(strategy_analysis.get('problems', []))} 个问题",
            "speech": "；".join(strategy_analysis.get("problems", [])[:3]) or "策略表现整体稳定",
            "suggestions": strategy_analysis.get("suggestions", []),
            "confidence": strategy_analysis.get("confidence"),
            "details": strategy_analysis,
        })
        evo_assessment = self._evo_judge_assessment(facts, strategy_analysis)
        self._notify_progress({
            "agent": "EvoJudge",
            "status": "completed",
            "stage": "review",
            "progress_pct": 92,
            "message": f"进化评估完成，方向 {evo_assessment.get('evolution_direction', 'maintain')}",
            "speech": evo_assessment.get("reasoning") or f"建议方向 {evo_assessment.get('evolution_direction', 'maintain')}",
            "suggestions": evo_assessment.get("suggestions", []),
            "decision": evo_assessment.get("param_adjustments", {}),
            "confidence": evo_assessment.get("confidence"),
            "details": evo_assessment,
        })
        decision = self._review_decision(facts, strategy_analysis, evo_assessment, current_params)
        self._notify_progress({
            "agent": "ReviewDecision",
            "status": "completed",
            "stage": "review",
            "progress_pct": 96,
            "message": f"最终决策已形成，策略建议 {len(decision.get('strategy_suggestions', []))} 条",
            "speech": decision.get("reasoning") or "最终决策已生成",
            "suggestions": decision.get("strategy_suggestions", []),
            "decision": {
                "param_adjustments": decision.get("param_adjustments", {}),
                "agent_weight_adjustments": decision.get("agent_weight_adjustments", {}),
            },
            "details": decision,
        })

        # 反思闭环：让所有 Agent 根据事实和决策进行自我更新
        self._trigger_reflections(facts, decision)

        logger.info(f"📋 复盘会议 #{self.review_count} 完成")
        logger.info(f"  策略建议: {len(decision.get('strategy_suggestions', []))}条")
        logger.info(f"  参数调整: {decision.get('param_adjustments', {})}")
        logger.info(f"  权重调整: {decision.get('agent_weight_adjustments', {})}")
        logger.info(f"{'='*50}\n")

        return decision

    def run_with_eval_report(
        self,
        eval_report: EvalReport,
        agent_accuracy: dict,
        current_params: dict,
        regime_history: List[str] | None = None,
        recent_results: List[dict] | None = None,
        review_basis_window: dict[str, Any] | None = None,
        similar_results: List[dict] | None = None,
        similarity_summary: dict[str, Any] | None = None,
        causal_diagnosis: dict[str, Any] | None = None,
    ) -> dict:
        compiled_results = list(recent_results or [eval_report.to_dict()])
        decision = self._run_review(
            compiled_results,
            agent_accuracy,
            current_params,
            regime_history=regime_history,
            review_basis_window=review_basis_window,
            similar_results=similar_results,
            similarity_summary=similarity_summary,
            causal_diagnosis=causal_diagnosis,
        )
        advice = StrategyAdvice(
            source="review_meeting",
            selected_codes=list(eval_report.selected_codes),
            confidence=1.0 if decision.get("strategy_suggestions") else 0.6,
            reasoning=str(decision.get("reasoning", "")),
            strategy_suggestions=_string_items(decision.get("strategy_suggestions"), limit=6),
            param_adjustments=_dict_payload(decision.get("param_adjustments")),
            agent_weight_adjustments=_dict_payload(decision.get("agent_weight_adjustments")),
            metadata={
                "cycle_id": eval_report.cycle_id,
                "benchmark_passed": eval_report.benchmark_passed,
                "review_basis_window": dict(review_basis_window or {}),
                "review_window_size": len(compiled_results),
                "similarity_summary": dict(similarity_summary or {}),
                "causal_diagnosis": dict(causal_diagnosis or {}),
                "evidence_gate": dict(decision.get("evidence_gate") or {}),
                "reasoning_alignment": dict(decision.get("reasoning_alignment") or {}),
            },
        )
        result = dict(decision)
        result["review_basis_window"] = dict(review_basis_window or {})
        result["similar_results"] = [dict(item) for item in list(similar_results or [])]
        result["similarity_summary"] = dict(similarity_summary or {})
        result["causal_diagnosis"] = dict(causal_diagnosis or {})
        result["evidence_gate"] = dict(decision.get("evidence_gate") or {})
        result["reasoning_alignment"] = dict(decision.get("reasoning_alignment") or {})
        result["strategy_advice"] = advice.to_dict()
        return result

    def _trigger_reflections(self, facts: dict, decision: dict):
        """触发所有 Agent 的结构化反思，并将结果写入各 Agent 的记忆库 (Phase 4)"""
        if facts.get("empty"):
            return

        is_overall_profit = facts.get("win_rate", 0) >= 0.5
        win_rate = facts.get("win_rate", 0)
        avg_return = facts.get("avg_return", 0)
        problems = decision.get("strategy_suggestions", [])

        # 构建结构化上下文，供四步骤反思法使用
        situation = (
            f"市场概述：近{facts.get('total_cycles', 0)}周期 "
            f"胜率{win_rate:.0%} 均收益{avg_return:+.2f}%"
        )
        action = f"采纳建议: {problems[:2] if problems else ['维持现状']}"
        result_desc = f"胜率{win_rate:.0%}，均收益{avg_return:+.2f}%"

        outcome = {
            "correct": is_overall_profit,
            "delta": 0.05,
            "win_rate": win_rate,
            "situation": situation,
            "action": action,
            "result": result_desc,
        }

        self.strategist.reflect(outcome)
        self.evo_judge.reflect(outcome)
        self.review_decision_agent.reflect(outcome)
        
    @staticmethod
    def _extract_research_feedback(record: dict) -> dict:
        if not isinstance(record, dict):
            return {}
        raw_metadata = record.get("metadata")
        metadata = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
        feedback = metadata.get("research_feedback")
        if isinstance(feedback, dict) and feedback:
            return dict(feedback)
        direct_feedback = record.get("research_feedback")
        if isinstance(direct_feedback, dict) and direct_feedback:
            return dict(direct_feedback)
        return {}

    @staticmethod
    def _format_ratio(value: Any) -> str:
        try:
            if value is None or value == "":
                return "n/a"
            return f"{float(value):.0%}"
        except (TypeError, ValueError):
            return "n/a"

    def _research_feedback_lines(self, feedback: dict) -> list[str]:
        payload = dict(feedback or {})
        if not payload:
            return []
        recommendation = dict(payload.get("recommendation") or {})
        t20 = dict(payload.get("horizons") or {}).get("T+20") or {}
        reason_codes = [str(item).strip() for item in recommendation.get("reason_codes", []) if str(item).strip()]
        lines = [
            "## 问股校准反馈",
            f"- 建议偏置: {recommendation.get('bias') or 'unknown'}",
            f"- 样本数: {int(payload.get('sample_count') or 0)}",
        ]
        if recommendation.get("summary"):
            lines.append(f"- 摘要: {recommendation.get('summary')}")
        if reason_codes:
            lines.append(f"- 原因码: {', '.join(reason_codes)}")
        if t20:
            lines.append(f"- T+20 命中率: {self._format_ratio(t20.get('hit_rate'))}")
            lines.append(f"- T+20 失效率: {self._format_ratio(t20.get('invalidation_rate'))}")
            lines.append(f"- T+20 区间命中率: {self._format_ratio(t20.get('interval_hit_rate'))}")
        brier = payload.get("brier_like_direction_score")
        if brier is not None:
            try:
                lines.append(f"- 方向 Brier-like: {float(brier):.3f}")
            except (TypeError, ValueError):
                pass
        return lines

    def _research_feedback_summary(self, feedback: dict) -> str:
        payload = dict(feedback or {})
        if not payload:
            return ""
        recommendation = dict(payload.get("recommendation") or {})
        t20 = dict(payload.get("horizons") or {}).get("T+20") or {}
        parts = [
            f"bias={recommendation.get('bias') or 'unknown'}",
            f"sample_count={int(payload.get('sample_count') or 0)}",
        ]
        if t20.get("hit_rate") is not None:
            parts.append(f"T+20_hit_rate={self._format_ratio(t20.get('hit_rate'))}")
        if payload.get("brier_like_direction_score") is not None:
            try:
                parts.append(f"brier={_coerce_float(payload.get('brier_like_direction_score')):.3f}")
            except (TypeError, ValueError):
                pass
        return "；".join(parts)

    @staticmethod
    def _evaluate_evidence_gate(
        current_result: dict,
        *,
        similar_results: List[dict] | None = None,
        causal_diagnosis: dict[str, Any] | None = None,
        research_feedback: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        similar_cases = [dict(item) for item in list(similar_results or [])]
        diagnosis = dict(causal_diagnosis or {})
        feedback = dict(research_feedback or {})
        current_is_loss = not bool(current_result.get("is_profit", False))
        checks: list[dict[str, Any]] = []

        strict_matches = [
            item
            for item in similar_cases
            if (
                bool(item.get("strict_failure_match", False))
                or "strict_failure_match" not in item
            )
        ]
        if current_is_loss:
            checks.append(
                {
                    "name": "similar_failure_support",
                    "passed": bool(strict_matches),
                    "actual": len(strict_matches),
                    "threshold": 1,
                }
            )

        drivers = [dict(item) for item in list(diagnosis.get("drivers") or [])]
        supported_drivers = [
            item for item in drivers if list(item.get("evidence_cycle_ids") or [])
        ]
        if current_is_loss:
            checks.append(
                {
                    "name": "causal_driver_evidence",
                    "passed": bool(supported_drivers),
                    "actual": len(supported_drivers),
                    "threshold": 1,
                }
            )

        feedback_sample_count = int(feedback.get("sample_count") or 0)
        if feedback:
            checks.append(
                {
                    "name": "research_feedback_sample_count",
                    "passed": feedback_sample_count >= 3,
                    "actual": feedback_sample_count,
                    "threshold": 3,
                }
            )

        support_cycle_ids: list[int] = []
        for item in strict_matches:
            cycle_id = item.get("cycle_id")
            if cycle_id is not None and int(cycle_id) not in support_cycle_ids:
                support_cycle_ids.append(int(cycle_id))
        primary_driver = str(diagnosis.get("primary_driver") or "").strip()
        for driver in supported_drivers:
            if primary_driver and str(driver.get("code") or "").strip() != primary_driver:
                continue
            for cycle_id in list(driver.get("evidence_cycle_ids") or []):
                if int(cycle_id) not in support_cycle_ids:
                    support_cycle_ids.append(int(cycle_id))

        failed_checks = [item for item in checks if not bool(item.get("passed", False))]
        summary = (
            "结构化证据支持充分，可以执行本轮复盘调参。"
            if not failed_checks
            else "相似失败样本和结构化证据不足，暂不支持本轮直接调参。"
        )
        if not checks:
            summary = "当前以观察性复盘为主，本轮不强制追加证据门。"
        return {
            "passed": not failed_checks,
            "checks": checks,
            "failed_checks": failed_checks,
            "support_cycle_ids": support_cycle_ids,
            "summary": summary,
        }

    @staticmethod
    def _assess_reasoning_alignment(reasoning: str, facts: dict) -> dict[str, Any]:
        text = str(reasoning or "").strip()
        diagnosis = dict(facts.get("causal_diagnosis") or {})
        feedback = dict(facts.get("research_feedback") or {})
        recommendation = dict(feedback.get("recommendation") or {})
        anchors: list[str] = []
        primary_driver = str(diagnosis.get("primary_driver") or "").strip()
        if primary_driver:
            anchors.append(primary_driver)
        feedback_bias = str(recommendation.get("bias") or "").strip()
        if feedback_bias:
            anchors.append(feedback_bias)
        if not anchors:
            return {"passed": True, "anchors": [], "matched_anchors": []}
        matched = [anchor for anchor in anchors if anchor in text]
        return {
            "passed": bool(matched),
            "anchors": anchors,
            "matched_anchors": matched,
        }

    def _compile_facts(
        self,
        recent_results: List[dict],
        agent_accuracy: dict,
        *,
        similar_results: List[dict] | None = None,
        similarity_summary: dict[str, Any] | None = None,
        causal_diagnosis: dict[str, Any] | None = None,
    ) -> dict:
        """编译事实数据"""
        if not recent_results:
            return {"empty": True}

        total = len(recent_results)
        wins = sum(1 for r in recent_results if r.get("is_profit"))
        returns = [r.get("return_pct", 0) for r in recent_results]
        avg_return = sum(returns) / total
        research_feedback: Dict[str, Any] = {}

        def _is_meeting_result(record: dict) -> bool:
            selection_mode = str(record.get("selection_mode", "") or "")
            plan_source = str(record.get("plan_source", "") or "")
            advice = record.get("strategy_advice") or {}
            advice_source = str(advice.get("source", "") or "") if isinstance(advice, dict) else ""
            return (
                selection_mode.startswith("meeting")
                or plan_source in {"meeting", "llm", "model_meeting"}
                or advice_source in {"meeting", "llm", "model_meeting", "review_meeting"}
            )

        meeting_results = [r for r in recent_results if _is_meeting_result(r)]
        algo_results = [r for r in recent_results if not _is_meeting_result(r)]

        regime_stats: Dict[str, Dict] = {}
        for r in recent_results:
            rg = r.get("regime", "unknown")
            regime_stats.setdefault(rg, {"total": 0, "wins": 0, "returns": []})
            regime_stats[rg]["total"] += 1
            if r.get("is_profit"):
                regime_stats[rg]["wins"] += 1
            regime_stats[rg]["returns"].append(r.get("return_pct", 0))
            feedback = self._extract_research_feedback(r)
            if feedback:
                research_feedback = feedback
        current_result = dict(recent_results[-1] or {})
        evidence_gate = self._evaluate_evidence_gate(
            current_result,
            similar_results=similar_results,
            causal_diagnosis=causal_diagnosis,
            research_feedback=research_feedback,
        )

        return {
            "empty": False,
            "total_cycles": total,
            "wins": wins,
            "losses": total - wins,
            "win_rate": wins / total,
            "avg_return": round(avg_return, 2),
            "returns": returns,
            "meeting_stats": {
                "count": len(meeting_results),
                "wins": sum(1 for r in meeting_results if r.get("is_profit")),
                "win_rate": sum(1 for r in meeting_results if r.get("is_profit")) / len(meeting_results)
                    if meeting_results else 0,
            },
            "algo_stats": {
                "count": len(algo_results),
                "wins": sum(1 for r in algo_results if r.get("is_profit")),
                "win_rate": sum(1 for r in algo_results if r.get("is_profit")) / len(algo_results)
                    if algo_results else 0,
            },
            "regime_stats": {
                rg: {
                    "total": s["total"],
                    "wins": s["wins"],
                    "win_rate": round(s["wins"] / s["total"], 2) if s["total"] > 0 else 0,
                    "avg_return": round(sum(s["returns"]) / len(s["returns"]), 2),
                }
                for rg, s in regime_stats.items()
            },
            "agent_accuracy": agent_accuracy,
            "research_feedback": research_feedback,
            "similar_cases": [dict(item) for item in list(similar_results or [])],
            "similarity_summary": dict(similarity_summary or {}),
            "causal_diagnosis": dict(causal_diagnosis or {}),
            "evidence_gate": evidence_gate,
        }

    def _log_facts(self, facts: dict):
        if facts.get("empty"):
            logger.info("  无数据")
            return
        logger.info(
            f"  📊 近期表现: {facts['total_cycles']}轮, "
            f"胜率{facts['win_rate']:.0%}, 均收益{facts['avg_return']:+.2f}%"
        )
        research_feedback = dict(facts.get("research_feedback") or {})
        if research_feedback:
            logger.info("    ask校准: %s", self._research_feedback_summary(research_feedback))
        similarity_summary = dict(facts.get("similarity_summary") or {})
        if similarity_summary.get("matched_cycle_ids"):
            logger.info(
                "    相似样本: %s | dominant_regime=%s",
                similarity_summary.get("matched_cycle_ids"),
                similarity_summary.get("dominant_regime", "unknown"),
            )
        causal_diagnosis = dict(facts.get("causal_diagnosis") or {})
        if causal_diagnosis.get("primary_driver"):
            logger.info(
                "    因果诊断: %s | %s",
                causal_diagnosis.get("primary_driver"),
                causal_diagnosis.get("summary", ""),
            )
        for rg, rs in facts.get("regime_stats", {}).items():
            logger.info(f"    {rg}: {rs['total']}轮, 胜率{rs['win_rate']:.0%}")
        for agent, stats in facts.get("agent_accuracy", {}).items():
            logger.info(
                f"    {agent}: 推荐{stats['total_picks']}次, "
                f"盈利{stats['profitable_count']}/{stats['traded_count']} "
                f"({stats['accuracy']:.0%})"
            )

    def _strategist_analysis(self, facts: dict, current_params: dict) -> dict:
        if not self.llm or facts.get("empty"):
            return self._strategist_fallback(facts)

        system = (
            "你是投资策略分析师。请分析近期交易表现，找出策略层面的问题。\n"
            '以JSON输出：{"problems": ["问题1"], "suggestions": ["建议1"], "confidence": 0.0-1.0}'
        )
        user = self._format_facts_for_llm(facts, current_params)

        try:
            result = self.llm.call_json(system, user)
            if not result.get("_parse_error"):
                logger.info(f"  📋 Strategist(LLM): {len(result.get('problems', []))}个问题")
                return result
        except Exception as e:
            logger.exception(f"Strategist LLM调用失败: {e}")

        return self._strategist_fallback(facts)

    def _evo_judge_assessment(self, facts: dict, strategy_analysis: dict) -> dict:
        if not self.llm or facts.get("empty"):
            return self._evo_judge_fallback(facts)

        system = (
            "你是进化裁判。基于策略分析结果，给出具体的参数调整建议。\n"
            '以JSON输出：{"param_adjustments": {"stop_loss_pct": 值或null, '
            '"take_profit_pct": 值或null, "position_size": 值或null}, '
            '"evolution_direction": "aggressive/conservative/maintain", "confidence": 0.0-1.0}'
        )
        user = self._build_evo_user_message(facts, strategy_analysis)

        try:
            result = self.llm.call_json(system, user)
            if not result.get("_parse_error"):
                logger.info(f"  📋 EvoJudge(LLM): 方向={result.get('evolution_direction', '?')}")
                return result
        except Exception as e:
            logger.exception(f"EvoJudge LLM调用失败: {e}")

        return self._evo_judge_fallback(facts)

    def _review_decision(
        self,
        facts: dict,
        strategy_analysis: dict,
        evo_assessment: dict,
        current_params: dict,
    ) -> dict:
        if not self.llm or facts.get("empty"):
            return self._validate_decision(
                self._review_decision_fallback(facts, evo_assessment),
                facts,
            )

        if hasattr(self.review_decision_agent, "set_policy"):
            self.review_decision_agent.set_policy(self.review_policy)

        result = self.review_decision_agent.decide(
            facts=facts,
            strategy_analysis=strategy_analysis,
            evo_assessment=evo_assessment,
            current_params=current_params,
        )
        return self._validate_decision(result, facts)


    def set_policy(self, policy: Optional[dict[str, Any]] = None) -> None:
        self.review_policy = dict(policy or {})
        if hasattr(self.review_decision_agent, "set_policy"):
            self.review_decision_agent.set_policy(self.review_policy)

    def _policy_value(self, path: str, default: Any) -> Any:
        current: Any = self.review_policy
        for key in path.split('.'):
            if not isinstance(current, dict) or key not in current:
                return default
            current = current[key]
        return current

    def _sanitize_adjustment_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized: dict[str, float] = {}
        for key, value in (payload or {}).items():
            if value is None:
                continue
            try:
                normalized[key] = _normalize_param_value(key, float(value))
            except (TypeError, ValueError):
                continue
        risk_like = {key: value for key, value in normalized.items() if key in {"stop_loss_pct", "take_profit_pct", "position_size"}}
        clean_params = sanitize_risk_params(risk_like)
        cash_bounds = dict(self._policy_value('param_clamps.cash_reserve', {'min': 0.0, 'max': 0.80}) or {})
        trailing_bounds = dict(self._policy_value('param_clamps.trailing_pct', {'min': 0.03, 'max': 0.20}) or {})
        if 'cash_reserve' in normalized:
            clean_params['cash_reserve'] = max(float(cash_bounds.get('min', 0.0)), min(float(cash_bounds.get('max', 0.80)), normalized['cash_reserve']))
        if 'trailing_pct' in normalized:
            clean_params['trailing_pct'] = max(float(trailing_bounds.get('min', 0.03)), min(float(trailing_bounds.get('max', 0.20)), normalized['trailing_pct']))
        return clean_params

    def _normalize_confidence(self, raw: Any) -> float:
        default_conf = float(self._policy_value('confidence.default', 0.5) or 0.5)
        try:
            return max(0.0, min(1.0, float(raw if raw is not None else default_conf)))
        except (TypeError, ValueError):
            return default_conf

    # ===== 算法兜底 =====

    def _strategist_fallback(self, facts: dict) -> dict:
        problems, suggestions = [], []
        if facts.get("win_rate", 0) < float(self._policy_value('fallback.strategy.win_rate_low', 0.4) or 0.4):
            problems.append("胜率过低")
            suggestions.append("收紧选股标准")
        if facts.get("avg_return", 0) < float(self._policy_value('fallback.strategy.avg_return_low', -3.0) or -3.0):
            problems.append("平均亏损过大")
            suggestions.append("降低仓位")
        recommendation = dict(dict(facts.get("research_feedback") or {}).get("recommendation") or {})
        bias = str(recommendation.get("bias") or "")
        causal = dict(facts.get("causal_diagnosis") or {})
        primary_driver = str(causal.get("primary_driver") or "")
        if bias == "tighten_risk":
            problems.append("问股校准显示近期假设命中偏弱")
            suggestions.append("先收紧入场条件与风险暴露")
        elif bias == "recalibrate_probability":
            problems.append("问股校准显示概率表达偏乐观")
            suggestions.append("下调置信度并收紧触发阈值")
        if primary_driver == "regime_repeat_loss":
            problems.append("同一市场状态下重复亏损")
            suggestions.append("按市场状态拆分止损和仓位阈值")
        elif primary_driver == "benchmark_gap":
            problems.append("相似样本持续跑输基准")
            suggestions.append("缩小暴露并优先修复基准落后来源")
        return {"problems": problems, "suggestions": suggestions, "confidence": float(self._policy_value('fallback.strategy.confidence', 0.4) or 0.4)}

    def _evo_judge_fallback(self, facts: dict) -> dict:
        adjustments, direction = {}, "maintain"
        suggestions = []
        research_feedback = dict(facts.get("research_feedback") or {})
        recommendation = dict(research_feedback.get("recommendation") or {})
        bias = str(recommendation.get("bias") or "")
        causal = dict(facts.get("causal_diagnosis") or {})
        primary_driver = str(causal.get("primary_driver") or "")
        if bias in {"tighten_risk", "recalibrate_probability"}:
            adjustments = self._sanitize_adjustment_payload(
                dict(self._policy_value('fallback.evo.conservative_adjustments', {"stop_loss_pct": 0.03, "position_size": 0.15}) or {})
            )
            direction = "conservative"
            if bias == "tighten_risk":
                suggestions = ["问股校准显示近期命中偏弱，先收紧止损与仓位"]
            else:
                suggestions = ["问股校准显示概率偏乐观，先收紧触发条件与风险暴露"]
            if recommendation.get("summary"):
                suggestions.append(str(recommendation.get("summary")))
            return {
                "param_adjustments": adjustments,
                "evolution_direction": direction,
                "suggestions": suggestions,
                "confidence": float(self._policy_value('fallback.evo.confidence', 0.4) or 0.4),
                "reasoning": f"ask侧校准反馈要求先偏保守：{bias}",
            }

        if primary_driver in {"regime_repeat_loss", "benchmark_gap"}:
            adjustments = self._sanitize_adjustment_payload(
                dict(
                    self._policy_value(
                        'fallback.evo.conservative_adjustments',
                        {"stop_loss_pct": 0.03, "position_size": 0.15},
                    )
                    or {}
                )
            )
            direction = "conservative"
            suggestions = ["相似亏损样本已聚类，先收紧风险暴露并验证修复是否生效"]
            if causal.get("summary"):
                suggestions.append(str(causal.get("summary")))
            return {
                "param_adjustments": adjustments,
                "evolution_direction": direction,
                "suggestions": suggestions,
                "confidence": float(self._policy_value('fallback.evo.confidence', 0.4) or 0.4),
                "reasoning": f"因果诊断显示首要驱动为 {primary_driver}，优先转向保守修复。",
            }

        win_rate = facts.get("win_rate", 0.5)
        if win_rate < float(self._policy_value('fallback.evo.win_rate_conservative', 0.35) or 0.35):
            adjustments = self._sanitize_adjustment_payload(dict(self._policy_value('fallback.evo.conservative_adjustments', {"stop_loss_pct": 0.03, "position_size": 0.15}) or {}))
            direction = "conservative"
            suggestions = ["先收紧止损并降低仓位暴露"]
        elif win_rate > float(self._policy_value('fallback.evo.win_rate_aggressive', 0.65) or 0.65):
            adjustments = self._sanitize_adjustment_payload(dict(self._policy_value('fallback.evo.aggressive_adjustments', {"position_size": 0.25}) or {}))
            direction = "aggressive"
            suggestions = ["在保持纪律前提下可小幅提高仓位"]
        return {
            "param_adjustments": adjustments,
            "evolution_direction": direction,
            "suggestions": suggestions,
            "confidence": float(self._policy_value('fallback.evo.confidence', 0.4) or 0.4),
            "reasoning": f"算法判断当前方向为 {direction}",
        }

    def _review_decision_fallback(self, facts: dict, evo_assessment: dict) -> dict:
        weight_adjustments = {}
        min_trades = int(self._policy_value('agent_weight.min_traded_count', 3) or 3)
        formula_base = float(self._policy_value('agent_weight.formula_base', 0.5) or 0.5)
        default_weight = float(self._policy_value('agent_weight.default', 1.0) or 1.0)
        min_weight = float(self._policy_value('agent_weight.min', 0.3) or 0.3)
        max_weight = float(self._policy_value('agent_weight.max', 2.0) or 2.0)
        for agent, stats in facts.get("agent_accuracy", {}).items():
            acc = stats.get("accuracy", 0.5)
            if stats.get("traded_count", 0) >= min_trades:
                weight_adjustments[agent] = round(max(min_weight, min(max_weight, formula_base + acc)), 2)
            else:
                weight_adjustments[agent] = default_weight

        param_adj = {k: v for k, v in evo_assessment.get("param_adjustments", {}).items() if v is not None}
        return {
            "strategy_suggestions": evo_assessment.get("suggestions", []),
            "param_adjustments": param_adj,
            "agent_weight_adjustments": weight_adjustments,
            "reasoning": f"算法决策: 方向={evo_assessment.get('evolution_direction', 'maintain')}",
        }

    def _format_facts_for_llm(self, facts: dict, params: dict) -> str:
        lines = [
            f"## 近期交易表现（最近{facts['total_cycles']}轮）",
            f"- 胜率: {facts['win_rate']:.0%} ({facts['wins']}胜{facts['losses']}负)",
            f"- 平均收益: {facts['avg_return']:+.2f}%",
            f"- 会议方案胜率: {facts.get('meeting_stats', {}).get('win_rate', 0):.0%}",
            f"- 算法方案胜率: {facts.get('algo_stats', {}).get('win_rate', 0):.0%}",
        ]
        for rg, rs in facts.get("regime_stats", {}).items():
            lines.append(f"- {rg}: {rs['total']}轮, 胜率{rs['win_rate']:.0%}, 平均收益{rs['avg_return']:+.2f}%")
        research_feedback_lines = self._research_feedback_lines(dict(facts.get("research_feedback") or {}))
        if research_feedback_lines:
            lines.append("")
            lines.extend(research_feedback_lines)
        similarity_summary = dict(facts.get("similarity_summary") or {})
        similar_cases = [dict(item) for item in list(facts.get("similar_cases") or [])]
        if similar_cases:
            lines.append("")
            lines.append("## 相似样本检索")
            lines.append(
                f"- 命中周期: {similarity_summary.get('matched_cycle_ids', [])} | "
                f"dominant_regime={similarity_summary.get('dominant_regime', 'unknown')}"
            )
            for item in similar_cases[:3]:
                lines.append(
                    f"- cycle {item.get('cycle_id')}: regime={item.get('regime', 'unknown')}, "
                    f"return={_coerce_float(item.get('return_pct')):+.2f}%, "
                    f"features={','.join(item.get('matched_features', []))}"
                )
        causal_diagnosis = dict(facts.get("causal_diagnosis") or {})
        if causal_diagnosis:
            lines.append("")
            lines.append("## 因果诊断")
            lines.append(f"- primary_driver: {causal_diagnosis.get('primary_driver', 'unknown')}")
            if causal_diagnosis.get("summary"):
                lines.append(f"- summary: {causal_diagnosis.get('summary')}")
        if facts.get("agent_accuracy"):
            lines.append("\n## Agent 准确率")
            for name, stats in facts.get("agent_accuracy", {}).items():
                lines.append(
                    f"- {name}: 准确率{stats.get('accuracy', 0):.0%}, 交易{stats.get('traded_count', 0)}次, 平均评分{stats.get('avg_score', 0):.2f}"
                )
        lines.append(f"\n## 当前参数\n{params}")
        lines.append("\n请基于这些事实识别策略问题并提出改进建议。")
        return "\n".join(lines)

    def _build_evo_user_message(self, facts: dict, strategy_analysis: dict) -> str:
        lines = [
            f"## 近期表现\n- 胜率: {facts['win_rate']:.0%}\n- 平均收益: {facts['avg_return']:+.2f}%",
            f"## 策略问题\n{strategy_analysis.get('problems', [])}",
            f"## 策略建议\n{strategy_analysis.get('suggestions', [])}",
        ]
        research_feedback_lines = self._research_feedback_lines(dict(facts.get("research_feedback") or {}))
        if research_feedback_lines:
            lines.append("\n".join(research_feedback_lines))
        causal_diagnosis = dict(facts.get("causal_diagnosis") or {})
        if causal_diagnosis:
            lines.append(
                "## 因果诊断\n"
                f"- primary_driver: {causal_diagnosis.get('primary_driver', 'unknown')}\n"
                f"- summary: {causal_diagnosis.get('summary', '')}"
            )
        lines.append("请输出参数调整方向，保持风险口径清晰。")
        return "\n\n".join(lines)

    def _build_commander_user_message(
        self,
        facts: dict,
        strategy_analysis: dict,
        evo_assessment: dict,
        current_params: dict,
    ) -> str:
        aa = facts.get("agent_accuracy", {})
        agent_lines = [
            f"- {name}: 准确率{stats['accuracy']:.0%}, 盈利{stats['profitable_count']}/{stats['traded_count']}, 平均评分{stats.get('avg_score', 0):.2f}"
            for name, stats in aa.items()
        ]
        research_feedback_section = "\n".join(self._research_feedback_lines(dict(facts.get("research_feedback") or {})))
        if research_feedback_section:
            research_feedback_section += "\n\n"
        return (
            f"## 近期表现\n胜率{facts['win_rate']:.0%}，平均收益{facts['avg_return']:+.2f}%\n\n"
            f"## Agent准确率\n" + "\n".join(agent_lines) + "\n\n"
            f"{research_feedback_section}"
            f"## 策略分析师意见\n问题：{strategy_analysis.get('problems', [])}\n建议：{strategy_analysis.get('suggestions', [])}\n\n"
            f"## 进化裁判意见\n方向：{evo_assessment.get('evolution_direction', 'maintain')}\n"
            f"参数调整：{evo_assessment.get('param_adjustments', {})}\n"
            f"补充建议：{evo_assessment.get('suggestions', [])}\n\n"
            f"## 当前参数\n{current_params}\n\n"
            "请综合以上信息，给出下一轮可执行决策。"
        )

    def _validate_strategy_analysis(self, result: dict) -> dict:
        result["problems"] = _string_items(result.get("problems"), limit=4)
        result["suggestions"] = _string_items(result.get("suggestions"), limit=4)
        result["confidence"] = self._normalize_confidence(result.get("confidence"))
        return result

    def _validate_evo_assessment(self, result: dict) -> dict:
        if not isinstance(result.get("param_adjustments"), dict):
            result["param_adjustments"] = {}
        result["param_adjustments"] = self._sanitize_adjustment_payload(result.get("param_adjustments", {}))
        if result.get("evolution_direction") not in {"aggressive", "conservative", "maintain"}:
            result["evolution_direction"] = "maintain"
        result["suggestions"] = _string_items(result.get("suggestions"), limit=4)
        result["confidence"] = self._normalize_confidence(result.get("confidence"))
        if not isinstance(result.get("reasoning"), str):
            result["reasoning"] = ""
        return result

    def _validate_decision(self, result: dict, facts: dict) -> dict:
        result["strategy_suggestions"] = _string_items(result.get("strategy_suggestions"), limit=6)
        if not isinstance(result.get("param_adjustments"), dict):
            result["param_adjustments"] = {}

        result["param_adjustments"] = self._sanitize_adjustment_payload(result.get("param_adjustments", {}))

        valid_agents = set(facts.get("agent_accuracy", {}).keys())
        clean_weights = {}
        min_weight = float(self._policy_value('agent_weight.min', 0.3) or 0.3)
        max_weight = float(self._policy_value('agent_weight.max', 2.0) or 2.0)
        default_weight = float(self._policy_value('agent_weight.default', 1.0) or 1.0)
        for agent, w in _normalize_agent_weight_adjustments(result.get("agent_weight_adjustments")).items():
            if valid_agents and agent not in valid_agents:
                continue
            try:
                clean_weights[agent] = round(max(min_weight, min(max_weight, float(w))), 2)
            except (TypeError, ValueError):
                clean_weights[agent] = default_weight
        result["agent_weight_adjustments"] = clean_weights

        if not isinstance(result.get("reasoning"), str):
            result["reasoning"] = ""
        evidence_gate = dict(facts.get("evidence_gate") or {})
        if evidence_gate:
            result["evidence_gate"] = evidence_gate
            if not bool(evidence_gate.get("passed", True)):
                result["param_adjustments"] = {}
                result["agent_weight_adjustments"] = {}
                gate_summary = str(evidence_gate.get("summary") or "证据不足")
                result["strategy_suggestions"] = _string_items(
                    [gate_summary, *list(result.get("strategy_suggestions") or [])],
                    limit=6,
                )
                result["reasoning"] = f"{result.get('reasoning', '').strip()} {gate_summary}".strip()
        reasoning_alignment = self._assess_reasoning_alignment(result.get("reasoning", ""), facts)
        result["reasoning_alignment"] = reasoning_alignment
        if not bool(reasoning_alignment.get("passed", True)) and list(reasoning_alignment.get("anchors") or []):
            anchors = ",".join(str(item) for item in list(reasoning_alignment.get("anchors") or []))
            result["reasoning"] = f"{result.get('reasoning', '').strip()} 需明确引用证据锚点：{anchors}。".strip()
        applied_summary = self._build_applied_summary(result)
        if applied_summary:
            result["applied_summary"] = applied_summary
        else:
            result.pop("applied_summary", None)
        return result

    def _build_applied_summary(self, result: dict) -> str:
        parts = []
        param_adjustments = result.get("param_adjustments") if isinstance(result.get("param_adjustments"), dict) else {}
        if param_adjustments:
            param_parts = []
            for key, value in param_adjustments.items():
                if value is None:
                    continue
                try:
                    numeric_value = float(value)
                except (TypeError, ValueError):
                    continue
                if key in {"stop_loss_pct", "take_profit_pct", "position_size", "cash_reserve", "trailing_pct"}:
                    param_parts.append(f"{key}={numeric_value:.0%}")
                else:
                    param_parts.append(f"{key}={numeric_value:g}")
            if param_parts:
                parts.append("最终执行参数：" + "，".join(param_parts))

        weight_adjustments = result.get("agent_weight_adjustments") if isinstance(result.get("agent_weight_adjustments"), dict) else {}
        if weight_adjustments:
            weight_parts = []
            for agent, weight in weight_adjustments.items():
                try:
                    weight_parts.append(f"{agent}={float(weight):.2f}")
                except (TypeError, ValueError):
                    continue
            if weight_parts:
                parts.append("最终执行权重：" + "，".join(weight_parts))

        return "；".join(parts)


# ============================================================
# Part 3: 会议记录持久化（合并 MeetingLogger + MeetingRecorder）

__all__ = ["ReviewMeeting"]
