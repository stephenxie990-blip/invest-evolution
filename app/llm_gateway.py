"""
Unified LLM gateway for both training pipeline and commander runtime.

This module is the only outbound channel to external LLM providers.
"""

from __future__ import annotations

import asyncio
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

    @property
    def available(self) -> bool:
        return litellm is not None and bool(self.api_key)

    def assert_available(self) -> None:
        if litellm is None:
            raise LLMUnavailableError("litellm is not installed")
        if not self.api_key:
            raise LLMUnavailableError("LLM API key is empty")

    def completion_raw(
        self,
        messages: list[dict[str, Any]],
        temperature: float = 0.7,
        max_tokens: int = 2048,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
    ) -> Any:
        self.assert_available()

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "api_key": self.api_key,
            "base_url": self.api_base,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "timeout": self.timeout,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice or "auto"

        retries = max(1, int(self.max_retries))
        last_error: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                return litellm.completion(**kwargs)
            except getattr(litellm, "RateLimitError", Exception) as exc: # pragma: no cover
                last_error = exc
                if attempt < retries:
                    time.sleep(min(15, 3 * attempt)) # Longer sleep for rate limit
                continue
            except getattr(litellm, "APIConnectionError", Exception) as exc: # pragma: no cover
                last_error = exc
                if attempt < retries:
                    time.sleep(min(5, 1.5 * attempt))
                continue
            except Exception as exc:  # pragma: no cover
                last_error = exc
                break # Don't retry on unknown exceptions like validation errors
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

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "api_key": self.api_key,
            "base_url": self.api_base,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "timeout": self.timeout,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice or "auto"

        retries = max(1, int(self.max_retries))
        last_error: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                return await litellm.acompletion(**kwargs)
            except getattr(litellm, "RateLimitError", Exception) as exc: # pragma: no cover
                last_error = exc
                if attempt < retries:
                    await asyncio.sleep(min(15, 3 * attempt))
                continue
            except getattr(litellm, "APIConnectionError", Exception) as exc: # pragma: no cover
                last_error = exc
                if attempt < retries:
                    await asyncio.sleep(min(5, 1.5 * attempt))
                continue
            except Exception as exc:  # pragma: no cover
                last_error = exc
                break
        raise LLMGatewayError(f"LLM async completion failed: {last_error}")
