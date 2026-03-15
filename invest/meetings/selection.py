import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from invest.agents.hunters import ContrarianAgent, TrendHunterAgent
from invest.agents.specialists import DefensiveAgent, QualityAgent
from invest.shared.contracts import PositionPlan, TradingPlan
from invest.shared.llm import LLMCaller
from invest.contracts import (
    AgentContext,
    ModelOutput,
    SignalPacket,
    StrategyAdvice,
    resolve_agent_context_confidence,
)
from invest.foundation.risk import (
    clamp_position_size,
    clamp_stop_loss_pct,
    clamp_take_profit_pct,
)
from invest.models.defaults import COMMON_PARAM_DEFAULTS

try:
    from invest.debate import DebateOrchestrator

    _HAS_DEBATE = True
except ImportError:
    DebateOrchestrator = None
    _HAS_DEBATE = False

logger = logging.getLogger(__name__)


def _agent_context_confidence(agent_context: Any, default: float) -> float:
    return _normalized_confidence(
        resolve_agent_context_confidence(agent_context, default=default),
        default=default,
    )


def _normalized_confidence(value: Any, *, default: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = float(default)
    return max(0.0, min(1.0, numeric))


class SelectionMeeting:
    """
    选股会议编排器

    流程：
    1. 格式化候选股票摘要
    2. **并行**调用多个 Agent（LLM 或 fallback）
    3. **并行**执行多空辩论
    4. 汇总 → 按 Agent 权重加权计分 → 生成 TradingPlan

    优化借鉴 LLM 推理系统设计：
    - Tensor Parallelism: 多 Agent 并行，耗时 = max(单Agent) 而非 sum(所有Agent)
    - Continuous Batching: 辩论阶段多股票并行处理
    - Pipeline Parallelism: Agent 阶段 → 辩论阶段 两阶段流水线
    """

    # 并行度上限，防止 LLM API 限流
    _MAX_AGENT_WORKERS = 4
    _MAX_DEBATE_WORKERS = 6

    def __init__(
        self,
        llm_caller: Optional[LLMCaller] = None,
        agent_weights: Optional[Dict[str, float]] = None,
        trend_hunter: Optional[Any] = None,
        contrarian: Optional[Any] = None,
        quality_agent: Optional[Any] = None,
        defensive_agent: Optional[Any] = None,
        max_hunters: Optional[int] = None,
        enable_debate: bool = True,
        max_debate_rounds: int = 1,
        deep_llm_caller: Optional[LLMCaller] = None,
        progress_callback: Optional[Callable[[dict], None]] = None,
    ):
        self.llm = llm_caller
        self.agent_weights = agent_weights or {
            "trend_hunter": 1.0,
            "contrarian": 1.0,
        }
        self.trend_hunter = trend_hunter or TrendHunterAgent()
        self.contrarian = contrarian or ContrarianAgent()
        self.quality_agent = quality_agent or QualityAgent()
        self.defensive_agent = defensive_agent or DefensiveAgent()
        self.max_hunters = max_hunters
        self.meeting_count = 0
        self.progress_callback = progress_callback

        # 线程安全锁：保护 progress 回调和 hunter_outputs 收集
        self._progress_lock = threading.Lock()

        self._debate: Optional[Any] = None
        if _HAS_DEBATE and DebateOrchestrator is not None and enable_debate and llm_caller is not None:
            deep = deep_llm_caller or llm_caller
            self._debate = DebateOrchestrator(
                fast_llm=llm_caller,
                deep_llm=deep,
                max_rounds=max_debate_rounds,
            )
            logger.info(
                "SelectionMeeting: debate enabled (max_rounds=%d)",
                max_debate_rounds,
            )

    # ==================================================================
    # Public API
    # ==================================================================

    def run(
        self,
        regime: Dict[str, Any],
        stock_summaries: Sequence[Mapping[str, Any]],
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

    def run_with_model_output(self, model_output: ModelOutput) -> Dict[str, Any]:
        return self.run_with_context(
            model_output.signal_packet, model_output.agent_context
        )

    def run_with_context(
        self,
        signal_packet: SignalPacket,
        agent_context: AgentContext,
    ) -> Dict[str, Any]:
        self.meeting_count += 1
        regime = {
            "regime": signal_packet.regime,
            "confidence": _agent_context_confidence(agent_context, default=0.6),
            "reasoning": agent_context.summary,
            "suggested_exposure": max(
                0.0, min(1.0, 1.0 - float(signal_packet.cash_reserve))
            ),
            "params": {
                "top_n": max(
                    len(signal_packet.selected_codes),
                    len(signal_packet.signals),
                ),
                "max_positions": signal_packet.max_positions,
                **dict(signal_packet.params or {}),
            },
        }
        stock_summaries = list(agent_context.stock_summaries or [])
        if not stock_summaries:
            return {
                "trading_plan": self._to_trading_plan(
                    self._fallback_empty(), regime, signal_packet.as_of_date
                ),
                "meeting_log": {},
                "strategy_advice": StrategyAdvice(source="empty").to_dict(),
            }

        top_n = signal_packet.max_positions or regime["params"].get("top_n", 5)
        meeting_result = self._run_llm(
            top_n,
            regime,
            stock_summaries,
            agent_context=agent_context,
            model_name=signal_packet.model_name,
        )
        trading_plan = self._to_trading_plan_v2(
            meeting_result, signal_packet, agent_context
        )
        strategy_advice = StrategyAdvice(
            source=str(meeting_result.get("source", "model_meeting")),
            selected_codes=[p.code for p in trading_plan.positions],
            selected_meta=list(meeting_result.get("selected_meta", [])),
            confidence=float(meeting_result.get("confidence", 0.0) or 0.0),
            reasoning=str(meeting_result.get("reasoning", "")),
            metadata={
                "model_name": signal_packet.model_name,
                "config_name": signal_packet.config_name,
                "regime": signal_packet.regime,
            },
        )
        meeting_log = {
            "regime": signal_packet.regime,
            "confidence": strategy_advice.confidence,
            "hunters": meeting_result.get("hunters", []),
            "selected": strategy_advice.selected_codes,
            "selected_meta": strategy_advice.selected_meta,
            "source": strategy_advice.source,
            "meeting_id": self.meeting_count,
            "cutoff_date": signal_packet.as_of_date,
            "model_name": signal_packet.model_name,
            "config_name": signal_packet.config_name,
            "agent_context_summary": agent_context.summary,
        }
        return {
            "trading_plan": trading_plan,
            "meeting_log": meeting_log,
            "strategy_advice": strategy_advice.to_dict(),
        }

    def update_weights(self, weight_adjustments: Dict[str, float]):
        """EMA 平滑更新 Agent 权重（由 ReviewMeeting 驱动）"""
        for agent, new_w in weight_adjustments.items():
            if agent in self.agent_weights:
                old = self.agent_weights[agent]
                self.agent_weights[agent] = round(old * 0.6 + new_w * 0.4, 3)
                logger.info(
                    f"📊 Agent权重更新 {agent}: {old:.2f} → {self.agent_weights[agent]:.2f}"
                )

    # ==================================================================
    # Internal: model roster
    # ==================================================================

    def _get_model_roster(self, model_name: str) -> List[Dict[str, Any]]:
        model_name = str(model_name or "").strip()
        specialist_map = {
            "momentum": [
                {
                    "key": "trend_hunter",
                    "label": "TrendHunterAgent",
                    "agent": self.trend_hunter,
                },
            ],
            "mean_reversion": [
                {
                    "key": "contrarian",
                    "label": "ContrarianAgent",
                    "agent": self.contrarian,
                },
            ],
            "value_quality": [
                {
                    "key": "quality_agent",
                    "label": "QualityAgent",
                    "agent": self.quality_agent,
                },
            ],
            "defensive_low_vol": [
                {
                    "key": "defensive_agent",
                    "label": "DefensiveAgent",
                    "agent": self.defensive_agent,
                },
            ],
        }
        return specialist_map.get(
            model_name,
            [
                {
                    "key": "trend_hunter",
                    "label": "TrendHunterAgent",
                    "agent": self.trend_hunter,
                },
                {
                    "key": "contrarian",
                    "label": "ContrarianAgent",
                    "agent": self.contrarian,
                },
            ],
        )

    # ==================================================================
    # Internal: thread-safe progress notification
    # ==================================================================

    def _notify_progress(self, payload: dict) -> None:
        if not self.progress_callback:
            return
        with self._progress_lock:
            try:
                self.progress_callback(dict(payload))
            except Exception as exc:
                logger.warning(
                    "Selection progress callback failed for keys=%s: %s",
                    sorted(payload.keys()),
                    exc,
                    exc_info=exc,
                )

    # ==================================================================
    # Internal: single-agent execution unit (thread target)
    #
    # LLM analogy — this is one "tensor shard" that runs on one "GPU".
    # Multiple shards execute simultaneously via ThreadPoolExecutor,
    # just like Tensor Parallelism splits a matrix multiply across devices.
    # ==================================================================

    def _execute_single_agent(
        self,
        spec: Dict[str, Any],
        stock_summaries: Sequence[Mapping[str, Any]],
        regime: Dict[str, Any],
        agent_context: Optional[AgentContext],
        progress_base: int,
    ) -> Optional[Dict[str, Any]]:
        """
        在独立线程中运行单个 Agent 的完整认知循环。

        返回 {"name": agent_key, "result": {...}} 或 None（失败时）。
        """
        agent_key = spec["key"]
        agent_label = spec["label"]
        agent = spec["agent"]

        t0 = time.perf_counter()
        self._notify_progress(
            {
                "agent": agent_label,
                "status": "running",
                "stage": "selection",
                "progress_pct": progress_base,
                "message": f"{agent_label} 分析 {len(stock_summaries)} 只候选...",
            }
        )

        try:
            if agent_context is not None and hasattr(agent, "analyze_context"):
                result = agent.analyze_context(agent_context)
            else:
                candidates = agent.perceive(stock_summaries)
                reasoning = agent.reason(candidates, context=regime)
                result = agent.act(reasoning)

            elapsed = time.perf_counter() - t0

            if result and not result.get("_parse_error"):
                picks_count = len(
                    result.get("picks", []) or result.get("selected", [])
                )
                self._notify_progress(
                    {
                        "agent": agent_label,
                        "status": "completed",
                        "stage": "selection",
                        "progress_pct": progress_base + 6,
                        "message": (
                            f"{agent_label} 完成 ({elapsed:.1f}s)，"
                            f"推荐 {picks_count} 只候选"
                        ),
                        "speech": (
                            result.get("overall_view")
                            or result.get("reasoning")
                            or f"{agent_label} 已输出候选"
                        ),
                        "picks": (result.get("picks", []) or [])[:10],
                        "confidence": _normalized_confidence(result.get("confidence"), default=0.5),
                    }
                )
                logger.info(
                    "✅ %s completed in %.1fs, %d picks",
                    agent_label,
                    elapsed,
                    picks_count,
                )
                return {"name": agent_key, "result": result}

            logger.warning(
                "⚠️ %s returned empty/error result in %.1fs", agent_label, elapsed
            )
            return None

        except Exception as e:
            elapsed = time.perf_counter() - t0
            self._notify_progress(
                {
                    "agent": agent_label,
                    "status": "error",
                    "message": f"{agent_label} 执行失败 ({elapsed:.1f}s): {e}",
                }
            )
            logger.warning(
                "%s 认知环路执行失败 (%.1fs): %s", agent_label, elapsed, e
            )
            return None

    # ==================================================================
    # Internal: single-debate execution unit (thread target)
    # ==================================================================

    def _execute_single_debate(
        self,
        code: str,
        stock_info: Mapping[str, Any],
        regime: Dict[str, Any],
    ) -> tuple[str, dict]:
        """在独立线程中对单只股票执行多空辩论。"""
        if self._debate is None:
            return code, {"verdict": "hold", "confidence": 0.5, "error": "debate_disabled"}
        try:
            result = self._debate.debate(stock_info, regime)
            return code, result
        except Exception as e:
            logger.warning("Debate failed for %s: %s", code, e)
            return code, {"verdict": "hold", "confidence": 0.5, "error": str(e)}

    # ==================================================================
    # Internal: parallel LLM orchestration
    #
    # Architecture mirrors LLM inference pipeline parallelism:
    #   Stage 1 (Prefill)  → Parallel Agent calls  (Tensor Parallel)
    #   Stage 2 (Decode)   → Parallel Debate calls  (Continuous Batching)
    #   Stage 3 (Postproc) → Sequential aggregation  (fast, no need to parallelise)
    # ==================================================================

    def _run_llm(
        self,
        top_n: int,
        regime: Dict,
        stock_summaries: Sequence[Mapping[str, Any]],
        agent_context: Optional[AgentContext] = None,
        model_name: str = "",
    ) -> Dict:
        t_meeting_start = time.perf_counter()

        roster = (
            self._get_model_roster(model_name)
            if agent_context is not None
            else [
                {
                    "key": "trend_hunter",
                    "label": "TrendHunter",
                    "agent": self.trend_hunter,
                },
                {
                    "key": "contrarian",
                    "label": "Contrarian",
                    "agent": self.contrarian,
                },
            ]
        )

        # =============================================================
        # Phase 1: Parallel Agent Execution  (Tensor Parallelism)
        # =============================================================
        hunter_outputs: List[Dict[str, Any]] = []
        n_agents = len(roster)
        n_workers = min(n_agents, self._MAX_AGENT_WORKERS)

        logger.info(
            "🚀 Phase 1: launching %d agents in parallel (workers=%d)",
            n_agents,
            n_workers,
        )

        if n_agents == 1:
            # 只有 1 个 Agent，无需线程池开销
            result = self._execute_single_agent(
                roster[0], stock_summaries, regime, agent_context, 30
            )
            if result:
                hunter_outputs.append(result)
        else:
            with ThreadPoolExecutor(
                max_workers=n_workers,
                thread_name_prefix="agent",
            ) as executor:
                future_to_spec = {
                    executor.submit(
                        self._execute_single_agent,
                        spec,
                        stock_summaries,
                        regime,
                        agent_context,
                        30 + idx * 8,
                    ): spec
                    for idx, spec in enumerate(roster)
                }

                for future in as_completed(future_to_spec):
                    spec = future_to_spec[future]
                    try:
                        result = future.result(timeout=120)
                        if result:
                            hunter_outputs.append(result)
                    except Exception as e:
                        logger.warning(
                            "Agent %s future failed: %s",
                            spec["label"],
                            e,
                        )

        t_agents_done = time.perf_counter()
        logger.info(
            "✅ Phase 1 complete: %d/%d agents succeeded in %.1fs",
            len(hunter_outputs),
            n_agents,
            t_agents_done - t_meeting_start,
        )

        # Apply agent weights to scores
        for hunter in hunter_outputs:
            r = hunter["result"]
            weight = self.agent_weights.get(hunter["name"], 1.0)
            for p in r.get("picks", []):
                p["score"] = p.get("score", 0.5) * weight

        if not hunter_outputs:
            return self._run_algorithm(stock_summaries, top_n)

        # =============================================================
        # Phase 2: Parallel Debate  (Continuous Batching)
        # =============================================================
        if self._debate is not None and stock_summaries:
            self._notify_progress(
                {
                    "agent": "SelectionMeeting",
                    "status": "running",
                    "stage": "debate",
                    "progress_pct": 52,
                    "message": "多空辩论筛选候选中...",
                }
            )

            code_to_summary = {s["code"]: s for s in stock_summaries}
            candidate_codes: List[str] = []
            for ho in hunter_outputs:
                for p in ho["result"].get("picks", []):
                    code = p.get("code", "")
                    if code and code not in candidate_codes:
                        candidate_codes.append(code)

            # Filter to codes we have summary data for
            debate_tasks = [
                (code, code_to_summary[code])
                for code in candidate_codes
                if code in code_to_summary
            ]

            debate_results: Dict[str, dict] = {}
            n_debates = len(debate_tasks)

            if n_debates == 0:
                pass
            elif n_debates == 1:
                # 单只股票无需线程池
                code, s_info = debate_tasks[0]
                _, d_result = self._execute_single_debate(code, s_info, regime)
                debate_results[code] = d_result
            else:
                n_debate_workers = min(n_debates, self._MAX_DEBATE_WORKERS)
                logger.info(
                    "🗣️ Phase 2: launching %d debates in parallel (workers=%d)",
                    n_debates,
                    n_debate_workers,
                )
                with ThreadPoolExecutor(
                    max_workers=n_debate_workers,
                    thread_name_prefix="debate",
                ) as executor:
                    future_to_code = {
                        executor.submit(
                            self._execute_single_debate, code, s_info, regime
                        ): code
                        for code, s_info in debate_tasks
                    }
                    for future in as_completed(future_to_code):
                        try:
                            code, d_result = future.result(timeout=90)
                            debate_results[code] = d_result
                        except Exception as e:
                            failed_code = future_to_code[future]
                            logger.warning("Debate future failed for %s: %s", failed_code, e)

            # Apply debate verdicts to scores
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

            t_debate_done = time.perf_counter()
            logger.info(
                "✅ Phase 2 complete: %d debates in %.1fs",
                len(debate_results),
                t_debate_done - t_agents_done,
            )
            self._notify_progress(
                {
                    "agent": "SelectionMeeting",
                    "status": "completed",
                    "stage": "debate",
                    "progress_pct": 56,
                    "message": f"多空辩论完成，筛查 {len(debate_results)} 只候选",
                    "speech": "多空辩论已完成，候选股票已重新排序",
                    "details": [
                        {"code": code, **result}
                        for code, result in list(debate_results.items())[:10]
                    ],
                }
            )

        # =============================================================
        # Phase 3: Aggregation (fast, purely in-memory)
        # =============================================================
        result = self._aggregate(hunter_outputs, regime, top_n)

        t_total = time.perf_counter() - t_meeting_start
        logger.info(
            "🏁 SelectionMeeting total: %.1fs (agents=%.1fs, debate+agg=%.1fs)",
            t_total,
            t_agents_done - t_meeting_start,
            t_total - (t_agents_done - t_meeting_start),
        )
        return result

    # ==================================================================
    # Internal: pure-algorithm fallback
    # ==================================================================

    def _run_algorithm(self, stock_summaries: Sequence[Mapping[str, Any]], top_n: int) -> Dict:
        sorted_stocks = sorted(
            stock_summaries,
            key=lambda x: x.get("algo_score", 0),
            reverse=True,
        )
        selected = [s["code"] for s in sorted_stocks[:top_n]]
        return {
            "selected": selected,
            "reasoning": f"算法排序：选取 algo_score 最高的{len(selected)}只",
            "confidence": 0.6,
            "source": "algorithm",
            "hunters": [],
        }

    # ==================================================================
    # Internal: score aggregation
    # ==================================================================

    def _aggregate(
        self, hunter_outputs: List[Dict], regime: Dict, top_n: int
    ) -> Dict:
        if not hunter_outputs:
            return self._fallback_empty()

        stock_scores: Dict[str, float] = {}
        pick_meta: Dict[str, Dict[str, Any]] = {}
        all_reasons = []

        for hunter in hunter_outputs:
            source_name = hunter["name"]
            result = hunter["result"]
            confidence = _normalized_confidence(result.get("confidence"), default=0.5)
            reason = str(result.get("overall_view", "")).strip()
            if reason:
                all_reasons.append(f"{source_name}: {reason}")

            picks = result.get("picks", [])
            if not picks:
                picks = [
                    {"code": code, "score": confidence}
                    for code in result.get("selected", [])
                ]

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
                    trailing_pct = COMMON_PARAM_DEFAULTS["trailing_pct"]

                meta = pick_meta.setdefault(
                    code,
                    {
                        "code": code,
                        "agg_score": 0.0,
                        "best_score": -1.0,
                        "reasonings": [],
                        "sources": [],
                        "source": source_name,
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
                    meta["trailing_pct"] = trailing_pct

        sorted_codes = sorted(
            stock_scores.items(), key=lambda item: item[1], reverse=True
        )
        max_pos = min(
            max(1, int(top_n)),
            regime.get("params", {}).get("max_positions", 2),
        )
        final_selected = [code for code, _ in sorted_codes[:max_pos]]

        selected_meta = []
        for code in final_selected:
            meta = pick_meta.get(
                code, {"code": code, "reasonings": [], "sources": []}
            )
            selected_meta.append(
                {
                    "code": code,
                    "score": round(
                        meta.get("agg_score", stock_scores.get(code, 0.0)), 3
                    ),
                    "source": meta.get("source", "meeting"),
                    "sources": meta.get("sources", []),
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
            "confidence": _normalized_confidence(
                sum(score for _, score in sorted_codes[: len(final_selected)])
                / max(len(final_selected), 1),
                default=0.0,
            ),
            "source": "llm",
            "hunters": hunter_outputs,
        }

    # ==================================================================
    # Internal: position weight helpers
    # ==================================================================

    def _resolve_default_weight(
        self,
        preferred_weight: Any,
        available_weight: float,
        count: int,
    ) -> float:
        if count <= 0:
            return float(COMMON_PARAM_DEFAULTS["position_size"])
        try:
            preferred = float(preferred_weight)
        except (TypeError, ValueError):
            preferred = float(COMMON_PARAM_DEFAULTS["position_size"])
        spread = max(0.0, available_weight / max(count, 1))
        if spread <= 0:
            return 0.0
        return round(min(clamp_position_size(preferred), spread), 3)

    @staticmethod
    def _clamp_trailing_pct(value: Any) -> float | None:
        if value is None:
            return None
        try:
            return max(0.03, min(0.30, float(value)))
        except (TypeError, ValueError):
            return None

    # ==================================================================
    # Internal: TradingPlan assembly
    # ==================================================================

    def _to_trading_plan(
        self,
        meeting_result: Dict[str, Any],
        regime: Dict[str, Any],
        date: str,
    ) -> TradingPlan:
        selected_meta = meeting_result.get("selected_meta") or [
            {"code": code} for code in meeting_result.get("selected", [])
        ]
        max_positions = regime.get("params", {}).get("max_positions", 2)
        regime_params = regime.get("params", {})
        suggested_exposure = float(
            regime.get("suggested_exposure", 0.7) or 0.7
        )
        cash_reserve = round(max(0.0, min(0.7, 1.0 - suggested_exposure)), 3)
        available_weight = max(0.0, 1.0 - cash_reserve)
        default_weight = self._resolve_default_weight(
            regime_params.get(
                "position_size", COMMON_PARAM_DEFAULTS["position_size"]
            ),
            available_weight,
            len(selected_meta),
        )

        positions = []
        for i, item in enumerate(selected_meta):
            source = str(item.get("source", "meeting"))
            trailing_pct = item.get("trailing_pct")
            if trailing_pct is None and source == "trend_hunter":
                trailing_pct = COMMON_PARAM_DEFAULTS["trailing_pct"]
            trailing_pct = self._clamp_trailing_pct(trailing_pct)

            stop_loss_value = item.get(
                "stop_loss_pct",
                regime_params.get(
                    "stop_loss_pct",
                    COMMON_PARAM_DEFAULTS["stop_loss_pct"],
                ),
            )
            take_profit_value = item.get(
                "take_profit_pct",
                regime_params.get(
                    "take_profit_pct",
                    COMMON_PARAM_DEFAULTS["take_profit_pct"],
                ),
            )
            positions.append(
                PositionPlan(
                    code=item["code"],
                    priority=i + 1,
                    weight=float(item.get("weight") or default_weight),
                    entry_method="market",
                    stop_loss_pct=clamp_stop_loss_pct(stop_loss_value),
                    take_profit_pct=clamp_take_profit_pct(take_profit_value),
                    trailing_pct=trailing_pct,
                    max_hold_days=int(
                        regime_params.get(
                            "max_hold_days",
                            COMMON_PARAM_DEFAULTS["max_hold_days"],
                        )
                        or COMMON_PARAM_DEFAULTS["max_hold_days"]
                    ),
                    reason=str(
                        item.get("reasoning")
                        or meeting_result.get("reasoning", "选股会议推荐")
                    ),
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

    def _to_trading_plan_v2(
        self,
        meeting_result: Dict[str, Any],
        signal_packet: SignalPacket,
        agent_context: AgentContext,
    ) -> TradingPlan:
        signal_by_code = {
            signal.code: signal for signal in signal_packet.signals
        }
        selected_meta = meeting_result.get("selected_meta") or [
            {"code": code}
            for code in meeting_result.get(
                "selected", signal_packet.selected_codes
            )
        ]
        cash_reserve = max(0.0, min(0.7, float(signal_packet.cash_reserve)))
        available_weight = max(0.0, 1.0 - cash_reserve)
        default_weight = self._resolve_default_weight(
            signal_packet.params.get(
                "position_size", COMMON_PARAM_DEFAULTS["position_size"]
            ),
            available_weight,
            len(selected_meta),
        )
        positions = []
        for idx, item in enumerate(selected_meta, start=1):
            code = item["code"]
            signal = signal_by_code.get(code)
            trailing_pct = item.get(
                "trailing_pct",
                signal.trailing_pct
                if signal
                else signal_packet.params.get("trailing_pct"),
            )
            trailing_pct = self._clamp_trailing_pct(trailing_pct)
            stop_loss_value = item.get("stop_loss_pct")
            if stop_loss_value is None:
                stop_loss_value = (
                    signal.stop_loss_pct
                    if signal and signal.stop_loss_pct is not None
                    else signal_packet.params.get(
                        "stop_loss_pct",
                        COMMON_PARAM_DEFAULTS["stop_loss_pct"],
                    )
                )
            take_profit_value = item.get("take_profit_pct")
            if take_profit_value is None:
                take_profit_value = (
                    signal.take_profit_pct
                    if signal and signal.take_profit_pct is not None
                    else signal_packet.params.get(
                        "take_profit_pct",
                        COMMON_PARAM_DEFAULTS["take_profit_pct"],
                    )
                )
            positions.append(
                PositionPlan(
                    code=code,
                    priority=idx,
                    weight=min(
                        float(
                            item.get("weight")
                            or (
                                signal.weight_hint
                                if signal and signal.weight_hint is not None
                                else default_weight
                            )
                        ),
                        max(available_weight, 0.0),
                    ),
                    entry_method="market",
                    stop_loss_pct=clamp_stop_loss_pct(stop_loss_value),
                    take_profit_pct=clamp_take_profit_pct(take_profit_value),
                    trailing_pct=trailing_pct,
                    max_hold_days=int(
                        signal_packet.params.get(
                            "max_hold_days",
                            COMMON_PARAM_DEFAULTS["max_hold_days"],
                        )
                        or COMMON_PARAM_DEFAULTS["max_hold_days"]
                    ),
                    reason=str(
                        item.get("reasoning")
                        or meeting_result.get("reasoning")
                        or agent_context.summary
                    ),
                    source=str(
                        item.get("source")
                        or meeting_result.get("source", "model_meeting")
                    ),
                )
            )
        return TradingPlan(
            date=signal_packet.as_of_date,
            positions=positions,
            cash_reserve=cash_reserve,
            max_positions=signal_packet.max_positions or len(positions),
            source=str(meeting_result.get("source", "model_meeting")),
            reasoning=str(
                meeting_result.get("reasoning", agent_context.summary)
            ),
        )

    def _fallback_empty(self) -> Dict:
        return {
            "selected": [],
            "reasoning": "无候选股票",
            "confidence": 0.0,
            "source": "algorithm",
            "hunters": [],
        }


# ============================================================
# Part 2: 复盘会议
# ============================================================

__all__ = ["SelectionMeeting"]
