import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from config import config


@dataclass
class PredictionRecord:
    """单条 Agent 预测记录"""
    cycle: int
    agent: str           # "trend_hunter" / "contrarian" / ...
    code: str            # 股票代码
    score: float         # Agent 给的评分 (0-1)
    stop_loss_pct: float
    take_profit_pct: float
    reasoning: str = ""

    # 交易结束后填入
    actual_return: Optional[float] = None  # 实际盈亏（绝对值）
    was_selected: bool = False             # 是否被 Commander 选中
    was_profitable: bool = False           # 是否盈利


class AgentTracker:
    """
    Agent 预测追踪器

    记录每个 Agent 的推荐，交易结束后与实际结果对账
    为复盘会议提供事实数据
    """

    def __init__(self):
        self.predictions: List[PredictionRecord] = []
        self._by_cycle: Dict[int, List[PredictionRecord]] = {}

    def record_predictions(self, cycle: int, agent_name: str, picks: List[dict]):
        """记录一个 Agent 的推荐（picks = Agent.analyze() 的 picks 列表）"""
        for p in picks:
            record = PredictionRecord(
                cycle=cycle,
                agent=agent_name,
                code=p.get("code", ""),
                score=p.get("score", 0.5),
                stop_loss_pct=p.get("stop_loss_pct", 0.05),
                take_profit_pct=p.get("take_profit_pct", 0.15),
                reasoning=p.get("reasoning", ""),
            )
            self.predictions.append(record)
            self._by_cycle.setdefault(cycle, []).append(record)

    def mark_selected(self, cycle: int, selected_codes: List[str]):
        """标记哪些推荐被 Commander 选中"""
        selected_set = set(selected_codes)
        for record in self._by_cycle.get(cycle, []):
            record.was_selected = record.code in selected_set

    def record_outcomes(self, cycle: int, per_stock_pnl: Dict[str, float]):
        """记录实际交易结果（per_stock_pnl = {code: 盈亏金额}）"""
        for record in self._by_cycle.get(cycle, []):
            if record.code in per_stock_pnl:
                pnl = per_stock_pnl[record.code]
                record.actual_return = pnl
                record.was_profitable = pnl > 0

    def compute_accuracy(
        self,
        agent_name: str | None = None,
        last_n_cycles: int | None = None,
    ) -> dict:
        """
        计算 Agent 预测准确率

        Returns:
            {agent_name: {total_picks, selected_count, traded_count,
                          profitable_count, accuracy, avg_score}}
        """
        records = self.predictions

        if last_n_cycles is not None and self._by_cycle:
            recent = set(sorted(self._by_cycle.keys())[-last_n_cycles:])
            records = [r for r in records if r.cycle in recent]

        if agent_name:
            records = [r for r in records if r.agent == agent_name]

        agent_records: Dict[str, List[PredictionRecord]] = {}
        for r in records:
            agent_records.setdefault(r.agent, []).append(r)

        stats = {}
        for name, recs in agent_records.items():
            total = len(recs)
            selected = sum(1 for r in recs if r.was_selected)
            traded = sum(1 for r in recs if r.actual_return is not None)
            profitable = sum(1 for r in recs if r.was_profitable)
            avg_score = sum(r.score for r in recs) / total if total > 0 else 0

            stats[name] = {
                "total_picks": total,
                "selected_count": selected,
                "traded_count": traded,
                "profitable_count": profitable,
                "accuracy": profitable / traded if traded > 0 else 0.0,
                "avg_score": round(avg_score, 3),
            }
        return stats

    def get_cycle_summary(self, cycle: int) -> dict:
        """获取单个 cycle 的预测摘要（按 Agent 分组）"""
        by_agent = {}
        for r in self._by_cycle.get(cycle, []):
            by_agent.setdefault(r.agent, []).append({
                "code": r.code,
                "score": r.score,
                "selected": r.was_selected,
                "profitable": r.was_profitable,
                "actual_return": r.actual_return,
            })
        return by_agent

    def get_summary(self) -> dict:
        """获取总体摘要"""
        return {
            "total_predictions": len(self.predictions),
            "total_cycles": len(self._by_cycle),
            "accuracy_by_agent": self.compute_accuracy(),
        }


# ============================================================
# Part 7: 决策追踪日志
# ============================================================

class TraceLog:
    """
    决策追踪日志

    记录每轮决策的全过程，便于复盘和调试
    """

    def __init__(self, log_dir: str | None = None):
        base_logs_dir = config.logs_dir or (config.output_dir / "logs" if config.output_dir is not None else Path("runtime/logs"))
        self.log_dir = log_dir or str(base_logs_dir / "trace")
        self.current_round: int | None = None
        self.round_data: dict = {}

    def start_round(self, round_id: int, t0_date: str):
        """开始一轮"""
        self.current_round = round_id
        self.round_data = {
            "round_id": round_id,
            "t0_date": t0_date,
            "start_time": datetime.now().isoformat(),
            "steps": [],
        }

    def log_step(self, step_name: str, data: Dict):
        """记录步骤"""
        self.round_data["steps"].append({
            "step": step_name,
            "timestamp": datetime.now().isoformat(),
            "data": data,
        })

    def log_decision(self, decision: Dict):
        """记录决策"""
        self.round_data["decision"] = decision

    def log_result(self, result: Dict):
        """记录结果"""
        self.round_data["result"] = result
        self.round_data["end_time"] = datetime.now().isoformat()

    def save(self):
        """保存本轮日志"""
        if not self.current_round:
            return
        os.makedirs(self.log_dir, exist_ok=True)
        filepath = os.path.join(self.log_dir, f"round_{self.current_round:04d}.json")
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.round_data, f, ensure_ascii=False, indent=2, default=str)
        self.current_round = None
        self.round_data = {}

    def load_round(self, round_id: int) -> Dict:
        """加载某轮日志"""
        filepath = os.path.join(self.log_dir, f"round_{round_id:04d}.json")
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

__all__ = ["PredictionRecord", "AgentTracker", "TraceLog"]
