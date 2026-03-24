from datetime import datetime
from invest_evolution.market_data.manager import DataIngestionService
from invest_evolution.market_data.repository import MarketDataRepository

repo = MarketDataRepository('data/stock_history.db')
service = DataIngestionService(repository=repo)
codes = repo.list_security_codes()
existing = set(repo.query_daily_bars(end_date=datetime.now().strftime('%Y%m%d'))['code'].unique().tolist())
missing = [c for c in codes if c not in existing]
print({'started_at': datetime.now().isoformat(timespec='seconds'), 'total': len(codes), 'existing': len(existing), 'missing': len(missing)}, flush=True)
processed = 0
for start in range(0, len(missing), 10):
    batch = missing[start:start+10]
    try:
        result = service.sync_daily_bars(codes=batch, start_date='20180101')
        print({'processed': processed + len(batch), 'rows_added': result.get('row_count', 0), 'stocks_added': result.get('stock_count', 0), 'batch_first': batch[0], 'batch_last': batch[-1]}, flush=True)
    except Exception as exc:
        print({'processed': processed, 'error': str(exc), 'batch_first': batch[0], 'batch_last': batch[-1]}, flush=True)
    processed += len(batch)
print({'finished_at': datetime.now().isoformat(timespec='seconds'), 'processed': processed}, flush=True)
