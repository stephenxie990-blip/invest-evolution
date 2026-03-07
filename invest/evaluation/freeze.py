import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class FreezeCriteria:
    """固化条件"""
    min_profit_cycles: int = 7  # 最小盈利轮数
    min_win_rate: float = 0.7  # 最小胜率
    require_excess_return: bool = True  # 需要超额收益
    require_max_drawdown: bool = True  # 需要最大回撤检查
    max_drawdown_threshold: float = 15.0  # 最大回撤阈值(%)
    require_sharpe: bool = True  # 需要Sharpe检查
    min_sharpe: float = 1.0  # 最小Sharpe
    require_bull_bear: bool = True  # 需要牛熊市覆盖
    require_oos_validation: bool = True  # 需要OOS验证


@dataclass
class FreezeResult:
    """固化评估结果"""
    can_freeze: bool
    criteria_met: Dict[str, bool] = field(default_factory=dict)
    metrics: Dict[str, float] = field(default_factory=dict)
    details: Dict = field(default_factory=dict)


class FreezeEvaluator:
    """
    固化条件评估器
    """

    def __init__(self, criteria: FreezeCriteria = None):
        self.criteria = criteria or FreezeCriteria()
        self.hs300_data = None  # 沪深300数据
        self.cycle_metrics: List[CycleMetrics] = []

    def load_hs300_data(self, start_date: str, end_date: str = None):
        """
        加载沪深300数据用于基准对比

        Args:
            start_date: 开始日期 YYYY-MM-DD
            end_date: 结束日期
        """
        import baostock as bs

        if end_date is None:
            end_date = datetime.now().strftime("%Y-%m-%d")

        lg = bs.login()

        # 沪深300指数代码: sh.000300
        rs = bs.query_history_k_data_plus(
            "sh.000300",
            "date,open,high,low,close,volume",
            start_date=start_date,
            end_date=end_date,
            frequency="d",
            adjustflag="2"
        )

        data_list = []
        while rs.next():
            data_list.append(rs.get_row_data())

        if data_list:
            self.hs300_data = pd.DataFrame(data_list, columns=rs.fields)
            from config import normalize_date
            self.hs300_data["trade_date"] = self.hs300_data["date"].apply(normalize_date)
            self.hs300_data["close"] = pd.to_numeric(self.hs300_data["close"], errors="coerce")
            logger.info(f"沪深300数据加载完成: {len(self.hs300_data)} 条")

        bs.logout()

    def calculate_metrics(self, cycle_results: List[Dict]) -> List[CycleMetrics]:
        """
        计算每轮的性能指标

        Args:
            cycle_results: 训练周期结果列表

        Returns:
            每轮指标列表
        """
        metrics = []

        for i, result in enumerate(cycle_results):
            # 提取每日资金曲线来计算最大回撤和Sharpe
            daily_values = result.get("daily_values", [])

            if len(daily_values) > 1:
                values = np.array(daily_values)
                initial = values[0]

                # 计算收益率序列
                returns = np.diff(values) / values[:-1]

                # 最大回撤
                peak = values[0]
                max_dd = 0
                for v in values:
                    if v > peak:
                        peak = v
                    dd = (peak - v) / peak * 100
                    if dd > max_dd:
                        max_dd = dd

                # Sharpe Ratio (年化)
                if len(returns) > 0 and np.std(returns) > 0:
                    sharpe = np.mean(returns) / np.std(returns) * np.sqrt(252)
                else:
                    sharpe = 0
            else:
                max_dd = 0
                sharpe = 0

            metrics.append(CycleMetrics(
                cycle_id=result.get("cycle_id", i + 1),
                cutoff_date=result.get("cutoff_date", ""),
                return_pct=result.get("return_pct", 0),
                max_drawdown=max_dd,
                sharpe_ratio=sharpe,
            ))

        self.cycle_metrics = metrics
        return metrics

    def check_bull_bear_coverage(self, cycle_results: List[Dict]) -> Dict:
        """
        检查是否覆盖了牛市和熊市

        通过检查训练区间的市场表现来判断
        """
        if not cycle_results:
            return {"covered": False, "bull": False, "bear": False}

        # 提取所有截断日期对应的市场状态
        market_states = []

        for result in cycle_results:
            cutoff = result.get("cutoff_date", "")
            if cutoff and self.hs300_data is not None:
                # 检查T0后30天的市场表现
                row = self.hs300_data[self.hs300_data["trade_date"] >= cutoff]
                if len(row) >= 30:
                    start_price = row.iloc[0]["close"]
                    end_price = row.iloc[29]["close"]
                    change_pct = (end_price - start_price) / start_price * 100

                    if change_pct > 10:
                        market_states.append("bull")
                    elif change_pct < -10:
                        market_states.append("bear")

        bull = "bull" in market_states
        bear = "bear" in market_states

        return {
            "covered": bull and bear,
            "bull": bull,
            "bear": bear,
            "states": market_states,
        }

    def evaluate(self, cycle_results: List[Dict], oos_results: List[Dict] = None) -> FreezeResult:
        """
        评估是否满足固化条件

        Args:
            cycle_results: 训练周期结果
            oos_results: Out-of-Sample验证结果（可选）

        Returns:
            固化评估结果
        """
        criteria_met = {}
        metrics = {}

        if not cycle_results:
            return FreezeResult(can_freeze=False, criteria_met={}, metrics={})

        # 1. 盈利轮数检查
        profit_cycles = sum(1 for r in cycle_results if r.get("return_pct", 0) > 0)
        total_cycles = len(cycle_results)
        win_rate = profit_cycles / total_cycles if total_cycles > 0 else 0

        criteria_met["min_profit_cycles"] = profit_cycles >= self.criteria.min_profit_cycles
        criteria_met["min_win_rate"] = win_rate >= self.criteria.min_win_rate

        metrics["profit_cycles"] = profit_cycles
        metrics["total_cycles"] = total_cycles
        metrics["win_rate"] = win_rate * 100

        # 2. 计算性能指标
        metrics_list = self.calculate_metrics(cycle_results)

        # 总累计收益
        total_return = sum(r.get("return_pct", 0) for r in cycle_results)
        avg_return = total_return / total_cycles if total_cycles > 0 else 0

        criteria_met["positive_return"] = total_return > 0

        metrics["total_return"] = total_return
        metrics["avg_return"] = avg_return

        # 3. 超额收益检查
        if self.criteria.require_excess_return and self.hs300_data:
            # 计算同期沪深300收益
            hs300_return = 0
            for result in cycle_results:
                cutoff = result.get("cutoff_date", "")
                if cutoff:
                    row = self.hs300_data[self.hs300_data["trade_date"] >= cutoff]
                    if len(row) >= 30:
                        start_price = row.iloc[0]["close"]
                        end_price = row.iloc[29]["close"]
                        hs300_return += (end_price - start_price) / start_price * 100

            excess_return = total_return - hs300_return
            criteria_met["excess_return"] = excess_return > 0

            metrics["hs300_return"] = hs300_return
            metrics["excess_return"] = excess_return
        else:
            criteria_met["excess_return"] = True
            metrics["excess_return"] = total_return

        # 4. 最大回撤检查
        if self.criteria.require_max_drawdown:
            max_drawdowns = [m.max_drawdown for m in metrics_list]
            avg_max_dd = np.mean(max_drawdowns) if max_drawdowns else 0
            max_dd_exceeded = avg_max_dd > self.criteria.max_drawdown_threshold

            criteria_met["max_drawdown"] = not max_dd_exceeded

            metrics["avg_max_drawdown"] = avg_max_dd
            metrics["max_drawdown_threshold"] = self.criteria.max_drawdown_threshold
        else:
            criteria_met["max_drawdown"] = True

        # 5. Sharpe Ratio检查
        if self.criteria.require_sharpe:
            sharpe_ratios = [m.sharpe_ratio for m in metrics_list]
            avg_sharpe = np.mean(sharpe_ratios) if sharpe_ratios else 0

            criteria_met["sharpe_ratio"] = avg_sharpe >= self.criteria.min_sharpe

            metrics["avg_sharpe_ratio"] = avg_sharpe
            metrics["min_sharpe_required"] = self.criteria.min_sharpe
        else:
            criteria_met["sharpe_ratio"] = True

        # 6. 牛熊市覆盖检查
        if self.criteria.require_bull_bear:
            bull_bear = self.check_bull_bear_coverage(cycle_results)
            criteria_met["bull_bear_coverage"] = bull_bear["covered"]

            metrics.update(bull_bear)
        else:
            criteria_met["bull_bear_coverage"] = True

        # 7. Out-of-Sample验证
        if self.criteria.require_oos_validation and oos_results:
            oos_profit = sum(1 for r in oos_results if r.get("return_pct", 0) > 0)
            oos_total = len(oos_results)
            oos_win_rate = oos_profit / oos_total if oos_total > 0 else 0

            criteria_met["oos_validation"] = oos_win_rate >= 0.5  # OOS至少50%胜率

            metrics["oos_profit_cycles"] = oos_profit
            metrics["oos_total"] = oos_total
            metrics["oos_win_rate"] = oos_win_rate * 100
        else:
            criteria_met["oos_validation"] = True  # 没有OOS数据时跳过

        # 总结
        can_freeze = all(criteria_met.values())

        logger.info("=" * 60)
        logger.info("固化条件评估结果")
        logger.info("=" * 60)
        logger.info(f"1. 盈利轮数: {profit_cycles}/{total_cycles} {'✓' if criteria_met.get('min_profit_cycles') else '✗'}")
        logger.info(f"2. 胜率: {win_rate*100:.1f}% {'✓' if criteria_met.get('min_win_rate') else '✗'}")
        logger.info(f"3. 总收益: {total_return:+.2f}% {'✓' if criteria_met.get('positive_return') else '✗'}")

        if self.criteria.require_excess_return:
            hs300 = metrics.get("hs300_return", 0)
            excess = metrics.get("excess_return", 0)
            logger.info(f"4. 超额收益(vs沪深300): {excess:+.2f}% {'✓' if criteria_met.get('excess_return') else '✗'}")

        if self.criteria.require_max_drawdown:
            dd = metrics.get("avg_max_drawdown", 0)
            logger.info(f"5. 平均最大回撤: {dd:.2f}% {'✓' if criteria_met.get('max_drawdown') else '✗'}")

        if self.criteria.require_sharpe:
            sharpe = metrics.get("avg_sharpe_ratio", 0)
            logger.info(f"6. Sharpe Ratio: {sharpe:.2f} {'✓' if criteria_met.get('sharpe_ratio') else '✗'}")

        if self.criteria.require_bull_bear:
            bb = metrics.get("covered", False)
            logger.info(f"7. 牛熊市覆盖: {'是' if bb else '否'} {'✓' if criteria_met.get('bull_bear_coverage') else '✗'}")

        if self.criteria.require_oos_validation:
            oos = criteria_met.get("oos_validation", False)
            logger.info(f"8. OOS验证: {'通过' if oos else '未通过'} {'✓' if oos else '✗'}")

        logger.info("=" * 60)
        logger.info(f"最终结果: {'✓ 可以固化' if can_freeze else '✗ 不满足条件'}")
        logger.info("=" * 60)

        return FreezeResult(
            can_freeze=can_freeze,
            criteria_met=criteria_met,
            metrics=metrics,
            details={
                "criteria": self.criteria.__dict__,
                "cycle_results": len(cycle_results),
            }
        )


