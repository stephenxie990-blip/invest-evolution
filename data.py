"""
投资进化系统 - 数据加载

包含：
1. DataCache            — SQLite 本地缓存（主数据源，baostock 下载）
2. OfflineDataLoader    — 从 SQLite 读取历史数据（主接口）
3. DataDownloader       — 从 Tushare 下载数据到 SQLite（一次性）
4. EvolutionDataLoader  — 随机截断日期 + 在线数据加载
5. HistoricalStockPool  — T0 时间点股票池（修正幸存者偏差）
6. T0DataLoader         — T0 数据加载器
7. generate_mock_stock_data — 生成模拟数据（测试用）
8. DataManager          — 统一入口，自动选择合适的加载器

所有加载器遵守 T0 约束：
  只使用 T0 时间点之前的数据，不引入未来信息
"""

import argparse
import logging
import os
import random
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Protocol

import numpy as np
import pandas as pd

from config import config, PROJECT_ROOT, normalize_date

logger = logging.getLogger(__name__)

# ============================================================
# 共享常量
# ============================================================

_DEFAULT_DB_PATH = Path(os.environ.get("INVEST_DB_PATH", str(PROJECT_ROOT / "data" / "stock_history.db")))
_DEFAULT_OFFLINE_DB_PATH = _DEFAULT_DB_PATH

# 常见股票池（各行业龙头，供 EvolutionDataLoader 使用）
DEFAULT_STOCK_POOL = [
    "sh.600519",  # 贵州茅台
    "sh.000858",  # 五粮液
    "sh.601318",  # 中国平安
    "sh.600036",  # 招商银行
    "sh.600900",  # 长江电力
    "sz.000333",  # 美的集团
    "sz.002594",  # 比亚迪
    "sh.600276",  # 恒瑞医药
    "sz.000651",  # 格力电器
    "sh.601888",  # 中国中免
    "sz.300750",  # 宁德时代
    "sz.002475",  # 立讯精密
    "sh.600030",  # 中信证券
    "sh.601012",  # 隆基绿能
    "sz.000002",  # 万科A
    "sh.600016",  # 民生银行
    "sh.601166",  # 兴业银行
    "sh.601398",  # 工商银行
    "sh.601857",  # 中国石油
    "sz.000001",  # 平安银行
]


def _random_cutoff_date(
    min_date: str = "20180101",
    max_date: str = None,
    min_history_days: int = 730,
) -> str:
    """
    共享工具：随机选择历史截断日期 T0

    规则：T0 之前至少有 min_history_days 天历史数据
    """
    if max_date is None:
        max_date = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")

    min_dt = datetime.strptime(min_date, "%Y%m%d")
    max_dt = datetime.strptime(max_date, "%Y%m%d")
    earliest_valid = min_dt + timedelta(days=min_history_days)

    if earliest_valid >= max_dt:
        logger.warning(f"历史数据不足，使用最小日期: {min_date}")
        return min_date

    random_days = random.randint(0, (max_dt - earliest_valid).days)
    result = (earliest_valid + timedelta(days=random_days)).strftime("%Y%m%d")
    logger.info(f"随机截断日期: {result}")
    return result


# ============================================================
# Part 1: SQLite 本地缓存（baostock 数据）
# ============================================================

