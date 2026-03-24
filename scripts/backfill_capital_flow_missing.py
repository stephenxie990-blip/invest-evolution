#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from invest_evolution.market_data.manager import _fetch_capital_flow_history_with_session  # noqa: E402
from invest_evolution.market_data.repository import MarketDataRepository  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="断点续跑补齐 capital_flow_daily 缺失股票")
    parser.add_argument("--db", default=None, help="SQLite 数据库路径，默认项目 data/stock_history.db")
    parser.add_argument("--batch-size", type=int, default=150, help="每批处理股票数")
    parser.add_argument("--rounds", type=int, default=0, help="最多轮数，0 表示直到补完")
    parser.add_argument("--sleep", type=float, default=1.0, help="批次间休眠秒数")
    parser.add_argument("--request-sleep", type=float, default=0.12, help="单股请求间隔秒数")
    parser.add_argument("--timeout", type=float, default=6.0, help="单股请求超时秒数")
    parser.add_argument("--retries", type=int, default=1, help="单股额外重试次数")
    return parser.parse_args()


def get_missing_codes(repository: MarketDataRepository) -> list[str]:
    query = """
        SELECT DISTINCT d.code
        FROM daily_bar d
        WHERE d.adj_flag = 'hfq'
          AND (d.code LIKE 'sh.%' OR d.code LIKE 'sz.%' OR d.code LIKE 'bj.%')
          AND d.code NOT IN (
              SELECT DISTINCT code FROM capital_flow_daily
          )
        ORDER BY d.code
    """
    with repository.connect() as conn:
        rows = conn.execute(query).fetchall()
    return [str(row[0]) for row in rows if row and row[0]]


def get_stats(repository: MarketDataRepository) -> tuple[int, int, int]:
    query = """
        SELECT 'codes', COUNT(DISTINCT code) FROM capital_flow_daily
        UNION ALL
        SELECT 'rows', COUNT(*) FROM capital_flow_daily
        UNION ALL
        SELECT 'missing', COUNT(*) FROM (
            SELECT DISTINCT d.code
            FROM daily_bar d
            WHERE d.adj_flag='hfq'
              AND d.code NOT IN (SELECT DISTINCT code FROM capital_flow_daily)
        )
    """
    with repository.connect() as conn:
        result = {name: value for name, value in conn.execute(query).fetchall()}
    return int(result.get("codes", 0)), int(result.get("rows", 0)), int(result.get("missing", 0))


def rows_to_records(code: str, frame) -> list[dict]:
    records: list[dict] = []
    if frame is None or frame.empty:
        return records
    for _, flow_row in frame.iterrows():
        records.append(
            {
                "code": code,
                "trade_date": flow_row.get("日期"),
                "close": flow_row.get("收盘价"),
                "pct_chg": flow_row.get("涨跌幅"),
                "main_net_inflow": flow_row.get("主力净流入-净额"),
                "main_net_inflow_ratio": flow_row.get("主力净流入-净占比"),
                "super_large_net_inflow": flow_row.get("超大单净流入-净额"),
                "super_large_net_inflow_ratio": flow_row.get("超大单净流入-净占比"),
                "large_net_inflow": flow_row.get("大单净流入-净额"),
                "large_net_inflow_ratio": flow_row.get("大单净流入-净占比"),
                "medium_net_inflow": flow_row.get("中单净流入-净额"),
                "medium_net_inflow_ratio": flow_row.get("中单净流入-净占比"),
                "small_net_inflow": flow_row.get("小单净流入-净额"),
                "small_net_inflow_ratio": flow_row.get("小单净流入-净占比"),
                "source": "eastmoney",
            }
        )
    return records


def main() -> int:
    args = parse_args()
    db_path = Path(args.db).expanduser() if args.db else None
    repository = MarketDataRepository(db_path)
    repository.initialize_schema()

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            )
        }
    )

    round_index = 0
    while True:
        missing = get_missing_codes(repository)
        if not missing:
            codes, rows, remain = get_stats(repository)
            print(f"[done] capital_flow_daily 已补齐: codes={codes}, rows={rows}, missing={remain}", flush=True)
            return 0

        round_index += 1
        if args.rounds and round_index > args.rounds:
            codes, rows, remain = get_stats(repository)
            print(f"[stop] 达到轮数上限: rounds={args.rounds}, codes={codes}, rows={rows}, missing={remain}", flush=True)
            return 0

        total = len(missing)
        batches = math.ceil(total / max(1, args.batch_size))
        print(f"[round {round_index}] missing={total}, batch_size={args.batch_size}, batches={batches}", flush=True)

        for batch_index in range(batches):
            batch = missing[batch_index * args.batch_size:(batch_index + 1) * args.batch_size]
            if not batch:
                continue
            started = time.time()
            records: list[dict] = []
            touched = 0
            failed = 0

            for code in batch:
                frame = None
                error = None
                for attempt in range(args.retries + 1):
                    try:
                        frame = _fetch_capital_flow_history_with_session(session, code, timeout=args.timeout)
                        error = None
                        break
                    except Exception as exc:
                        error = exc
                        if attempt < args.retries:
                            time.sleep(max(0.2, args.request_sleep) * (attempt + 1))
                if error is not None:
                    failed += 1
                    print(f"    fail {code}: {error}", flush=True)
                    continue
                if frame is None or frame.empty:
                    continue
                touched += 1
                records.extend(rows_to_records(code, frame))
                time.sleep(max(0.0, args.request_sleep))

            inserted = repository.upsert_capital_flow_daily(records)
            repository.upsert_meta(
                {
                    "last_capital_flow_sync": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "capital_flow_source": "eastmoney",
                }
            )
            elapsed = time.time() - started
            codes_now, rows_now, remain_now = get_stats(repository)
            print(
                f"  batch {batch_index + 1}/{batches}: req={len(batch)} touched={touched} failed={failed} "
                f"rows+={inserted} elapsed={elapsed:.1f}s total_codes={codes_now} total_rows={rows_now} remaining={remain_now}",
                flush=True,
            )
            time.sleep(max(0.0, args.sleep))


if __name__ == "__main__":
    raise SystemExit(main())
