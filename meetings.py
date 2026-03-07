"""
投资进化系统 - 会议编排

包含：
1. SelectionMeeting   — 选股会议（Agent 协商 → TradingPlan）
2. ReviewMeeting      — 复盘会议（Strategist → EvoJudge → Commander 三阶段 LLM）
3. MeetingRecorder    — 会议记录持久化（JSON + Markdown，合并原 MeetingLogger + MeetingRecorder）
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from core import (
    LLMCaller,
    TradingPlan,
    PositionPlan,
    summarize_stocks,
    format_stock_table,
    AgentTracker,
)
from config import config

try:
    from debate import DebateOrchestrator, RiskDebateOrchestrator
    _HAS_DEBATE = True
except ImportError:
    _HAS_DEBATE = False

logger = logging.getLogger(__name__)


# ============================================================
# Part 1: 选股会议
# ============================================================

# 内嵌 Prompt（SelectionMeeting 自己管理 Prompt，不依赖 agents.py）
_SM_TREND_SYSTEM = "你是专业的A股趋势猎手，专注于寻找处于上升趋势的股票。"
_SM_CONTRARIAN_SYSTEM = "你是专业的A股逆向投资者，专注于寻找被过度抛售、有反弹潜力的股票。"

_SM_TREND_PROMPT = """你是一个专业的趋势交易猎手，专注于寻找A股中处于上升趋势的股票。

分析依据（按重要性排序）：
1. 均线状态：优先选择MA趋势为"多头"的股票
2. MACD信号：优先选择"金叉"或"看多"的股票
3. RSI水平：优先选择RSI在35-70区间的股票
4. 近期走势：优先选择5日和20日涨幅为正的股票

注意：
- 不要设置过于严格的硬性门槛，根据整体表现综合评判
- 必须从提供的候选列表中选择，不要编造股票代码

市场背景：{regime_info}

候选股票：
{stock_table}

严格以JSON格式输出：
{{
    "picks": [
        {{
            "code": "候选列表中的股票代码",
            "score": 0.0到1.0的评分,
            "reasoning": "一句话选择理由",
            "stop_loss_pct": 0.03到0.07之间的止损比例,
            "take_profit_pct": 0.10到0.25之间的止盈比例
        }}
    ],
    "overall_view": "一句话总结",
    "confidence": 0.0到1.0
}}"""

_SM_CONTRARIAN_PROMPT = """你是一个专业的逆向投资猎手，专注于寻找A股中超跌反弹机会。

分析依据（按重要性排序）：
1. RSI水平：优先选择RSI低于40的超卖股票
2. 布林带位置：优先选择BB位置低于0.3的股票
3. 近期跌幅：优先选择20日跌幅较大但近5日企稳的股票

注意：
- 超跌反弹的风险较大，止损应比趋势股更宽
- 必须从提供的候选列表中选择，不要编造股票代码

市场背景：{regime_info}

候选股票：
{stock_table}

