"""
投资进化系统 — 双轨 LLM 路由器 (Phase 1)

借鉴 TradingAgents 的 quick_think_llm / deep_think_llm 分离设计：
- fast()  — 快思考模型：数据筛选、摘要、指标分析（高频低成本）
- deep()  — 慢思考模型：策略裁判、复盘评估、风控辩论（低频高质量）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from invest.core import LLMCaller

if TYPE_CHECKING:
    from config import EvolutionConfig

logger = logging.getLogger(__name__)


@dataclass
class LLMRouter:
    """双轨 LLM 路由器

    封装快/慢两个 LLMCaller，由调用方根据任务复杂度选择：
    - router.fast() → 数据密集型、高频、低推理要求的任务
    - router.deep() → 关键决策、辩论裁判、策略评估等慢推理任务

    Example::

        router = LLMRouter.from_config(config, dry_run=True)
        regime = market_agent.analyze(stats, llm=router.fast())
        verdict = debate.run(stocks, llm=router.deep())
    """

    _fast_caller: LLMCaller
    _deep_caller: LLMCaller

    # ------------------------------------------------------------------ #
    # 构造                                                                  #
    # ------------------------------------------------------------------ #

    @classmethod
    def from_config(
        cls,
        cfg: "EvolutionConfig",
        dry_run: bool = False,
    ) -> "LLMRouter":
        """从 EvolutionConfig 构建路由器。

        Args:
            cfg: 全局配置对象
            dry_run: 若为 True，两个 caller 均以 dry_run 模式启动（不发起真实 API 调用）
        """
        fast_caller = LLMCaller(
            model=cfg.llm_fast_model,
            api_key=cfg.llm_api_key,
            api_base=cfg.llm_api_base,
            timeout=cfg.llm_timeout,
            max_retries=cfg.llm_max_retries,
            dry_run=dry_run,
        )
        # 若 deep 模型与 fast 相同，直接复用同一 caller（节省资源）
        if cfg.llm_deep_model == cfg.llm_fast_model:
            deep_caller = fast_caller
        else:
            deep_caller = LLMCaller(
                model=cfg.llm_deep_model,
                api_key=cfg.llm_api_key,
                api_base=cfg.llm_api_base,
                timeout=cfg.llm_timeout,
                max_retries=cfg.llm_max_retries,
                dry_run=dry_run,
            )

        logger.info(
            "LLMRouter initialized | fast=%s | deep=%s | dry_run=%s",
            cfg.llm_fast_model,
            cfg.llm_deep_model,
            dry_run,
        )
        return cls(_fast_caller=fast_caller, _deep_caller=deep_caller)

    # ------------------------------------------------------------------ #
    # 路由方法                                                              #
    # ------------------------------------------------------------------ #

    def fast(self) -> LLMCaller:
        """返回快思考 LLMCaller（用于高频、低推理要求任务）。

        适用场景：
        - TrendHunterAgent / ContrarianAgent 候选筛选
        - MarketRegimeAgent 市场状态分析
        - 数据摘要生成
        """
        return self._fast_caller

    def deep(self) -> LLMCaller:
        """返回深度推理 LLMCaller（用于关键决策任务）。

        适用场景：
        - StrategistAgent 组合风险评估
        - ReviewMeeting EvoJudge / Commander 复盘裁判
        - DebateOrchestrator 辩论裁判
        """
        return self._deep_caller

    # ------------------------------------------------------------------ #
    # 统计                                                                  #
    # ------------------------------------------------------------------ #

    def get_stats(self) -> dict:
        """返回双轨调用统计。"""
        fast_stats = self._fast_caller.get_stats()
        # 若 fast 和 deep 是同一对象则只显示一份
        if self._fast_caller is self._deep_caller:
            return {"fast": fast_stats, "deep": fast_stats, "shared": True}
        deep_stats = self._deep_caller.get_stats()
        return {
            "fast": fast_stats,
            "deep": deep_stats,
            "shared": False,
        }
