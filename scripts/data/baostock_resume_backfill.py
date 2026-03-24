from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / 'data' / 'stock_history.db'
VENV_PYTHON = PROJECT_ROOT / '.venv' / 'bin' / 'python'
SKIP_PATH = PROJECT_ROOT / 'runtime' / 'logs' / 'baostock_no_data_codes.json'
BATCH_SIZE = 10
BATCH_TIMEOUT = 180
SLEEP_SECONDS = 2
START_DATE = '20180101'


def _load_skip_codes() -> set[str]:
    if not SKIP_PATH.exists():
        return set()
    try:
        return set(json.loads(SKIP_PATH.read_text(encoding='utf-8')))
    except Exception:
        return set()


def _save_skip_codes(codes: set[str]) -> None:
    SKIP_PATH.parent.mkdir(parents=True, exist_ok=True)
    SKIP_PATH.write_text(json.dumps(sorted(codes), ensure_ascii=False, indent=2), encoding='utf-8')


def _target_codes() -> list[str]:
    conn = sqlite3.connect(str(DB_PATH))
    try:
        latest = conn.execute('SELECT MAX(trade_date) FROM daily_bar').fetchone()[0] or datetime.now().strftime('%Y%m%d')
        rows = conn.execute(
            """
            SELECT code
            FROM security_master
            WHERE list_date != ''
              AND list_date <= ?
              AND (delist_date = '' OR delist_date >= ?)
            ORDER BY code
            """,
            (latest, START_DATE),
        ).fetchall()
        return [row[0] for row in rows]
    finally:
        conn.close()


def _covered_codes() -> set[str]:
    conn = sqlite3.connect(str(DB_PATH))
    try:
        rows = conn.execute("SELECT DISTINCT code FROM daily_bar WHERE adj_flag='hfq'").fetchall()
        return {row[0] for row in rows}
    finally:
        conn.close()


def _db_summary() -> dict[str, object]:
    conn = sqlite3.connect(str(DB_PATH))
    try:
        latest = conn.execute('SELECT MAX(trade_date) FROM daily_bar').fetchone()[0]
        return {
            'stock_count': conn.execute('SELECT COUNT(*) FROM security_master').fetchone()[0],
            'target_codes': conn.execute(
                """
                SELECT COUNT(*) FROM security_master
                WHERE list_date != ''
                  AND list_date <= ?
                  AND (delist_date = '' OR delist_date >= ?)
                """,
                (latest or datetime.now().strftime('%Y%m%d'), START_DATE),
            ).fetchone()[0],
            'covered_codes': conn.execute("SELECT COUNT(DISTINCT code) FROM daily_bar WHERE adj_flag='hfq'").fetchone()[0],
            'kline_count': conn.execute('SELECT COUNT(*) FROM daily_bar').fetchone()[0],
            'latest_date': latest,
        }
    finally:
        conn.close()


def _log(payload: dict[str, object]) -> None:
    payload = {'ts': datetime.now().isoformat(timespec='seconds'), **payload}
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def _run_worker(batch: list[str]) -> dict[str, object]:
    cmd = [str(VENV_PYTHON), str(Path(__file__).resolve()), '--worker', json.dumps(batch, ensure_ascii=False)]
    env = os.environ.copy()
    env['PYTHONPATH'] = str(PROJECT_ROOT)
    started = time.time()
    try:
        completed = subprocess.run(cmd, cwd=str(PROJECT_ROOT), env=env, capture_output=True, text=True, timeout=BATCH_TIMEOUT)
        duration = round(time.time() - started, 2)
        stdout = completed.stdout.strip().splitlines()
        stderr = completed.stderr.strip().splitlines()
        if completed.returncode != 0:
            return {
                'ok': False,
                'duration_sec': duration,
                'batch': batch,
                'error': stderr[-1] if stderr else f'worker exit {completed.returncode}',
                'stdout_tail': stdout[-3:],
            }
        payload = json.loads(stdout[-1]) if stdout else {}
        payload.update({'ok': True, 'duration_sec': duration, 'batch': batch})
        return payload
    except subprocess.TimeoutExpired:
        return {'ok': False, 'duration_sec': BATCH_TIMEOUT, 'batch': batch, 'error': 'timeout'}


def _worker(batch: list[str]) -> None:
    from invest_evolution.market_data.manager import DataIngestionService
    from invest_evolution.market_data.repository import MarketDataRepository

    repo = MarketDataRepository(DB_PATH)
    service = DataIngestionService(repository=repo)
    result = service.sync_daily_bars(codes=batch, start_date=START_DATE)
    print(json.dumps(result, ensure_ascii=False), flush=True)


def _controller() -> None:
    skip_codes = _load_skip_codes()
    total = len(_target_codes())
    _log({'event': 'start', 'db': _db_summary(), 'target_total': total, 'skip_codes': len(skip_codes)})
    round_no = 0
    while True:
        target_codes = _target_codes()
        covered_before_round = _covered_codes()
        missing = [code for code in target_codes if code not in covered_before_round and code not in skip_codes]
        if not missing:
            _log({'event': 'done', 'db': _db_summary(), 'target_total': len(target_codes), 'skip_codes': len(skip_codes)})
            return

        round_no += 1
        _log({'event': 'round_start', 'round': round_no, 'missing_codes': len(missing), 'covered_codes': len(covered_before_round), 'skip_codes': len(skip_codes)})
        for index in range(0, len(missing), BATCH_SIZE):
            batch = missing[index:index + BATCH_SIZE]
            before = _covered_codes()
            result = _run_worker(batch)
            after = _covered_codes()
            newly_covered = [code for code in batch if code not in before and code in after]
            unresolved = [code for code in batch if code not in after]
            summary = _db_summary()
            if result.get('ok'):
                if unresolved:
                    skip_codes.update(unresolved)
                    _save_skip_codes(skip_codes)
                _log({
                    'event': 'batch_ok',
                    'round': round_no,
                    'batch_index': index // BATCH_SIZE + 1,
                    'rows_added': result.get('row_count', 0),
                    'stocks_added': result.get('stock_count', 0),
                    'newly_covered': len(newly_covered),
                    'marked_skip': len(unresolved),
                    'duration_sec': result.get('duration_sec'),
                    'batch_first': batch[0],
                    'batch_last': batch[-1],
                    'db': summary,
                })
            else:
                _log({
                    'event': 'batch_fail',
                    'round': round_no,
                    'batch_index': index // BATCH_SIZE + 1,
                    'duration_sec': result.get('duration_sec'),
                    'batch_first': batch[0],
                    'batch_last': batch[-1],
                    'error': result.get('error'),
                    'db': summary,
                })
            time.sleep(SLEEP_SECONDS)


if __name__ == '__main__':
    if len(sys.argv) >= 3 and sys.argv[1] == '--worker':
        _worker(json.loads(sys.argv[2]))
    else:
        _controller()