严格以JSON格式输出：
{{
    "picks": [
        {{
            "code": "候选列表中的股票代码",
            "score": 0.0到1.0的评分,
            "reasoning": "一句话选择理由",
            "stop_loss_pct": 0.06到0.12之间的止损比例,
            "take_profit_pct": 0.12到0.30之间的止盈比例
        }}
    ],
    "overall_view": "一句话总结",
    "confidence": 0.0到1.0
}}"""


class SelectionMeeting:
    """
    选股会议编排器

    流程：
    1. 格式化候选股票摘要
    2. 并行调用 TrendHunter + Contrarian（LLM 或 fallback）
    3. 汇总 → 按 Agent 权重加权计分 → 生成 TradingPlan

    支持 Agent 权重动态调整（由 ReviewMeeting 驱动）
    """

    def __init__(
        self,
        llm_caller: Optional[LLMCaller] = None,
        agent_weights: Optional[Dict[str, float]] = None,
        trend_hunter: Optional[Any] = None,
        contrarian: Optional[Any] = None,
        max_hunters: Optional[int] = None,
        enable_debate: bool = True,
        max_debate_rounds: int = 1,
        deep_llm_caller: Optional[LLMCaller] = None,
    ):
        from agents import TrendHunterAgent, ContrarianAgent
        self.llm = llm_caller
        self.agent_weights = agent_weights or {"trend_hunter": 1.0, "contrarian": 1.0}
        self.trend_hunter = trend_hunter or TrendHunterAgent()
        self.contrarian = contrarian or ContrarianAgent()
        self.max_hunters = max_hunters
        self.meeting_count = 0

        self._debate: Optional[Any] = None
        if _HAS_DEBATE and enable_debate and llm_caller is not None:
            deep = deep_llm_caller or llm_caller
            self._debate = DebateOrchestrator(
                fast_llm=llm_caller,
                deep_llm=deep,
                max_rounds=max_debate_rounds,
            )
            logger.info("SelectionMeeting: debate enabled (max_rounds=%d)", max_debate_rounds)

    def run(
        self,
        regime: Dict[str, Any],
        stock_summaries: List[Dict],
        top_n: int = 5,
    ) -> Dict[str, Any]:
        """
        运行选股会议

        Args:
            regime: MarketRegimeAgent.analyze() 的输出
            stock_summaries: summarize_stocks() 输出
            top_n: 最终选几只

        Returns:
            {"selected": [...], "reasoning": str, "confidence": float,
             "source": str, "hunters": [...]}
        """
        self.meeting_count += 1

        if not stock_summaries:
            return self._fallback_empty()

        stock_table = format_stock_table(stock_summaries[:30])
        regime_info = (
            f"当前市场状态: {regime.get('regime', 'unknown')}, "
            f"置信度: {regime.get('confidence', 0):.0%}"
        )

        if self.llm:
            return self._run_llm(regime_info, stock_table, top_n, regime, stock_summaries)
        return self._run_algorithm(stock_summaries, top_n)

    def run_with_data(
        self,
        regime: Dict[str, Any],
        stock_data: Dict[str, Any],
        cutoff_date: str,
    ) -> Dict[str, Any]:
        """
        完整流程：自动计算摘要并运行选股会议

        Returns:
            {"trading_plan": TradingPlan, "meeting_log": Dict}
        """
        stock_codes = list(stock_data.keys())[:100]
        stock_summaries = summarize_stocks(stock_data, stock_codes, cutoff_date)

        if not stock_summaries:
            return {
                "trading_plan": self._to_trading_plan(self._fallback_empty(), regime, cutoff_date),
                "meeting_log": {},
            }

        top_n = regime.get("params", {}).get("top_n", 5)
        meeting_result = self.run(regime, stock_summaries, top_n)
        trading_plan = self._to_trading_plan(meeting_result, regime, cutoff_date)

        meeting_log = {
            "regime": regime.get("regime"),
            "confidence": regime.get("confidence"),
            "hunters": meeting_result.get("hunters", []),
            "selected": meeting_result.get("selected", []),
            "source": meeting_result.get("source", "algorithm"),
            "meeting_id": self.meeting_count,
            "cutoff_date": cutoff_date,
        }

        return {"trading_plan": trading_plan, "meeting_log": meeting_log}

    def update_weights(self, weight_adjustments: Dict[str, float]):
        """EMA 平滑更新 Agent 权重（由 ReviewMeeting 驱动）"""
        for agent, new_w in weight_adjustments.items():
            if agent in self.agent_weights:
                old = self.agent_weights[agent]
                self.agent_weights[agent] = round(old * 0.6 + new_w * 0.4, 3)
                logger.info(f"📊 Agent权重更新 {agent}: {old:.2f} → {self.agent_weights[agent]:.2f}")

    # ===== 内部方法 =====

    def _run_llm(
        self,
        regime_info: str,
        stock_table: str,
        top_n: int,
        regime: Dict,
        stock_summaries: List[Dict],
    ) -> Dict:
        hunter_outputs = []

        # 1. 趋势猎手 - 认知环路
        try:
            candidates = self.trend_hunter.perceive(stock_summaries)
            reasoning = self.trend_hunter.reason(candidates, context=regime)
            # Act
            result = self.trend_hunter.act(reasoning)
            
            if result and not result.get("_parse_error"):
                hunter_outputs.append({"name": "trend_hunter", "result": result})
        except Exception as e:
            logger.warning(f"TrendHunter 认知环路执行失败: {e}")

        # 2. 逆向猎手 - 认知环路
        try:
            candidates = self.contrarian.perceive(stock_summaries)
            reasoning = self.contrarian.reason(candidates, context=regime)
            # Act
            result = self.contrarian.act(reasoning)
            
            if result and not result.get("_parse_error"):
                hunter_outputs.append({"name": "contrarian", "result": result})
        except Exception as e:
            logger.warning(f"Contrarian 认知环路执行失败: {e}")

        # 3. 应用 Agent 权重
        for hunter in hunter_outputs:
            r = hunter["result"]
            weight = self.agent_weights.get(hunter["name"], 1.0)
            for p in r.get("picks", []):
                p["score"] = p.get("score", 0.5) * weight

        if not hunter_outputs:
            return self._run_algorithm(stock_summaries, top_n)

        # Phase 3: 多空辩论筛选
        if self._debate is not None and stock_summaries:
            code_to_summary = {s["code"]: s for s in stock_summaries}
            candidate_codes: set = set()
            for ho in hunter_outputs:
                for p in ho["result"].get("picks", []):
                    candidate_codes.add(p.get("code", ""))

            debate_results: Dict[str, dict] = {}
            for code in candidate_codes:
                s_info = code_to_summary.get(code)
                if s_info is None:
                    continue
                d_result = self._debate.debate(s_info, regime)
                debate_results[code] = d_result

            for ho in hunter_outputs:
                for p in ho["result"].get("picks", []):
                    code = p.get("code", "")
                    if code not in debate_results:
                        continue
                    verdict = debate_results[code].get("verdict", "hold")
                    d_conf = debate_results[code].get("confidence", 0.5)
                    if verdict == "avoid":
                        p["score"] = p.get("score", 0.5) * 0.5
                    elif verdict == "buy":
                        p["score"] = p.get("score", 0.5) * (1.0 + d_conf * 0.3)

            logger.info("Debate complete: %d candidates screened", len(debate_results))

        return self._aggregate(hunter_outputs, regime)

    def _run_algorithm(self, stock_summaries: List[Dict], top_n: int) -> Dict:
        sorted_stocks = sorted(stock_summaries, key=lambda x: x.get("algo_score", 0), reverse=True)
        selected = [s["code"] for s in sorted_stocks[:top_n]]
        return {
            "selected": selected,
            "reasoning": f"算法排序：选取 algo_score 最高的{len(selected)}只",
            "confidence": 0.6,
            "source": "algorithm",
            "hunters": [],
        }

    def _aggregate(self, hunter_outputs: List[Dict], regime: Dict) -> Dict:
        if not hunter_outputs:
            return self._fallback_empty()

        stock_scores: Dict[str, float] = {}
        all_reasons = []

        for hunter in hunter_outputs:
            r = hunter["result"]
            confidence = r.get("confidence", 0.5)
            reason = r.get("overall_view", "")
            if reason:
                all_reasons.append(f"{hunter['name']}: {reason}")

            picks = r.get("picks", [])
            if not picks:
                picks = [{"code": c, "score": confidence} for c in r.get("selected", [])]

            for p in picks:
                code = p.get("code", "")
                if not code:
                    continue
                ws = p.get("score", confidence) * confidence
                stock_scores[code] = stock_scores.get(code, 0) + ws

        sorted_codes = sorted(stock_scores.items(), key=lambda x: x[1], reverse=True)
        max_pos = regime.get("params", {}).get("max_positions", 2)
        final_selected = [code for code, _ in sorted_codes[:max_pos]]

        regime_name = regime.get("regime", "unknown")
        hint = {"bull": "牛市环境，倾向趋势策略", "bear": "熊市，控制风险为主"}.get(regime_name, "震荡市，灵活配置")
        reasoning = f"{hint}。{' '.join(all_reasons)}"

        return {
            "selected": final_selected,
            "reasoning": reasoning,
            "confidence": (
                sum(s for _, s in sorted_codes[:len(final_selected)]) / max(len(final_selected), 1)
            ),
            "source": "llm",
            "hunters": hunter_outputs,
        }

    def _to_trading_plan(
        self, meeting_result: Dict[str, Any], regime: Dict[str, Any], date: str
    ) -> TradingPlan:
        selected = meeting_result.get("selected", [])
        max_positions = regime.get("params", {}).get("max_positions", 2)
        regime_params = regime.get("params", {})

        positions = [
            PositionPlan(
                code=code,
                priority=i + 1,
                weight=0.20,
                entry_method="market",
                stop_loss_pct=regime_params.get("stop_loss_pct", 0.05),
                take_profit_pct=regime_params.get("take_profit_pct", 0.15),
                trailing_pct=0.10,
                max_hold_days=30,
                reason=meeting_result.get("reasoning", "选股会议推荐"),
                source="meeting",
            )
            for i, code in enumerate(selected)
        ]

        return TradingPlan(
            date=date,
            positions=positions,
            cash_reserve=0.30,
            max_positions=max_positions,
            source=meeting_result.get("source", "algorithm"),
            reasoning=meeting_result.get("reasoning", ""),
        )

    def _fallback_empty(self) -> Dict:
        return {
            "selected": [], "reasoning": "无候选股票",
            "confidence": 0.0, "source": "algorithm", "hunters": [],
        }


# ============================================================
# Part 2: 复盘会议
# ============================================================

class ReviewMeeting:
    """
    复盘会议编排器

    流程：
    1. 事实对账（算法）：Agent 预测 vs 实际结果
    2. Strategist 分析（LLM）：策略层面问题
    3. EvoJudge 评估（LLM）：参数层面调整建议
    4. Commander 决策（LLM）：最终采纳

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
    ):
        from agents import StrategistAgent, EvoJudgeAgent, CommanderAgent
        self.llm = llm_caller
        self.tracker = agent_tracker
        self.strategist = strategist or StrategistAgent()
        self.evo_judge = evo_judge or EvoJudgeAgent()
        self.commander = commander or CommanderAgent()
        self.review_count = 0

        self._risk_debate: Optional[Any] = None
        if _HAS_DEBATE and enable_risk_debate and llm_caller is not None:
            deep = deep_llm_caller or llm_caller
            self._risk_debate = RiskDebateOrchestrator(
                fast_llm=llm_caller,
                deep_llm=deep,
                max_rounds=max_risk_discuss_rounds,
            )
            logger.info("ReviewMeeting: risk debate enabled (max_rounds=%d)", max_risk_discuss_rounds)

    def run(
        self,
        recent_results: List[dict],
        agent_accuracy: dict,
        current_params: dict,
        regime_history: List[str] = None,
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

        facts = self._compile_facts(recent_results, agent_accuracy)
        self._log_facts(facts)

        strategy_analysis = self._strategist_analysis(facts, current_params)
        evo_assessment = self._evo_judge_assessment(facts, strategy_analysis)
        decision = self._commander_decision(facts, strategy_analysis, evo_assessment, current_params)

        # 反思闭环：让所有 Agent 根据事实和决策进行自我更新
        self._trigger_reflections(facts, decision)

        logger.info(f"📋 复盘会议 #{self.review_count} 完成")
        logger.info(f"  策略建议: {len(decision.get('strategy_suggestions', []))}条")
        logger.info(f"  参数调整: {decision.get('param_adjustments', {})}")
        logger.info(f"  权重调整: {decision.get('agent_weight_adjustments', {})}")
        logger.info(f"{'='*50}\n")

        return decision

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
        self.commander.reflect(outcome)
        
    def _compile_facts(self, recent_results: List[dict], agent_accuracy: dict) -> dict:
        """编译事实数据"""
        if not recent_results:
            return {"empty": True}

        total = len(recent_results)
        wins = sum(1 for r in recent_results if r.get("is_profit"))
        returns = [r.get("return_pct", 0) for r in recent_results]
        avg_return = sum(returns) / total

        meeting_results = [r for r in recent_results if r.get("plan_source") == "meeting"]
        algo_results = [r for r in recent_results if r.get("plan_source") != "meeting"]

        regime_stats: Dict[str, Dict] = {}
        for r in recent_results:
            rg = r.get("regime", "unknown")
            regime_stats.setdefault(rg, {"total": 0, "wins": 0, "returns": []})
            regime_stats[rg]["total"] += 1
            if r.get("is_profit"):
                regime_stats[rg]["wins"] += 1
            regime_stats[rg]["returns"].append(r.get("return_pct", 0))

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
        }

    def _log_facts(self, facts: dict):
        if facts.get("empty"):
            logger.info("  无数据")
            return
        logger.info(
            f"  📊 近期表现: {facts['total_cycles']}轮, "
            f"胜率{facts['win_rate']:.0%}, 均收益{facts['avg_return']:+.2f}%"
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
        user = (
            f"近期表现：胜率{facts['win_rate']:.0%}，平均收益{facts['avg_return']:+.2f}%\n"
            f"策略问题：{strategy_analysis.get('problems', [])}\n"
            f"策略建议：{strategy_analysis.get('suggestions', [])}\n"
            f"请给出参数调整建议。"
        )

        try:
            result = self.llm.call_json(system, user)
            if not result.get("_parse_error"):
                logger.info(f"  📋 EvoJudge(LLM): 方向={result.get('evolution_direction', '?')}")
                return result
        except Exception as e:
            logger.exception(f"EvoJudge LLM调用失败: {e}")

        return self._evo_judge_fallback(facts)

    def _commander_decision(
        self,
        facts: dict,
        strategy_analysis: dict,
        evo_assessment: dict,
        current_params: dict,
    ) -> dict:
        if not self.llm or facts.get("empty"):
            return self._commander_fallback(facts, evo_assessment)

        system = (
            "你是投资团队指挥官。综合策略分析和进化评估，做最终决策。\n"
            '以JSON输出：{"strategy_suggestions": ["建议1"], '
            '"param_adjustments": {"key": value}, '
            '"agent_weight_adjustments": {"trend_hunter": 1.0, "contrarian": 0.8}, '
            '"reasoning": "决策理由"}'
        )

        aa = facts.get("agent_accuracy", {})
        agent_lines = [
            f"  {name}: 准确率{stats['accuracy']:.0%} (盈利{stats['profitable_count']}/{stats['traded_count']})"
            for name, stats in aa.items()
        ]

        user = (
            f"## 近期表现\n胜率{facts['win_rate']:.0%}，平均收益{facts['avg_return']:+.2f}%\n\n"
            f"## Agent准确率\n" + "\n".join(agent_lines) + "\n\n"
            f"## 策略分析师意见\n问题：{strategy_analysis.get('problems', [])}\n"
            f"建议：{strategy_analysis.get('suggestions', [])}\n\n"
            f"## 进化裁判意见\n方向：{evo_assessment.get('evolution_direction', '未知')}\n"
            f"参数调整：{evo_assessment.get('param_adjustments', {})}\n\n"
            f"## 当前参数\n{current_params}\n\n"
            f"请综合以上信息，输出最终决策。"
            f"对于Agent权重：准确率高的给更高权重（>1.0），低的降低（<1.0）。"
        )

        try:
            result = self.llm.call_json(system, user)
            if not result.get("_parse_error"):
                return self._validate_decision(result, facts)
        except Exception as e:
            logger.exception(f"Commander LLM调用失败: {e}")

        return self._commander_fallback(facts, evo_assessment)

    # ===== 算法兜底 =====

    def _strategist_fallback(self, facts: dict) -> dict:
        problems, suggestions = [], []
        if facts.get("win_rate", 0) < 0.4:
            problems.append("胜率过低"); suggestions.append("收紧选股标准")
        if facts.get("avg_return", 0) < -3:
            problems.append("平均亏损过大"); suggestions.append("降低仓位")
        return {"problems": problems, "suggestions": suggestions, "confidence": 0.4}

    def _evo_judge_fallback(self, facts: dict) -> dict:
        adjustments, direction = {}, "maintain"
        win_rate = facts.get("win_rate", 0.5)
        if win_rate < 0.35:
            adjustments = {"stop_loss_pct": 0.03, "position_size": 0.15}
            direction = "conservative"
        elif win_rate > 0.65:
            adjustments = {"position_size": 0.25}
            direction = "aggressive"
        return {"param_adjustments": adjustments, "evolution_direction": direction, "confidence": 0.4}

    def _commander_fallback(self, facts: dict, evo_assessment: dict) -> dict:
        weight_adjustments = {}
        for agent, stats in facts.get("agent_accuracy", {}).items():
            acc = stats.get("accuracy", 0.5)
            weight_adjustments[agent] = round(0.5 + acc, 2) if stats.get("traded_count", 0) >= 3 else 1.0

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
        ]
        for rg, rs in facts.get("regime_stats", {}).items():
            lines.append(f"- {rg}: {rs['total']}轮, 胜率{rs['win_rate']:.0%}")
        lines.append(f"\n## 当前参数\n{params}")
        lines.append("\n请分析存在的问题，给出改进建议。")
        return "\n".join(lines)

    def _validate_decision(self, result: dict, facts: dict) -> dict:
        if not isinstance(result.get("strategy_suggestions"), list):
            result["strategy_suggestions"] = []
        if not isinstance(result.get("param_adjustments"), dict):
            result["param_adjustments"] = {}

        clean_params = {}
        for k, v in result.get("param_adjustments", {}).items():
            if v is None:
                continue
            try:
                v = float(v)
                if k == "stop_loss_pct":    v = max(0.02, min(0.15, v))
                elif k == "take_profit_pct": v = max(0.05, min(0.50, v))
                elif k == "position_size":   v = max(0.05, min(0.30, v))
                clean_params[k] = v
            except (TypeError, ValueError):
                continue
        result["param_adjustments"] = clean_params

        clean_weights = {}
        for agent, w in result.get("agent_weight_adjustments", {}).items():
            try:
                clean_weights[agent] = round(max(0.3, min(2.0, float(w))), 2)
            except (TypeError, ValueError):
                clean_weights[agent] = 1.0
        result["agent_weight_adjustments"] = clean_weights

        if not isinstance(result.get("reasoning"), str):
            result["reasoning"] = ""
        return result


