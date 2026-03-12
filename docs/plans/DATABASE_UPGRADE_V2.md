# 数据库升级 V2 状态说明

## 1. 当前结论

数据库 V2 升级在当前仓库里已经**基本落地完成**。

“V2” 在当前实现中的含义，不再是计划中的未来结构，而是：

- canonical SQLite schema 已统一
- 训练/网页/状态接口已切到统一数据层
- 数据源同步项已经覆盖日线、指数、财务、资金流、龙虎榜、60 分钟线等

## 2. 当前表结构范围

当前 repository 初始化的核心表：

- `security_master`
- `daily_bar`
- `index_bar`
- `financial_snapshot`
- `trading_calendar`
- `security_status_daily`
- `factor_snapshot`
- `capital_flow_daily`
- `dragon_tiger_list`
- `intraday_bar_60m`
- `ingestion_meta`

## 3. 已完成的升级目标

### 3.1 单库收口

默认所有主流程都读写：

- `data/stock_history.db`

### 3.2 读写分离清晰化

- 写入：`DataIngestionService`
- 读取：`TrainingDatasetBuilder` / `WebDatasetService` / 其他 dataset service

### 3.3 状态与质量内建

V2 不再只是一组数据表，而是连同以下能力一起交付：

- `get_status_summary()`
- `DataQualityService.audit()`
- readiness 诊断
- `ingestion_meta` 元数据维护

### 3.4 Web 与训练都在用同一套数据层

这意味着：

- `/api/data/*`
- `DataManager.load_stock_data()`
- leaderboard / allocator 所需的底层训练结果读取

都不再各自维护一套数据访问逻辑。

## 4. 当前仍保留的现实约束

### 4.1 不是通用迁移框架

当前项目没有引入 Alembic / Flyway 之类的正式迁移工具；schema 初始化仍由 repository 代码维护。

### 4.2 旧辅助脚本已移除

当前仓库中已不再保留旧的历史补数脚本，数据库主链只以 `market_data/` 为准。

### 4.3 仍以 SQLite 为单机场景优化

当前设计适合本地研究与单机运行。若未来要做多进程/多实例共享，需要额外设计锁、事务与并发访问策略。

## 5. 当前建议

- 如果只是继续扩充字段，优先扩 `MarketDataRepository` + `DataIngestionService`
- 如果只是训练使用新数据，优先扩 `datasets.py` 的 read-side builder
- 如果要做重大 schema 变更，先更新 `docs/blueprints/DATA_LAYER_UNIFICATION_PLAN.md`，再落代码
