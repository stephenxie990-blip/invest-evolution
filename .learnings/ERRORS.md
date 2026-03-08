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
## [ERR-20260308-001] session-catchup-python

**Logged**: 2026-03-08T00:00:00+08:00
**Priority**: low
**Status**: resolved
**Area**: infra

### Summary
`python` command unavailable in this workspace shell; switched to `python3`.

### Error
```
zsh:1: command not found: python
```

### Context
- Command attempted: `python /Users/zhangsan/.agents/skills/pi-planning-with-files/scripts/session-catchup.py "$(pwd)"`
- Fallback used: `python3 ...`

### Suggested Fix
Prefer `python3` in this environment for local helper scripts.

### Metadata
- Reproducible: yes
- Related Files: .learnings/ERRORS.md

---
## [ERR-20260308-003] web_service_not_running

**Logged**: 2026-03-08T13:06:59+08:00
**Priority**: medium
**Status**: resolved
**Area**: infra

### Summary
请求启动真实训练时，本地 Web 服务未在 8080 端口监听，导致 API 调用失败

### Error
```
curl: (7) Failed to connect to 127.0.0.1 port 8080 after 0 ms: Couldn't connect to server
```

### Context
- Operation: 通过 `POST /api/train` 启动真实训练
- Root cause: 开发服务未运行或已退出
- Resolution: 先重启 `web_server.py`，再发起训练请求

### Suggested Fix
执行训练前先探测 `/api/status`，若失败则自动拉起 Web 服务

### Metadata
- Reproducible: yes
- Related Files: web_server.py

---

## [ERR-20260308-004] shell_quote_mismatch

**Logged**: 2026-03-08T13:16:38+08:00
**Priority**: low
**Status**: resolved
**Area**: infra

### Summary
一次组合命令包含未转义反引号/引号，导致 zsh 解析失败

### Error
```
zsh:1: unmatched "
```

### Context
- Operation: 同时读取训练产物与代码检索
- Resolution: 改为分段命令，避免在 `rg` 模式里混用复杂引号

### Suggested Fix
复杂检索命令优先拆分为多段，或使用 here-doc / 单独 Python 脚本

### Metadata
- Reproducible: yes
- Related Files: .learnings/ERRORS.md

---