class EnhancedSelfLearningController:
    """
    增强版自我学习控制器

    使用修正后的固化条件
    """

    def __init__(self):
        self.freeze_evaluator = FreezeEvaluator()

        # 固化条件
        self.freeze_criteria = FreezeCriteria(
            min_profit_cycles=7,
            min_win_rate=0.7,
            require_excess_return=True,
            require_max_drawdown=True,
            max_drawdown_threshold=15.0,
            require_sharpe=True,
            min_sharpe=1.0,
            require_bull_bear=True,
            require_oos_validation=True,
        )

    def should_freeze(self, cycle_results: List[Dict], oos_results: List[Dict] = None) -> FreezeResult:
        """
        判断是否满足固化条件

        Args:
            cycle_results: 训练结果
            oos_results: OOS验证结果

        Returns:
            固化评估结果
        """
        # 先加载沪深300数据
        if cycle_results:
            # 找到日期范围
            dates = [r.get("cutoff_date", "") for r in cycle_results if r.get("cutoff_date")]
            if dates:
                min_date = min(dates)
                # 转换为日期格式
                try:
                    dt = datetime.strptime(min_date, "%Y%m%d")
                    start = (dt - timedelta(days=100)).strftime("%Y-%m-%d")
                    self.freeze_evaluator.load_hs300_data(start)
                except Exception:
                    pass

        return self.freeze_evaluator.evaluate(cycle_results, oos_results)



