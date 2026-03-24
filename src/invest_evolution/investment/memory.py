"""
投资进化系统 — 市场情境记忆库 (Phase 2)

借鉴 TradingAgents 的 FinancialSituationMemory 设计：
- 使用 BM25 算法（无需 API 调用、离线可用）检索历史相似市场情境
- Agent 在 reason() 阶段获取相似历史经验，作为 Prompt 上下文注入
- Agent 在 reflect() 阶段将新经验存入记忆库

BM25 算法核心优势：
- 无向量数据库依赖
- 无 Embedding API 调用（本地纯文本匹配）
- 中文友好（字符级分词）
- 低内存占用
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    from rank_bm25 import BM25Okapi
    _HAS_BM25 = True
except ImportError:
    BM25Okapi = None
    _HAS_BM25 = False


# ===========================================================
# 单条记忆数据类
# ===========================================================

@dataclass
class MemoryEntry:
    """单条市场情境记忆"""
    situation: str   # 市场情境描述（输入）
    action: str      # 采取的决策/行动
    outcome: str     # 结果总结（盈亏、教训）
    context: Dict = field(default_factory=dict)  # 附加上下文（可选）


# ===========================================================
# BM25 市场情境记忆库
# ===========================================================

class MarketSituationMemory:
    """BM25 市场情境记忆库

    为每个 Agent 提供"从类似历史情境中学习"的能力。

    核心使用模式：
    1. 在 reflect() 阶段调用 add_experience() 存入经验
    2. 在 reason() 阶段调用 query() 检索相似历史经验
    3. 将检索结果拼接成文本后注入 LLM Prompt

    Example::

        memory = MarketSituationMemory("trend_hunter")

        # 存入经验
        memory.add_experience(
            situation="牛市初期，沪指连续5日上涨，量能持续放大",
            action="重仓趋势突破股，止损设5%",
            outcome="获利12%，止损点位合理，策略有效"
        )

        # 检索相似经验
        hints = memory.query("大盘量能放大，上涨趋势明显", n_matches=2)
        for h in hints:
            print(h['outcome'])  # 注入 Prompt
    """

    def __init__(self, name: str, save_dir: Optional[Path] = None, *, rebuild_batch_size: int = 32):
        """初始化记忆库。

        Args:
            name: 记忆库名称（通常是 Agent 名称），用于日志和文件命名
            save_dir: 持久化目录，若为 None 则不自动持久化
        """
        self.name = name
        self.save_dir = Path(save_dir) if save_dir else None
        self._entries: List[MemoryEntry] = []
        self._bm25: Optional[Any] = None
        self._tokenized_entries: List[List[str]] = []
        self._indexed_entries_count = 0
        self._bm25_dirty = False
        self._rebuild_batch_size = max(1, int(rebuild_batch_size))

    # ------------------------------------------------------------------ #
    # 写入                                                                  #
    # ------------------------------------------------------------------ #

    def add_experience(
        self,
        situation: str,
        action: str,
        outcome: str,
        context: Optional[Dict] = None,
    ) -> None:
        """存入一条市场情境经验。

        Args:
            situation: 市场情境描述（应当包含行情特征关键词）
            action: 当时采取的操作/决策
            outcome: 结果总结（盈亏、教训、改进方向）
            context: 附加上下文（regime、日期等）
        """
        entry = MemoryEntry(
            situation=situation,
            action=action,
            outcome=outcome,
            context=context or {},
        )
        self._entries.append(entry)
        self._tokenized_entries.append(self._tokenize(entry.situation))
        if _HAS_BM25:
            self._bm25_dirty = True
        logger.debug("Memory[%s] add_experience: %d entries total", self.name, len(self._entries))

    def add_experiences(self, experiences: List[Dict]) -> None:
        """批量存入经验（用于从文件恢复）。

        Args:
            experiences: 每条为 {"situation", "action", "outcome", "context"} 字典列表
        """
        for exp in experiences:
            entry = MemoryEntry(
                situation=exp.get("situation", ""),
                action=exp.get("action", ""),
                outcome=exp.get("outcome", ""),
                context=exp.get("context", {}),
            )
            self._entries.append(entry)
            self._tokenized_entries.append(self._tokenize(entry.situation))
        self._indexed_entries_count = 0
        self._bm25_dirty = bool(self._entries) if _HAS_BM25 else False

    # ------------------------------------------------------------------ #
    # 查询                                                                  #
    # ------------------------------------------------------------------ #

    def query(
        self,
        current_situation: str,
        n_matches: int = 3,
        min_score: float = 0.0,
    ) -> List[Dict]:
        """检索与当前市场情境最相似的历史经验。

        Args:
            current_situation: 当前市场情境描述
            n_matches: 返回的最大结果数
            min_score: 归一化相似度最低阈值（0~1），低于此值不返回

        Returns:
            匹配结果列表，每条含：
            - situation: 历史情境
            - action: 历史决策
            - outcome: 历史结果/教训
            - similarity_score: 归一化相似度
        """
        if not self._entries:
            return []

        self._ensure_bm25_index()
        if _HAS_BM25 and self._bm25 is not None:
            return self._query_bm25(current_situation, n_matches, min_score)
        else:
            return self._query_keyword(current_situation, n_matches)

    def format_hints_for_prompt(
        self,
        current_situation: str,
        n_matches: int = 2,
    ) -> str:
        """将查询结果格式化为 Prompt 可直接插入的文本块。

        Args:
            current_situation: 当前情境描述
            n_matches: 返回条数

        Returns:
            格式化的历史经验文本，若无记忆则返回空字符串
        """
        hits = self.query(current_situation, n_matches)
        if not hits:
            return ""

        lines = ["历史相似情境参考："]
        for i, hit in enumerate(hits, 1):
            lines.append(
                f"{i}. 【情境】{hit['situation'][:100]}\n"
                f"   【决策】{hit['action'][:80]}\n"
                f"   【教训】{hit['outcome'][:150]}"
            )
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # 持久化                                                                #
    # ------------------------------------------------------------------ #

    def save(self, path: Optional[Path] = None) -> None:
        """将记忆库序列化到 JSON 文件。

        Args:
            path: 文件路径，若为 None 则使用 save_dir/{name}.json
        """
        target = path or self._default_path()
        if target is None:
            logger.debug("Memory[%s] save_dir not set, skip save.", self.name)
            return

        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        data = [
            {
                "situation": e.situation,
                "action": e.action,
                "outcome": e.outcome,
                "context": e.context,
            }
            for e in self._entries
        ]
        with open(target, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info("Memory[%s] saved %d entries → %s", self.name, len(self._entries), target)

    def load(self, path: Optional[Path] = None) -> None:
        """从 JSON 文件恢复记忆库。

        Args:
            path: 文件路径，若为 None 则尝试从 save_dir/{name}.json 加载
        """
        target = path or self._default_path()
        if target is None or not Path(target).exists():
            return

        with open(Path(target), encoding="utf-8") as f:
            data = json.load(f)
        self.clear()
        self.add_experiences(data)
        logger.info("Memory[%s] loaded %d entries ← %s", self.name, len(self._entries), target)

    # ------------------------------------------------------------------ #
    # 工具                                                                  #
    # ------------------------------------------------------------------ #

    def clear(self) -> None:
        """清空所有记忆。"""
        self._entries.clear()
        self._bm25 = None
        self._tokenized_entries = []
        self._indexed_entries_count = 0
        self._bm25_dirty = False

    def __len__(self) -> int:
        return len(self._entries)

    def _default_path(self) -> Optional[Path]:
        if self.save_dir is None:
            return None
        return Path(self.save_dir) / f"{self.name}.json"

    def _tokenize(self, text: str) -> List[str]:
        """中英文分词（字符级）。"""
        # 英文：按词分割；中文：逐字切分
        tokens = re.findall(r"[a-zA-Z0-9]+|[\u4e00-\u9fff]", text.lower())
        return tokens if tokens else ["_empty_"]

    def _rebuild_index(self) -> None:
        """重建 BM25 索引。"""
        if not _HAS_BM25:
            return
        if not self._entries:
            self._bm25 = None
            self._indexed_entries_count = 0
            self._bm25_dirty = False
            return
        tokenized = list(self._tokenized_entries) or [self._tokenize(e.situation) for e in self._entries]
        self._tokenized_entries = tokenized
        assert BM25Okapi is not None
        self._bm25 = BM25Okapi(tokenized)
        self._indexed_entries_count = len(tokenized)
        self._bm25_dirty = False

    def _ensure_bm25_index(self) -> None:
        if not _HAS_BM25:
            return
        pending_entries = self._pending_entries_count()
        if self._bm25 is None or self._indexed_entries_count <= 0:
            self._rebuild_index()
            return
        if self._bm25_dirty and pending_entries >= self._rebuild_batch_size:
            self._rebuild_index()

    def _query_bm25(
        self,
        query: str,
        n_matches: int,
        min_score: float,
    ) -> List[Dict]:
        """使用 BM25 检索。"""
        tokens = self._tokenize(query)
        candidates: List[tuple[float, int]] = []
        if self._bm25 is not None and self._indexed_entries_count > 0:
            indexed_scores = list(self._bm25.get_scores(tokens))  # type: ignore[arg-type]
            indexed_scores = indexed_scores[: self._indexed_entries_count]
            indexed_max = max(indexed_scores) if indexed_scores and max(indexed_scores) > 0 else 0.0
            for idx, score in enumerate(indexed_scores):
                normalized = float(score) / indexed_max if indexed_max > 0 else 0.0
                candidates.append((normalized, idx))

        query_tokens = set(tokens)
        for idx in range(self._indexed_entries_count, len(self._entries)):
            pending_score = self._keyword_overlap_score(query_tokens, self._tokenized_entries[idx])
            candidates.append((pending_score, idx))

        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        results = []
        for score, idx in candidates[:n_matches]:
            if score < min_score:
                continue
            e = self._entries[idx]
            results.append({
                "situation": e.situation,
                "action": e.action,
                "outcome": e.outcome,
                "context": e.context,
                "similarity_score": score,
            })
        return results

    def _query_keyword(
        self,
        query: str,
        n_matches: int,
    ) -> List[Dict]:
        """降级：简单关键词重叠率检索（BM25 不可用时）。"""
        query_tokens = set(self._tokenize(query))
        scored = []
        for idx, e in enumerate(self._entries):
            overlap = self._keyword_overlap_score(query_tokens, self._tokenized_entries[idx])
            scored.append((overlap, e))

        scored.sort(key=lambda x: x[0], reverse=True)
        results = []
        for score, e in scored[:n_matches]:
            results.append({
                "situation": e.situation,
                "action": e.action,
                "outcome": e.outcome,
                "context": e.context,
                "similarity_score": score,
            })
        return results

    def _pending_entries_count(self) -> int:
        return max(0, len(self._entries) - self._indexed_entries_count)

    def _keyword_overlap_score(self, query_tokens: set[str], entry_tokens: List[str]) -> float:
        entry_token_set = set(entry_tokens)
        if not entry_token_set:
            return 0.0
        return len(query_tokens & entry_token_set) / (len(query_tokens | entry_token_set) + 1e-9)
