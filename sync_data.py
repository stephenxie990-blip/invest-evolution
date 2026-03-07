#!/usr/bin/env python3
"""快速数据同步脚本 - 带进度显示和错误处理"""

import sys
import time
import sqlite3
import logging
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

import baostock as bs
from market_data.repository import MarketDataRepository

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    db_path = 'data/stock_history.db'
    repo = MarketDataRepository(db_path)

    # 获取需要同步的股票
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute('SELECT DISTINCT code FROM daily_bar')
    existing = set(row[0] for row in cursor.fetchall())

    cursor.execute('SELECT code FROM security_master')
    all_codes = [row[0] for row in cursor.fetchall()]
    conn.close()

    to_sync = [c for c in all_codes if c not in existing]
    total = len(to_sync)

    logger.info(f"需要同步 {total} 只股票...")
    logger.info(f"已有数据: {len(existing)} 只")

    # 登录 baostock
    lg = bs.login()
    if lg.error_code != '0':
        logger.error(f"Baostock 登录失败: {lg.error_msg}")
        return

    logger.info(f"Baostock 登录成功, 开始同步...")

    synced = 0
    failed = 0
    total_rows = 0
    rate_limit_count = 0

    try:
        for i, code in enumerate(to_sync, 1):
            try:
                rs = bs.query_history_k_data_plus(
                    code,
                    "date,code,open,high,low,close,volume,amount,pctChg,turn",
                    start_date='2018-01-01',
                    end_date='2026-03-07',
                    frequency='d',
                    adjustflag='2',
                )

                if rs.error_code != '0':
                    logger.warning(f"{code} 查询失败: {rs.error_msg}")
                    failed += 1
                    continue

                records = []
                while rs.next():
                    row = dict(zip(rs.fields, rs.get_row_data()))
                    records.append({
                        "code": row.get("code", code),
                        "trade_date": row.get("date", "").replace("-", ""),
                        "open": row.get("open"),
                        "high": row.get("high"),
                        "low": row.get("low"),
                        "close": row.get("close"),
                        "volume": row.get("volume"),
                        "amount": row.get("amount"),
                        "pct_chg": row.get("pctChg"),
                        "turnover": row.get("turn"),
                        "adj_flag": "hfq",
                        "source": "baostock",
                    })

                if records:
                    inserted = repo.upsert_daily_bars(records)
                    synced += 1
                    total_rows += inserted
                else:
                    # 无数据，可能已退市
                    pass

                # 进度显示
                if i % 50 == 0 or i == total:
                    conn = sqlite3.connect(db_path)
                    cursor = conn.cursor()
                    cursor.execute('SELECT COUNT(DISTINCT code) FROM daily_bar')
                    current_count = cursor.fetchone()[0]
                    conn.close()
                    logger.info(f"进度: {i}/{total} ({i*100//total}%) - 已同步: {current_count} 只")

                # 避免请求过快
                time.sleep(0.05)

            except Exception as e:
                logger.error(f"{code} 处理异常: {e}")
                failed += 1

                # 检查是否被限流
                if "limit" in str(e).lower() or "频率" in str(e):
                    rate_limit_count += 1
                    if rate_limit_count > 5:
                        logger.warning("检测到限流，停止同步")
                        break

    finally:
        bs.logout()

    # 最终统计
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(DISTINCT code) FROM daily_bar')
    final_count = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM daily_bar')
    final_rows = cursor.fetchone()[0]
    conn.close()

    logger.info("=" * 50)
    logger.info(f"同步完成!")
    logger.info(f"本次同步: {synced} 只, {total_rows:,} 条")
    logger.info(f"失败: {failed} 只")
    logger.info(f"数据库总计: {final_count} 只, {final_rows:,} 条")
    logger.info("=" * 50)


if __name__ == "__main__":
    main()
