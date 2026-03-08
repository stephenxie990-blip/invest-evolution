
## [LRN-20260308-001] best_practice

**Logged**: 2026-03-08T11:00:17
**Priority**: critical
**Status**: fixed
**Area**: config

### Summary
运行时配置快照不应落盘明文密钥；审计快照应统一走脱敏视图

### Details
真实训练生成的 `config_snapshots` 和 `*_config_snapshot.json` 包含明文 `llm_api_key`，这会把生产凭证扩散到训练产物目录和历史快照中。

### Suggested Action
所有配置快照、训练快照和导出副本统一调用脱敏函数；已有快照需要离线清理并建议轮换暴露过的密钥。

### Metadata
- Source: error
- Related Files: config/services.py
- Tags: security, config, snapshot

---
