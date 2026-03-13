import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

import pandas as pd

from config import PROJECT_ROOT, normalize_date

logger = logging.getLogger(__name__)

_STATUS_SUMMARY_SNAPSHOT_KEY = "status_summary_snapshot"
_STATUS_SUMMARY_UPDATED_AT_KEY = "status_summary_updated_at"
_STATUS_SUMMARY_MAX_AGE_SECONDS = 30
_QUALITY_AUDIT_SNAPSHOT_KEY = "quality_audit_snapshot"
_QUALITY_AUDIT_UPDATED_AT_KEY = "quality_audit_updated_at"


def _default_db_path() -> Path:
    return Path(os.environ.get("INVEST_DB_PATH", str(PROJECT_ROOT / "data" / "stock_history.db")))


def _normalize_optional_date(value: Any) -> str:
    if value in (None, "", "None"):
        return ""
    return normalize_date(value)


def _to_float(value: Any) -> float | None:
    if value in (None, "", "None"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class MarketDataRepository:
    """Canonical SQLite repository for market data."""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path else _default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def initialize_schema(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS security_master (
                    code TEXT PRIMARY KEY,
                    name TEXT,
                    list_date TEXT NOT NULL DEFAULT '',
                    delist_date TEXT NOT NULL DEFAULT '',
                    industry TEXT,
                    is_st INTEGER NOT NULL DEFAULT 0,
                    source TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_bar (
                    code TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume REAL,
                    amount REAL,
                    pct_chg REAL,
                    turnover REAL,
                    adj_flag TEXT NOT NULL DEFAULT 'hfq',
                    source TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (code, trade_date, adj_flag)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_daily_bar_code_date ON daily_bar(code, trade_date)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_daily_bar_trade_date ON daily_bar(trade_date)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_daily_bar_adj_code_date ON daily_bar(adj_flag, code, trade_date)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS index_bar (
                    index_code TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume REAL,
                    amount REAL,
                    pct_chg REAL,
                    source TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (index_code, trade_date)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_index_bar_code_date ON index_bar(index_code, trade_date)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_index_bar_trade_date ON index_bar(trade_date)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS financial_snapshot (
                    code TEXT NOT NULL,
                    report_date TEXT NOT NULL,
                    publish_date TEXT NOT NULL DEFAULT '',
                    roe REAL,
                    net_profit REAL,
                    revenue REAL,
                    total_assets REAL,
                    market_cap REAL,
                    source TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (code, report_date)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_financial_snapshot_code_date ON financial_snapshot(code, report_date)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS trading_calendar (
                    market TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    prev_trade_date TEXT NOT NULL DEFAULT '',
                    next_trade_date TEXT NOT NULL DEFAULT '',
                    is_open INTEGER NOT NULL DEFAULT 1,
                    source TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (market, trade_date)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trading_calendar_market_date ON trading_calendar(market, trade_date)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS security_status_daily (
                    code TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    is_st INTEGER NOT NULL DEFAULT 0,
                    is_new_stock_window INTEGER NOT NULL DEFAULT 0,
                    is_limit_up INTEGER NOT NULL DEFAULT 0,
                    is_limit_down INTEGER NOT NULL DEFAULT 0,
                    source TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (code, trade_date)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_security_status_daily_code_date ON security_status_daily(code, trade_date)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS factor_snapshot (
                    code TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    ma5 REAL,
                    ma10 REAL,
                    ma20 REAL,
                    ma60 REAL,
                    momentum20 REAL,
                    momentum60 REAL,
                    volatility20 REAL,
                    volume_ratio REAL,
                    turnover_mean20 REAL,
                    drawdown60 REAL,
                    relative_strength_hs300 REAL,
                    breakout20 INTEGER,
                    source TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (code, trade_date)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_factor_snapshot_code_date ON factor_snapshot(code, trade_date)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS capital_flow_daily (
                    code TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    close REAL,
                    pct_chg REAL,
                    main_net_inflow REAL,
                    main_net_inflow_ratio REAL,
                    super_large_net_inflow REAL,
                    super_large_net_inflow_ratio REAL,
                    large_net_inflow REAL,
                    large_net_inflow_ratio REAL,
                    medium_net_inflow REAL,
                    medium_net_inflow_ratio REAL,
                    small_net_inflow REAL,
                    small_net_inflow_ratio REAL,
                    source TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (code, trade_date)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_capital_flow_daily_code_date ON capital_flow_daily(code, trade_date)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS dragon_tiger_list (
                    code TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    name TEXT,
                    interpretation TEXT,
                    close REAL,
                    pct_chg REAL,
                    net_buy REAL,
                    buy_amount REAL,
                    sell_amount REAL,
                    turnover_amount REAL,
                    market_turnover_amount REAL,
                    net_buy_ratio REAL,
                    turnover_ratio REAL,
                    turnover_rate REAL,
                    float_market_cap REAL,
                    reason TEXT,
                    next_day_return REAL,
                    next_2day_return REAL,
                    next_5day_return REAL,
                    next_10day_return REAL,
                    source TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (code, trade_date, reason)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_dragon_tiger_list_code_date ON dragon_tiger_list(code, trade_date)")
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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ingestion_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                "INSERT OR REPLACE INTO ingestion_meta(key, value, updated_at) VALUES ('schema_version', 'canonical_v1', CURRENT_TIMESTAMP)"
            )

    def _invalidate_status_summary_snapshot(self) -> None:
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM ingestion_meta WHERE key IN (?, ?, ?, ?)",
                (
                    _STATUS_SUMMARY_SNAPSHOT_KEY,
                    _STATUS_SUMMARY_UPDATED_AT_KEY,
                    _QUALITY_AUDIT_SNAPSHOT_KEY,
                    _QUALITY_AUDIT_UPDATED_AT_KEY,
                ),
            )

    def upsert_security_master(self, records: Iterable[Mapping[str, Any]]) -> int:
        rows = [
            (
                str(record.get("code", "")).strip(),
                str(record.get("name", "")).strip(),
                _normalize_optional_date(record.get("list_date")),
                _normalize_optional_date(record.get("delist_date")),
                str(record.get("industry", "") or "").strip(),
                int(bool(record.get("is_st", 0))),
                str(record.get("source", "") or "").strip(),
            )
            for record in records
            if str(record.get("code", "")).strip()
        ]
        if not rows:
            return 0
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO security_master (
                    code, name, list_date, delist_date, industry, is_st, source, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                rows,
            )
        inserted = len(rows)
        if inserted:
            self._invalidate_status_summary_snapshot()
        return inserted

    def upsert_daily_bars(self, records: Iterable[Mapping[str, Any]], adj_flag: str = "hfq") -> int:
        rows = [
            (
                str(record.get("code", "")).strip(),
                _normalize_optional_date(record.get("trade_date")),
                _to_float(record.get("open")),
                _to_float(record.get("high")),
                _to_float(record.get("low")),
                _to_float(record.get("close")),
                _to_float(record.get("volume")),
                _to_float(record.get("amount")),
                _to_float(record.get("pct_chg")),
                _to_float(record.get("turnover")),
                str(record.get("adj_flag", adj_flag) or adj_flag),
                str(record.get("source", "") or "").strip(),
            )
            for record in records
            if str(record.get("code", "")).strip() and _normalize_optional_date(record.get("trade_date"))
        ]
        if not rows:
            return 0
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO daily_bar (
                    code, trade_date, open, high, low, close, volume, amount,
                    pct_chg, turnover, adj_flag, source, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                rows,
            )
        inserted = len(rows)
        if inserted:
            self._invalidate_status_summary_snapshot()
        return inserted

    def upsert_intraday_bars_60m(self, records: Iterable[Mapping[str, Any]]) -> int:
        rows = [
            (
                str(record.get("code", "")).strip(),
                _normalize_optional_date(record.get("trade_date")),
                str(record.get("bar_time", "") or "").strip(),
                _to_float(record.get("open")),
                _to_float(record.get("high")),
                _to_float(record.get("low")),
                _to_float(record.get("close")),
                _to_float(record.get("volume")),
                _to_float(record.get("amount")),
                str(record.get("source", "") or "").strip(),
            )
            for record in records
            if str(record.get("code", "")).strip() and _normalize_optional_date(record.get("trade_date")) and str(record.get("bar_time", "") or "").strip()
        ]
        if not rows:
            return 0
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO intraday_bar_60m (
                    code, trade_date, bar_time, open, high, low, close, volume, amount, source, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                rows,
            )
        inserted = len(rows)
        if inserted:
            self._invalidate_status_summary_snapshot()
        return inserted

    def upsert_index_bars(self, records: Iterable[Mapping[str, Any]]) -> int:
        rows = [
            (
                str(record.get("index_code", "")).strip(),
                _normalize_optional_date(record.get("trade_date")),
                _to_float(record.get("open")),
                _to_float(record.get("high")),
                _to_float(record.get("low")),
                _to_float(record.get("close")),
                _to_float(record.get("volume")),
                _to_float(record.get("amount")),
                _to_float(record.get("pct_chg")),
                str(record.get("source", "") or "").strip(),
            )
            for record in records
            if str(record.get("index_code", "")).strip() and _normalize_optional_date(record.get("trade_date"))
        ]
        if not rows:
            return 0
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO index_bar (
                    index_code, trade_date, open, high, low, close, volume, amount,
                    pct_chg, source, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                rows,
            )
        inserted = len(rows)
        if inserted:
            self._invalidate_status_summary_snapshot()
        return inserted

    def upsert_financial_snapshots(self, records: Iterable[Mapping[str, Any]]) -> int:
        rows = [
            (
                str(record.get("code", "")).strip(),
                _normalize_optional_date(record.get("report_date")),
                _normalize_optional_date(record.get("publish_date")),
                _to_float(record.get("roe")),
                _to_float(record.get("net_profit")),
                _to_float(record.get("revenue")),
                _to_float(record.get("total_assets")),
                _to_float(record.get("market_cap")),
                str(record.get("source", "") or "").strip(),
            )
            for record in records
            if str(record.get("code", "")).strip() and _normalize_optional_date(record.get("report_date"))
        ]
        if not rows:
            return 0
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO financial_snapshot (
                    code, report_date, publish_date, roe, net_profit, revenue,
                    total_assets, market_cap, source, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                rows,
            )
        inserted = len(rows)
        if inserted:
            self._invalidate_status_summary_snapshot()
        return inserted

    def upsert_trading_calendar(self, records: Iterable[Mapping[str, Any]]) -> int:
        rows = [
            (
                str(record.get("market", "CN_A")).strip() or "CN_A",
                _normalize_optional_date(record.get("trade_date")),
                _normalize_optional_date(record.get("prev_trade_date")),
                _normalize_optional_date(record.get("next_trade_date")),
                int(bool(record.get("is_open", 1))),
                str(record.get("source", "") or "").strip(),
            )
            for record in records
            if _normalize_optional_date(record.get("trade_date"))
        ]
        if not rows:
            return 0
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO trading_calendar (
                    market, trade_date, prev_trade_date, next_trade_date, is_open, source, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                rows,
            )
        inserted = len(rows)
        if inserted:
            self._invalidate_status_summary_snapshot()
        return inserted

    def upsert_security_status_daily(self, records: Iterable[Mapping[str, Any]]) -> int:
        rows = [
            (
                str(record.get("code", "")).strip(),
                _normalize_optional_date(record.get("trade_date")),
                int(bool(record.get("is_st", 0))),
                int(bool(record.get("is_new_stock_window", 0))),
                int(bool(record.get("is_limit_up", 0))),
                int(bool(record.get("is_limit_down", 0))),
                str(record.get("source", "") or "").strip(),
            )
            for record in records
            if str(record.get("code", "")).strip() and _normalize_optional_date(record.get("trade_date"))
        ]
        if not rows:
            return 0
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO security_status_daily (
                    code, trade_date, is_st, is_new_stock_window, is_limit_up, is_limit_down, source, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                rows,
            )
        inserted = len(rows)
        if inserted:
            self._invalidate_status_summary_snapshot()
        return inserted

    def upsert_factor_snapshots(self, records: Iterable[Mapping[str, Any]]) -> int:
        rows = [
            (
                str(record.get("code", "")).strip(),
                _normalize_optional_date(record.get("trade_date")),
                _to_float(record.get("ma5")),
                _to_float(record.get("ma10")),
                _to_float(record.get("ma20")),
                _to_float(record.get("ma60")),
                _to_float(record.get("momentum20")),
                _to_float(record.get("momentum60")),
                _to_float(record.get("volatility20")),
                _to_float(record.get("volume_ratio")),
                _to_float(record.get("turnover_mean20")),
                _to_float(record.get("drawdown60")),
                _to_float(record.get("relative_strength_hs300")),
                int(bool(record.get("breakout20", 0))) if record.get("breakout20") is not None else None,
                str(record.get("source", "") or "").strip(),
            )
            for record in records
            if str(record.get("code", "")).strip() and _normalize_optional_date(record.get("trade_date"))
        ]
        if not rows:
            return 0
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO factor_snapshot (
                    code, trade_date, ma5, ma10, ma20, ma60, momentum20, momentum60,
                    volatility20, volume_ratio, turnover_mean20, drawdown60,
                    relative_strength_hs300, breakout20, source, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                rows,
            )
        inserted = len(rows)
        if inserted:
            self._invalidate_status_summary_snapshot()
        return inserted

    def upsert_capital_flow_daily(self, records: Iterable[Mapping[str, Any]]) -> int:
        rows = [
            (
                str(record.get("code", "")).strip(),
                _normalize_optional_date(record.get("trade_date")),
                _to_float(record.get("close")),
                _to_float(record.get("pct_chg")),
                _to_float(record.get("main_net_inflow")),
                _to_float(record.get("main_net_inflow_ratio")),
                _to_float(record.get("super_large_net_inflow")),
                _to_float(record.get("super_large_net_inflow_ratio")),
                _to_float(record.get("large_net_inflow")),
                _to_float(record.get("large_net_inflow_ratio")),
                _to_float(record.get("medium_net_inflow")),
                _to_float(record.get("medium_net_inflow_ratio")),
                _to_float(record.get("small_net_inflow")),
                _to_float(record.get("small_net_inflow_ratio")),
                str(record.get("source", "") or "").strip(),
            )
            for record in records
            if str(record.get("code", "")).strip() and _normalize_optional_date(record.get("trade_date"))
        ]
        if not rows:
            return 0
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO capital_flow_daily (
                    code, trade_date, close, pct_chg, main_net_inflow, main_net_inflow_ratio,
                    super_large_net_inflow, super_large_net_inflow_ratio, large_net_inflow, large_net_inflow_ratio,
                    medium_net_inflow, medium_net_inflow_ratio, small_net_inflow, small_net_inflow_ratio,
                    source, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                rows,
            )
        inserted = len(rows)
        if inserted:
            self._invalidate_status_summary_snapshot()
        return inserted

    def upsert_dragon_tiger_list(self, records: Iterable[Mapping[str, Any]]) -> int:
        rows = [
            (
                str(record.get("code", "")).strip(),
                _normalize_optional_date(record.get("trade_date")),
                str(record.get("name", "") or "").strip(),
                str(record.get("interpretation", "") or "").strip(),
                _to_float(record.get("close")),
                _to_float(record.get("pct_chg")),
                _to_float(record.get("net_buy")),
                _to_float(record.get("buy_amount")),
                _to_float(record.get("sell_amount")),
                _to_float(record.get("turnover_amount")),
                _to_float(record.get("market_turnover_amount")),
                _to_float(record.get("net_buy_ratio")),
                _to_float(record.get("turnover_ratio")),
                _to_float(record.get("turnover_rate")),
                _to_float(record.get("float_market_cap")),
                str(record.get("reason", "") or "").strip(),
                _to_float(record.get("next_day_return")),
                _to_float(record.get("next_2day_return")),
                _to_float(record.get("next_5day_return")),
                _to_float(record.get("next_10day_return")),
                str(record.get("source", "") or "").strip(),
            )
            for record in records
            if str(record.get("code", "")).strip() and _normalize_optional_date(record.get("trade_date"))
        ]
        if not rows:
            return 0
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO dragon_tiger_list (
                    code, trade_date, name, interpretation, close, pct_chg, net_buy, buy_amount,
                    sell_amount, turnover_amount, market_turnover_amount, net_buy_ratio, turnover_ratio,
                    turnover_rate, float_market_cap, reason, next_day_return, next_2day_return,
                    next_5day_return, next_10day_return, source, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                rows,
            )
        inserted = len(rows)
        if inserted:
            self._invalidate_status_summary_snapshot()
        return inserted

    def upsert_meta(self, mapping: Mapping[str, Any]) -> int:
        rows = [(str(key), str(value)) for key, value in mapping.items()]
        if not rows:
            return 0
        with self.connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO ingestion_meta(key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
                rows,
            )
        inserted = len(rows)
        snapshot_keys = {_STATUS_SUMMARY_SNAPSHOT_KEY, _STATUS_SUMMARY_UPDATED_AT_KEY, _QUALITY_AUDIT_SNAPSHOT_KEY, _QUALITY_AUDIT_UPDATED_AT_KEY}
        if inserted and any(key not in snapshot_keys for key, _ in rows):
            self._invalidate_status_summary_snapshot()
        return inserted

    def get_meta(self, keys: Sequence[str] | None = None) -> dict[str, str]:
        self.initialize_schema()
        with self.connect() as conn:
            if keys:
                placeholders = ",".join(["?"] * len(keys))
                rows = conn.execute(
                    f"SELECT key, value FROM ingestion_meta WHERE key IN ({placeholders})",
                    list(keys),
                ).fetchall()
            else:
                rows = conn.execute("SELECT key, value FROM ingestion_meta").fetchall()
        return {str(row[0]): str(row[1]) for row in rows}

    def has_daily_bars(self) -> bool:
        self.initialize_schema()
        with self.connect() as conn:
            row = conn.execute("SELECT COUNT(*) FROM daily_bar").fetchone()
        return bool(row and row[0] > 0)

    def _compute_status_summary(self) -> dict[str, Any]:
        self.initialize_schema()
        with self.connect() as conn:
            stock_count = conn.execute("SELECT COUNT(*) FROM security_master").fetchone()[0]
            kline_count = conn.execute("SELECT COUNT(*) FROM daily_bar").fetchone()[0]
            latest_row = conn.execute("SELECT MAX(trade_date) FROM daily_bar").fetchone()
            latest_date = latest_row[0] if latest_row and latest_row[0] else ""
            index_count = conn.execute("SELECT COUNT(DISTINCT index_code) FROM index_bar").fetchone()[0]
            index_kline_count = conn.execute("SELECT COUNT(*) FROM index_bar").fetchone()[0]
            index_latest_row = conn.execute("SELECT MAX(trade_date) FROM index_bar").fetchone()
            index_latest_date = index_latest_row[0] if index_latest_row and index_latest_row[0] else ""
            financial_count = conn.execute("SELECT COUNT(*) FROM financial_snapshot").fetchone()[0]
            calendar_count = conn.execute("SELECT COUNT(*) FROM trading_calendar").fetchone()[0]
            status_count = conn.execute("SELECT COUNT(*) FROM security_status_daily").fetchone()[0]
            factor_count = conn.execute("SELECT COUNT(*) FROM factor_snapshot").fetchone()[0]
            capital_flow_count = conn.execute("SELECT COUNT(*) FROM capital_flow_daily").fetchone()[0]
            dragon_tiger_count = conn.execute("SELECT COUNT(*) FROM dragon_tiger_list").fetchone()[0]
            intraday_60m_count = conn.execute("SELECT COUNT(*) FROM intraday_bar_60m").fetchone()[0]
        size_mb = round(self.db_path.stat().st_size / (1024 * 1024), 2) if self.db_path.exists() else 0.0
        return {
            "db_path": str(self.db_path.absolute()),
            "size_mb": size_mb,
            "stock_count": stock_count,
            "kline_count": kline_count,
            "financial_count": financial_count,
            "calendar_count": calendar_count,
            "status_count": status_count,
            "factor_count": factor_count,
            "capital_flow_count": capital_flow_count,
            "dragon_tiger_count": dragon_tiger_count,
            "intraday_60m_count": intraday_60m_count,
            "latest_date": latest_date,
            "index_count": index_count,
            "index_kline_count": index_kline_count,
            "index_latest_date": index_latest_date,
            "schema": "canonical_v1",
        }

    def get_status_summary(self, *, use_snapshot: bool = True, max_age_seconds: int = _STATUS_SUMMARY_MAX_AGE_SECONDS) -> dict[str, Any]:
        self.initialize_schema()
        if use_snapshot:
            meta = self.get_meta([_STATUS_SUMMARY_SNAPSHOT_KEY, _STATUS_SUMMARY_UPDATED_AT_KEY])
            raw_snapshot = meta.get(_STATUS_SUMMARY_SNAPSHOT_KEY, "")
            updated_at = meta.get(_STATUS_SUMMARY_UPDATED_AT_KEY, "")
            if raw_snapshot and updated_at:
                try:
                    age = (datetime.now() - datetime.fromisoformat(updated_at)).total_seconds()
                    if age <= max(0, int(max_age_seconds)):
                        payload = json.loads(raw_snapshot)
                        if isinstance(payload, dict):
                            payload["db_path"] = str(self.db_path.absolute())
                            payload["size_mb"] = round(self.db_path.stat().st_size / (1024 * 1024), 2) if self.db_path.exists() else 0.0
                            return payload
                except (TypeError, ValueError, json.JSONDecodeError, OSError) as exc:
                    logger.warning("Ignoring invalid status summary snapshot for %s: %s", self.db_path, exc)

        summary = self._compute_status_summary()
        try:
            snapshot = dict(summary)
            snapshot.pop("db_path", None)
            snapshot.pop("size_mb", None)
            self.upsert_meta({
                _STATUS_SUMMARY_SNAPSHOT_KEY: json.dumps(snapshot, ensure_ascii=False),
                _STATUS_SUMMARY_UPDATED_AT_KEY: datetime.now().isoformat(timespec="seconds"),
            })
        except (TypeError, ValueError, OSError) as exc:
            logger.warning("Failed to persist status summary snapshot for %s: %s", self.db_path, exc)
        return summary

    def list_security_codes(self) -> list[str]:
        self.initialize_schema()
        with self.connect() as conn:
            rows = conn.execute("SELECT code FROM security_master ORDER BY code").fetchall()
        return [row[0] for row in rows]

    def list_securities(self) -> list[dict[str, Any]]:
        self.initialize_schema()
        with self.connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT code, name, list_date, delist_date, industry, is_st, source FROM security_master ORDER BY code"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_available_date_range(self) -> tuple[Optional[str], Optional[str]]:
        self.initialize_schema()
        with self.connect() as conn:
            row = conn.execute("SELECT MIN(trade_date), MAX(trade_date) FROM daily_bar WHERE adj_flag='hfq'").fetchone()
        if not row:
            return None, None
        return row[0], row[1]

    def get_stock_count(self) -> int:
        self.initialize_schema()
        with self.connect() as conn:
            row = conn.execute("SELECT COUNT(DISTINCT code) FROM daily_bar WHERE adj_flag='hfq'").fetchone()
        return int(row[0]) if row else 0

    def get_index_count(self) -> int:
        self.initialize_schema()
        with self.connect() as conn:
            row = conn.execute("SELECT COUNT(DISTINCT index_code) FROM index_bar").fetchone()
        return int(row[0]) if row else 0

    def get_index_available_date_range(self) -> tuple[Optional[str], Optional[str]]:
        self.initialize_schema()
        with self.connect() as conn:
            row = conn.execute("SELECT MIN(trade_date), MAX(trade_date) FROM index_bar").fetchone()
        if not row:
            return None, None
        return row[0], row[1]

    def query_index_bars(
        self,
        index_codes: Sequence[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        self.initialize_schema()
        clauses: list[str] = []
        params: list[Any] = []

        if index_codes:
            placeholders = ",".join(["?"] * len(index_codes))
            clauses.append(f"index_code IN ({placeholders})")
            params.extend(index_codes)
        if start_date:
            clauses.append("trade_date >= ?")
            params.append(normalize_date(start_date))
        if end_date:
            clauses.append("trade_date <= ?")
            params.append(normalize_date(end_date))

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"""
            SELECT index_code, trade_date, open, high, low, close, volume, amount, pct_chg
            FROM index_bar
            {where}
            ORDER BY index_code, trade_date
        """
        with self.connect() as conn:
            return pd.read_sql_query(query, conn, params=params)

    def query_securities(self, codes: Sequence[str] | None = None) -> list[dict[str, Any]]:
        self.initialize_schema()
        clauses: list[str] = []
        params: list[Any] = []
        if codes:
            placeholders = ",".join(["?"] * len(codes))
            clauses.append(f"code IN ({placeholders})")
            params.extend(codes)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"SELECT code, name, list_date, delist_date, industry, is_st, source FROM security_master {where} ORDER BY code",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def get_industry_map(self, codes: Sequence[str] | None = None) -> dict[str, str]:
        rows = self.query_securities(codes)
        return {str(row.get("code")): str(row.get("industry") or "") for row in rows if str(row.get("code"))}

    def query_latest_financial_snapshots(
        self,
        codes: Sequence[str] | None = None,
        cutoff_date: str | None = None,
    ) -> list[dict[str, Any]]:
        self.initialize_schema()
        cutoff = normalize_date(cutoff_date) if cutoff_date else None
        clauses: list[str] = []
        params: list[Any] = []
        if codes:
            placeholders = ",".join(["?"] * len(codes))
            clauses.append(f"code IN ({placeholders})")
            params.extend(codes)
        if cutoff:
            clauses.append("CASE WHEN publish_date != '' THEN publish_date ELSE report_date END <= ?")
            params.append(cutoff)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"""
            SELECT fs.code, fs.report_date, fs.publish_date, fs.roe, fs.net_profit, fs.revenue,
                   fs.total_assets, fs.market_cap, fs.source
            FROM financial_snapshot fs
            JOIN (
                SELECT code,
                       MAX(CASE WHEN publish_date != '' THEN publish_date ELSE report_date END) AS effective_date
                FROM financial_snapshot
                {where}
                GROUP BY code
            ) latest
              ON fs.code = latest.code
             AND (CASE WHEN fs.publish_date != '' THEN fs.publish_date ELSE fs.report_date END) = latest.effective_date
            ORDER BY fs.code
        """
        with self.connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def query_trading_calendar(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        market: str = "CN_A",
    ) -> pd.DataFrame:
        self.initialize_schema()
        clauses = ["market = ?"]
        params: list[Any] = [str(market or "CN_A")]
        if start_date:
            clauses.append("trade_date >= ?")
            params.append(normalize_date(start_date))
        if end_date:
            clauses.append("trade_date <= ?")
            params.append(normalize_date(end_date))
        where = " AND ".join(clauses)
        query = f"SELECT market, trade_date, prev_trade_date, next_trade_date, is_open FROM trading_calendar WHERE {where} ORDER BY trade_date"
        with self.connect() as conn:
            return pd.read_sql_query(query, conn, params=params)

    def query_security_status_daily(
        self,
        codes: Sequence[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        self.initialize_schema()
        clauses: list[str] = []
        params: list[Any] = []
        if codes:
            placeholders = ",".join(["?"] * len(codes))
            clauses.append(f"code IN ({placeholders})")
            params.extend(codes)
        if start_date:
            clauses.append("trade_date >= ?")
            params.append(normalize_date(start_date))
        if end_date:
            clauses.append("trade_date <= ?")
            params.append(normalize_date(end_date))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"""
            SELECT code, trade_date, is_st, is_new_stock_window, is_limit_up, is_limit_down
            FROM security_status_daily
            {where}
            ORDER BY code, trade_date
        """
        with self.connect() as conn:
            return pd.read_sql_query(query, conn, params=params)

    def query_capital_flow_daily(
        self,
        codes: Sequence[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        self.initialize_schema()
        clauses: list[str] = []
        params: list[Any] = []
        if codes:
            placeholders = ",".join(["?"] * len(codes))
            clauses.append(f"code IN ({placeholders})")
            params.extend(codes)
        if start_date:
            clauses.append("trade_date >= ?")
            params.append(normalize_date(start_date))
        if end_date:
            clauses.append("trade_date <= ?")
            params.append(normalize_date(end_date))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"""
            SELECT code, trade_date, close, pct_chg, main_net_inflow, main_net_inflow_ratio,
                   super_large_net_inflow, super_large_net_inflow_ratio, large_net_inflow, large_net_inflow_ratio,
                   medium_net_inflow, medium_net_inflow_ratio, small_net_inflow, small_net_inflow_ratio
            FROM capital_flow_daily
            {where}
            ORDER BY code, trade_date
        """
        with self.connect() as conn:
            return pd.read_sql_query(query, conn, params=params)

    def query_dragon_tiger_list(
        self,
        codes: Sequence[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        self.initialize_schema()
        clauses: list[str] = []
        params: list[Any] = []
        if codes:
            placeholders = ",".join(["?"] * len(codes))
            clauses.append(f"code IN ({placeholders})")
            params.extend(codes)
        if start_date:
            clauses.append("trade_date >= ?")
            params.append(normalize_date(start_date))
        if end_date:
            clauses.append("trade_date <= ?")
            params.append(normalize_date(end_date))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"""
            SELECT code, trade_date, name, interpretation, close, pct_chg, net_buy, buy_amount,
                   sell_amount, turnover_amount, market_turnover_amount, net_buy_ratio,
                   turnover_ratio, turnover_rate, float_market_cap, reason, next_day_return,
                   next_2day_return, next_5day_return, next_10day_return
            FROM dragon_tiger_list
            {where}
            ORDER BY trade_date DESC, code ASC
        """
        with self.connect() as conn:
            return pd.read_sql_query(query, conn, params=params)

    def query_factor_snapshots(
        self,
        codes: Sequence[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        self.initialize_schema()
        clauses: list[str] = []
        params: list[Any] = []
        if codes:
            placeholders = ",".join(["?"] * len(codes))
            clauses.append(f"code IN ({placeholders})")
            params.extend(codes)
        if start_date:
            clauses.append("trade_date >= ?")
            params.append(normalize_date(start_date))
        if end_date:
            clauses.append("trade_date <= ?")
            params.append(normalize_date(end_date))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"""
            SELECT code, trade_date, ma5, ma10, ma20, ma60, momentum20, momentum60,
                   volatility20, volume_ratio, turnover_mean20, drawdown60,
                   relative_strength_hs300, breakout20
            FROM factor_snapshot
            {where}
            ORDER BY code, trade_date
        """
        with self.connect() as conn:
            return pd.read_sql_query(query, conn, params=params)

    def count_codes_with_history(
        self,
        cutoff_date: str,
        min_history_days: int,
        adj_flag: str = "hfq",
    ) -> int:
        self.initialize_schema()
        cutoff = normalize_date(cutoff_date)
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*)
                FROM (
                    SELECT code
                    FROM daily_bar
                    WHERE trade_date <= ? AND adj_flag = ?
                    GROUP BY code
                    HAVING COUNT(*) >= ?
                ) eligible
                """,
                (cutoff, adj_flag, max(1, int(min_history_days))),
            ).fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    def select_codes_with_history(
        self,
        cutoff_date: str,
        min_history_days: int,
        stock_count: int,
        adj_flag: str = "hfq",
    ) -> list[str]:
        self.initialize_schema()
        cutoff = normalize_date(cutoff_date)
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT code, COUNT(*) AS days
                FROM daily_bar
                WHERE trade_date <= ? AND adj_flag = ?
                GROUP BY code
                HAVING days >= ?
                ORDER BY days DESC, code ASC
                LIMIT ?
                """,
                (cutoff, adj_flag, max(1, int(min_history_days)), max(1, int(stock_count))),
            ).fetchall()
        return [row[0] for row in rows]

    def query_daily_bars(
        self,
        codes: Sequence[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        adj_flag: str = "hfq",
    ) -> pd.DataFrame:
        self.initialize_schema()
        clauses = ["adj_flag = ?"]
        params: list[Any] = [adj_flag]

        if codes:
            placeholders = ",".join(["?"] * len(codes))
            clauses.append(f"code IN ({placeholders})")
            params.extend(codes)
        if start_date:
            clauses.append("trade_date >= ?")
            params.append(normalize_date(start_date))
        if end_date:
            clauses.append("trade_date <= ?")
            params.append(normalize_date(end_date))

        where = " AND ".join(clauses)
        query = f"""
            SELECT code, trade_date, open, high, low, close, volume, amount, pct_chg, turnover
            FROM daily_bar
            WHERE {where}
            ORDER BY code, trade_date
        """

        with self.connect() as conn:
            return pd.read_sql_query(query, conn, params=params)

    def query_training_bars(
        self,
        *,
        codes: Sequence[str],
        cutoff_date: str,
        history_limit: int,
        end_date: str | None = None,
        adj_flag: str = "hfq",
        include_capital_flow: bool = False,
    ) -> pd.DataFrame:
        self.initialize_schema()
        if not codes:
            columns = [
                "code",
                "trade_date",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "amount",
                "pct_chg",
                "turnover",
                "is_st",
                "is_new_stock_window",
                "is_limit_up",
                "is_limit_down",
                "ma5",
                "ma10",
                "ma20",
                "ma60",
                "momentum20",
                "momentum60",
                "volatility20",
                "volume_ratio",
                "turnover_mean20",
                "drawdown60",
                "relative_strength_hs300",
                "breakout20",
            ]
            if include_capital_flow:
                columns.extend([
                    "main_net_inflow",
                    "main_net_inflow_ratio",
                    "super_large_net_inflow",
                    "super_large_net_inflow_ratio",
                    "large_net_inflow",
                    "large_net_inflow_ratio",
                    "medium_net_inflow",
                    "medium_net_inflow_ratio",
                    "small_net_inflow",
                    "small_net_inflow_ratio",
                ])
            return pd.DataFrame(columns=columns)

        normalized_cutoff = normalize_date(cutoff_date)
        normalized_end = normalize_date(end_date or normalized_cutoff)
        keep_history = max(1, int(history_limit))
        placeholders = ",".join(["?"] * len(codes))
        capital_flow_join = ""
        capital_flow_select = ""
        if include_capital_flow:
            capital_flow_join = """
            LEFT JOIN capital_flow_daily cfd
              ON cfd.code = td.code
             AND cfd.trade_date = td.trade_date
            """
            capital_flow_select = """
            , cfd.main_net_inflow
            , cfd.main_net_inflow_ratio
            , cfd.super_large_net_inflow
            , cfd.super_large_net_inflow_ratio
            , cfd.large_net_inflow
            , cfd.large_net_inflow_ratio
            , cfd.medium_net_inflow
            , cfd.medium_net_inflow_ratio
            , cfd.small_net_inflow
            , cfd.small_net_inflow_ratio
            """

        query = f"""
            WITH training_daily AS (
                WITH history AS (
                    SELECT code, trade_date, open, high, low, close, volume, amount, pct_chg, turnover,
                           ROW_NUMBER() OVER (PARTITION BY code ORDER BY trade_date DESC) AS rn
                    FROM daily_bar
                    WHERE adj_flag = ?
                      AND code IN ({placeholders})
                      AND trade_date <= ?
                ),
                future AS (
                    SELECT code, trade_date, open, high, low, close, volume, amount, pct_chg, turnover, NULL AS rn
                    FROM daily_bar
                    WHERE adj_flag = ?
                      AND code IN ({placeholders})
                      AND trade_date > ?
                      AND trade_date <= ?
                )
                SELECT code, trade_date, open, high, low, close, volume, amount, pct_chg, turnover
                FROM history
                WHERE rn <= ?
                UNION ALL
                SELECT code, trade_date, open, high, low, close, volume, amount, pct_chg, turnover
                FROM future
            )
            SELECT td.code, td.trade_date, td.open, td.high, td.low, td.close, td.volume, td.amount, td.pct_chg, td.turnover,
                   ssd.is_st, ssd.is_new_stock_window, ssd.is_limit_up, ssd.is_limit_down,
                   fs.ma5, fs.ma10, fs.ma20, fs.ma60, fs.momentum20, fs.momentum60,
                   fs.volatility20, fs.volume_ratio, fs.turnover_mean20, fs.drawdown60,
                   fs.relative_strength_hs300, fs.breakout20
                   {capital_flow_select}
            FROM training_daily td
            LEFT JOIN security_status_daily ssd
              ON ssd.code = td.code
             AND ssd.trade_date = td.trade_date
            LEFT JOIN factor_snapshot fs
              ON fs.code = td.code
             AND fs.trade_date = td.trade_date
            {capital_flow_join}
            ORDER BY td.code, td.trade_date
        """
        params: list[Any] = [
            adj_flag,
            *codes,
            normalized_cutoff,
            adj_flag,
            *codes,
            normalized_cutoff,
            normalized_end,
            keep_history,
        ]
        with self.connect() as conn:
            return pd.read_sql_query(query, conn, params=params)

    def query_intraday_bars_60m(
        self,
        codes: Sequence[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        self.initialize_schema()
        clauses: list[str] = []
        params: list[Any] = []

        if codes:
            placeholders = ",".join(["?"] * len(codes))
            clauses.append(f"code IN ({placeholders})")
            params.extend(codes)
        if start_date:
            clauses.append("trade_date >= ?")
            params.append(normalize_date(start_date))
        if end_date:
            clauses.append("trade_date <= ?")
            params.append(normalize_date(end_date))

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"""
            SELECT code, trade_date, bar_time, open, high, low, close, volume, amount
            FROM intraday_bar_60m
            {where}
            ORDER BY code, bar_time
        """

        with self.connect() as conn:
            return pd.read_sql_query(query, conn, params=params)

    def get_stock(self, code: str, cutoff_date: str | None = None) -> pd.DataFrame:
        return self.query_daily_bars(codes=[code], end_date=cutoff_date)

    def get_security_pool_at_date(self, cutoff_date: str) -> list[str]:
        self.initialize_schema()
        cutoff = normalize_date(cutoff_date)
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT sm.code
                FROM security_master sm
                LEFT JOIN security_status_daily ssd
                  ON sm.code = ssd.code AND ssd.trade_date = ?
                WHERE sm.list_date != ''
                  AND sm.list_date <= ?
                  AND (sm.delist_date = '' OR sm.delist_date >= ?)
                  AND COALESCE(ssd.is_st, sm.is_st, 0) = 0
                  AND COALESCE(ssd.is_new_stock_window, 0) = 0
                ORDER BY sm.code
                """,
                (cutoff, cutoff, cutoff),
            ).fetchall()
            if rows:
                return [row[0] for row in rows]

            fallback = conn.execute(
                "SELECT DISTINCT code FROM daily_bar WHERE trade_date <= ? ORDER BY code",
                (cutoff,),
            ).fetchall()
        return [row[0] for row in fallback]

    def get_survival_flags(self, cutoff_date: str, codes: Sequence[str]) -> dict[str, bool]:
        if not codes:
            return {}
        self.initialize_schema()
        cutoff = normalize_date(cutoff_date)
        placeholders = ",".join(["?"] * len(codes))
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT code, delist_date FROM security_master WHERE code IN ({placeholders})",
                list(codes),
            ).fetchall()
        delist_map = {row[0]: row[1] for row in rows}
        return {
            code: (not delist_map.get(code)) or normalize_date(delist_map[code]) > cutoff
            for code in codes
        }
