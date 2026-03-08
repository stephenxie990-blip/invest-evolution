"""
投资进化系统 - 全局配置

所有模块通过 `from config import config, PROJECT_ROOT` 获取配置

API Key 优先从环境变量读取：
    export LLM_API_KEY="sk-..."
    export LLM_API_BASE="https://api.minimaxi.com/v1"
    export LLM_MODEL="minimax/MiniMax-M2.5-highspeed"
"""

import os
import json
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional, Any
from datetime import datetime

logger = logging.getLogger(__name__)

try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


# ===========================================================
# 路径常量
# ===========================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
RUNTIME_DIR = PROJECT_ROOT / "runtime"
OUTPUT_DIR = RUNTIME_DIR / "outputs"
LOGS_DIR = RUNTIME_DIR / "logs"
MEMORY_DIR = RUNTIME_DIR / "memory"
SESSIONS_DIR = RUNTIME_DIR / "sessions"
WORKSPACE_DIR = RUNTIME_DIR / "workspace"


# ===========================================================
# LLM 默认配置（优先环境变量，其次 fallback）
# ===========================================================

# 快思考模型：用于数据汇总、预筛、摘要等轻量任务
DEFAULT_LLM_MODEL = os.environ.get(
    "LLM_MODEL",
    "minimax/MiniMax-M2.5-highspeed",
)
# 慢思考/推理模型：用于决策裁判、策略评估、风险辩论等高质量推理任务
# 若未设置，默认与快模型相同
DEFAULT_LLM_DEEP_MODEL = os.environ.get(
    "LLM_DEEP_MODEL",
    DEFAULT_LLM_MODEL,
)
DEFAULT_LLM_API_KEY = os.environ.get(
    "LLM_API_KEY",
    "",  # 不再硬编码 — 必须通过环境变量或 YAML 配置
)
DEFAULT_LLM_API_BASE = os.environ.get(
    "LLM_API_BASE",
    "https://api.minimaxi.com/v1",
)

# ===========================================================
# 标准日期格式（各模块统一使用 YYYYMMDD，此函数负责归一化）
# ===========================================================
DATE_FORMAT = "%Y%m%d"  # 内部标准: 20240315


def normalize_date(d) -> str:
    """
    将各种日期格式归一化为 YYYYMMDD

    支持: "2024-03-15", "20240315", datetime 对象
    """
    if isinstance(d, datetime):
        return d.strftime(DATE_FORMAT)
    s = str(d).strip().replace("-", "").replace("/", "")
    return s[:8]  # 截取前 8 位


# ===========================================================
# EvolutionConfig
# ===========================================================

@dataclass
class EvolutionConfig:
    """进化系统全局配置"""

    # --- 模拟交易参数 ---
    initial_capital: float = 100_000.0   # 初始资金
    max_positions: int = 5               # 最大持仓数
    position_size_pct: float = 0.20      # 单票仓位

    # --- 数据参数 ---
    max_stocks: int = 50                 # 每轮加载的最大股票数
    simulation_days: int = 30            # 每轮模拟交易天数
    min_history_days: int = 750          # 最小历史交易日数（约 3 年）

    # --- 固化/淘汰条件 ---
    max_consecutive_loss: int = 3        # 连续亏损淘汰阈值
    max_consecutive_profit: int = 3      # 连续盈利进入候选
    freeze_total_cycles: int = 10        # 固化统计周期数
    freeze_profit_required: int = 7      # 固化所需盈利次数

    # --- 数据源 ---
    data_source: str = "baostock"        # baostock / tushare / akshare
    index_codes: Optional[List[str]] = None  # 大盘指数代码list

    # --- 算法与策略参数 ---
    regime_params: dict = field(default_factory=lambda: {
        "bull": {"ma_short_weight": 0.4, "ma_long_weight": 0.2, "rsi_weight": 0.2, "vol_weight": 0.2},
        "bear": {"ma_short_weight": 0.2, "ma_long_weight": 0.5, "rsi_weight": 0.1, "vol_weight": 0.2},
        "shock": {"ma_short_weight": 0.3, "ma_long_weight": 0.2, "rsi_weight": 0.4, "vol_weight": 0.1},
    })
    rsi_thresholds: dict = field(default_factory=lambda: {
        "oversold": 25,
        "overbought": 75,
    })
    emergency_params: dict = field(default_factory=lambda: {
        "index_drop_threshold": -0.03,      # 宽基指数暴跌阈值
        "sector_drop_threshold": -0.05,     # 行业板块暴跌阈值
        "broad_selloff_ratio": 0.8,         # 普跌阈值(80%股票下跌)
    })

    # --- LLM (双轨配置) ---
    # 快思考模型：用于数据汇总、猎手分析、摘要等高频低开销任务
    llm_fast_model: str = DEFAULT_LLM_MODEL
    # 慢思考/推理模型：用于决策裁判、复盘评估、风控辩论等关键推理任务
    llm_deep_model: str = DEFAULT_LLM_DEEP_MODEL
    llm_api_key: str = DEFAULT_LLM_API_KEY
    llm_api_base: str = DEFAULT_LLM_API_BASE
    llm_timeout: int = 60
    llm_max_retries: int = 2

    # --- 辩论配置 (Phase 3) ---
    enable_debate: bool = True          # 是否启用多空辩论（可关闭省 Token）
    max_debate_rounds: int = 1          # 多空辩论往返轮数
    max_risk_discuss_rounds: int = 1    # 风控三方讨论轮数

    # --- 输出路径 ---
    output_dir: Optional[Path] = None
    case_library_dir: Optional[Path] = None
    logs_dir: Optional[Path] = None
    memory_dir: Optional[Path] = None   # 市场情境记忆库存储目录 (Phase 2)

    def __post_init__(self):
        if self.output_dir is None:
            self.output_dir = OUTPUT_DIR / "evolution"
        if self.case_library_dir is None:
            self.case_library_dir = OUTPUT_DIR / "case_library"
        if self.logs_dir is None:
            self.logs_dir = LOGS_DIR
        if self.memory_dir is None:
            self.memory_dir = OUTPUT_DIR / "memory"
        if self.index_codes is None:
            self.index_codes = [
                "000001.SH",  # 上证指数
                "399001.SZ",  # 深证成指
                "399006.SZ",  # 创业板指
                "000300.SH",  # 沪深300
            ]

        # 自动创建目录
        for d in (self.output_dir, self.case_library_dir, self.logs_dir, self.memory_dir):
            Path(d).mkdir(parents=True, exist_ok=True)

        # API Key 检查
        if not self.llm_api_key:
            import logging
            logging.getLogger(__name__).warning(
                "LLM_API_KEY 未设置！LLM 功能将不可用。"
                "请设置环境变量: export LLM_API_KEY='sk-...'"
            )