class DataCache:
    """
    本地数据缓存

    预下载全量 baostock 数据到 SQLite，训练时直接从本地读取
    解决性能问题：避免 1200 轮训练 × 5500 只股票的大量 API 调用
    """

    def __init__(self, db_path: str = None):
        self.db_path = Path(db_path) if db_path else _DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn: Optional[sqlite3.Connection] = None

    def connect(self):
        """连接数据库，启用并发安全设置"""
        self.conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            timeout=30.0
        )
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA synchronous=NORMAL;")
        logger.info(f"数据库连接: {self.db_path}")

    def close(self):
        """关闭连接"""
        if self.conn:
            self.conn.close()
            self.conn = None

    def _ensure_connected(self):
        if not self.conn:
            self.connect()

    def create_tables(self):
        """创建数据库表"""
        self._ensure_connected()
        cursor = self.conn.cursor()

        # 股票基本信息表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS stock_info (
                code TEXT PRIMARY KEY,
                name TEXT,
                list_date TEXT,
                delist_date TEXT,
                industry TEXT,
                is_st INTEGER DEFAULT 0
            )
        """)

        # 日K线数据表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS daily_kline (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                amount REAL,
                UNIQUE(code, trade_date)
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_kline_code_date ON daily_kline(code, trade_date)"
        )

        # 财务数据表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS financial_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL,
                report_date TEXT NOT NULL,
                publish_date TEXT,
                roe REAL,
                net_profit REAL,
                revenue REAL,
                total_assets REAL,
                market_cap REAL,
                UNIQUE(code, report_date)
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_financial_code_date ON financial_data(code, report_date)"
        )

        self.conn.commit()
        logger.info("数据库表创建完成")

    def get_status_summary(self) -> dict:
        """获取本地数据库状态统计信息用于前端显示"""
        self._ensure_connected()
        cursor = self.conn.cursor()
        
        try:
            cursor.execute("SELECT COUNT(*) FROM stock_info")
            stock_count = cursor.fetchone()[0]
        except Exception:
            stock_count = 0
            
        try:
            cursor.execute("SELECT MAX(trade_date) FROM daily_kline")
            res = cursor.fetchone()
            latest_date = res[0] if res and res[0] else ""
        except Exception:
            latest_date = ""
            
        try:
            cursor.execute("SELECT COUNT(*) FROM daily_kline")
            kline_count = cursor.fetchone()[0]
        except Exception:
            kline_count = 0
            
        size_mb = 0.0
        if self.db_path.exists():
            size_mb = self.db_path.stat().st_size / (1024 * 1024)
            
        return {
            "db_path": str(self.db_path.absolute()),
            "size_mb": round(size_mb, 2),
            "stock_count": stock_count,
            "kline_count": kline_count,
            "latest_date": latest_date
        }

    def download_stock_info(self):
        """从 baostock 下载全部股票基本信息"""
        import baostock as bs
        self._ensure_connected()
        bs.login()

        rs = bs.query_stock_basic()
        cursor = self.conn.cursor()
        count = 0

        while rs.next():
            row = rs.get_row_data()
            code, name, ipo_date, out_date = row[0], row[1], row[2] or "", row[3] or ""

            # 只保留 A 股
            if not (code.startswith("sh.6") or code.startswith("sz.00") or code.startswith("sz.30")):
                continue

            is_st = 1 if "ST" in name or "*ST" in name else 0
            cursor.execute(
                "INSERT OR REPLACE INTO stock_info (code, name, list_date, delist_date, is_st) VALUES (?, ?, ?, ?, ?)",
                (code, name, ipo_date, out_date, is_st)
            )
            count += 1

        self.conn.commit()
        bs.logout()
        logger.info(f"股票基本信息下载完成: {count} 只")

    def download_daily_kline(
        self,
        codes: List[str] = None,
        start_date: str = "2016-01-01",
        end_date: str = None,
    ):
        """批量下载日 K 线数据到 SQLite"""
        import baostock as bs

        if end_date is None:
            end_date = datetime.now().strftime("%Y-%m-%d")

        self._ensure_connected()
        if codes is None:
            cursor = self.conn.cursor()
            cursor.execute("SELECT code FROM stock_info")
            codes = [row[0] for row in cursor.fetchall()]

        logger.info(f"下载日K线: {len(codes)} 只, {start_date} ~ {end_date}")
        bs.login()
        cursor = self.conn.cursor()
        success = 0

        for i, code in enumerate(codes):
            try:
                rs = bs.query_history_k_data_plus(
                    code,
                    "date,code,open,high,low,close,volume,amount",
                    start_date=start_date,
                    end_date=end_date,
                    frequency="d",
                    adjustflag="2",
                )
                while rs.next():
                    row = rs.get_row_data()
                    trade_date = normalize_date(row[0])
                    cursor.execute(
                        "INSERT OR REPLACE INTO daily_kline (code, trade_date, open, high, low, close, volume, amount) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            code, trade_date,
                            float(row[2]) if row[2] else None,
                            float(row[3]) if row[3] else None,
                            float(row[4]) if row[4] else None,
                            float(row[5]) if row[5] else None,
                            float(row[6]) if row[6] else None,
                            float(row[7]) if row[7] else None,
                        ),
                    )
                success += 1
                if (i + 1) % 100 == 0:
                    self.conn.commit()
                    logger.info(f"  进度: {i+1}/{len(codes)}")
            except Exception as e:
                logger.debug(f"下载 {code} 失败: {e}")

        self.conn.commit()
        bs.logout()
        logger.info(f"日K线下载完成: {success}/{len(codes)} 只")

    def load_daily_kline(
        self,
        code: str,
        start_date: str = None,
        end_date: str = None,
    ) -> pd.DataFrame:
        """从 SQLite 加载单支股票日 K 线"""
        self._ensure_connected()
        query = "SELECT * FROM daily_kline WHERE code = ?"
        params = [code]
        if start_date:
            query += " AND trade_date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND trade_date <= ?"
            params.append(end_date)
        query += " ORDER BY trade_date"
        return pd.read_sql_query(query, self.conn, params=params)

    def load_stock_list(self, filter_st: bool = True) -> List[str]:
        """从 SQLite 读取股票列表"""
        self._ensure_connected()
        query = "SELECT code FROM stock_info"
        if filter_st:
            query += " WHERE is_st = 0"
        cursor = self.conn.cursor()
        cursor.execute(query)
        return [row[0] for row in cursor.fetchall()]

    def get_stocks_at_date(self, cutoff_date: str) -> List[str]:
        """获取指定日期在市的股票（修正幸存者偏差）"""
        self._ensure_connected()
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT code FROM stock_info
            WHERE list_date <= ?
            AND (delist_date > ? OR delist_date = '')
        """, (cutoff_date, cutoff_date))
        return [row[0] for row in cursor.fetchall()]


