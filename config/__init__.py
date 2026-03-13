"""
投资进化系统 - 全局配置

所有模块通过 `from config import config, PROJECT_ROOT` 获取配置

API Key 优先从环境变量读取：
    export LLM_API_KEY="sk-..."
    export LLM_API_BASE="https://api.minimaxi.com/v1"
    export LLM_MODEL="minimax/MiniMax-M2.5-highspeed"
"""

import os
import re
import json
import logging
import sqlite3
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

def _allow_codex_auth_fallback() -> bool:
    raw = os.environ.get("INVEST_ALLOW_CODEX_AUTH_FALLBACK")
    return str(raw or "").strip().lower() in {"1", "true", "t", "yes", "y", "on"}


def _load_codex_openai_api_key() -> str:
    if not _allow_codex_auth_fallback():
        return ""
    auth_path = Path.home() / ".codex" / "auth.json"
    if not auth_path.exists():
        return ""
    try:
        payload = json.loads(auth_path.read_text(encoding="utf-8"))
    except Exception:
        logger.debug("Failed to parse Codex auth file: %s", auth_path, exc_info=True)
        return ""
    return str(payload.get("OPENAI_API_KEY", "") or "").strip()


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
    os.environ.get("OPENAI_API_KEY", _load_codex_openai_api_key()),
)
DEFAULT_LLM_API_BASE = os.environ.get(
    "LLM_API_BASE",
    "https://api.minimaxi.com/v1",
)

# ===========================================================
# 标准日期格式（各模块统一使用 YYYYMMDD，此函数负责归一化）
# ===========================================================
DATE_FORMAT = "%Y%m%d"  # 内部标准: 20240315


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid %s=%r, fallback to %s", name, raw, default)
        return default


_TRUE_VALUES = {"1", "true", "t", "yes", "y", "on"}
_FALSE_VALUES = {"0", "false", "f", "no", "n", "off"}
_ENV_PLACEHOLDER = re.compile(r"\$\{ENV:([A-Z0-9_]+)(?::-(.*?))?\}")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    normalized = str(raw).strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    logger.warning("Invalid %s=%r, fallback to %s", name, raw, default)
    return default


