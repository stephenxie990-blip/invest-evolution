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

## [ERR-20260308-002] llm_json_response_instability

**Logged**: 2026-03-08T11:00:17
**Priority**: high
**Status**: fixed
**Area**: backend

### Summary
真实训练中，LLM 多次返回带 Markdown 代码块或额外说明的 JSON，导致解析告警并拉长辩论阶段耗时

### Error
```
Failed to parse JSON from LLM response: ```json ...
```

### Context
- Operation: 通过 Web 触发真实训练 `POST /api/train`
- Provider: MiniMax via LiteLLM
- Symptoms: Debate / review 阶段多次出现 parse warning，但流程未中断

### Suggested Fix
统一 LLM JSON 解析器，支持 fenced JSON、前后说明文本、未闭合代码块和 JSON 前缀场景

### Metadata
- Reproducible: yes
- Related Files: invest/shared/llm.py

---
