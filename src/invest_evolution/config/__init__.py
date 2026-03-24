"""
投资进化系统 - 全局配置

所有模块通过 `from invest_evolution.config import config, PROJECT_ROOT` 获取配置。

Evolution 配置分层的 canonical baseline 固定为版本化
`config/evolution.yaml.example`；`config/evolution.yaml`
仅作为本地 materialized working copy，不再承担共享基线语义。

Canonical provider/model/api_key ownership lives in:
    - config/control_plane.yaml
    - config/control_plane.local.yaml

Legacy runtime env fallback for provider/model/api_key has been retired.
Only explicit config layers and `${ENV:...}` placeholders inside config files
may inject those values now.
"""

import os
import re
import json
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

try:
    import yaml
    _HAS_YAML = True
except ImportError:
    yaml = None
    _HAS_YAML = False


# ===========================================================
# 路径常量
# ===========================================================

# `src/invest_evolution/config/__init__.py` -> repo root is 3 levels up.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "data"
RUNTIME_DIR = PROJECT_ROOT / "runtime"
OUTPUT_DIR = RUNTIME_DIR / "outputs"
LOGS_DIR = RUNTIME_DIR / "logs"
MEMORY_DIR = RUNTIME_DIR / "memory"
SESSIONS_DIR = RUNTIME_DIR / "sessions"
WORKSPACE_DIR = RUNTIME_DIR / "workspace"


# ===========================================================
# LLM 默认配置（static local defaults）
# ===========================================================

