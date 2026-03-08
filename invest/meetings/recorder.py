import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import config

logger = logging.getLogger(__name__)


class MeetingRecorder:
    """
    会议记录持久化

    同时支持 JSON（机器读）和 Markdown（人读）
    目录结构：
        {base_dir}/
        ├── selection/
        │   ├── meeting_0001.json
        │   ├── meeting_0001.md
        │   └── ...
        └── review/
            ├── review_0001.json
            ├── review_0001.md
            └── ...
    """

    def __init__(self, base_dir: str = None):
        if base_dir:
            self.base_dir = Path(base_dir)
        else:
            self.base_dir = config.logs_dir / "meetings"

        self.selection_dir = self.base_dir / "selection"
        self.review_dir = self.base_dir / "review"
        self.selection_dir.mkdir(parents=True, exist_ok=True)
        self.review_dir.mkdir(parents=True, exist_ok=True)

        # 内存记录（用于生成汇总报告）
        self._selection_records: List[Dict] = []
        self._review_records: List[Dict] = []

    def save_selection(self, meeting_log: dict, cycle: int):
        """保存选股会议记录"""
        if not meeting_log or meeting_log.get("fallback"):
            return

        record = {
            "cycle": cycle,
            "timestamp": datetime.now().isoformat(),
            "type": "selection",
            **meeting_log,
        }
        self._selection_records.append(record)

        mid = meeting_log.get("meeting_id", cycle)
        self._write_json(self.selection_dir / f"meeting_{mid:04d}.json", record)
        self._write_text(
            self.selection_dir / f"meeting_{mid:04d}.md",
            self._selection_to_md(meeting_log, cycle),
        )
        logger.debug(f"选股会议记录已保存: cycle={cycle}")

    def save_selection_meeting(self, meeting_log: dict, cycle: int = 0):
        normalized = dict(meeting_log or {})
        if "selected" not in normalized and "final_stocks" in normalized:
            normalized["selected"] = normalized.get("final_stocks", [])
        if "confidence" not in normalized and "regime_confidence" in normalized:
            normalized["confidence"] = normalized.get("regime_confidence")
        if "hunters" not in normalized:
            hunters = []
            if "trend_picks" in normalized:
                hunters.append({"name": "trend_hunter", "result": normalized.get("trend_picks", {})})
            if "contrarian_picks" in normalized:
                hunters.append({"name": "contrarian", "result": normalized.get("contrarian_picks", {})})
            if hunters:
                normalized["hunters"] = hunters
        self.save_selection(normalized, cycle)

    def save_review(self, review_result: dict, facts: dict, cycle: int):
        """保存复盘会议记录"""
        record = {
            "cycle": cycle,
            "timestamp": datetime.now().isoformat(),
            "type": "review",
            "facts": facts,
            "decision": review_result,
        }
        self._review_records.append(record)

        self._write_json(self.review_dir / f"review_{cycle:04d}.json", record)
        self._write_text(
            self.review_dir / f"review_{cycle:04d}.md",
            self._review_to_md(review_result, facts, cycle),
        )
        logger.debug(f"复盘会议记录已保存: cycle={cycle}")

    def save_review_meeting(self, review_result: dict, facts: dict, cycle: int = 0):
        self.save_review(review_result, facts, cycle)

    def get_summary(self) -> Dict:
        """获取汇总统计"""
        all_returns = [
            r.get("facts", {}).get("avg_return", 0)
            for r in self._review_records
        ]
        return {
            "selection_meetings": len(self._selection_records),
            "review_meetings": len(self._review_records),
            "avg_return": sum(all_returns) / len(all_returns) if all_returns else 0,
        }

    def _selection_to_md(self, log: dict, cycle: int) -> str:
        lines = [
            f"# 选股会议 #{log.get('meeting_id', cycle)}",
            f"",
            f"**训练周期**: #{cycle}",
            f"**截断日期**: {log.get('cutoff_date', '')}",
            f"**市场状态**: {log.get('regime', '')} (置信度{log.get('confidence', 0):.0%})",
            f"",
            f"## 最终选股",
        ]
        for code in log.get("selected", []):
            lines.append(f"- {code}")
        lines.append(f"\n**来源**: {log.get('source', '')}")
        for hunter in log.get("hunters", []):
            picks = hunter.get("result", {}).get("picks", [])
            lines.append(f"\n### {hunter.get('name', 'unknown')}")
            for p in picks:
                lines.append(f"- {p.get('code', '')} 评分{p.get('score', 0):.2f}: {p.get('reasoning', '')}")
        return "\n".join(lines)

    def _review_to_md(self, result: dict, facts: dict, cycle: int) -> str:
        lines = [
            f"# 复盘会议 (Cycle #{cycle})",
            f"",
            f"**时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"",
            f"## 近期表现",
            f"- 总轮数: {facts.get('total_cycles', 0)}",
            f"- 胜率: {facts.get('win_rate', 0):.0%}",
            f"- 平均收益: {facts.get('avg_return', 0):+.2f}%",
            f"",
            f"## 决策",
        ]
        for s in result.get("strategy_suggestions", []):
            lines.append(f"- {s}")
        pa = result.get("param_adjustments", {})
        if pa:
            lines.append(f"\n### 参数调整")
            for k, v in pa.items():
                lines.append(f"- {k}: {v}")
        wa = result.get("agent_weight_adjustments", {})
        if wa:
            lines.append(f"\n### Agent 权重调整")
            for agent, w in wa.items():
                arrow = "↑" if w > 1.0 else ("↓" if w < 1.0 else "→")
                lines.append(f"- {agent}: {w:.2f} {arrow}")
        applied_summary = result.get("applied_summary", "")
        if applied_summary:
            lines.append(f"\n**最终执行摘要**: {applied_summary}")
        lines.append(f"\n**理由**: {result.get('reasoning', '')}")
        return "\n".join(lines)

    def _write_json(self, path: Path, data: dict):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)

    def _write_text(self, path: Path, content: str):
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

__all__ = ["MeetingRecorder"]