# ============================================================
# Part 2: 离线数据加载器（从 SQLite 读取，主接口）
# ============================================================

class OfflineDataLoader:
    """
    离线数据加载器

    从本地 SQLite 数据库读取股票历史数据
    数据库由 DataDownloader 预先下载写入
    """

    def __init__(self, db_path: str = None):
        self.db_path = Path(db_path) if db_path else _DEFAULT_OFFLINE_DB_PATH
        self._db_exists = self.db_path.exists()

        if self._db_exists:
            self._check_data()
        else:
            logger.warning(f"数据库不存在: {self.db_path}，请先运行 DataDownloader.download_all()")

    def _check_data(self):
        """检查数据完整性"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(DISTINCT code), MIN(trade_date), MAX(trade_date) FROM stock_daily")
            count, min_date, max_date = cursor.fetchone()
            conn.close()
            if count:
                logger.info(f"数据库: {count} 只股票, {min_date} ~ {max_date}")
        except Exception as e:
            logger.warning(f"检查数据库失败: {e}")
            self._db_exists = False

    def get_stocks(
        self,
        cutoff_date: str,
        stock_count: int = 50,
        min_history_days: int = 200,
        include_future_days: int = 0,
    ) -> Dict[str, pd.DataFrame]:
        """
        获取截止日期之前的股票数据

        Args:
            cutoff_date: YYYYMMDD
            stock_count: 返回的股票数量
            min_history_days: 需要的最小历史天数
            include_future_days: 额外加载 cutoff 之后的自然日窗口（用于训练期逐日释放）

        Returns:
            {code: DataFrame}
        """
        if not self._db_exists:
            logger.warning("数据库不存在，使用模拟数据")
            return generate_mock_stock_data(stock_count)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT code, COUNT(*) as days
            FROM stock_daily
            WHERE trade_date <= ?
            GROUP BY code
            HAVING days >= ?
            ORDER BY days DESC
            LIMIT ?
        """, (cutoff_date, min_history_days, stock_count))

        valid_codes = [row[0] for row in cursor.fetchall()]

        if not valid_codes:
            conn.close()
            logger.warning("没有找到足够历史数据的股票，使用模拟数据")
            return generate_mock_stock_data(stock_count)

        query_end_date = cutoff_date
        if include_future_days > 0:
            cutoff_dt = datetime.strptime(cutoff_date, "%Y%m%d")
            # 按自然日放大窗口覆盖交易日（周末/节假日）
            query_end_date = (cutoff_dt + timedelta(days=include_future_days * 2)).strftime("%Y%m%d")

        placeholders = ",".join(["?"] * len(valid_codes))
        query = f"""
            SELECT code, trade_date, open, high, low, close, volume, amount, pct_chg, turnover
            FROM stock_daily
            WHERE code IN ({placeholders}) AND trade_date <= ?
            ORDER BY code, trade_date
        """
        df = pd.read_sql_query(query, conn, params=valid_codes + [query_end_date])
        conn.close()

        stock_data = {}
        for code in valid_codes:
            stock_df = df[df["code"] == code].copy()
            if not stock_df.empty:
                stock_df["date"] = stock_df["trade_date"]
                stock_data[code] = stock_df

        logger.info(f"加载了 {len(stock_data)} 只股票数据 (cutoff={cutoff_date})")
        return stock_data

    def get_stock(self, code: str, cutoff_date: str = None) -> Optional[pd.DataFrame]:
        """获取单只股票的数据"""
        if not self._db_exists:
            return None

        conn = sqlite3.connect(self.db_path)
        params = [code, cutoff_date] if cutoff_date else [code]
        where = "WHERE code = ? AND trade_date <= ?" if cutoff_date else "WHERE code = ?"
        df = pd.read_sql_query(
            f"SELECT * FROM stock_daily {where} ORDER BY trade_date",
            conn, params=params
        )
        conn.close()

        if df.empty:
            return None
        df["date"] = df["trade_date"]
        return df

    def get_available_date_range(self) -> Tuple[Optional[str], Optional[str]]:
        """获取数据库中的日期范围"""
        if not self._db_exists:
            return None, None
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT MIN(trade_date), MAX(trade_date) FROM stock_daily")
        result = cursor.fetchone()
        conn.close()
        return result

    def get_stock_count(self) -> int:
        """获取数据库中的股票数量"""
        if not self._db_exists:
            return 0
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(DISTINCT code) FROM stock_daily")
        count = cursor.fetchone()[0]
        conn.close()
        return count

    @property
    def available(self) -> bool:
        return self._db_exists


