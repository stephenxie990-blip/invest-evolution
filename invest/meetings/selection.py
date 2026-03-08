import logging
from typing import Any, Callable, Dict, List, Optional

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
        progress_callback: Optional[Callable[[dict], None]] = None,
    ):
        from invest.agents import TrendHunterAgent, ContrarianAgent
        self.llm = llm_caller
        self.agent_weights = agent_weights or {"trend_hunter": 1.0, "contrarian": 1.0}
        self.trend_hunter = trend_hunter or TrendHunterAgent()
        self.contrarian = contrarian or ContrarianAgent()
        self.max_hunters = max_hunters
        self.meeting_count = 0
        self.progress_callback = progress_callback

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

        if self.llm:
            return self._run_llm(top_n, regime, stock_summaries)
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


    def _notify_progress(self, payload: dict) -> None:
        if not self.progress_callback:
            return
        try:
            self.progress_callback(dict(payload))
        except Exception:
            logger.debug("selection progress callback failed", exc_info=True)

    def _run_llm(
        self,
        top_n: int,
        regime: Dict,
        stock_summaries: List[Dict],
    ) -> Dict:
        hunter_outputs = []

        # 1. 趋势猎手 - 认知环路
        try:
            self._notify_progress({"agent": "TrendHunter", "status": "running", "message": f"趋势猎手分析 {len(stock_summaries)} 只候选..."})
            candidates = self.trend_hunter.perceive(stock_summaries)
            reasoning = self.trend_hunter.reason(candidates, context=regime)
            # Act
            result = self.trend_hunter.act(reasoning)
            
            if result and not result.get("_parse_error"):
                hunter_outputs.append({"name": "trend_hunter", "result": result})
                self._notify_progress({"agent": "TrendHunter", "status": "completed", "message": f"趋势猎手完成，推荐 {len(result.get("picks", [])) or len(result.get("selected", []))} 只候选"})
        except Exception as e:
            self._notify_progress({"agent": "TrendHunter", "status": "error", "message": f"趋势猎手执行失败: {e}"})
            logger.warning(f"TrendHunter 认知环路执行失败: {e}")

        # 2. 逆向猎手 - 认知环路
        try:
            self._notify_progress({"agent": "Contrarian", "status": "running", "message": f"逆向交易者分析 {len(stock_summaries)} 只候选..."})
            candidates = self.contrarian.perceive(stock_summaries)
            reasoning = self.contrarian.reason(candidates, context=regime)
            # Act
            result = self.contrarian.act(reasoning)
            
            if result and not result.get("_parse_error"):
                hunter_outputs.append({"name": "contrarian", "result": result})
                self._notify_progress({"agent": "Contrarian", "status": "completed", "message": f"逆向交易者完成，推荐 {len(result.get("picks", [])) or len(result.get("selected", []))} 只候选"})
        except Exception as e:
            self._notify_progress({"agent": "Contrarian", "status": "error", "message": f"逆向交易者执行失败: {e}"})
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
            self._notify_progress({"agent": "SelectionMeeting", "status": "running", "message": "多空辩论筛选候选中..."})
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
            self._notify_progress({"agent": "SelectionMeeting", "status": "completed", "message": f"多空辩论完成，筛查 {len(debate_results)} 只候选"})

        return self._aggregate(hunter_outputs, regime, top_n)

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

    def _aggregate(self, hunter_outputs: List[Dict], regime: Dict, top_n: int) -> Dict:
        if not hunter_outputs:
            return self._fallback_empty()

        stock_scores: Dict[str, float] = {}
        pick_meta: Dict[str, Dict[str, Any]] = {}
        all_reasons = []

        for hunter in hunter_outputs:
            source_name = hunter["name"]
            result = hunter["result"]
            confidence = result.get("confidence", 0.5)
            reason = str(result.get("overall_view", "")).strip()
            if reason:
                all_reasons.append(f"{source_name}: {reason}")

            picks = result.get("picks", [])
            if not picks:
                picks = [{"code": code, "score": confidence} for code in result.get("selected", [])]

            for pick in picks:
                code = pick.get("code", "")
                if not code:
                    continue
                raw_score = float(pick.get("score", confidence) or confidence)
                weighted_score = raw_score * confidence
                stock_scores[code] = stock_scores.get(code, 0.0) + weighted_score

                trailing_pct = pick.get("trailing_pct")
                if trailing_pct in ("", "null"):
                    trailing_pct = None
                if trailing_pct is None and source_name == "trend_hunter":
                    trailing_pct = 0.10

                meta = pick_meta.setdefault(
                    code,
                    {
                        "code": code,
                        "agg_score": 0.0,
                        "best_score": -1.0,
                        "reasonings": [],
                        "sources": [],
                        "source": source_name,
                        "stop_loss_pct": pick.get("stop_loss_pct"),
                        "take_profit_pct": pick.get("take_profit_pct"),
                        "trailing_pct": trailing_pct,
                    },
                )
                meta["agg_score"] += weighted_score
                if source_name not in meta["sources"]:
                    meta["sources"].append(source_name)
                reasoning = str(pick.get("reasoning", "")).strip()
                if reasoning and reasoning not in meta["reasonings"]:
                    meta["reasonings"].append(reasoning)
                if weighted_score >= meta.get("best_score", -1.0):
                    meta["best_score"] = weighted_score
                    meta["source"] = source_name
                    meta["stop_loss_pct"] = pick.get("stop_loss_pct")
                    meta["take_profit_pct"] = pick.get("take_profit_pct")
                    meta["trailing_pct"] = trailing_pct

        sorted_codes = sorted(stock_scores.items(), key=lambda item: item[1], reverse=True)
        max_pos = min(max(1, int(top_n)), regime.get("params", {}).get("max_positions", 2))
        final_selected = [code for code, _ in sorted_codes[:max_pos]]

        selected_meta = []
        for code in final_selected:
            meta = pick_meta.get(code, {"code": code, "reasonings": [], "sources": []})
            selected_meta.append(
                {
                    "code": code,
                    "score": round(meta.get("agg_score", stock_scores.get(code, 0.0)), 3),
                    "source": meta.get("source", "meeting"),
                    "sources": meta.get("sources", []),
                    "stop_loss_pct": meta.get("stop_loss_pct"),
                    "take_profit_pct": meta.get("take_profit_pct"),
                    "trailing_pct": meta.get("trailing_pct"),
                    "reasoning": "；".join(meta.get("reasonings", [])[:2]),
                }
            )

        regime_name = regime.get("regime", "unknown")
        hint = {
            "bull": "牛市环境，倾向趋势策略",
            "bear": "熊市环境，控制风险为主",
        }.get(regime_name, "震荡市，灵活配置")
        reasoning = f"{hint}。{' '.join(all_reasons)}".strip()

        return {
            "selected": final_selected,
            "selected_meta": selected_meta,
            "reasoning": reasoning,
            "confidence": (
                sum(score for _, score in sorted_codes[: len(final_selected)]) / max(len(final_selected), 1)
            ),
            "source": "llm",
            "hunters": hunter_outputs,
        }

    def _to_trading_plan(
        self, meeting_result: Dict[str, Any], regime: Dict[str, Any], date: str
    ) -> TradingPlan:
        selected_meta = meeting_result.get("selected_meta") or [
            {"code": code} for code in meeting_result.get("selected", [])
        ]
        max_positions = regime.get("params", {}).get("max_positions", 2)
        regime_params = regime.get("params", {})
        suggested_exposure = float(regime.get("suggested_exposure", 0.7) or 0.7)
        cash_reserve = round(max(0.0, min(0.7, 1.0 - suggested_exposure)), 3)
        available_weight = max(0.0, 1.0 - cash_reserve)
        default_weight = round(min(0.25, available_weight / max(len(selected_meta), 1)), 3) if selected_meta else 0.20

        positions = []
        for i, item in enumerate(selected_meta):
            source = str(item.get("source", "meeting"))
            trailing_pct = item.get("trailing_pct")
            if trailing_pct is None and source == "trend_hunter":
                trailing_pct = 0.10
            if trailing_pct is not None:
                trailing_pct = max(0.05, min(0.20, float(trailing_pct)))

            positions.append(
                PositionPlan(
                    code=item["code"],
                    priority=i + 1,
                    weight=default_weight,
                    entry_method="market",
                    stop_loss_pct=max(0.01, min(0.15, float(item.get("stop_loss_pct", regime_params.get("stop_loss_pct", 0.05))))),
                    take_profit_pct=max(0.05, min(0.50, float(item.get("take_profit_pct", regime_params.get("take_profit_pct", 0.15))))),
                    trailing_pct=trailing_pct,
                    max_hold_days=30,
                    reason=str(item.get("reasoning") or meeting_result.get("reasoning", "选股会议推荐")),
                    source=source,
                )
            )

        return TradingPlan(
            date=date,
            positions=positions,
            cash_reserve=cash_reserve,
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
