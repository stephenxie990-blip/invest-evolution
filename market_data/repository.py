import logging
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

import pandas as pd

from config import PROJECT_ROOT, normalize_date

logger = logging.getLogger(__name__)


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
        return len(rows)

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
        return len(rows)

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
        return len(rows)

    def upsert_meta(self, mapping: Mapping[str, Any]) -> int:
        rows = [(str(key), str(value)) for key, value in mapping.items()]
        if not rows:
            return 0
        with self.connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO ingestion_meta(key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
                rows,
            )
        return len(rows)

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

    def get_status_summary(self) -> dict[str, Any]:
        self.initialize_schema()
        with self.connect() as conn:
            stock_count = conn.execute("SELECT COUNT(*) FROM security_master").fetchone()[0]
            kline_count = conn.execute("SELECT COUNT(*) FROM daily_bar").fetchone()[0]
            latest_row = conn.execute("SELECT MAX(trade_date) FROM daily_bar").fetchone()
            latest_date = latest_row[0] if latest_row and latest_row[0] else ""
            financial_count = conn.execute("SELECT COUNT(*) FROM financial_snapshot").fetchone()[0]
        size_mb = round(self.db_path.stat().st_size / (1024 * 1024), 2) if self.db_path.exists() else 0.0
        return {
            "db_path": str(self.db_path.absolute()),
            "size_mb": size_mb,
            "stock_count": stock_count,
            "kline_count": kline_count,
            "financial_count": financial_count,
            "latest_date": latest_date,
            "schema": "canonical_v1",
        }

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

    def get_stock(self, code: str, cutoff_date: str | None = None) -> pd.DataFrame:
        return self.query_daily_bars(codes=[code], end_date=cutoff_date)

    def get_security_pool_at_date(self, cutoff_date: str) -> list[str]:
        self.initialize_schema()
        cutoff = normalize_date(cutoff_date)
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT code
                FROM security_master
                WHERE list_date != ''
                  AND list_date <= ?
                  AND (delist_date = '' OR delist_date >= ?)
                ORDER BY code
                """,
                (cutoff, cutoff),
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