# ============================================================
# Part 3: 数据下载器（Tushare → SQLite，一次性运行）
# ============================================================

class DataDownloader:
    """
    数据下载器

    从 Tushare 一次性下载历史数据到 SQLite
    训练时 OfflineDataLoader 从 SQLite 读取，不依赖网络
    """

    def __init__(self, tushare_token: str = None, db_path: str = None):
        self.token = tushare_token or os.environ.get("TUSHARE_TOKEN", "")
        self.db_path = Path(db_path) if db_path else _DEFAULT_OFFLINE_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._pro = None

    def _init_tushare(self) -> bool:
        try:
            import tushare as ts
            if not self.token:
                logger.error("TUSHARE_TOKEN 未设置，无法初始化 Tushare")
                return False
            ts.set_token(self.token)
            self._pro = ts.pro_api()
            logger.info("Tushare 初始化成功")
            return True
        except Exception as e:
            logger.error(f"Tushare 初始化失败: {e}")
            return False

    def init_db(self):
        """初始化数据库表结构"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS stock_daily (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                open REAL, high REAL, low REAL, close REAL,
                volume REAL, amount REAL,
                pct_chg REAL, change REAL, turnover REAL,
                UNIQUE(code, trade_date)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_code ON stock_daily(code)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_trade_date ON stock_daily(trade_date)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_code_date ON stock_daily(code, trade_date)")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY, value TEXT
            )
        """)
        conn.commit()
        conn.close()
        logger.info(f"数据库初始化完成: {self.db_path}")

    def get_stock_list(self) -> List[str]:
        """获取 A 股股票列表"""
        if not self._pro and not self._init_tushare():
            return DEFAULT_STOCK_POOL

        try:
            df = self._pro.stock_basic(
                exchange="", list_status="L",
                fields="ts_code,symbol,name,area,industry,list_date"
            )
            # Tushare 格式转 baostock 格式
            codes = []
            for ts_code in df["ts_code"].tolist():
                if ts_code.endswith(".SH"):
                    codes.append(f"sh.{ts_code[:-3]}")
                elif ts_code.endswith(".SZ"):
                    codes.append(f"sz.{ts_code[:-3]}")
            return codes[:500]
        except Exception as e:
            logger.warning(f"获取股票列表失败: {e}")
            return DEFAULT_STOCK_POOL

    def _is_data_exists(self, code: str, start_date: str, end_date: str) -> bool:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COUNT(*) FROM stock_daily
            WHERE code = ? AND trade_date >= ? AND trade_date <= ?
        """, (code, start_date, end_date))
        count = cursor.fetchone()[0]
        conn.close()
        return count > 200

    def _download_one(self, code: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        if not self._pro and not self._init_tushare():
            return None

        # baostock 格式 → Tushare 格式
        if code.startswith("sh."):
            ts_code = code[3:] + ".SH"
        elif code.startswith("sz."):
            ts_code = code[3:] + ".SZ"
        else:
            ts_code = code

        for attempt in range(3):
            try:
                df = self._pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
                if df is None or df.empty:
                    return None
                df["code"] = code
                df = df.rename(columns={"vol": "volume"})
                if "volume" in df.columns and "amount" in df.columns:
                    df["turnover"] = df["amount"] / df["close"] / 10_000_000 * 100
                return df
            except Exception as e:
                logger.debug(f"下载 {code} attempt {attempt+1}: {e}")
                if attempt < 2:
                    time.sleep(1)
        return None

    def download_all(
        self,
        stock_list: List[str] = None,
        start_date: str = "20180101",
        end_date: str = None,
        batch_size: int = 50,
    ):
        """
        批量下载所有股票数据

        Args:
            stock_list: 股票代码列表，None 则自动获取
            start_date: 开始日期 YYYYMMDD
            end_date: 结束日期 YYYYMMDD
            batch_size: 打印进度的批次大小
        """
        if not self._init_tushare():
            logger.error("Tushare 初始化失败，无法下载数据")
            return

        if end_date is None:
            end_date = datetime.now().strftime("%Y%m%d")

        if stock_list is None:
            stock_list = self.get_stock_list()

        self.init_db()
        total = len(stock_list)
        success, failed = 0, []

        for i, code in enumerate(stock_list):
            if self._is_data_exists(code, start_date, end_date):
                logger.debug(f"[{i+1}/{total}] 跳过(已存在): {code}")
                success += 1
                continue

            logger.info(f"[{i+1}/{total}] 下载: {code}")
            df = self._download_one(code, start_date, end_date)

            if df is not None and not df.empty:
                conn = sqlite3.connect(self.db_path)
                try:
                    df.to_sql("stock_daily", conn, if_exists="append", index=False, method="multi")
                    success += 1
                    logger.info(f"  → 保存 {len(df)} 条")
                except Exception as e:
                    logger.debug(f"  → 保存失败: {e}")
                finally:
                    conn.close()
            else:
                failed.append(code)
                logger.warning(f"  → 下载失败: {code}")

            if (i + 1) % batch_size == 0:
                logger.info(f"=== 进度: {i+1}/{total} (成功={success}, 失败={len(failed)}) ===")

        # 写元数据
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.executemany("INSERT OR REPLACE INTO metadata VALUES (?, ?)", [
            ("last_update", datetime.now().isoformat()),
            ("date_range", f"{start_date}-{end_date}"),
            ("stock_count", str(success)),
        ])
        conn.commit()
        conn.close()

        logger.info(f"=== 下载完成: {success}/{total}。失败: {failed[:10]} ===")


# ============================================================
# Part 4: 进化系统在线数据加载器
# ============================================================

class EvolutionDataLoader:
    """
    进化系统在线数据加载器

    随机截取历史截断日期 T0，从 baostock/tushare 在线加载数据
    适合 T0 数量少、不频繁使用的场景
    """

    def __init__(
        self,
        data_source: str = None,
        min_history_days: int = 365 * 3,
        stock_pool: List[str] = None,
    ):
        self.data_source = data_source or config.data_source
        self.min_history_days = min_history_days
        self.stock_pool = stock_pool or DEFAULT_STOCK_POOL
        self._client = None
        self._init_client()

    def _init_client(self):
        try:
            if self.data_source == "baostock":
                import baostock as bs
                self._bs = bs
                bs.login()
                logger.info("使用 baostock 数据源")
            else:
                logger.warning(f"数据源 {self.data_source} 需要 OfflineDataLoader")
        except Exception as e:
            logger.warning(f"baostock 初始化或登录失败: {e}")

    def random_cutoff_date(
        self, min_date: str = "20180101", max_date: str = None
    ) -> str:
        return _random_cutoff_date(min_date, max_date, self.min_history_days)

    def load_all_data_before(self, cutoff_date: str) -> Dict:
        """加载截断日期前的所有可用数据"""
        data = {"cutoff_date": cutoff_date, "index": {}, "sectors": {}, "stocks": {}}

        logger.info(f"加载 T0={cutoff_date} 之前的数据...")
        data["stocks"] = self._load_stock_data(cutoff_date)
        logger.info(f"数据加载完成: {len(data['stocks'])} 只股票")
        return data

    def _load_stock_data(self, cutoff_date: str) -> Dict[str, pd.DataFrame]:
        stock_data = {}
        if self.data_source != "baostock" or not hasattr(self, "_bs"):
            logger.warning("baostock 不可用，返回模拟数据")
            return generate_mock_stock_data(len(self.stock_pool))

        end_dt = datetime.strptime(cutoff_date, "%Y%m%d")
        start_dt = end_dt - timedelta(days=self.min_history_days + 30)
        start_date = start_dt.strftime("%Y-%m-%d")
        end_date = end_dt.strftime("%Y-%m-%d")

        for code in self.stock_pool:
            try:
                rs = self._bs.query_history_k_data_plus(
                    code,
                    "date,code,open,high,low,close,volume",
                    start_date=start_date,
                    end_date=end_date,
                    frequency="d",
                    adjustflag="2",
                )
                data_list = []
                while rs.next():
                    data_list.append(rs.get_row_data())

                if data_list and len(data_list) > 100:
                    df = pd.DataFrame(data_list, columns=rs.fields)
                    df["trade_date"] = df["date"].apply(normalize_date)
                    df["close"] = pd.to_numeric(df["close"], errors="coerce")
                    df["pct_chg"] = df["close"].pct_change() * 100
                    stock_data[code] = df
            except Exception as e:
                logger.debug(f"加载 {code} 失败: {e}")

        return stock_data

    def get_trading_dates(self, start_date: str, end_date: str) -> List[str]:
        """获取交易日列表"""
        if self.stock_pool and self.data_source == "baostock" and hasattr(self, "_bs"):
            sample = self.stock_pool[0]
            stock_data = self._load_stock_data(end_date)
            df = stock_data.get(sample)
            if df is not None:
                dates = df["trade_date"].tolist()
                return sorted([d for d in dates if start_date <= d <= end_date])
        return []


# ============================================================
# Part 5: T0 数据加载器（修正幸存者偏差）
# ============================================================

@dataclass
class HistoricalStock:
    """历史股票信息（含已退市）"""
    code: str
    name: str
    ipo_date: str   # 上市日期
    out_date: str   # 退市日期（空=未退市）


class HistoricalStockPool:
    """
    历史股票池管理器

    关键功能：
    1. 获取 T0 时间点的股票池（含后来退市的股票）
    2. 排除 T0 时尚未上市的股票
    修正幸存者偏差
    """

    def __init__(self):
        self.all_stocks: List[HistoricalStock] = []
        self._load_all_stocks()

    def _load_all_stocks(self):
        """从 baostock 加载全部历史股票（含已退市）"""
        try:
            import baostock as bs
            lg = bs.login()
            if lg.error_code != "0":
                raise RuntimeError("Baostock 登录失败")

            rs = bs.query_stock_basic()
            stocks = []
            while rs.next():
                row = rs.get_row_data()
                code, name, ipo_date, out_date = row[0], row[1], row[2] or "", row[3] or ""
                if not (code.startswith("sh.6") or code.startswith("sz.00") or code.startswith("sz.30")):
                    continue
                stocks.append(HistoricalStock(code=code, name=name, ipo_date=ipo_date, out_date=out_date))

            self.all_stocks = stocks
            active = sum(1 for s in stocks if not s.out_date)
            bs.logout()
            logger.info(f"历史股票总数: {len(stocks)} (活跃={active}, 退市={len(stocks)-active})")
        except Exception as e:
            logger.warning(f"加载历史股票失败: {e}，使用默认股票池")
            self.all_stocks = [
                HistoricalStock(code=c, name=c, ipo_date="2010-01-01", out_date="")
                for c in DEFAULT_STOCK_POOL
            ]

    def get_pool_at_date(self, cutoff_date: str) -> List[str]:
        """
        获取指定日期的股票池（已上市 + 未退市）

        修正幸存者偏差：包含 T0 后才退市的股票
        """
        cutoff = datetime.strptime(cutoff_date, "%Y%m%d")
        pool = []

        for stock in self.all_stocks:
            try:
                if stock.ipo_date:
                    ipo = datetime.strptime(stock.ipo_date, "%Y-%m-%d")
                    if ipo > cutoff:
                        continue  # T0 时还没上市
                else:
                    continue

                if stock.out_date:
                    out = datetime.strptime(stock.out_date, "%Y-%m-%d")
                    if out < cutoff:
                        continue  # T0 时已经退市
            except Exception:
                continue
            pool.append(stock.code)

        logger.info(f"T0={cutoff_date} 股票池: {len(pool)} 只")
        return pool

    def get_survived_stocks(self, cutoff_date: str, stocks: List[str]) -> Dict[str, bool]:
        """返回每只股票是否存活到今天"""
        cutoff = datetime.strptime(cutoff_date, "%Y%m%d")
        result = {}
        for code in stocks:
            stock_obj = next((s for s in self.all_stocks if s.code == code), None)
            if stock_obj:
                if not stock_obj.out_date:
                    result[code] = True
                else:
                    try:
                        out = datetime.strptime(stock_obj.out_date, "%Y-%m-%d")
                        result[code] = out > cutoff
                    except Exception:
                        result[code] = False
            else:
                result[code] = False
        return result


class T0DataLoader:
    """
    T0 时间点数据加载器

    完全修正幸存者偏差：
    - 使用 T0 时间点的股票池（含后来退市）
    - 数据范围：T0 前 2 年 到 T0 后 3 个月（供逐日模拟使用）
    """

    def __init__(self):
        self.historical_pool = HistoricalStockPool()

    def random_cutoff_date(
        self, min_date: str = "20180101", max_date: str = None, min_history_days: int = 730
    ) -> str:
        return _random_cutoff_date(min_date, max_date, min_history_days)

    def load_data_at_t0(self, cutoff_date: str, max_stocks: int = 500) -> Dict:
        """
        在 T0 时间点加载数据

        Returns:
            {"cutoff_date", "stocks": {code: DataFrame}, "survived": {code: bool}}
        """
        import baostock as bs

        stock_pool = self.historical_pool.get_pool_at_date(cutoff_date)
        if len(stock_pool) > max_stocks:
            stock_pool = random.sample(stock_pool, max_stocks)

        cutoff_dt = datetime.strptime(cutoff_date, "%Y%m%d")
        start_date = (cutoff_dt - timedelta(days=800)).strftime("%Y-%m-%d")
        end_date = (cutoff_dt + timedelta(days=90)).strftime("%Y-%m-%d")

        bs.login()
        stock_data = {}

        for i, code in enumerate(stock_pool):
            try:
                rs = bs.query_history_k_data_plus(
                    code,
                    "date,code,open,high,low,close,volume",
                    start_date=start_date,
                    end_date=end_date,
                    frequency="d",
                    adjustflag="2",
                )
                data_list = []
                while rs.next():
                    data_list.append(rs.get_row_data())

                if data_list and len(data_list) > 100:
                    df = pd.DataFrame(data_list, columns=rs.fields)
                    df["trade_date"] = df["date"].apply(normalize_date)
                    df["close"] = pd.to_numeric(df["close"], errors="coerce")
                    df["pct_chg"] = df["close"].pct_change() * 100
                    stock_data[code] = df

                    if (len(stock_data)) % 50 == 0:
                        logger.info(f"  已加载 {len(stock_data)} 只...")
            except Exception:
                pass

        bs.logout()
        logger.info(f"T0 数据加载完成: {len(stock_data)} 只")

        survived = self.historical_pool.get_survived_stocks(cutoff_date, list(stock_data.keys()))
        return {"cutoff_date": cutoff_date, "stocks": stock_data, "survived": survived}


# ============================================================
# Part 6: 模拟数据生成（开发测试用）
# ============================================================

def generate_mock_stock_data(
    stock_count: int = 50,
    days: int = 300,
    start_date: str = "20230101",
) -> Dict[str, pd.DataFrame]:
    """
    生成模拟股票数据（开发测试用）

    当没有真实数据时用此生成模拟数据，随机游走价格
    """
    dates = pd.date_range(start=start_date, periods=days, freq="B")
    trade_dates = dates.strftime("%Y%m%d").tolist()
    stock_data = {}

    for i in range(stock_count):
        code = f"sh.{600000 + i}"
        np.random.seed(42 + i)
        trend = np.random.choice([-0.001, 0, 0.001])
        close = 10 + np.cumsum(np.random.randn(days) * 0.3 + trend)
        close = np.maximum(close, 1)

        df = pd.DataFrame({
            "date": dates.strftime("%Y-%m-%d"),
            "trade_date": trade_dates,
            "open": close * (1 + np.random.randn(days) * 0.003),
            "high": close * (1 + np.abs(np.random.randn(days)) * 0.01),
            "low": close * (1 - np.abs(np.random.randn(days)) * 0.01),
            "close": close,
            "volume": np.random.randint(100_000, 50_000_000, days).astype(float),
            "amount": np.random.randint(100_000_000, 5_000_000_000, days).astype(float),
            "pct_chg": pd.Series(close).pct_change().fillna(0) * 100,
            "turnover": np.random.uniform(1, 10, days),
            "code": code,
        })
        stock_data[code] = df

    logger.info(f"生成了 {len(stock_data)} 只模拟股票数据")
    return stock_data


# ============================================================
# Part 7: 统一入口 DataManager
# ============================================================

class DataProvider(Protocol):
    """Data source strategy interface for DataManager."""

    def random_cutoff_date(self, min_date: str = "20180101", max_date: str | None = None) -> str:
        ...

    def load_stock_data(
        self,
        cutoff_date: str,
        stock_count: int = 50,
        min_history_days: int = 200,
        include_future_days: int = 0,
    ) -> Dict[str, pd.DataFrame]:
        ...


class MockDataProvider:
    """In-memory mock provider used for deterministic local training."""

    def __init__(
        self,
        stock_count: int = 30,
        days: int = 1500,
        start_date: str = "20200101",
        seed_cutoff_min: int = 250,
        seed_cutoff_tail: int = 60,
    ):
        self.data = generate_mock_stock_data(stock_count=stock_count, days=days, start_date=start_date)
        dates = []
        for df in self.data.values():
            dates.extend(df["trade_date"].tolist())
        uniq = sorted(set(dates))
        self._dates = uniq
        self._seed_cutoff_min = max(10, int(seed_cutoff_min))
        self._seed_cutoff_tail = max(10, int(seed_cutoff_tail))

    def random_cutoff_date(self, min_date: str = "20180101", max_date: str | None = None) -> str:
        del min_date, max_date
        if len(self._dates) < (self._seed_cutoff_min + self._seed_cutoff_tail + 1):
            return self._dates[-1] if self._dates else "20231201"
        return random.choice(self._dates[self._seed_cutoff_min : -self._seed_cutoff_tail])

    def load_stock_data(
        self,
        cutoff_date: str,
        stock_count: int = 50,
        min_history_days: int = 200,
        include_future_days: int = 0,
    ) -> Dict[str, pd.DataFrame]:
        del include_future_days
        selected: Dict[str, pd.DataFrame] = {}
        for code in list(self.data.keys())[: max(1, int(stock_count))]:
            df = self.data[code]
            df_before = df[df["trade_date"] <= cutoff_date]
            if len(df_before) >= max(1, int(min_history_days)):
                selected[code] = df.copy()
        return selected


class DataManager:
    """
    数据加载统一入口

    自动选择最合适的加载器：
    1. 首选 OfflineDataLoader（本地 SQLite，最快）
    2. 次选 EvolutionDataLoader（在线 baostock）
    3. 兜底 generate_mock_stock_data（模拟数据，测试用）

    也支持通过 data_provider 注入自定义数据策略（替代 monkey-patch）。
    """

    def __init__(
        self,
        db_path: str = None,
        prefer_offline: bool = True,
        data_provider: Optional[DataProvider] = None,
    ):
        self._provider = data_provider
        self._offline = OfflineDataLoader(db_path)
        self._online: Optional[EvolutionDataLoader] = None
        self._prefer_offline = prefer_offline

        if not self._offline.available:
            logger.info("离线数据库不可用，将使用在线数据源或模拟数据")

    def random_cutoff_date(
        self, min_date: str = "20180101", max_date: str = None
    ) -> str:
        if self._provider is not None:
            return self._provider.random_cutoff_date(min_date=min_date, max_date=max_date)

        if self._prefer_offline and self._offline.available:
            db_min, db_max = self._offline.get_available_date_range()
            if db_min and db_max:
                min_bound = max(normalize_date(min_date), normalize_date(db_min))

                if max_date:
                    max_bound = min(normalize_date(max_date), normalize_date(db_max))
                else:
                    latest_safe = datetime.strptime(normalize_date(db_max), "%Y%m%d") - timedelta(
                        days=max(getattr(config, "simulation_days", 30) * 2, 60)
                    )
                    max_bound = latest_safe.strftime("%Y%m%d")

                if min_bound < max_bound:
                    return _random_cutoff_date(min_bound, max_bound, config.min_history_days)

        return _random_cutoff_date(min_date, max_date, config.min_history_days)

    def load_stock_data(
        self,
        cutoff_date: str,
        stock_count: int = 50,
        min_history_days: int = 200,
        include_future_days: int = 0,
    ) -> Dict[str, pd.DataFrame]:
        if self._provider is not None:
            return self._provider.load_stock_data(
                cutoff_date=cutoff_date,
                stock_count=stock_count,
                min_history_days=min_history_days,
                include_future_days=include_future_days,
            )

        if self._prefer_offline and self._offline.available:
            return self._offline.get_stocks(
                cutoff_date=cutoff_date,
                stock_count=stock_count,
                min_history_days=min_history_days,
                include_future_days=include_future_days,
            )

        if self._online is None:
            try:
                self._online = EvolutionDataLoader()
            except Exception as exc:
                logger.warning(f"在线加载器初始化失败: {exc}")
                return generate_mock_stock_data(stock_count)

        try:
            effective_cutoff = cutoff_date
            if include_future_days > 0:
                cutoff_dt = datetime.strptime(cutoff_date, "%Y%m%d")
                effective_cutoff = (cutoff_dt + timedelta(days=include_future_days * 2)).strftime("%Y%m%d")

            data = self._online.load_all_data_before(effective_cutoff)
            stocks = data.get("stocks", {})
            if stocks:
                return dict(list(stocks.items())[:stock_count])
        except Exception as exc:
            logger.warning(f"在线数据加载失败: {exc}")

        logger.warning("所有数据源不可用，使用模拟数据")
        return generate_mock_stock_data(stock_count)

    @property
    def offline_available(self) -> bool:
        return self._offline.available
# ============================================================
# Part 8: 命令行入口（python data.py）
# ============================================================

def _cli_main():
    parser = argparse.ArgumentParser(description="投资进化系统 - 数据下载器")
    parser.add_argument("--stocks", type=int, default=200, help="股票数量")
    parser.add_argument("--start", type=str, default="20180101", help="开始日期 YYYYMMDD")
    parser.add_argument("--end", type=str, default=None, help="结束日期 YYYYMMDD")
    parser.add_argument("--token", type=str, default=None, help="Tushare Token")
    parser.add_argument("--test", action="store_true", help="测试模式（只下3只）")

    args = parser.parse_args()

    downloader = DataDownloader(tushare_token=args.token)
    stock_list = downloader.get_stock_list()

    if args.test:
        stock_list = stock_list[:3]
    else:
        stock_list = stock_list[: args.stocks]

    logger.info(f"将下载 {len(stock_list)} 只股票的数据")
    downloader.download_all(stock_list, args.start, args.end)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    _cli_main()

