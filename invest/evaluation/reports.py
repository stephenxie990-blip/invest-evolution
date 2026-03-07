import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class StrategyCase:
    """策略案例"""
    case_id: str
    case_type: str  # "success" or "failure"

    # 策略参数
    strategy_name: str
    strategy_params: Dict

    # 训练结果
    cycle_id: int
    cutoff_date: str
    initial_capital: float
    final_value: float
    return_pct: float
    profit_loss: float

    # 选股结果
    selected_stocks: List[str]

    # 交易统计
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float

    # 时间戳
    created_at: str

    # 原因
    reason: str  # "连续3次盈利" or "连续3次亏损"

    # 元数据
    tags: List[str] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def generate_id(params: Dict) -> str:
        """根据策略参数生成唯一ID"""
        params_str = json.dumps(params, sort_keys=True)
        return hashlib.md5(params_str.encode()).hexdigest()[:12]


class CaseLibrary:
    """
    案例库管理器

    功能：
    - 存储成功/失败案例
    - 查询策略是否被禁用
    - 统计案例库信息
    """

    def __init__(self, library_dir: str = None):
        """
        初始化案例库

        Args:
            library_dir: 案例库目录路径
        """
        if library_dir is None:
            from config import config

            library_dir = config.case_library_dir

        self.library_dir = Path(library_dir)
        self.library_dir.mkdir(parents=True, exist_ok=True)

        # 文件路径
        self.success_file = self.library_dir / "success_cases.json"
        self.failure_file = self.library_dir / "failure_cases.json"

        # 加载已有案例
        self.success_cases: List[StrategyCase] = self._load_cases(self.success_file)
        self.failure_cases: List[StrategyCase] = self._load_cases(self.failure_file)

        logger.info(
            f"案例库初始化: 成功案例 {len(self.success_cases)} 个, "
            f"失败案例 {len(self.failure_cases)} 个"
        )

    def _load_cases(self, file_path: Path) -> List[StrategyCase]:
        """加载案例"""
        if not file_path.exists():
            return []

        try:
            with open(file_path, encoding="utf-8") as f:
                data = json.load(f)
                return [StrategyCase(**case) for case in data]
        except Exception as e:
            logger.warning(f"加载案例失败: {file_path}, {e}")
            return []

    def _save_cases(self, file_path: Path, cases: List[StrategyCase]):
        """保存案例"""
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump([c.to_dict() for c in cases], f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存案例失败: {file_path}, {e}")

    def _save(self):
        """保存所有案例"""
        self._save_cases(self.success_file, self.success_cases)
        self._save_cases(self.failure_file, self.failure_cases)

    def add_success_case(
        self,
        strategy_name: str,
        strategy_params: Dict,
        cycle_result: Dict,
        reason: str = "连续3次盈利",
    ) -> StrategyCase:
        """
        添加成功案例

        Args:
            strategy_name: 策略名称
            strategy_params: 策略参数
            cycle_result: 周期结果
            reason: 原因

        Returns:
            StrategyCase: 创建的案例
        """
        case = StrategyCase(
            case_id=StrategyCase.generate_id(strategy_params),
            case_type="success",
            strategy_name=strategy_name,
            strategy_params=strategy_params,
            cycle_id=cycle_result.get("cycle_id", 0),
            cutoff_date=cycle_result.get("cutoff_date", ""),
            initial_capital=cycle_result.get("initial_capital", 0),
            final_value=cycle_result.get("final_value", 0),
            return_pct=cycle_result.get("return_pct", 0),
            profit_loss=cycle_result.get("profit_loss", 0),
            selected_stocks=cycle_result.get("selected_stocks", []),
            total_trades=cycle_result.get("total_trades", 0),
            winning_trades=cycle_result.get("winning_trades", 0),
            losing_trades=cycle_result.get("losing_trades", 0),
            win_rate=cycle_result.get("win_rate", 0),
            created_at=datetime.now().isoformat(),
            reason=reason,
        )

        self.success_cases.append(case)
        self._save()

        logger.info(f"添加成功案例: {case.case_id}, 策略: {strategy_name}")
        return case

    def add_failure_case(
        self,
        strategy_name: str,
        strategy_params: Dict,
        cycle_result: Dict,
        reason: str = "连续3次亏损",
    ) -> StrategyCase:
        """
        添加失败案例

        Args:
            strategy_name: 策略名称
            strategy_params: 策略参数
            cycle_result: 周期结果
            reason: 原因

        Returns:
            StrategyCase: 创建的案例
        """
        case = StrategyCase(
            case_id=StrategyCase.generate_id(strategy_params),
            case_type="failure",
            strategy_name=strategy_name,
            strategy_params=strategy_params,
            cycle_id=cycle_result.get("cycle_id", 0),
            cutoff_date=cycle_result.get("cutoff_date", ""),
            initial_capital=cycle_result.get("initial_capital", 0),
            final_value=cycle_result.get("final_value", 0),
            return_pct=cycle_result.get("return_pct", 0),
            profit_loss=cycle_result.get("profit_loss", 0),
            selected_stocks=cycle_result.get("selected_stocks", []),
            total_trades=cycle_result.get("total_trades", 0),
            winning_trades=cycle_result.get("winning_trades", 0),
            losing_trades=cycle_result.get("losing_trades", 0),
            win_rate=cycle_result.get("win_rate", 0),
            created_at=datetime.now().isoformat(),
            reason=reason,
        )

        self.failure_cases.append(case)
        self._save()

        logger.warning(f"添加失败案例: {case.case_id}, 策略: {strategy_name}")
        return case

    def is_strategy_allowed(self, strategy_params: Dict) -> bool:
        """
        检查策略是否允许使用

        Args:
            strategy_params: 策略参数

        Returns:
            bool: 是否允许使用
        """
        case_id = StrategyCase.generate_id(strategy_params)

        # 检查是否在失败案例中
        for case in self.failure_cases:
            if case.case_id == case_id:
                logger.info(f"策略 {case_id} 已被禁用")
                return False

        return True

    def get_strategy_stats(self, strategy_name: str = None) -> Dict:
        """
        获取策略统计

        Args:
            strategy_name: 策略名称（可选）

        Returns:
            dict: 统计信息
        """
        success = self.success_cases
        failure = self.failure_cases

        if strategy_name:
            success = [c for c in success if c.strategy_name == strategy_name]
            failure = [c for c in failure if c.strategy_name == strategy_name]

        return {
            "success_count": len(success),
            "failure_count": len(failure),
            "total_cases": len(success) + len(failure),
            "success_rate": len(success) / (len(success) + len(failure)) if (success or failure) else 0,
            "avg_return_pct": sum(c.return_pct for c in success) / len(success) if success else 0,
            "total_profit": sum(c.profit_loss for c in success),
            "total_loss": sum(c.profit_loss for c in failure),
        }

    def get_recent_cases(self, case_type: str = None, limit: int = 10) -> List[StrategyCase]:
        """
        获取最近的案例

        Args:
            case_type: "success" or "failure" or None
            limit: 返回数量

        Returns:
            list: 案例列表
        """
        all_cases = []

        if case_type is None or case_type == "success":
            all_cases.extend(self.success_cases)
        if case_type is None or case_type == "failure":
            all_cases.extend(self.failure_cases)

        # 按创建时间排序
        all_cases.sort(key=lambda x: x.created_at, reverse=True)

        return all_cases[:limit]

    def export_report(self) -> str:
        """
        导出案例库报告

        Returns:
            str: 报告文本
        """
        lines = []
        lines.append("# 策略案例库报告")
        lines.append("")
        lines.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("")

        # 统计
        stats = self.get_strategy_stats()
        lines.append("## 统计概览")
        lines.append("")
        lines.append(f"- 成功案例: {stats['success_count']} 个")
        lines.append(f"- 失败案例: {stats['failure_count']} 个")
        lines.append(f"- 成功率: {stats['success_rate']*100:.1f}%")
        lines.append(f"- 平均收益率: {stats['avg_return_pct']:.2f}%")
        lines.append(f"- 总盈利: {stats['total_profit']:.2f}")
        lines.append(f"- 总亏损: {stats['total_loss']:.2f}")
        lines.append("")

        # 最近案例
        lines.append("## 最近案例")
        lines.append("")

        recent = self.get_recent_cases(limit=5)
        for case in recent:
            lines.append(f"### {case.strategy_name} ({case.case_type})")
            lines.append(f"- 案例ID: {case.case_id}")
            lines.append(f"- 周期: #{case.cycle_id}")
            lines.append(f"- 收益率: {case.return_pct:.2f}%")
            lines.append(f"- 原因: {case.reason}")
            lines.append(f"- 创建时间: {case.created_at}")
            lines.append("")

        return "\n".join(lines)

    def clear(self, case_type: str = None):
        """
        清空案例库

        Args:
            case_type: "success" or "failure" or None (全部)
        """
        if case_type is None:
            self.success_cases = []
            self.failure_cases = []
        elif case_type == "success":
            self.success_cases = []
        elif case_type == "failure":
            self.failure_cases = []

        self._save()
        logger.info(f"案例库已清空: {case_type or '全部'}")



# ============================================================
# strategy_manager.py
# ============================================================

"""
策略管理器 - 可插拔策略管理

功能：
1. 策略注册与注销
2. 策略切换
3. 策略参数管理
4. 策略表现追踪

这是自我进化系统的核心模块，支持策略的动态替换和演化
"""

import logging
import json
from typing import Dict, List, Optional, Callable, Any
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from enum import Enum

logger = logging.getLogger(__name__)


class StrategyStatus(Enum):
    """策略状态"""
    ACTIVE = "active"
    INACTIVE = "inactive"
    TESTING = "testing"
    ELIMINATED = "eliminated"
    FROZEN = "frozen"


@dataclass
class StrategyConfig:
    """策略配置"""
    name: str
    description: str = ""
    version: str = "1.0"

    # 策略参数
    params: Dict = field(default_factory=dict)

    # 策略函数
    select_stocks_func: Callable = None  # 选股函数
    analyze_func: Callable = None  # 分析函数
    signal_func: Callable = None  # 信号生成函数

    # 状态
    status: str = "inactive"

    # 表现统计
    total_runs: int = 0
    profit_runs: int = 0
    loss_runs: int = 0
    avg_return: float = 0.0

    # 时间戳
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        """转换为字典"""
        data = asdict(self)
        # 移除函数对象
        data.pop("select_stocks_func", None)
        data.pop("analyze_func", None)
        data.pop("signal_func", None)
        return data


class StrategyManager:
    """
    策略管理器

    核心功能：
    - 注册新策略
    - 切换当前使用的策略
    - 更新策略参数
    - 追踪策略表现
    - 支持策略进化（参数自动调整）
    """

    def __init__(self, config_dir: str = None):
        """
        初始化策略管理器

        Args:
            config_dir: 策略配置目录
        """
        if config_dir is None:
            from config import config

            config_dir = config.output_dir / "strategies"

        self.config_dir = Path(config_dir)
        self.config_dir.mkdir(parents=True, exist_ok=True)

        # 策略注册表
        self.strategies: Dict[str, StrategyConfig] = {}

        # 当前活跃策略
        self.active_strategy: Optional[str] = None

        # 默认策略
        self._register_default_strategies()

        logger.info(f"策略管理器初始化完成: {len(self.strategies)} 个策略")

    def _register_default_strategies(self):
        """注册默认策略"""
        # 1. 趋势跟踪策略
        self.register_strategy(
            name="trend_following",
            description="趋势跟踪策略 - 跟随大盘/板块趋势选择股票",
            params={
                "ma_short": 5,
                "ma_long": 20,
                "trend_period": 20,
            },
            select_stocks_func=self._trend_following_selector,
        )

        # 2. 动量策略
        self.register_strategy(
            name="momentum",
            description="动量策略 - 选择近期涨幅靠前的股票",
            params={
                "momentum_period": 10,
                "top_n": 5,
                "min_volume": 1000000,
            },
            select_stocks_func=self._momentum_selector,
        )

        # 3. 价值策略
        self.register_strategy(
            name="value",
            description="价值策略 - 选择低估值的优质股票",
            params={
                "pe_threshold": 30,
                "pb_threshold": 3,
                "roe_threshold": 10,
            },
            select_stocks_func=self._value_selector,
        )

        # 4. 均衡策略
        self.register_strategy(
            name="balanced",
            description="均衡策略 - 综合多种因素均衡选择",
            params={
                "momentum_weight": 0.3,
                "value_weight": 0.3,
                "quality_weight": 0.4,
            },
            select_stocks_func=self._balanced_selector,
        )

        # 设置默认策略
        self.active_strategy = "trend_following"
        self.strategies["trend_following"].status = "active"

    def register_strategy(
        self,
        name: str,
        description: str = "",
        params: Dict = None,
        select_stocks_func: Callable = None,
        analyze_func: Callable = None,
        signal_func: Callable = None,
    ) -> StrategyConfig:
        """
        注册新策略

        Args:
            name: 策略名称
            description: 策略描述
            params: 策略参数
            select_stocks_func: 选股函数
            analyze_func: 分析函数
            signal_func: 信号函数

        Returns:
            StrategyConfig: 策略配置
        """
        if name in self.strategies:
            logger.warning(f"策略 {name} 已存在，将被覆盖")
        config = StrategyConfig(
            name=name,
            description=description,
            params=params or {},
            select_stocks_func=select_stocks_func,
            analyze_func=analyze_func,
            signal_func=signal_func,
            status="inactive",
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
        )

        self.strategies[name] = config

        # 保存到文件
        self._save_strategy_config(name, config)

        logger.info(f"注册策略: {name}")
        return config

    def unregister_strategy(self, name: str) -> bool:
        """
        注销策略

        Args:
            name: 策略名称

        Returns:
            bool: 是否成功
        """
        if name not in self.strategies:
            logger.warning(f"策略 {name} 不存在")
            return False

        # 不能注销活跃策略
        if name == self.active_strategy:
            logger.warning(f"无法注销活跃策略 {name}")
            return False

        del self.strategies[name]

        # 删除配置文件
        config_file = self.config_dir / f"{name}.json"
        if config_file.exists():
            config_file.unlink()

        logger.info(f"注销策略: {name}")
        return True

    def activate_strategy(self, name: str) -> bool:
        """
        激活策略

        Args:
            name: 策略名称

        Returns:
            bool: 是否成功
        """
        if name not in self.strategies:
            logger.error(f"策略 {name} 不存在")
            return False

        # 禁用当前活跃策略
        if self.active_strategy and self.active_strategy in self.strategies:
            self.strategies[self.active_strategy].status = "inactive"

        # 激活新策略
        self.strategies[name].status = "active"
        self.active_strategy = name

        logger.info(f"激活策略: {name}")
        return True

    def update_strategy_params(self, name: str, params: Dict) -> bool:
        """
        更新策略参数

        Args:
            name: 策略名称
            params: 新参数

        Returns:
            bool: 是否成功
        """
        if name not in self.strategies:
            logger.error(f"策略 {name} 不存在")
            return False

        strategy = self.strategies[name]
        old_params = strategy.params.copy()

        # 更新参数
        strategy.params.update(params)
        strategy.updated_at = datetime.now().isoformat()

        # 保存
        self._save_strategy_config(name, strategy)

        logger.info(f"更新策略参数: {name}, {old_params} -> {strategy.params}")
        return True

    def get_active_strategy(self) -> Optional[StrategyConfig]:
        """获取当前活跃策略"""
        if self.active_strategy:
            return self.strategies.get(self.active_strategy)
        return None

    def get_strategy(self, name: str) -> Optional[StrategyConfig]:
        """获取指定策略"""
        return self.strategies.get(name)

    def list_strategies(self, status: str = None) -> List[StrategyConfig]:
        """
        列出策略

        Args:
            status: 状态过滤

        Returns:
            list: 策略列表
        """
        strategies = list(self.strategies.values())

        if status:
            strategies = [s for s in strategies if s.status == status]

        return strategies

    def record_run_result(
        self,
        strategy_name: str,
        is_profit: bool,
        return_pct: float,
    ):
        """
        记录策略运行结果

        Args:
            strategy_name: 策略名称
            is_profit: 是否盈利
            return_pct: 收益率
        """
        if strategy_name not in self.strategies:
            return

        strategy = self.strategies[strategy_name]
        strategy.total_runs += 1

        if is_profit:
            strategy.profit_runs += 1
        else:
            strategy.loss_runs += 1

        # 更新平均收益率
        total = strategy.total_runs
        old_avg = strategy.avg_return
        strategy.avg_return = (old_avg * (total - 1) + return_pct) / total

        strategy.updated_at = datetime.now().isoformat()

        self._save_strategy_config(strategy_name, strategy)

    def evolve_strategy(
        self,
        strategy_name: str,
        evolution_method: str = "random",
    ) -> Optional[Dict]:
        """
        演化策略

        根据运行结果自动调整参数

        Args:
            strategy_name: 策略名称
            evolution_method: 演化方法 ("random", "genetic", "bayesian")

        Returns:
            dict: 新的参数
        """
        if strategy_name not in self.strategies:
            return None

        strategy = self.strategies[strategy_name]

        if strategy.total_runs < 3:
            logger.info(f"策略 {strategy_name} 运行次数不足，等待更多数据")
            return None

        # 简单演化：如果是亏损，减少风险参数；如果是盈利，增加激进参数
        if strategy.avg_return < 0:
            # 亏损，减少风险
            new_params = strategy.params.copy()
            if "position_size" in new_params:
                new_params["position_size"] *= 0.8
            if "stop_loss" in new_params:
                new_params["stop_loss"] *= 0.9
        else:
            # 盈利，可适当增加风险
            new_params = strategy.params.copy()
            if "position_size" in new_params:
                new_params["position_size"] = min(new_params["position_size"] * 1.1, 0.5)
            if "take_profit" in new_params:
                new_params["take_profit"] *= 1.1

        # 应用新参数
        self.update_strategy_params(strategy_name, new_params)

        logger.info(f"策略 {strategy_name} 演化: {strategy.params} -> {new_params}")
        return new_params

    def _save_strategy_config(self, name: str, config: StrategyConfig):
        """保存策略配置"""
        config_file = self.config_dir / f"{name}.json"
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(config.to_dict(), f, ensure_ascii=False, indent=2)

    # ========== 内置选股函数 ==========

    def _trend_following_selector(
        self,
        stock_data: Dict,
        cutoff_date: str,
        params: Dict,
        max_stocks: int = 5,
    ) -> List[str]:
        """趋势跟踪选股"""
        import pandas as pd

        ma_short = params.get("ma_short", 5)
        ma_long = params.get("ma_long", 20)

        scores = []

        for ts_code, df in stock_data.items():
            if df is None or df.empty:
                continue

            # 过滤截止日期前的数据
            df = df[df["trade_date"] <= cutoff_date]
            if len(df) < ma_long:
                continue

            # 计算均线
            df = df.tail(60).copy()
            df["ma_s"] = df["close"].rolling(ma_short).mean()
            df["ma_l"] = df["close"].rolling(ma_long).mean()

            if df.iloc[-1]["ma_s"] > df.iloc[-1]["ma_l"]:
                # 上涨趋势
                score = (df.iloc[-1]["ma_s"] / df.iloc[-1]["ma_l"] - 1) * 100
                scores.append((ts_code, score))

        scores.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scores[:max_stocks]]

    def _momentum_selector(
        self,
        stock_data: Dict,
        cutoff_date: str,
        params: Dict,
        max_stocks: int = 5,
    ) -> List[str]:
        """动量选股"""
        period = params.get("momentum_period", 10)

        scores = []

        for ts_code, df in stock_data.items():
            if df is None or df.empty:
                continue

            df = df[df["trade_date"] <= cutoff_date]
            if len(df) < period:
                continue

            # 计算动量
            recent = df.tail(period)
            momentum = (recent.iloc[-1]["close"] - recent.iloc[0]["close"]) / recent.iloc[0]["close"] * 100
            scores.append((ts_code, momentum))

        scores.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scores[:max_stocks]]

    def _value_selector(
        self,
        stock_data: Dict,
        cutoff_date: str,
        params: Dict,
        max_stocks: int = 5,
    ) -> List[str]:
        """价值选股（简化版）"""
        # 简化：选择低波动率的股票作为价值股
        scores = []

        for ts_code, df in stock_data.items():
            if df is None or df.empty:
                continue

            df = df[df["trade_date"] <= cutoff_date]
            if len(df) < 20:
                continue

            # 低波动 = 价值
            volatility = df["pct_chg"].std()
            score = -volatility  # 负值，波动越小分数越高
            scores.append((ts_code, score))

        scores.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scores[:max_stocks]]

    def _balanced_selector(
        self,
        stock_data: Dict,
        cutoff_date: str,
        params: Dict,
        max_stocks: int = 5,
    ) -> List[str]:
        """均衡选股"""
        momentum = self._momentum_selector(stock_data, cutoff_date, params, max_stocks * 2)
        value = self._value_selector(stock_data, cutoff_date, params, max_stocks * 2)

        # 合并去重
        combined = list(set(momentum + value))[:max_stocks]
        return combined



__all__ = ["StrategyCase", "CaseLibrary", "StrategyStatus", "StrategyConfig", "StrategyManager"]
