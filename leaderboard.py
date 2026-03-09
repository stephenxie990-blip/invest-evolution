from __future__ import annotations

import argparse
import json
from pathlib import Path

from invest.leaderboard import write_leaderboard


def main() -> None:
    parser = argparse.ArgumentParser(description="多模型比较器 / leaderboard 生成器")
    parser.add_argument("--root", type=str, default="runtime/outputs", help="训练输出根目录")
    parser.add_argument("--output", type=str, default=None, help="leaderboard 输出路径，默认写到 root/leaderboard.json")
    args = parser.parse_args()

    leaderboard = write_leaderboard(Path(args.root), Path(args.output) if args.output else None)
    print(json.dumps({
        "total_records": leaderboard.get("total_records", 0),
        "total_models": leaderboard.get("total_models", 0),
        "best_model": (leaderboard.get("best_model") or {}).get("model_name"),
        "output": str(Path(args.output) if args.output else Path(args.root) / "leaderboard.json"),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