# ============================================================
# Part 3: 会议记录持久化（合并 MeetingLogger + MeetingRecorder）
# ============================================================

class MeetingRecorder:
    """
    会议记录持久化

    同时支持 JSON（机器读）和 Markdown（人读）
    目录结构：
        {base_dir}/
        ├── selection/
        │   ├── meeting_0001.json
        │   ├── meeting_0001.md
        │   └── ...
        └── review/
            ├── review_0001.json
            ├── review_0001.md
            └── ...
    """

    def __init__(self, base_dir: str = None):
        if base_dir:
            self.base_dir = Path(base_dir)
        else:
            self.base_dir = config.logs_dir / "meetings"

        self.selection_dir = self.base_dir / "selection"
        self.review_dir = self.base_dir / "review"
        self.selection_dir.mkdir(parents=True, exist_ok=True)
        self.review_dir.mkdir(parents=True, exist_ok=True)

        # 内存记录（用于生成汇总报告）
        self._selection_records: List[Dict] = []
        self._review_records: List[Dict] = []

    def save_selection(self, meeting_log: dict, cycle: int):
        """保存选股会议记录"""
        if not meeting_log or meeting_log.get("fallback"):
            return

        record = {
            "cycle": cycle,
            "timestamp": datetime.now().isoformat(),
            "type": "selection",
            **meeting_log,
        }
        self._selection_records.append(record)

        mid = meeting_log.get("meeting_id", cycle)
        self._write_json(self.selection_dir / f"meeting_{mid:04d}.json", record)
        self._write_text(
            self.selection_dir / f"meeting_{mid:04d}.md",
            self._selection_to_md(meeting_log, cycle),
        )
        logger.debug(f"选股会议记录已保存: cycle={cycle}")

    def save_selection_meeting(self, meeting_log: dict, cycle: int = 0):
        normalized = dict(meeting_log or {})
        if "selected" not in normalized and "final_stocks" in normalized:
            normalized["selected"] = normalized.get("final_stocks", [])
        if "confidence" not in normalized and "regime_confidence" in normalized:
            normalized["confidence"] = normalized.get("regime_confidence")
        if "hunters" not in normalized:
            hunters = []
            if "trend_picks" in normalized:
                hunters.append({"name": "trend_hunter", "result": normalized.get("trend_picks", {})})
            if "contrarian_picks" in normalized:
                hunters.append({"name": "contrarian", "result": normalized.get("contrarian_picks", {})})
            if hunters:
                normalized["hunters"] = hunters
        self.save_selection(normalized, cycle)

    def save_review(self, review_result: dict, facts: dict, cycle: int):
        """保存复盘会议记录"""
        record = {
            "cycle": cycle,
            "timestamp": datetime.now().isoformat(),
            "type": "review",
            "facts": facts,
            "decision": review_result,
        }
        self._review_records.append(record)

        self._write_json(self.review_dir / f"review_{cycle:04d}.json", record)
        self._write_text(
            self.review_dir / f"review_{cycle:04d}.md",
            self._review_to_md(review_result, facts, cycle),
        )
        logger.debug(f"复盘会议记录已保存: cycle={cycle}")

    def save_review_meeting(self, review_result: dict, facts: dict, cycle: int = 0):
        self.save_review(review_result, facts, cycle)

    def get_summary(self) -> Dict:
        """获取汇总统计"""
        all_returns = [
            r.get("facts", {}).get("avg_return", 0)
            for r in self._review_records
        ]
        return {
            "selection_meetings": len(self._selection_records),
            "review_meetings": len(self._review_records),
            "avg_return": sum(all_returns) / len(all_returns) if all_returns else 0,
        }

    def _selection_to_md(self, log: dict, cycle: int) -> str:
        lines = [
            f"# 选股会议 #{log.get('meeting_id', cycle)}",
            f"",
            f"**训练周期**: #{cycle}",
            f"**截断日期**: {log.get('cutoff_date', '')}",
            f"**市场状态**: {log.get('regime', '')} (置信度{log.get('confidence', 0):.0%})",
            f"",
            f"## 最终选股",
        ]
        for code in log.get("selected", []):
            lines.append(f"- {code}")
        lines.append(f"\n**来源**: {log.get('source', '')}")
        for hunter in log.get("hunters", []):
            picks = hunter.get("result", {}).get("picks", [])
            lines.append(f"\n### {hunter.get('name', 'unknown')}")
            for p in picks:
                lines.append(f"- {p.get('code', '')} 评分{p.get('score', 0):.2f}: {p.get('reasoning', '')}")
        return "\n".join(lines)

    def _review_to_md(self, result: dict, facts: dict, cycle: int) -> str:
        lines = [
            f"# 复盘会议 (Cycle #{cycle})",
            f"",
            f"**时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"",
            f"## 近期表现",
            f"- 总轮数: {facts.get('total_cycles', 0)}",
            f"- 胜率: {facts.get('win_rate', 0):.0%}",
            f"- 平均收益: {facts.get('avg_return', 0):+.2f}%",
            f"",
            f"## 决策",
        ]
        for s in result.get("strategy_suggestions", []):
            lines.append(f"- {s}")
        pa = result.get("param_adjustments", {})
        if pa:
            lines.append(f"\n### 参数调整")
            for k, v in pa.items():
                lines.append(f"- {k}: {v}")
        wa = result.get("agent_weight_adjustments", {})
        if wa:
            lines.append(f"\n### Agent 权重调整")
            for agent, w in wa.items():
                arrow = "↑" if w > 1.0 else ("↓" if w < 1.0 else "→")
                lines.append(f"- {agent}: {w:.2f} {arrow}")
        lines.append(f"\n**理由**: {result.get('reasoning', '')}")
        return "\n".join(lines)

    def _write_json(self, path: Path, data: dict):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)

    def _write_text(self, path: Path, content: str):
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