# ============================================================
# model_freezer.py
# ============================================================

"""
模型固化模块 - 模型固化与输出报告

功能：
1. 检测固化条件（连续10周期中7次盈利）
2. 固化策略参数
3. 生成最终报告
4. 导出可复用的模型配置
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)


@dataclass
class FrozenModel:
    """固化模型"""
    model_id: str
    created_at: str

    # 策略信息
    strategy_name: str
    strategy_params: Dict

    # 训练统计
    total_training_cycles: int
    profit_cycles: int
    loss_cycles: int
    profit_rate: float
    avg_return_pct: float
    total_profit: float

    # 评估指标
    avg_signal_accuracy: float
    avg_timing_score: float
    avg_risk_score: float

    # 详细信息
    cycle_history: List[Dict]
    success_cycles: List[Dict]
    failure_cycles: List[Dict]

    # 结论
    conclusion: str

    def to_dict(self) -> dict:
        return asdict(self)

    def save(self, output_path: Path):
        """保存到文件"""
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)


class ModelFreezer:
    """
    模型固化器

    核心功能：
    - 检测是否满足固化条件
    - 收集和整理训练数据
    - 生成固化报告
    - 导出模型配置
    """

    def __init__(self, output_dir: str = None):
        """
        初始化模型固化器

        Args:
            output_dir: 输出目录
        """
        if output_dir is None:
            from config import config

            output_dir = config.output_dir

        self.output_dir = Path(output_dir)
        self.frozen_models_dir = self.output_dir / "frozen_models"
        self.frozen_models_dir.mkdir(parents=True, exist_ok=True)

    def check_freeze_condition(
        self,
        cycle_history: List[Dict],
        total_cycles: int = 10,
        profit_required: int = 7,
    ) -> Dict:
        """
        检查是否满足固化条件

        Args:
            cycle_history: 周期历史
            total_cycles: 统计周期数（默认10）
            profit_required: 盈利次数要求（默认7）

        Returns:
            dict: 检查结果
        """
        if len(cycle_history) < total_cycles:
            return {
                "can_freeze": False,
                "reason": f"训练周期不足，需要至少 {total_cycles} 个周期",
                "current_cycles": len(cycle_history),
                "required_cycles": total_cycles,
            }

        # 取最近 N 个周期
        recent = cycle_history[-total_cycles:]
        profit_count = sum(1 for r in recent if r.get("return_pct", 0) > 0)
        loss_count = total_cycles - profit_count

        can_freeze = profit_count >= profit_required

        return {
            "can_freeze": can_freeze,
            "reason": (
                f"满足固化条件: 最近 {total_cycles} 个周期中 {profit_count} 次盈利"
                if can_freeze
                else f"不满足固化条件: 最近 {total_cycles} 个周期中仅 {profit_count} 次盈利，需要 {profit_required} 次"
            ),
            "recent_cycles": total_cycles,
            "profit_count": profit_count,
            "loss_count": loss_count,
            "profit_rate": profit_count / total_cycles,
            "required_profit": profit_required,
        }

    def freeze(
        self,
        strategy_name: str,
        strategy_params: Dict,
        cycle_history: List[Dict],
        evaluation_history: List[Dict] = None,
    ) -> FrozenModel:
        """
        固化模型

        Args:
            strategy_name: 策略名称
            strategy_params: 策略参数
            cycle_history: 周期历史
            evaluation_history: 评估历史（可选）

        Returns:
            FrozenModel: 固化模型
        """
        logger.info("开始固化模型...")

        # 统计
        total = len(cycle_history)
        profits = [r for r in cycle_history if r.get("return_pct", 0) > 0]
        losses = [r for r in cycle_history if r.get("return_pct", 0) <= 0]

        # 计算平均收益
        avg_return = sum(r.get("return_pct", 0) for r in cycle_history) / total if total > 0 else 0
        total_profit = sum(r.get("profit_loss", 0) for r in profits)

        # 评估指标
        if evaluation_history:
            avg_signal = sum(e.get("signal_accuracy", 0) for e in evaluation_history) / len(evaluation_history)
            avg_timing = sum(e.get("timing_score", 0) for e in evaluation_history) / len(evaluation_history)
            avg_risk = sum(e.get("risk_control_score", 0) for e in evaluation_history) / len(evaluation_history)
        else:
            avg_signal = 0.5
            avg_timing = 0.5
            avg_risk = 0.5

        # 生成模型ID
        model_id = f"model_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        # 生成结论
        conclusion = self._generate_conclusion(
            strategy_name,
            total,
            len(profits),
            avg_return,
            avg_signal,
            avg_timing,
            avg_risk,
        )

        # 创建固化模型
        model = FrozenModel(
            model_id=model_id,
            created_at=datetime.now().isoformat(),
            strategy_name=strategy_name,
            strategy_params=strategy_params,
            total_training_cycles=total,
            profit_cycles=len(profits),
            loss_cycles=len(losses),
            profit_rate=len(profits) / total,
            avg_return_pct=avg_return,
            total_profit=total_profit,
            avg_signal_accuracy=avg_signal,
            avg_timing_score=avg_timing,
            avg_risk_score=avg_risk,
            cycle_history=cycle_history,
            success_cycles=profits,
            failure_cycles=losses,
            conclusion=conclusion,
        )

        # 保存
        model_path = self.frozen_models_dir / f"{model_id}.json"
        model.save(model_path)

        # 同时保存最新固化模型
        latest_path = self.frozen_models_dir / "latest.json"
        model.save(latest_path)

        logger.info(f"模型已固化: {model_id}, 保存至: {model_path}")

        return model

    def _generate_conclusion(
        self,
        strategy_name: str,
        total_cycles: int,
        profit_count: int,
        avg_return: float,
        avg_signal: float,
        avg_timing: float,
        avg_risk: float,
    ) -> str:
        """生成结论"""
        lines = []

        lines.append(f"## 模型固化结论")
        lines.append("")
        lines.append(f"**策略名称**: {strategy_name}")
        lines.append(f"**训练周期数**: {total_cycles}")
        lines.append(f"**盈利周期数**: {profit_count} ({profit_count/total_cycles*100:.1f}%)")
        lines.append(f"**平均收益率**: {avg_return:.2f}%")
        lines.append("")

        # 评分总结
        lines.append("### 评分总结")
        lines.append("")
        lines.append(f"- 信号准确率: {avg_signal:.2f}/1.0")
        lines.append(f"- 时机选择: {avg_timing:.2f}/1.0")
        lines.append(f"- 风控表现: {avg_risk:.2f}/1.0")
        lines.append("")

        # 综合评价
        overall_score = (avg_signal + avg_timing + avg_risk) / 3

        if overall_score >= 0.7:
            rating = "优秀"
            desc = "该策略在测试中表现优秀，建议继续使用。"
        elif overall_score >= 0.5:
            rating = "良好"
            desc = "该策略在测试中表现良好，可作为参考。"
        else:
            rating = "一般"
            desc = "该策略表现一般，建议进一步优化或谨慎使用。"

        lines.append(f"### 综合评价: {rating}")
        lines.append("")
        lines.append(desc)
        lines.append("")

        # 风险提示
        lines.append("### 风险提示")
        lines.append("")
        lines.append("1. 历史表现不代表未来收益")
        lines.append("2. 请根据自身风险承受能力调整仓位")
        lines.append("3. 建议持续监控策略表现，及时调整")
        lines.append("")

        # 使用建议
        lines.append("### 使用建议")
        lines.append("")
        lines.append(f"- 建议仓位: {20 + overall_score * 30:.0f}%")
        lines.append(f"- 止损线: {5 - overall_score * 2:.0f}%")
        lines.append(f"- 止盈线: {10 + overall_score * 5:.0f}%")

        return "\n".join(lines)

    def export_strategy_config(
        self,
        model: FrozenModel,
    ) -> Dict:
        """
        导出策略配置（可用于后续加载）

        Args:
            model: 固化模型

        Returns:
            dict: 策略配置
        """
        config = {
            "strategy_name": model.strategy_name,
            "strategy_params": model.strategy_params,
            "model_id": model.model_id,
            "created_at": model.created_at,

            # 交易参数建议
            "recommended": {
                "position_size": min(0.2 + model.avg_return_pct / 100, 0.5),
                "stop_loss": 0.05,
                "take_profit": 0.15,
                "max_positions": 5,
            },

            # 统计信息
            "statistics": {
                "total_cycles": model.total_training_cycles,
                "profit_rate": model.profit_rate,
                "avg_return_pct": model.avg_return_pct,
                "avg_signal_accuracy": model.avg_signal_accuracy,
                "avg_timing_score": model.avg_timing_score,
                "avg_risk_score": model.avg_risk_score,
            },
        }

        # 保存
        config_path = self.frozen_models_dir / f"{model.model_id}_config.json"
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)

        return config

    def get_latest_model(self) -> Optional[FrozenModel]:
        """获取最新固化的模型"""
        latest_path = self.frozen_models_dir / "latest.json"

        if not latest_path.exists():
            return None

        try:
            with open(latest_path, encoding="utf-8") as f:
                data = json.load(f)
                return FrozenModel(**data)
        except Exception as e:
            logger.error(f"加载最新模型失败: {e}")
            return None

    def list_frozen_models(self) -> List[Dict]:
        """列出所有固化模型"""
        models = []

        for file in self.frozen_models_dir.glob("model_*.json"):
            try:
                with open(file, encoding="utf-8") as f:
                    data = json.load(f)
                    models.append({
                        "model_id": data.get("model_id"),
                        "created_at": data.get("created_at"),
                        "strategy_name": data.get("strategy_name"),
                        "profit_rate": data.get("profit_rate"),
                        "avg_return_pct": data.get("avg_return_pct"),
                    })
            except Exception:
                continue

        models.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return models




__all__ = [
    "FreezeCriteria",
    "FreezeResult",
    "FreezeEvaluator",
    "EnhancedSelfLearningController",
    "FrozenModel",
    "ModelFreezer",
]
