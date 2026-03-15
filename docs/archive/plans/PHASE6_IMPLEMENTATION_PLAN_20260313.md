# 第六阶段结构性重构实施计划（2026-03-13）

## 1. 目标

按 RFC 的 Wave A-F 顺序推进整体重构，在不破坏现有 CLI / Web / runtime 行为的前提下，逐步建立清晰的 `interface -> application -> domain -> infrastructure` 边界。

## 2. 实施顺序

### Wave A：骨架建立

- 新增 `app/application/`、`app/interfaces/`
- 为训练、指挥、投资分析建立 application facade
- 为 Web 路由建立 interface registry
- 为 `market_data/`、`invest/` 落 service facade
- 增加结构守卫测试

### Wave B：训练编排拆分

- 从 `SelfLearningController` 抽 `TrainingOrchestrator`
- 抽持久化、诊断、评估聚合边界
- 降低 `app/train.py` 主对象复杂度

### Wave C：投资分析与会议编排拆分

- 抽 `SelectionMeetingService`
- 抽 `ReviewMeetingService`
- 抽 `EvolutionService`
- 将 `StockAnalysisService` 收敛为 facade

### Wave D：市场数据子服务化

- 切出 availability / resolver / benchmark / quality / sync 责任
- 收窄 `DataManager` 作为兼容 facade 的角色

### Wave E：runtime protocol 与 Web 资源路由

- 继续拆 runtime core / protocol / presentation
- 把 Web route group 从 read / ops / data / command 逐步迁移到资源化组织

### Wave F：守卫固化与兼容层清理

- 增加架构依赖测试
- 清理阶段性适配层
- 更新架构文档与验证门禁

## 3. 当前状态

- RFC：已完成
- Wave A：进行中
- Wave B-F：待执行

## 4. 每波统一门禁

- `ruff check .`
- `pyright .`
- `pytest`
- `python3 -m app.freeze_gate --mode quick`