# 快思考模型：用于数据汇总、预筛、摘要等轻量任务
DEFAULT_LLM_MODEL = "minimax/MiniMax-M2.5-highspeed"
# 慢思考/推理模型：用于决策裁判、策略评估、风险辩论等高质量推理任务
# 若未设置，默认与快模型相同
DEFAULT_LLM_DEEP_MODEL = DEFAULT_LLM_MODEL
DEFAULT_LLM_API_KEY = ""
DEFAULT_LLM_API_BASE = "https://api.minimaxi.com/v1"
EFFECTIVE_RUNTIME_MODE = "manager_portfolio"
RUNTIME_CONTRACT_VERSION = 1
DEPRECATED_MANAGER_RUNTIME_FLAGS = (
    "manager_arch_enabled",
    "manager_allocator_enabled",
    "portfolio_assembly_enabled",
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


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid %s=%r, fallback to %s", name, raw, default)
        return default


def normalize_manager_active_ids(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = value.split(",")
    else:
        items = list(value or [])
    return [str(item).strip() for item in items if str(item).strip()]


def normalize_manager_budget_weights(value: Any) -> dict[str, float]:
    if value is None:
        return {}
    return {
        str(key).strip(): float(weight)
        for key, weight in dict(value or {}).items()
        if str(key).strip()
    }


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
    assert yaml is not None
    with open(path, encoding="utf-8") as f:
        loaded = yaml.safe_load(f) or {}
    known = {f.name for f in EvolutionConfig.__dataclass_fields__.values()}
    data = {k: v for k, v in loaded.items() if k in known}
    return _expand_env_placeholders(data)


def _canonical_evolution_config_path(config_dir: Path | None = None) -> Path:
    cfg_dir = Path(config_dir or (PROJECT_ROOT / "config"))
    return cfg_dir / "evolution.yaml.example"


def _materialized_evolution_config_path(config_dir: Path | None = None) -> Path:
    cfg_dir = Path(config_dir or (PROJECT_ROOT / "config"))
    return cfg_dir / "evolution.yaml"


def _default_primary_config_path() -> Path:
    config_dir = PROJECT_ROOT / "config"
    canonical = _canonical_evolution_config_path(config_dir)
    materialized = _materialized_evolution_config_path(config_dir)
    if canonical.exists():
        return canonical
    if materialized.exists():
        return materialized
    return canonical


def get_runtime_override_path(config_path: str | Path | None = None) -> Path:
    primary = Path(config_path) if config_path else _default_primary_config_path()
    return primary.parent.parent / "runtime" / "state" / "evolution.runtime.yaml"


def get_config_layer_paths(config_path: str | Path | None = None) -> list[Path]:
    layers: list[Path] = []
    if config_path is None:
        config_dir = _default_primary_config_path().parent
        canonical = _canonical_evolution_config_path(config_dir)
        materialized = _materialized_evolution_config_path(config_dir)
        if canonical.exists():
            layers.append(canonical)
        elif materialized.exists():
            layers.append(materialized)
        if materialized.exists() and all(
            (path.resolve() if path.exists() else path) != materialized.resolve()
            for path in layers
        ):
            layers.append(materialized)
    else:
        primary = Path(config_path)
        config_dir = primary.parent
        canonical = _canonical_evolution_config_path(config_dir)
        materialized = _materialized_evolution_config_path(config_dir)
        if primary.name == canonical.name and canonical.exists():
            layers.append(canonical)
            if materialized.exists() and materialized.resolve() != canonical.resolve():
                layers.append(materialized)
        elif primary.exists():
            layers.append(primary)

    local_override = config_dir / "evolution.local.yaml"
    if local_override.exists() and all(
        (path.resolve() if path.exists() else path) != local_override.resolve()
        for path in layers
    ):
        layers.append(local_override)

    runtime_anchor = layers[0] if layers else (Path(config_path) if config_path else _default_primary_config_path())
    runtime_override = get_runtime_override_path(runtime_anchor)
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
    env_map: dict[str, tuple[str, Callable[[str], Any]]] = {
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
        "web_rate_limit_max_keys": ("WEB_RATE_LIMIT_MAX_KEYS", int),
        "web_status_training_lab_limit": ("WEB_STATUS_TRAINING_LAB_LIMIT", int),
        "web_status_events_summary_limit": ("WEB_STATUS_EVENTS_SUMMARY_LIMIT", int),
        "web_event_history_limit": ("WEB_EVENT_HISTORY_LIMIT", int),
        "web_event_buffer_limit": ("WEB_EVENT_BUFFER_LIMIT", int),
        "web_event_wait_timeout_sec": ("WEB_EVENT_WAIT_TIMEOUT_SEC", float),
        "web_runtime_async_timeout_sec": ("WEB_RUNTIME_ASYNC_TIMEOUT_SEC", int),
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
    default_manager_id: str = "momentum"  # 默认经理
    default_manager_config_ref: str = "src/invest_evolution/investment/runtimes/configs/momentum_v1.yaml"
    allocator_enabled: bool = False
    allocator_top_n: int = 3
    manager_arch_enabled: bool = True
    manager_shadow_mode: bool = False
    manager_allocator_enabled: bool = False
    portfolio_assembly_enabled: bool = True
    dual_review_enabled: bool = True
    manager_persistence_enabled: bool = False
    manager_active_ids: Optional[List[str]] = None
    manager_budget_weights: dict = field(default_factory=dict)
    governance_enabled: bool = True
    governance_mode: str = "rule"  # off / rule / hybrid / agent
    governance_allowed_manager_ids: Optional[List[str]] = None
    governance_cooldown_cycles: int = 2
    governance_min_confidence: float = 0.60
    governance_hysteresis_margin: float = 0.08
    governance_agent_override_enabled: bool = False
    governance_agent_override_max_gap: float = 0.18
    governance_policy: dict = field(default_factory=lambda: {
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
    web_rate_limit_max_keys: int = field(default_factory=lambda: _env_int("WEB_RATE_LIMIT_MAX_KEYS", 4096))
    web_status_training_lab_limit: int = field(default_factory=lambda: _env_int("WEB_STATUS_TRAINING_LAB_LIMIT", 3))
    web_status_events_summary_limit: int = field(default_factory=lambda: _env_int("WEB_STATUS_EVENTS_SUMMARY_LIMIT", 20))
    web_event_history_limit: int = field(default_factory=lambda: _env_int("WEB_EVENT_HISTORY_LIMIT", 200))
    web_event_buffer_limit: int = field(default_factory=lambda: _env_int("WEB_EVENT_BUFFER_LIMIT", 512))
    web_event_wait_timeout_sec: float = field(default_factory=lambda: _env_float("WEB_EVENT_WAIT_TIMEOUT_SEC", 15.0))
    web_runtime_async_timeout_sec: int = field(default_factory=lambda: _env_int("WEB_RUNTIME_ASYNC_TIMEOUT_SEC", 600))
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
        self.web_rate_limit_max_keys = max(1, int(self.web_rate_limit_max_keys or 4096))
        self.web_status_training_lab_limit = max(1, int(self.web_status_training_lab_limit or 3))
        self.web_status_events_summary_limit = max(1, int(self.web_status_events_summary_limit or 20))
        self.web_event_history_limit = max(1, int(self.web_event_history_limit or 200))
        self.web_event_buffer_limit = max(1, int(self.web_event_buffer_limit or 512))
        self.web_event_wait_timeout_sec = max(0.1, float(self.web_event_wait_timeout_sec or 15.0))
        self.web_runtime_async_timeout_sec = max(1, int(self.web_runtime_async_timeout_sec or 600))
        if self.manager_active_ids is None:
            self.manager_active_ids = [
                "momentum",
                "mean_reversion",
                "value_quality",
                "defensive_low_vol",
            ]
        self.manager_active_ids = normalize_manager_active_ids(self.manager_active_ids)
        self.manager_budget_weights = normalize_manager_budget_weights(self.manager_budget_weights)
        if self.governance_allowed_manager_ids is None:
            self.governance_allowed_manager_ids = [
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


def load_config(config_path: str | None = None) -> EvolutionConfig:
    """从 YAML 或默认值加载配置。

    默认优先级：环境变量 > INVEST_CONFIG_PATH > evolution.runtime.yaml >
    evolution.local.yaml > evolution.yaml(本地 materialized working copy) >
    evolution.yaml.example(版本化 canonical baseline) > dataclass 默认值。

    若显式传入 config_path，则按该路径作为起始层，再叠加同目录 local/runtime/extra 覆盖。
    """
    base_path = Path(config_path) if config_path else _default_primary_config_path()

    data: dict[str, Any] = {}
    for layer_path in get_config_layer_paths(base_path):
        data.update(_load_yaml_layer(layer_path))

    data = _apply_env_overrides(data)
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

    def __init__(self, json_path: Path | None = None, db_path: Path | None = None):
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
            from invest_evolution.market_data.repository import MarketDataRepository

            repository = MarketDataRepository(self._db_path)
            self._db_map = repository.read_industry_map_snapshot()
        except Exception as exc:
            logger.warning(
                "Failed to load industry mappings from database %s: %s",
                self._db_path,
                exc,
                exc_info=True,
            )
            self._db_map = {}

    def _load_overrides(self):
        self._override_map = {}
        if self._path.exists():
            try:
                with open(self._path, encoding="utf-8") as f:
                    payload = json.load(f)
            except Exception as exc:
                logger.warning(
                    "Failed to load industry override mappings from %s: %s",
                    self._path,
                    exc,
                    exc_info=True,
                )
                return
            if not isinstance(payload, dict):
                logger.warning(
                    "Industry override mapping must be a JSON object: path=%s type=%s",
                    self._path,
                    type(payload).__name__,
                )
                return
            self._override_map = {
                str(key).strip(): str(value).strip()
                for key, value in payload.items()
                if str(key).strip()
            }

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
                payload = json.loads(self.json_path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.error(f"Failed to load agent configs from {self.json_path}: {e}")
                self._configs = {}
                return
            if not isinstance(payload, dict):
                logger.warning(
                    "Agent configs must be a JSON object: path=%s type=%s",
                    self.json_path,
                    type(payload).__name__,
                )
                self._configs = {}
                return
            normalized: dict[str, dict[str, Any]] = {}
            for key, value in payload.items():
                name = str(key or "").strip()
                if not name:
                    continue
                if not isinstance(value, dict):
                    logger.warning(
                        "Agent config entry must be a JSON object: path=%s name=%s type=%s",
                        self.json_path,
                        name,
                        type(value).__name__,
                    )
                    continue
                normalized[name] = dict(value)
            self._configs = normalized

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
