## [ERR-20260308-001] tushare_financial_snapshot_permission

**Logged**: 2026-03-08T09:41:31
**Priority**: high
**Status**: pending
**Area**: backend

### Summary
Tushare 财务快照同步因账号无对应接口权限而失败

### Error
```
Exception: 抱歉，您没有接口访问权限，权限的具体详情访问：https://tushare.pro/document/1?doc_id=108。
```

### Context
- Command: `./.venv/bin/python -m market_data --source tushare --financials --stocks 500`
- Token 已提供，但 `daily_basic` / 财务接口权限不足
- 需要改为降级策略或使用具备权限的 Tushare 账户

### Suggested Fix
为受限接口增加 graceful fallback，并在启动前校验账户权限范围

### Metadata
- Reproducible: yes
- Related Files: market_data/ingestion.py

---
