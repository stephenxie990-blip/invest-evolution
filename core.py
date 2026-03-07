"""
投资进化系统 - 核心基础设施

包含：
1. LLMCaller          — 统一 LLM 调用接口
2. TradingPlan        — Selector ↔ Trader 数据合同
3. make_simple_plan() — 算法生成交易计划
4. 技术指标工具函数   — RSI / MACD / 布林带 / 量比（共享，避免重复）
5. summarize_stocks() — 批量生成股票技术摘要（供 Agent Prompt 使用）
6. compute_market_stats() — 计算市场整体统计（供 MarketRegime 使用）
7. AgentTracker       — Agent 预测追踪与归因
8. TraceLog           — 决策追踪日志
"""

import json
import os
import re
import time
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional, TypedDict, Literal

import numpy as np
import pandas as pd

from config import config, normalize_date  # noqa: E402  ← 全局配置
from llm_gateway import LLMGateway, LLMGatewayError, LLMUnavailableError

logger = logging.getLogger(__name__)



# ============================================================
# Part 1: LLM 调用器
# ============================================================

class LLMCaller:
    """
    统一 LLM 调用接口

    职责：
    1. 管理 API 配置
    2. 发送请求，处理超时和重试
    3. 解析 JSON 响应
    4. 调用计数和成本追踪
    """

    def __init__(
        self,
        model: str = None,
        api_key: str = None,
        api_base: str = None,
        timeout: int = None,
        max_retries: int = None,
        dry_run: bool = False,
    ):
        self.model = model or config.llm_fast_model
        self.api_key = api_key or config.llm_api_key
        self.api_base = api_base or config.llm_api_base
        self.timeout = timeout or config.llm_timeout
        self.max_retries = max_retries or config.llm_max_retries
        self.dry_run = dry_run

        masked_key = f"{self.api_key[:4]}...{self.api_key[-4:]}" if self.api_key and len(self.api_key) > 8 else "***"
        logger.debug(f"Initialized LLMCaller with model: {self.model}, api_key: {masked_key}")

        self.gateway = LLMGateway(
            model=self.model,
            api_key=self.api_key,
            api_base=self.api_base,
            timeout=self.timeout,
            max_retries=self.max_retries,
        )

        # 统计
        self.call_count = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_time = 0.0
        self.errors = 0

    def call(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> str:
        """
        调用 LLM，返回原始文本。

        设计约束：训练链路默认“可降级不中断”。当无 key 或 provider 不可用时，
        返回空 JSON 字符串，后续 call_json 会触发 parse_error 并进入算法 fallback。
        """
        if self.dry_run:
            logger.info("[DRY RUN] LLM call skipped. Prompt length: %s", len(user_message))
            return '{"dry_run": true}'

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

        try:
            start_ts = time.time()
            response = self.gateway.completion_raw(
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            content = response.choices[0].message.content
            self.total_time += time.time() - start_ts

            self.call_count += 1
            usage = getattr(response, "usage", None)
            if usage:
                self.total_input_tokens += getattr(usage, "prompt_tokens", 0) or 0
                self.total_output_tokens += getattr(usage, "completion_tokens", 0) or 0
            return content
        except LLMUnavailableError as exc:
            self.errors += 1
            logger.warning("LLM unavailable, fallback to algorithm path: %s", exc)
            return ""
        except LLMGatewayError as exc:
            self.errors += 1
            logger.warning("LLM gateway error, fallback to algorithm path: %s", exc)
            return ""
        except Exception as exc:
            self.errors += 1
            logger.warning("Unexpected LLM error, fallback to algorithm path: %s", exc)
            return ""

    def call_json(
        self,
        system_prompt: str,
        user_message: str,
        **kwargs,
    ) -> dict:
        raw = self.call(system_prompt, user_message, **kwargs)
        return self._parse_json(raw)

    def _parse_json(self, text: str) -> dict:
        if not text or not text.strip():
            return {"_parse_error": True, "_raw": "", "_error": "llm_unavailable_or_empty"}

        block_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
        if block_match:
            try:
                return json.loads(block_match.group(1).strip())
            except json.JSONDecodeError:
                pass

        brace_match = re.search(r"\{.*\}", text, re.DOTALL)
        if brace_match:
            try:
                return json.loads(brace_match.group())
            except json.JSONDecodeError:
                pass

        logger.warning("Failed to parse JSON from LLM response: %s...", text[:200])
        return {"_parse_error": True, "_raw": text}

    def get_stats(self) -> dict:
        return {
            "call_count": self.call_count,
            "errors": self.errors,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_tokens": self.total_input_tokens + self.total_output_tokens,
            "total_time_sec": round(self.total_time, 1),
            "avg_time_sec": round(self.total_time / max(self.call_count, 1), 1),
        }


# ============================================================
# Part 2: 交易计划数据结构
# ============================================================

@dataclass
class PositionPlan:
    """单个持仓计划"""
    code: str                              # 股票代码
    priority: int                          # 优先级（1=最高）
    weight: float = 0.20                   # 目标仓位权重 (0.0-1.0)
    entry_method: str = "market"           # "market"=开盘买 / "limit"=限价
    entry_price: Optional[float] = None    # limit 时的目标买入价
    stop_loss_pct: float = 0.05            # 止损比例（正数，跌5%止损）
    take_profit_pct: float = 0.15          # 止盈比例（正数，涨15%止盈）
    trailing_pct: Optional[float] = None   # 跟踪止盈回撤比例
    expire_days: int = 5                   # limit单有效天数
    max_hold_days: int = 30                # 最长持有天数
    reason: str = ""                       # 选股理由
    source: str = "algorithm"             # 推荐来源


@dataclass
class TradingPlan:
    """
    完整交易计划

    Selector/Meeting → TradingPlan → Trader 的唯一数据合同
    Trader 只按此计划执行，不自行选股
    """
    date: str                              # 计划生成日期
    positions: List[PositionPlan] = field(default_factory=list)
    cash_reserve: float = 0.0             # 现金储备比例 (0.0-1.0)
    max_positions: int = 2                 # 最大同时持仓数
    source: str = "algorithm"             # "algorithm" / "meeting"
    reasoning: str = ""                    # 整体决策理由

    @property
    def stock_codes(self) -> List[str]:
        """返回所有计划中的股票代码"""
        return [p.code for p in self.positions]

    def get_position_plan(self, code: str) -> Optional[PositionPlan]:
        """根据股票代码获取持仓计划"""
        for p in self.positions:
            if p.code == code:
                return p
        return None


def make_simple_plan(
    selected_stocks: List[str],
    cutoff_date: str = "",
    stock_scores: Optional[Dict[str, float]] = None,
    stop_loss_pct: float = 0.05,
    take_profit_pct: float = 0.15,
    trailing_pct: float = 0.10,
    position_size: float = 0.20,
    max_positions: int = 2,
    max_hold_days: int = 30,
) -> TradingPlan:
    """
    生成简单的算法交易计划（算法兜底，不依赖 LLM）

    Args:
        selected_stocks: 股票代码列表（已按得分排序）
        cutoff_date: 截断日期
        stock_scores: {code: score}，可选
    """
    scores = stock_scores or {}
    positions = []

    for i, code in enumerate(selected_stocks):
        score = scores.get(code, 0.0)
        positions.append(PositionPlan(
            code=code,
            priority=i + 1,
            weight=position_size,
            entry_method="market",
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            trailing_pct=trailing_pct,
            max_hold_days=max_hold_days,
            reason=f"多因子选股得分:{score:.2f}",
            source="algorithm",
        ))

    plan = TradingPlan(
        date=cutoff_date,
        positions=positions,
        cash_reserve=0.0,
        max_positions=max_positions,
        source="algorithm",
        reasoning=(
            f"算法选股: {len(positions)}只候选, "
            f"最大持仓{max_positions}, "
            f"止损{stop_loss_pct:.0%}, 止盈{take_profit_pct:.0%}"
        ),
    )

    logger.info(
        f"📋 生成交易计划: {len(positions)}只候选, "
        f"最大持仓{max_positions}, "
        f"止损{stop_loss_pct:.0%}, 止盈{take_profit_pct:.0%}"
    )
    return plan


# ============================================================
# Part 3: 技术指标共享工具函数
# ============================================================

def _get_date_col(df: pd.DataFrame) -> Optional[str]:
    """获取 DataFrame 的日期列名（适配 trade_date / date）"""
    if "trade_date" in df.columns:
        return "trade_date"
    if "date" in df.columns:
        return "date"
    return None


def _filter_by_cutoff(df: pd.DataFrame, cutoff_norm: str) -> pd.DataFrame:
    """按截断日期过滤 DataFrame（cutoff_norm 格式 YYYYMMDD）"""
    date_col = _get_date_col(df)
    if date_col is None:
        return pd.DataFrame()
    dates_norm = df[date_col].apply(normalize_date)
    return df.loc[dates_norm <= cutoff_norm].copy()


def compute_rsi(close: pd.Series, period: int = 14) -> float:
    """计算 RSI（共享工具，供多处调用）"""
    if len(close) < period + 1:
        return 50.0
    delta = close.diff().iloc[-(period + 1):]
    gain = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    last_gain = gain.iloc[-1]
    last_loss = loss.iloc[-1]
    if last_loss == 0:
        return 100.0 if last_gain > 0 else 50.0
    return float(100 - (100 / (1 + last_gain / last_loss)))


def compute_macd_signal(close: pd.Series) -> str:
    """计算 MACD 信号字符串（金叉/死叉/看多/看空/中性）"""
    if len(close) < 26:
        return "中性"
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    curr_m, curr_s = macd.iloc[-1], signal.iloc[-1]
    prev_m, prev_s = macd.iloc[-2], signal.iloc[-2]
    if prev_m <= prev_s and curr_m > curr_s:
        return "金叉"
    if prev_m >= prev_s and curr_m < curr_s:
        return "死叉"
    if curr_m > curr_s and curr_m > 0:
        return "看多"
    if curr_m < curr_s and curr_m < 0:
        return "看空"
    return "中性"


def compute_bb_position(close: pd.Series, period: int = 20) -> float:
    """计算布林带位置（0=下轨，1=上轨）"""
    if len(close) < period:
        return 0.5
    recent = close.iloc[-period:]
    sma = recent.mean()
    std = recent.std()
    if std == 0:
        return 0.5
    upper = sma + 2 * std
    lower = sma - 2 * std
    pos = (float(close.iloc[-1]) - lower) / (upper - lower) if upper != lower else 0.5
    return max(0.0, min(1.0, pos))


def compute_volume_ratio(df: pd.DataFrame) -> float:
    """计算量比（5日均量 / 20日均量）"""
    if "volume" not in df.columns:
        return 1.0
    vol = pd.to_numeric(df["volume"], errors="coerce").dropna()
    if len(vol) < 20:
        return 1.0
    avg_5 = vol.iloc[-5:].mean()
    avg_20 = vol.iloc[-20:].mean()
    return float(avg_5 / avg_20) if avg_20 > 0 else 1.0


def compute_pct_change(latest: float, series: pd.Series, n: int) -> float:
    """计算 N 日涨跌幅（%）"""
    if len(series) < n:
        return 0.0
    past = float(series.iloc[-n])
    return (latest / past - 1) * 100 if past > 0 else 0.0


def compute_algo_score(
    change_5d: float,
    change_20d: float,
    ma_trend: str,
    rsi: float,
    macd_signal: str,
    bb_pos: float,
) -> float:
    """综合算法评分（用于兜底排序）"""
    score = 0.0
    score += max(-1, min(1, change_5d / 10)) * 0.15
    score += max(-1, min(1, change_20d / 20)) * 0.15
    if ma_trend == "多头":
        score += 0.2
    elif ma_trend == "空头":
        score -= 0.1
    if 40 <= rsi <= 60:
        score += 0.15
    elif rsi < 30:
        score += 0.05
    elif rsi > 70:
        score -= 0.1
    macd_scores = {"金叉": 0.2, "看多": 0.1, "中性": 0, "看空": -0.1, "死叉": -0.15}
    score += macd_scores.get(macd_signal, 0)
    if bb_pos < 0.3:
        score += 0.15
    elif bb_pos > 0.8:
        score -= 0.1
    return score


# ============================================================
# Part 4: 股票技术摘要
# ============================================================

def summarize_stocks(
    stock_data: Dict[str, pd.DataFrame],
    codes: List[str],
    cutoff_date: str,
) -> List[dict]:
    """
    批量计算股票技术摘要，供 Agent Prompt 使用

    Args:
        stock_data: {code: DataFrame}
        codes: 要分析的股票代码列表
        cutoff_date: 截断日期 (YYYYMMDD 或 YYYY-MM-DD)

    Returns:
        list[dict]: 每只股票的技术摘要，按 algo_score 降序
    """
    cutoff_norm = normalize_date(cutoff_date)
    results = []

    for code in codes:
        df = stock_data.get(code)
        if df is None:
            continue
        summary = _compute_stock_summary(df, code, cutoff_norm)
        if summary:
            results.append(summary)

    results.sort(key=lambda x: x.get("algo_score", 0), reverse=True)
    return results


def _compute_stock_summary(df: pd.DataFrame, code: str, cutoff_norm: str) -> Optional[dict]:
    """计算单只股票的技术摘要"""
    try:
        sub = _filter_by_cutoff(df, cutoff_norm)
        if len(sub) < 30:
            return None

        close = pd.to_numeric(sub["close"], errors="coerce").dropna()
        if len(close) < 30 or close.iloc[-1] <= 0:
            return None

        latest = float(close.iloc[-1])

        change_5d = compute_pct_change(latest, close, 5)
        change_20d = compute_pct_change(latest, close, 20)

        ma5 = float(close.iloc[-5:].mean()) if len(close) >= 5 else latest
        ma20 = float(close.iloc[-20:].mean()) if len(close) >= 20 else latest
        if ma5 > ma20 * 1.01:
            ma_trend = "多头"
        elif ma5 < ma20 * 0.99:
            ma_trend = "空头"
        else:
            ma_trend = "交叉"

        rsi = compute_rsi(close, 14)
        macd_signal = compute_macd_signal(close)
        bb_pos = compute_bb_position(close, 20)
        vol_ratio = compute_volume_ratio(sub)
        returns = close.pct_change().dropna()
        volatility = float(returns.iloc[-20:].std()) if len(returns) >= 20 else 0.0
        algo_score = compute_algo_score(change_5d, change_20d, ma_trend, rsi, macd_signal, bb_pos)

        return {
            "code": code,
            "close": round(latest, 2),
            "change_5d": round(change_5d, 2),
            "change_20d": round(change_20d, 2),
            "ma_trend": ma_trend,
            "rsi": round(rsi, 1),
            "macd": macd_signal,
            "bb_pos": round(bb_pos, 2),
            "vol_ratio": round(vol_ratio, 2),
            "volatility": round(volatility, 4),
            "algo_score": round(algo_score, 3),
        }
    except Exception as e:
        logger.debug(f"摘要计算失败 {code}: {e}")
        return None


def format_stock_table(summaries: List[dict]) -> str:
    """将股票摘要格式化为 Markdown 表格（给 LLM 看的）"""
    if not summaries:
        return "（无候选股票）"

    lines = [
        "| # | 代码 | 收盘价 | 5日涨跌% | 20日涨跌% | MA趋势 | RSI | MACD信号 | BB位置 | 量比 |",
        "|---|------|--------|----------|-----------|--------|-----|----------|--------|------|",
    ]
    for i, s in enumerate(summaries):
        lines.append(
            f"| {i+1} "
            f"| {s['code']} "
            f"| {s['close']:.1f} "
            f"| {s['change_5d']:+.1f} "
            f"| {s['change_20d']:+.1f} "
            f"| {s['ma_trend']} "
            f"| {s['rsi']:.0f} "
            f"| {s['macd']} "
            f"| {s['bb_pos']:.2f} "
            f"| {s['vol_ratio']:.1f} |"
        )
    return "\n".join(lines)


# ============================================================
# Part 5: 市场统计（供 MarketRegime 使用）
# ============================================================

def compute_market_stats(
    stock_data: Dict[str, pd.DataFrame],
    cutoff_date: str,
    min_valid: Optional[int] = None,
) -> dict:
    """
    计算市场整体统计摘要

    Args:
        stock_data: {code: DataFrame}
        cutoff_date: 截断日期
        min_valid: 有效股票最低数量（默认None=动态调整：测试环境1，小规模3，大规模5%）

    Returns:
        dict: 市场统计量，供 MarketRegime 判断
    """
    total = len(stock_data)
    if total == 0:
        return _empty_market_stats()

    # 动态调整阈值
    if min_valid is None:
        if total <= 10:
            min_valid = 1  # 测试环境
        elif total <= 100:
            min_valid = 3  # 小规模
        else:
            min_valid = max(10, int(total * 0.05))  # 大规模：至少10只或5%

    cutoff_norm = normalize_date(cutoff_date)
    changes_5d, changes_20d, volatilities = [], [], []
    above_ma20 = 0
    valid_count = 0

    for code, df in stock_data.items():
        try:
            sub = _filter_by_cutoff(df, cutoff_norm)
            if len(sub) < 30:
                continue
            close = pd.to_numeric(sub["close"], errors="coerce").dropna()
            if len(close) < 30 or close.iloc[-1] <= 0:
                continue

            latest = float(close.iloc[-1])
            c5 = compute_pct_change(latest, close, 5)
            c20 = compute_pct_change(latest, close, 20)
            ma20 = float(close.iloc[-20:].mean()) if len(close) >= 20 else latest
            vol = float(close.pct_change().dropna().iloc[-20:].std()) if len(close) >= 20 else 0.0

            valid_count += 1
            changes_5d.append(c5)
            changes_20d.append(c20)
            volatilities.append(vol)
            if latest > ma20:
                above_ma20 += 1
        except Exception:
            continue

    if valid_count < min_valid:
        logger.warning(f"有效股票数过少: {valid_count}/{total}，使用默认统计")
        return _empty_market_stats()

    arr5 = np.array(changes_5d)
    arr20 = np.array(changes_20d)

    result = {
        "total_stocks": total,
        "valid_stocks": valid_count,
        "advance_ratio_5d": float(np.mean(arr5 > 0)),
        "avg_change_5d": float(np.mean(arr5)),
        "median_change_5d": float(np.median(arr5)),
        "avg_change_20d": float(np.mean(arr20)),
        "median_change_20d": float(np.median(arr20)),
        "above_ma20_ratio": above_ma20 / valid_count,
        "avg_volatility": float(np.mean(volatilities)),
        "cutoff_date": cutoff_norm,
    }

    logger.info(
        f"📊 市场统计: {valid_count}只有效 | "
        f"5日中位{result['median_change_5d']:+.2f}% | "
        f"20日中位{result['median_change_20d']:+.2f}% | "
        f"站上MA20 {result['above_ma20_ratio']:.0%}"
    )
    return result


def _empty_market_stats() -> dict:
    """数据不足时的默认市场统计"""
    return {
        "total_stocks": 0,
        "valid_stocks": 0,
        "advance_ratio_5d": 0.5,
        "avg_change_5d": 0.0,
        "median_change_5d": 0.0,
        "avg_change_20d": 0.0,
        "median_change_20d": 0.0,
        "above_ma20_ratio": 0.5,
        "avg_volatility": 0.02,
        "cutoff_date": "",
    }


# ============================================================
# Part 6: Agent 预测追踪器
# ============================================================

@dataclass
class PredictionRecord:
    """单条 Agent 预测记录"""
    cycle: int
    agent: str           # "trend_hunter" / "contrarian" / ...
    code: str            # 股票代码
    score: float         # Agent 给的评分 (0-1)
    stop_loss_pct: float
    take_profit_pct: float
    reasoning: str = ""

    # 交易结束后填入
    actual_return: Optional[float] = None  # 实际盈亏（绝对值）
    was_selected: bool = False             # 是否被 Commander 选中
    was_profitable: bool = False           # 是否盈利


class AgentTracker:
    """
    Agent 预测追踪器

    记录每个 Agent 的推荐，交易结束后与实际结果对账
    为复盘会议提供事实数据
    """

    def __init__(self):
        self.predictions: List[PredictionRecord] = []
        self._by_cycle: Dict[int, List[PredictionRecord]] = {}

    def record_predictions(self, cycle: int, agent_name: str, picks: List[dict]):
        """记录一个 Agent 的推荐（picks = Agent.analyze() 的 picks 列表）"""
        for p in picks:
            record = PredictionRecord(
                cycle=cycle,
                agent=agent_name,
                code=p.get("code", ""),
                score=p.get("score", 0.5),
                stop_loss_pct=p.get("stop_loss_pct", 0.05),
                take_profit_pct=p.get("take_profit_pct", 0.15),
                reasoning=p.get("reasoning", ""),
            )
            self.predictions.append(record)
            self._by_cycle.setdefault(cycle, []).append(record)

    def mark_selected(self, cycle: int, selected_codes: List[str]):
        """标记哪些推荐被 Commander 选中"""
        selected_set = set(selected_codes)
        for record in self._by_cycle.get(cycle, []):
            record.was_selected = record.code in selected_set

    def record_outcomes(self, cycle: int, per_stock_pnl: Dict[str, float]):
        """记录实际交易结果（per_stock_pnl = {code: 盈亏金额}）"""
        for record in self._by_cycle.get(cycle, []):
            if record.code in per_stock_pnl:
                pnl = per_stock_pnl[record.code]
                record.actual_return = pnl
                record.was_profitable = pnl > 0

    def compute_accuracy(
        self,
        agent_name: str = None,
        last_n_cycles: int = None,
    ) -> dict:
        """
        计算 Agent 预测准确率

        Returns:
            {agent_name: {total_picks, selected_count, traded_count,
                          profitable_count, accuracy, avg_score}}
        """
        records = self.predictions

        if last_n_cycles is not None and self._by_cycle:
            recent = set(sorted(self._by_cycle.keys())[-last_n_cycles:])
            records = [r for r in records if r.cycle in recent]

        if agent_name:
            records = [r for r in records if r.agent == agent_name]

        agent_records: Dict[str, List[PredictionRecord]] = {}
        for r in records:
            agent_records.setdefault(r.agent, []).append(r)

        stats = {}
        for name, recs in agent_records.items():
            total = len(recs)
            selected = sum(1 for r in recs if r.was_selected)
            traded = sum(1 for r in recs if r.actual_return is not None)
            profitable = sum(1 for r in recs if r.was_profitable)
            avg_score = sum(r.score for r in recs) / total if total > 0 else 0

            stats[name] = {
                "total_picks": total,
                "selected_count": selected,
                "traded_count": traded,
                "profitable_count": profitable,
                "accuracy": profitable / traded if traded > 0 else 0.0,
                "avg_score": round(avg_score, 3),
            }
        return stats

    def get_cycle_summary(self, cycle: int) -> dict:
        """获取单个 cycle 的预测摘要（按 Agent 分组）"""
        by_agent = {}
        for r in self._by_cycle.get(cycle, []):
            by_agent.setdefault(r.agent, []).append({
                "code": r.code,
                "score": r.score,
                "selected": r.was_selected,
                "profitable": r.was_profitable,
                "actual_return": r.actual_return,
            })
        return by_agent

    def get_summary(self) -> dict:
        """获取总体摘要"""
        return {
            "total_predictions": len(self.predictions),
            "total_cycles": len(self._by_cycle),
            "accuracy_by_agent": self.compute_accuracy(),
        }


# ============================================================
# Part 7: 决策追踪日志
# ============================================================

class TraceLog:
    """
    决策追踪日志

    记录每轮决策的全过程，便于复盘和调试
    """

    def __init__(self, log_dir: str = None):
        self.log_dir = log_dir or str(config.logs_dir / "trace")
        self.current_round = None
        self.round_data = {}

    def start_round(self, round_id: int, t0_date: str):
        """开始一轮"""
        self.current_round = round_id
        self.round_data = {
            "round_id": round_id,
            "t0_date": t0_date,
            "start_time": datetime.now().isoformat(),
            "steps": [],
        }

    def log_step(self, step_name: str, data: Dict):
        """记录步骤"""
        self.round_data["steps"].append({
            "step": step_name,
            "timestamp": datetime.now().isoformat(),
            "data": data,
        })

    def log_decision(self, decision: Dict):
        """记录决策"""
        self.round_data["decision"] = decision

    def log_result(self, result: Dict):
        """记录结果"""
        self.round_data["result"] = result
        self.round_data["end_time"] = datetime.now().isoformat()

    def save(self):
        """保存本轮日志"""
        if not self.current_round:
            return
        os.makedirs(self.log_dir, exist_ok=True)
        filepath = os.path.join(self.log_dir, f"round_{self.current_round:04d}.json")
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.round_data, f, ensure_ascii=False, indent=2, default=str)
        self.current_round = None
        self.round_data = {}

    def load_round(self, round_id: int) -> Dict:
        """加载某轮日志"""
        filepath = os.path.join(self.log_dir, f"round_{round_id:04d}.json")
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}
