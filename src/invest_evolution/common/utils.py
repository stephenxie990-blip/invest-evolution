"""Common utilities, LLM gateway, and router services."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import signal
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TYPE_CHECKING

try:
    import litellm
except Exception:  # pragma: no cover
    litellm = None

logger = logging.getLogger(__name__)


def normalize_limit(
    limit: int | None,
    *,
    default: int,
    maximum: int | None = None,
) -> int:
    if limit in (None, ""):
        normalized = int(default)
    else:
        normalized = int(limit)
    if normalized <= 0:
        return 0
    if maximum is not None:
        return min(normalized, int(maximum))
    return normalized


def list_json_artifact_paths(directory: Path, *, limit: int, default: int = 20) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    normalized_limit = normalize_limit(limit, default=default)
    if normalized_limit <= 0:
        return []
    return sorted(directory.glob("*.json"), reverse=True)[:normalized_limit]


def safe_read_json_dict(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}

_OPENAI_COMPAT_MODEL_PATTERN = re.compile(
    r"^(gpt-[a-z0-9._-]+|o[134](?:[a-z0-9._-]*)|text-embedding-[a-z0-9._-]+|omni-[a-z0-9._-]+|tts-[a-z0-9._-]+|whisper-[a-z0-9._-]+)$",
    re.IGNORECASE,
)


class LLMGatewayError(RuntimeError):
    """Base gateway error."""


class LLMUnavailableError(LLMGatewayError):
    """Raised when provider dependencies/configuration are unavailable."""


def _hard_timeout_grace_seconds() -> float:
    try:
        return max(0.0, float(os.environ.get("INVEST_LLM_HARD_TIMEOUT_GRACE", "5")))
    except (TypeError, ValueError):
        return 5.0


@contextlib.contextmanager
def _sync_timeout_guard(seconds: float):
    if seconds <= 0 or threading.current_thread() is not threading.main_thread() or not hasattr(signal, "setitimer"):
        yield
        return

    def _raise_timeout(signum, frame):
        raise TimeoutError(f"LLM hard timeout exceeded ({seconds:.1f}s)")

    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    signal.signal(signal.SIGALRM, _raise_timeout)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer != (0.0, 0.0):
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)


@dataclass
class LLMGateway:
    model: str
    api_key: str = ""
    api_base: str = ""
    timeout: int = 60
    max_retries: int = 2
    unavailable_message: str = ""

    def __post_init__(self):
        masked_key = f"{self.api_key[:4]}...{self.api_key[-4:]}" if self.api_key and len(self.api_key) > 8 else "***"
        logger.debug("LLMGateway initialized for model %s with key %s", self.model, masked_key)
        if litellm is not None:
            for attr, value in (("set_verbose", False), ("suppress_debug_info", True), ("turn_off_message_logging", True)):
                if hasattr(litellm, attr):
                    try:
                        setattr(litellm, attr, value)
                    except Exception as exc:
                        logger.debug("Failed to configure litellm.%s=%r: %s", attr, value, exc)

    @property
    def available(self) -> bool:
        return litellm is not None and bool(self.api_key)

    def assert_available(self) -> None:
        if litellm is None:
            raise LLMUnavailableError("litellm is not installed")
        if not self.api_key:
            raise LLMUnavailableError(
                self.unavailable_message
                or "LLM provider api_key is empty; configure the system provider api_key."
            )

    def _normalized_temperature(self, temperature: float) -> float:
        model_name = self._model_family_name()
        if model_name.startswith("gpt-5") and float(temperature) != 1.0:
            return 1.0
        return temperature

    def _model_family_name(self) -> str:
        normalized = self._normalized_model_name().lower()
        if "/" in normalized:
            return normalized.split("/", 1)[1]
        return normalized

    def _normalized_model_name(self) -> str:
        model_name = str(self.model or "").strip()
        if not model_name or "/" in model_name:
            return model_name
        if self.api_base and _OPENAI_COMPAT_MODEL_PATTERN.match(model_name):
            return f"openai/{model_name}"
        return model_name

    def _has_valid_choices(self, response: Any) -> bool:
        try:
            choices = getattr(response, "choices", None)
            return bool(choices) and len(choices) > 0 and getattr(choices[0], "message", None) is not None
        except Exception:
            return False

    def _build_completion_kwargs(
        self,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self._normalized_model_name(),
            "messages": messages,
            "api_key": self.api_key,
            "base_url": self.api_base,
            "temperature": self._normalized_temperature(temperature),
            "max_tokens": max_tokens,
            "timeout": self.timeout,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice or "auto"
        return kwargs

    def _hard_timeout_seconds(self) -> float:
        return max(1.0, float(self.timeout)) + _hard_timeout_grace_seconds()

    @staticmethod
    def _matches_provider_error(exc: Exception, error_name: str) -> bool:
        provider_error = getattr(litellm, error_name, None)
        return isinstance(provider_error, type) and isinstance(exc, provider_error)

    def completion_raw(
        self,
        messages: list[dict[str, Any]],
        temperature: float = 0.7,
        max_tokens: int = 2048,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
    ) -> Any:
        self.assert_available()
        client = litellm
        if client is None:
            raise LLMUnavailableError("litellm is not installed")

        kwargs = self._build_completion_kwargs(messages, temperature, max_tokens, tools=tools, tool_choice=tool_choice)

        retries = max(1, int(self.max_retries))
        last_error: Exception | None = None
        hard_timeout = self._hard_timeout_seconds()
        for attempt in range(1, retries + 1):
            try:
                with _sync_timeout_guard(hard_timeout):
                    response = client.completion(**kwargs)
                if self._has_valid_choices(response):
                    return response
                last_error = ValueError("LLM returned empty or malformed choices")
                if attempt < retries:
                    time.sleep(min(3, 1 * attempt))
                    continue
                break
            except TimeoutError as exc:  # pragma: no cover
                last_error = exc
                break
            except Exception as exc:  # pragma: no cover
                last_error = exc
                if self._matches_provider_error(exc, "RateLimitError"):
                    if attempt < retries:
                        time.sleep(min(15, 3 * attempt))
                        continue
                    break
                if self._matches_provider_error(exc, "APIConnectionError"):
                    if attempt < retries:
                        time.sleep(min(5, 1.5 * attempt))
                        continue
                    break
                break
        raise LLMGatewayError(f"LLM completion failed: {last_error}")

    async def acompletion_raw(
        self,
        messages: list[dict[str, Any]],
        temperature: float = 0.7,
        max_tokens: int = 2048,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
    ) -> Any:
        self.assert_available()
        client = litellm
        if client is None:
            raise LLMUnavailableError("litellm is not installed")

        kwargs = self._build_completion_kwargs(messages, temperature, max_tokens, tools=tools, tool_choice=tool_choice)

        retries = max(1, int(self.max_retries))
        last_error: Exception | None = None
        hard_timeout = self._hard_timeout_seconds()
        for attempt in range(1, retries + 1):
            try:
                response = await asyncio.wait_for(client.acompletion(**kwargs), timeout=hard_timeout)
                if self._has_valid_choices(response):
                    return response
                last_error = ValueError("LLM returned empty or malformed choices")
                if attempt < retries:
                    await asyncio.sleep(min(3, 1 * attempt))
                    continue
                break
            except asyncio.TimeoutError as exc:  # pragma: no cover
                last_error = exc
                break
            except Exception as exc:  # pragma: no cover
                last_error = exc
                if self._matches_provider_error(exc, "RateLimitError"):
                    if attempt < retries:
                        await asyncio.sleep(min(15, 3 * attempt))
                        continue
                    break
                if self._matches_provider_error(exc, "APIConnectionError"):
                    if attempt < retries:
                        await asyncio.sleep(min(5, 1.5 * attempt))
                        continue
                    break
                break
        raise LLMGatewayError(f"LLM async completion failed: {last_error}")

if TYPE_CHECKING:
    from invest_evolution.config import EvolutionConfig


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

    _fast_caller: Any
    _deep_caller: Any

    # ------------------------------------------------------------------ #
    # 构造                                                                  #
    # ------------------------------------------------------------------ #

    @classmethod
    def from_config(
        cls,
        cfg: "EvolutionConfig",
        dry_run: bool = False,
    ) -> "LLMRouter":
        from invest_evolution.investment.shared.llm import LLMCaller

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

    def fast(self) -> Any:
        """返回快思考 LLMCaller（用于高频、低推理要求任务）。

        适用场景：
        - TrendHunterAgent / ContrarianAgent 候选筛选
        - MarketRegimeAgent 市场状态分析
        - 数据摘要生成
        """
        return self._fast_caller

    def deep(self) -> Any:
        """返回深度推理 LLMCaller（用于关键决策任务）。

        适用场景：
        - StrategistAgent 组合风险评估
        - Dual review EvoJudge / ReviewDecision 复盘裁判
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
