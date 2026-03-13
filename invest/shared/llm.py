import ast
import json
import logging
import os
import re
import time

from app.llm_gateway import LLMGateway, LLMGatewayError, LLMUnavailableError
from config import config
from config.control_plane import resolve_default_llm

logger = logging.getLogger(__name__)

_FENCE_PATTERN = re.compile(r"```(?:json)?\s*(.*?)(?:```|$)", re.IGNORECASE | re.DOTALL)


def parse_llm_json_object(text: str) -> dict:
    return LLMCaller.parse_json_text(text)


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
        model: str | None = None,
        api_key: str | None = None,
        api_base: str | None = None,
        timeout: int | None = None,
        max_retries: int | None = None,
        dry_run: bool = False,
    ):
        default_fast = resolve_default_llm("fast")
        self.model = model or default_fast.model
        self.api_key = api_key or default_fast.api_key
        self.api_base = api_base or default_fast.api_base
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

        self.call_count = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_time = 0.0
        self.errors = 0

    def apply_runtime_limits(self, *, timeout: int | None = None, max_retries: int | None = None) -> None:
        updated = False
        if timeout is not None:
            self.timeout = int(timeout)
            updated = True
        if max_retries is not None:
            self.max_retries = int(max_retries)
            updated = True
        if updated:
            self.gateway.timeout = self.timeout
            self.gateway.max_retries = self.max_retries


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

        if os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("INVEST_ENABLE_LIVE_LLM_TESTS"):
            logger.info("[PYTEST] Live LLM call disabled; using fallback path.")
            return ""

        if os.environ.get("INVEST_DISABLE_LIVE_LLM"):
            logger.info("[RUNTIME] Live LLM disabled by INVEST_DISABLE_LIVE_LLM; using fallback path.")
            return ""

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
        *,
        warn_on_parse_error: bool = True,
        **kwargs,
    ) -> dict:
        raw = self.call(system_prompt, user_message, **kwargs)
        return self._parse_json(raw, warn_on_parse_error=warn_on_parse_error)

    @classmethod
    def parse_json_text(cls, text: str, warn_on_failure: bool = True) -> dict:
        normalized = cls._normalize_text(text)
        if not normalized:
            return {"_parse_error": True, "_raw": "", "_error": "llm_unavailable_or_empty"}

        candidates = cls._collect_json_candidates(normalized)
        seen: set[str] = set()
        for candidate in candidates:
            candidate = cls._normalize_candidate(candidate)
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            parsed = cls._try_parse_object(candidate)
            if parsed is not None:
                return parsed

        if warn_on_failure:
            logger.warning("Failed to parse JSON from LLM response: %s...", normalized[:200])
        return {"_parse_error": True, "_raw": normalized}

    def _parse_json(self, text: str, warn_on_parse_error: bool = True) -> dict:
        return self.parse_json_text(text, warn_on_failure=warn_on_parse_error)

    @staticmethod
    def _normalize_text(text: str) -> str:
        normalized = (text or "").replace("\ufeff", "").replace("\u200b", "").strip()
        normalized = normalized.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace("\u2019", "'")
        return normalized

    @classmethod
    def _collect_json_candidates(cls, text: str) -> list[str]:
        candidates = [text]
        candidates.extend(match.group(1).strip() for match in _FENCE_PATTERN.finditer(text) if match.group(1).strip())

        stripped_fences = cls._strip_markdown_fences(text)
        if stripped_fences and stripped_fences != text:
            candidates.append(stripped_fences)

        first_object = cls._extract_first_object_candidate(text)
        if first_object and first_object not in candidates:
            candidates.append(first_object)

        candidates.extend(cls._extract_balanced_json_objects(text))
        return candidates

    @staticmethod
    def _strip_markdown_fences(text: str) -> str:
        stripped = text.strip()
        if stripped.startswith("```"):
            first_newline = stripped.find("\n")
            if first_newline != -1:
                stripped = stripped[first_newline + 1 :]
            else:
                stripped = ""
        if stripped.endswith("```"):
            stripped = stripped[:-3]
        return stripped.strip()

    @staticmethod
    def _normalize_candidate(candidate: str) -> str:
        candidate = candidate.replace("\ufeff", "").replace("\u200b", "").strip()
        candidate = LLMCaller._strip_markdown_fences(candidate)
        candidate = re.sub(r"^json\s*", "", candidate, flags=re.IGNORECASE).strip()
        candidate = re.sub(r"^(下面是(?:最终)?JSON[：:]?|输出如下[：:]?)\s*", "", candidate)
        return candidate.strip()

    @classmethod
    def _try_parse_object(cls, candidate: str) -> dict | None:
        for variant in cls._candidate_variants(candidate):
            try:
                value = json.loads(variant)
            except json.JSONDecodeError:
                value = cls._raw_decode_object(variant)
                if value is None:
                    value = cls._literal_eval_object(variant)
            if isinstance(value, dict):
                return value
        return None

    @classmethod
    def _candidate_variants(cls, candidate: str) -> list[str]:
        variants: list[str] = []

        def _push(value: str):
            value = (value or "").strip()
            if value and value not in variants:
                variants.append(value)

        _push(candidate)
        first_brace = candidate.find('{')
        if first_brace > 0:
            _push(candidate[first_brace:])

        trimmed = cls._trim_to_outer_object(candidate)
        _push(trimmed)

        repaired = cls._repair_common_json_issues(trimmed or candidate)
        _push(repaired)

        if repaired:
            _push(cls._trim_to_outer_object(repaired))
        return variants

    @staticmethod
    def _raw_decode_object(candidate: str) -> dict | None:
        decoder = json.JSONDecoder()
        try:
            value, _ = decoder.raw_decode(candidate)
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass
        return None

    @staticmethod
    def _literal_eval_object(candidate: str) -> dict | None:
        normalized = candidate.strip()
        if not normalized or '{' not in normalized:
            return None
        try:
            value = ast.literal_eval(normalized)
            if isinstance(value, dict):
                return value
        except Exception:
            return None
        return None

    @staticmethod
    def _trim_to_outer_object(candidate: str) -> str:
        start = candidate.find('{')
        end = candidate.rfind('}')
        if start == -1:
            return candidate.strip()
        if end != -1 and end >= start:
            return candidate[start:end + 1].strip()
        return candidate[start:].strip()

    @staticmethod
    def _sanitize_string_controls(candidate: str) -> str:
        if not candidate:
            return candidate
        out: list[str] = []
        in_string = False
        escape = False
        for ch in candidate:
            if escape:
                out.append(ch)
                escape = False
                continue
            if ch == "\\":
                out.append(ch)
                escape = True
                continue
            if ch == '"':
                out.append(ch)
                in_string = not in_string
                continue
            if in_string and ch in {'\n', '\r', '\t'}:
                out.append(' ')
                continue
            out.append(ch)
        return ''.join(out)

    @staticmethod
    def _escape_unescaped_string_quotes(candidate: str) -> str:
        if not candidate:
            return candidate
        out: list[str] = []
        in_string = False
        escape = False
        length = len(candidate)
        for idx, ch in enumerate(candidate):
            if escape:
                out.append(ch)
                escape = False
                continue
            if ch == "\\":
                out.append(ch)
                escape = True
                continue
            if ch == '"':
                if not in_string:
                    out.append(ch)
                    in_string = True
                    continue

                look_ahead = idx + 1
                while look_ahead < length and candidate[look_ahead].isspace():
                    look_ahead += 1
                next_sig = candidate[look_ahead] if look_ahead < length else ''
                if next_sig in {',', '}', ']', ':'} or not next_sig:
                    out.append(ch)
                    in_string = False
                else:
                    out.append('\\"')
                continue

            out.append(ch)
        return ''.join(out)

    @classmethod
    def _repair_common_json_issues(cls, candidate: str) -> str:
        repaired = cls._trim_to_outer_object(candidate)
        if not repaired:
            return repaired

        repaired = cls._sanitize_string_controls(repaired)
        repaired = cls._escape_unescaped_string_quotes(repaired)
        repaired = re.sub(r',\s*([}\]])', r'\1', repaired)

        stack: list[str] = []
        in_string = False
        escape = False
        for ch in repaired:
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == '{':
                stack.append('}')
            elif ch == '[':
                stack.append(']')
            elif ch in '}]' and stack and ch == stack[-1]:
                stack.pop()

        if in_string:
            repaired += '"'
        if stack:
            repaired += ''.join(reversed(stack))
        return repaired.strip()

    @staticmethod
    def _extract_first_object_candidate(text: str) -> str:
        start = text.find('{')
        if start == -1:
            return ''
        return text[start:].strip()

    @staticmethod
    def _extract_balanced_json_objects(text: str) -> list[str]:
        results: list[str] = []
        in_string = False
        escape = False
        depth = 0
        start = -1

        for idx, ch in enumerate(text):
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == '{':
                if depth == 0:
                    start = idx
                depth += 1
            elif ch == '}':
                if depth == 0:
                    continue
                depth -= 1
                if depth == 0 and start >= 0:
                    results.append(text[start:idx + 1])
                    start = -1
        return results

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


__all__ = ["LLMCaller", "parse_llm_json_object"]
