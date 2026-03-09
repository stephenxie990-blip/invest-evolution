#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import baostock as bs

DB_PATH = PROJECT_ROOT / 'data' / 'stock_history.db'


def normalize_date(value: str) -> str:
    text = str(value or '').strip().replace('-', '').replace('/', '')
    if len(text) != 8 or not text.isdigit():
        raise ValueError(f'invalid date: {value}')
    return text


def format_bs_date(value: str) -> str:
    v = normalize_date(value)
    return f'{v[:4]}-{v[4:6]}-{v[6:8]}'


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS intraday_bar_60m (
            code TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            bar_time TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            amount REAL,
            source TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (code, bar_time)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_intraday_bar_60m_code_date ON intraday_bar_60m(code, trade_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_intraday_bar_60m_date_time ON intraday_bar_60m(trade_date, bar_time)")
    conn.commit()


def list_codes(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT code
        FROM daily_bar
        WHERE adj_flag='hfq'
          AND (code LIKE 'sh.%' OR code LIKE 'sz.%' OR code LIKE 'bj.%')
        ORDER BY code
        """
    ).fetchall()
    return [row[0] for row in rows if row and row[0]]


def already_done_codes(conn: sqlite3.Connection, start: str, end: str) -> set[str]:
    rows = conn.execute(
        """
        SELECT code
        FROM intraday_bar_60m
        WHERE trade_date BETWEEN ? AND ?
        GROUP BY code
        HAVING COUNT(*) >= 400
        """,
        (start, end),
    ).fetchall()
    return {row[0] for row in rows if row and row[0]}


def fetch_60m(code: str, start: str, end: str) -> list[tuple]:
    rs = bs.query_history_k_data_plus(
        code,
        'date,time,code,open,high,low,close,volume,amount',
        start_date=format_bs_date(start),
        end_date=format_bs_date(end),
        frequency='60',
        adjustflag='2',
    )
    if getattr(rs, 'error_code', '0') != '0':
        raise RuntimeError(getattr(rs, 'error_msg', 'unknown error'))

    records: list[tuple] = []
    while rs.next():
        row = dict(zip(rs.fields, rs.get_row_data()))
        trade_date = normalize_date(row.get('date', ''))
        bar_time_raw = str(row.get('time', '')).strip()
        if not trade_date or not bar_time_raw:
            continue
        records.append(
            (
                row.get('code', code),
                trade_date,
                bar_time_raw,
                row.get('open'),
                row.get('high'),
                row.get('low'),
                row.get('close'),
                row.get('volume'),
                row.get('amount'),
                'baostock',
            )
        )
    return records


def upsert_records(conn: sqlite3.Connection, records: list[tuple]) -> int:
    if not records:
        return 0
    conn.executemany(
        """
        INSERT OR REPLACE INTO intraday_bar_60m (
            code, trade_date, bar_time, open, high, low, close, volume, amount, source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        records,
    )
    conn.commit()
    return len(records)


def stats(conn: sqlite3.Connection, start: str, end: str) -> tuple[int, int]:
    row = conn.execute(
        """
        SELECT COUNT(DISTINCT code), COUNT(*)
        FROM intraday_bar_60m
        WHERE trade_date BETWEEN ? AND ?
        """,
        (start, end),
    ).fetchone()
    return int(row[0] or 0), int(row[1] or 0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='补齐最近3年全A股60分钟线')
    parser.add_argument('--start', default=(datetime.now() - timedelta(days=365 * 3)).strftime('%Y%m%d'))
    parser.add_argument('--end', default=datetime.now().strftime('%Y%m%d'))
    parser.add_argument('--sleep', type=float, default=0.02)
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--offset', type=int, default=0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    start = normalize_date(args.start)
    end = normalize_date(args.end)

    conn = sqlite3.connect(str(DB_PATH), timeout=60.0)
    conn.execute('PRAGMA journal_mode=WAL;')
    conn.execute('PRAGMA synchronous=NORMAL;')
    ensure_schema(conn)
    codes = list_codes(conn)
    if args.offset:
        codes = codes[args.offset:]
    if args.limit:
        codes = codes[:args.limit]

    done = already_done_codes(conn, start, end)
    codes = [code for code in codes if code not in done]
    print(f'[start] range={start}-{end} pending_codes={len(codes)} skipped_done={len(done)}', flush=True)

    login = bs.login()
    if getattr(login, 'error_code', '0') != '0':
        raise RuntimeError(f"Baostock 登录失败: {getattr(login, 'error_msg', '')}")

    ok = 0
    failed = 0
    inserted_rows = 0
    try:
        for idx, code in enumerate(codes, start=1):
            try:
                records = fetch_60m(code, start, end)
                inserted_rows += upsert_records(conn, records)
                ok += 1
            except Exception as exc:
                failed += 1
                print(f'[fail] {idx}/{len(codes)} {code} {exc}', flush=True)
                continue
            if idx % 20 == 0:
                code_count, row_count = stats(conn, start, end)
                print(
                    f'[progress] {idx}/{len(codes)} ok={ok} failed={failed} '
                    f'rows_added={inserted_rows} total_codes={code_count} total_rows={row_count}',
                    flush=True,
                )
            time.sleep(max(0.0, args.sleep))
    finally:
        bs.logout()
        conn.close()

    print(f'[done] ok={ok} failed={failed} rows_added={inserted_rows}', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
