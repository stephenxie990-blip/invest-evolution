import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from config import config
from invest.shared import (
    AgentTracker,
    LLMCaller,
    PositionPlan,
    TradingPlan,
    format_stock_table,
    summarize_stocks,
)

try:
    from invest.debate import DebateOrchestrator, RiskDebateOrchestrator
    _HAS_DEBATE = True
except ImportError:
    _HAS_DEBATE = False

logger = logging.getLogger(__name__)


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
        from invest.agents import TrendHunterAgent, ContrarianAgent
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

__all__ = ["SelectionMeeting"]