def load_config(config_path: str = None) -> EvolutionConfig:
    """从 YAML 或默认值加载配置"""
    if config_path is None:
        config_path = PROJECT_ROOT / "config" / "evolution.yaml"

    if _HAS_YAML and Path(config_path).exists():
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        # 过滤不认识的 key，避免 dataclass 报错
        known = {f.name for f in EvolutionConfig.__dataclass_fields__.values()}
        data = {k: v for k, v in data.items() if k in known}
        return EvolutionConfig(**data)

    return EvolutionConfig()


# 全局单例（各模块直接 import 使用）
config = load_config()


# ===========================================================
# 行业映射注册表（Single Source of Truth）
# ===========================================================

import json as _json


class IndustryRegistry:
    """
    统一行业映射注册表

    数据源: data/industry_map.json
    - get_industry(code)  → 行业名称 或 "其他"
    - register(code, ind) → 运行时动态注册
    - all()               → 返回完整映射副本
    """

    def __init__(self, json_path: Path = None):
        self._map: dict[str, str] = {}
        self._path = json_path or DATA_DIR / "industry_map.json"
        self._load()

    def _load(self):
        if self._path.exists():
            with open(self._path, encoding="utf-8") as f:
                self._map = _json.load(f)

    def get_industry(self, code: str) -> str:
        """查询股票所属行业，未命中返回 '其他'"""
        return self._map.get(code, "其他")

    def register(self, code: str, industry: str):
        """运行时动态注册 / 覆盖"""
        self._map[code] = industry

    def all(self) -> dict[str, str]:
        """返回完整映射的副本"""
        return dict(self._map)


# 全局行业注册表单例
industry_registry = IndustryRegistry()


class AgentConfigRegistry:
    """Registry for loading and persisting agent prompts and parameters from a JSON file."""

    def __init__(self, json_path: Path = PROJECT_ROOT / "agent_settings" / "agents_config.json"):
        self.json_path = json_path
        self._configs: dict[str, dict[str, Any]] = {}
        self.reload()

    def reload(self) -> None:
        if self.json_path.exists():
            try:
                self._configs = json.loads(self.json_path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.error(f"Failed to load agent configs from {self.json_path}: {e}")
                self._configs = {}

    def get_config(self, agent_name: str) -> dict[str, Any]:
        """Get the configuration dictionary for an agent by name."""
        return self._configs.get(agent_name, {})

    def save_config(self, agent_name: str, config_data: dict[str, Any]) -> bool:
        """Update and persist an agent's configuration."""
        try:
            self._configs[agent_name] = config_data
            self._persist()
            return True
        except Exception as e:
            logger.error(f"Failed to persist agent config for {agent_name}: {e}")
            return False

    def all(self) -> dict[str, dict[str, Any]]:
        return dict(self._configs)

    def list_configs(self) -> list[dict[str, Any]]:
        return [{"name": k, **v} for k, v in self._configs.items()]

    def _persist(self) -> None:
        self.json_path.parent.mkdir(parents=True, exist_ok=True)
        self.json_path.write_text(
            json.dumps(self._configs, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )


# Global singleton for agent config
agent_config_registry = AgentConfigRegistry()