def _expand_env_placeholders(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _expand_env_placeholders(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env_placeholders(item) for item in value]
    if isinstance(value, str):
        def _replace(match: re.Match[str]) -> str:
            env_name = match.group(1)
            fallback = match.group(2) if match.group(2) is not None else ""
            return os.environ.get(env_name, fallback)

        return _ENV_PLACEHOLDER.sub(_replace, value)
    return value


def _load_yaml_layer(path: Path) -> dict[str, Any]:
    if not (_HAS_YAML and Path(path).exists()):
        return {}
    with open(path, encoding="utf-8") as f:
        loaded = yaml.safe_load(f) or {}
    known = {f.name for f in EvolutionConfig.__dataclass_fields__.values()}
    data = {k: v for k, v in loaded.items() if k in known}
    return _expand_env_placeholders(data)


def get_runtime_override_path(config_path: str | Path | None = None) -> Path:
    primary = Path(config_path) if config_path else PROJECT_ROOT / "config" / "evolution.yaml"
    return primary.parent.parent / "runtime" / "state" / "evolution.runtime.yaml"


def get_config_layer_paths(config_path: str | Path | None = None) -> list[Path]:
    primary = Path(config_path) if config_path else PROJECT_ROOT / "config" / "evolution.yaml"
    config_dir = primary.parent
    layers: list[Path] = []

    if primary.exists():
        layers.append(primary)

    local_override = config_dir / "evolution.local.yaml"
    if local_override.exists() and local_override.resolve() != primary.resolve():
        layers.append(local_override)

    runtime_override = get_runtime_override_path(primary)
    if runtime_override.exists():
        layers.append(runtime_override)

    extra_path = os.environ.get("INVEST_CONFIG_PATH")
    if extra_path:
        candidate = Path(extra_path)
        if candidate.exists():
            try:
                resolved = candidate.resolve()
            except OSError:
                resolved = candidate
            if all((p.resolve() if p.exists() else p) != resolved for p in layers):
                layers.append(candidate)

    return layers


def _apply_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    env_map: dict[str, tuple[str, callable]] = {
        "llm_fast_model": ("LLM_MODEL", str),
        "llm_deep_model": ("LLM_DEEP_MODEL", str),
        "llm_api_key": ("LLM_API_KEY", str),
        "llm_api_base": ("LLM_API_BASE", str),
        "llm_timeout": ("LLM_TIMEOUT", int),
        "llm_max_retries": ("LLM_MAX_RETRIES", int),
        "web_api_token": ("WEB_API_TOKEN", str),
        "web_api_require_auth": ("WEB_API_REQUIRE_AUTH", lambda raw: str(raw).strip().lower() in _TRUE_VALUES),
        "web_api_public_read_enabled": ("WEB_API_PUBLIC_READ_ENABLED", lambda raw: str(raw).strip().lower() in _TRUE_VALUES),
        "web_rate_limit_enabled": ("WEB_RATE_LIMIT_ENABLED", lambda raw: str(raw).strip().lower() in _TRUE_VALUES),
        "web_rate_limit_window_sec": ("WEB_RATE_LIMIT_WINDOW_SEC", int),
        "web_rate_limit_read_max": ("WEB_RATE_LIMIT_READ_MAX", int),
        "web_rate_limit_write_max": ("WEB_RATE_LIMIT_WRITE_MAX", int),
        "web_rate_limit_heavy_max": ("WEB_RATE_LIMIT_HEAVY_MAX", int),
    }
    merged = dict(data)
    for field_name, (env_name, caster) in env_map.items():
        raw = os.environ.get(env_name)
        if raw is None or str(raw).strip() == "":
            continue
        try:
            merged[field_name] = caster(raw)
        except (TypeError, ValueError):
            logger.warning(
                "Invalid %s=%r for %s; keep existing value %r",
                env_name,
                raw,
                field_name,
                merged.get(field_name),
            )
    return merged


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
    investment_model: str = "momentum"  # 当前激活的投资模型
    investment_model_config: str = "invest/models/configs/momentum_v1.yaml"
    allocator_enabled: bool = False
    allocator_top_n: int = 3
    model_routing_enabled: bool = True
    model_routing_mode: str = "rule"  # off / rule / hybrid / agent
    model_routing_allowed_models: Optional[List[str]] = None
    model_switch_cooldown_cycles: int = 2
    model_switch_min_confidence: float = 0.60
    model_switch_hysteresis_margin: float = 0.08
    model_routing_agent_override_enabled: bool = False
    model_routing_agent_override_max_gap: float = 0.18
    model_routing_policy: dict = field(default_factory=lambda: {
        "bull_avg_change_20d": 3.0,
        "bull_above_ma20_ratio": 0.55,
        "bear_avg_change_20d": -3.0,
        "bear_above_ma20_ratio": 0.45,
        "high_volatility_threshold": 0.028,
        "weak_breadth_threshold": 0.42,
        "strong_breadth_threshold": 0.58,
        "index_bull_change_20d": 2.0,
        "index_bear_change_20d": -2.0,
        "default_regime": "oscillation",
    })
    stop_on_freeze: bool = True
    web_api_token: str = field(default_factory=lambda: os.environ.get("WEB_API_TOKEN", ""))
    web_api_require_auth: bool = field(default_factory=lambda: _env_bool("WEB_API_REQUIRE_AUTH", bool(os.environ.get("WEB_API_TOKEN", "").strip())))
    web_api_public_read_enabled: bool = field(default_factory=lambda: _env_bool("WEB_API_PUBLIC_READ_ENABLED", False))
    web_rate_limit_enabled: bool = field(default_factory=lambda: _env_bool("WEB_RATE_LIMIT_ENABLED", True))
    web_rate_limit_window_sec: int = field(default_factory=lambda: _env_int("WEB_RATE_LIMIT_WINDOW_SEC", 60))
    web_rate_limit_read_max: int = field(default_factory=lambda: _env_int("WEB_RATE_LIMIT_READ_MAX", 120))
    web_rate_limit_write_max: int = field(default_factory=lambda: _env_int("WEB_RATE_LIMIT_WRITE_MAX", 20))
    web_rate_limit_heavy_max: int = field(default_factory=lambda: _env_int("WEB_RATE_LIMIT_HEAVY_MAX", 5))
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
    llm_timeout: int = field(default_factory=lambda: _env_int("LLM_TIMEOUT", 60))
    llm_max_retries: int = field(default_factory=lambda: _env_int("LLM_MAX_RETRIES", 2))

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
        self.web_api_token = str(self.web_api_token or "").strip()
        if self.web_api_require_auth and not self.web_api_token:
            logger.warning(
                "WEB_API_REQUIRE_AUTH 已开启，但 WEB_API_TOKEN 未配置；"
                "仅允许回环地址本地启动，非回环部署会被拒绝。"
            )
        self.web_rate_limit_window_sec = max(1, int(self.web_rate_limit_window_sec or 60))
        self.web_rate_limit_read_max = max(1, int(self.web_rate_limit_read_max or 120))
        self.web_rate_limit_write_max = max(1, int(self.web_rate_limit_write_max or 20))
        self.web_rate_limit_heavy_max = max(1, int(self.web_rate_limit_heavy_max or 5))
        if self.model_routing_allowed_models is None:
            self.model_routing_allowed_models = [
                "momentum",
                "mean_reversion",
                "value_quality",
                "defensive_low_vol",
            ]
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

        # 不在配置初始化阶段做 LLM 缺失告警。
        # 当前系统的 LLM 入口已由 control-plane 统一管理，
        # import-time 的老式 `LLM_API_KEY` 告警容易与真实生效配置脱节，
        # 并且会在纯内建/显式工具路径中制造噪音。


def load_config(config_path: str = None) -> EvolutionConfig:
    """从 YAML 或默认值加载配置。

    优先级：环境变量 > INVEST_CONFIG_PATH > evolution.runtime.yaml >
    evolution.local.yaml > evolution.yaml > dataclass 默认值。
    """
    base_path = Path(config_path) if config_path else PROJECT_ROOT / "config" / "evolution.yaml"

    data: dict[str, Any] = {}
    for layer_path in get_config_layer_paths(base_path):
        data.update(_load_yaml_layer(layer_path))

    data = _apply_env_overrides(data)
    if not str(data.get("llm_api_key", "") or "").strip() and DEFAULT_LLM_API_KEY:
        data["llm_api_key"] = DEFAULT_LLM_API_KEY
    return EvolutionConfig(**data)


# 全局单例（各模块直接 import 使用）
config = load_config()


# ===========================================================
# 行业映射注册表（Single Source of Truth）
# ===========================================================

class IndustryRegistry:
    """
    统一行业映射注册表

    数据源优先级:
    1. security_master.industry（数据库主事实源）
    2. data/industry_map.json（人工覆盖补丁）
    """

    def __init__(self, json_path: Path = None, db_path: Path = None):
        self._db_map: dict[str, str] = {}
        self._override_map: dict[str, str] = {}
        self._path = json_path or DATA_DIR / "industry_map.json"
        self._db_path = db_path or Path(os.environ.get("INVEST_DB_PATH", str(DATA_DIR / "stock_history.db")))
        self._load()

    def _load_db(self):
        self._db_map = {}
        if not self._db_path.exists():
            return
        try:
            conn = sqlite3.connect(str(self._db_path))
            rows = conn.execute(
                "SELECT code, industry FROM security_master WHERE industry IS NOT NULL AND trim(industry) != ''"
            ).fetchall()
            conn.close()
            self._db_map = {str(code): str(industry) for code, industry in rows if str(code).strip()}
        except Exception:
            self._db_map = {}

    def _load_overrides(self):
        self._override_map = {}
        if self._path.exists():
            with open(self._path, encoding="utf-8") as f:
                self._override_map = json.load(f)

    def _load(self):
        self._load_db()
        self._load_overrides()

    def refresh(self):
        self._load()

    def get_industry(self, code: str) -> str:
        """查询股票所属行业，优先数据库，JSON 作为覆盖层，未命中返回 '其他'"""
        key = str(code or "").strip()
        if not key:
            return "其他"
        if key in self._override_map:
            return self._override_map[key]
        if key in self._db_map:
            return self._db_map[key]
        self._load_db()
        return self._override_map.get(key) or self._db_map.get(key) or "其他"

    def register(self, code: str, industry: str):
        """运行时动态注册 / 覆盖"""
        self._override_map[str(code)] = industry

    def all(self) -> dict[str, str]:
        """返回完整映射的副本"""
        merged = dict(self._db_map)
        merged.update(self._override_map)
        return merged


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
