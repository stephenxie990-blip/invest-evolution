from __future__ import annotations

import argparse
import json
from pathlib import Path

from invest_evolution.investment.governance.engine import write_leaderboard


def main() -> None:
    parser = argparse.ArgumentParser(description="多经理比较器 / leaderboard 生成器")
    parser.add_argument("--root", type=str, default="runtime/outputs", help="训练输出根目录")
    parser.add_argument("--output", type=str, default=None, help="leaderboard 输出路径，默认写到 root/leaderboard.json")
    args = parser.parse_args()

    leaderboard = write_leaderboard(Path(args.root), Path(args.output) if args.output else None)
    print(json.dumps({
        "total_records": leaderboard.get("total_records", 0),
        "total_managers": leaderboard.get("total_managers", 0),
        "eligible_managers": leaderboard.get("eligible_managers", 0),
        "best_entry_manager_id": (leaderboard.get("best_entry") or {}).get("manager_id"),
        "output": str(Path(args.output) if args.output else Path(args.root) / "leaderboard.json"),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
