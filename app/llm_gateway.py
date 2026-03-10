"""
Unified LLM gateway for both training pipeline and commander runtime.

This module is the only outbound channel to external LLM providers.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import threading
import time
from dataclasses import dataclass
from typing import Any

try:
    import litellm
except Exception:  # pragma: no cover
    litellm = None


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

    def __post_init__(self):
        masked_key = f"{self.api_key[:4]}...{self.api_key[-4:]}" if self.api_key and len(self.api_key) > 8 else "***"
        import logging
        logging.getLogger(__name__).debug(f"LLMGateway initialized for model {self.model} with key {masked_key}")
        if litellm is not None:
            for attr, value in (("set_verbose", False), ("suppress_debug_info", True), ("turn_off_message_logging", True)):
                if hasattr(litellm, attr):
                    try:
                        setattr(litellm, attr, value)
                    except Exception:
                        pass

    @property
    def available(self) -> bool:
        return litellm is not None and bool(self.api_key)

    def assert_available(self) -> None:
        if litellm is None:
            raise LLMUnavailableError("litellm is not installed")
        if not self.api_key:
            raise LLMUnavailableError("LLM API key is empty")

    def _normalized_temperature(self, temperature: float) -> float:
        model_name = (self.model or "").lower()
        if model_name.startswith("gpt-5") and float(temperature) != 1.0:
            return 1.0
        return temperature

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
            "model": self.model,
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

        kwargs = self._build_completion_kwargs(messages, temperature, max_tokens, tools=tools, tool_choice=tool_choice)

        retries = max(1, int(self.max_retries))
        last_error: Exception | None = None
        hard_timeout = self._hard_timeout_seconds()
        for attempt in range(1, retries + 1):
            try:
                with _sync_timeout_guard(hard_timeout):
                    response = litellm.completion(**kwargs)
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

        kwargs = self._build_completion_kwargs(messages, temperature, max_tokens, tools=tools, tool_choice=tool_choice)

        retries = max(1, int(self.max_retries))
        last_error: Exception | None = None
        hard_timeout = self._hard_timeout_seconds()
        for attempt in range(1, retries + 1):
            try:
                response = await asyncio.wait_for(litellm.acompletion(**kwargs), timeout=hard_timeout)
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
