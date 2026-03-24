from __future__ import annotations

import argparse
import json
from pathlib import Path

from invest_evolution.investment.governance.engine import build_allocation_plan


def main() -> None:
    parser = argparse.ArgumentParser(description="Market regime 自动模型分配器")
    parser.add_argument("--regime", type=str, required=True, help="当前市场状态: bull/bear/oscillation")
    parser.add_argument("--leaderboard", type=str, default="runtime/outputs/leaderboard.json", help="leaderboard 路径")
    parser.add_argument("--top-n", type=int, default=3, help="参与分配的前 N 个模型")
    args = parser.parse_args()

    plan = build_allocation_plan(args.regime, Path(args.leaderboard), top_n=max(1, args.top_n))
    print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
