import json
import logging
import re
import time

from app.llm_gateway import LLMGateway, LLMGatewayError, LLMUnavailableError
from config import config

logger = logging.getLogger(__name__)


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

__all__ = ["LLMCaller"]
