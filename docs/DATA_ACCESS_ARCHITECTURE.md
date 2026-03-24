# 数据访问架构

当前系统的数据访问已经统一到 `src/invest_evolution/market_data/`，核心原则是：

- **写入统一入库**
- **读取统一经由 dataset builder / service**
- **训练与 Web 不直接拼 SQL**

## 1. 核心对象与稳定边界

文中若省略前缀，`market_data/repository.py`、`market_data/manager.py`、`market_data/datasets.py` 都默认指向 `src/invest_evolution/market_data/` 下的当前源码文件。

### 1.1 Repository

`src/invest_evolution/market_data/repository.py` 中的 `MarketDataRepository` 负责：

- 初始化 canonical schema
- 提供查询接口
- 提供 upsert 接口
- 维护 ingestion meta 与状态摘要

同时当前稳定 owner 还包括：

- `DataQualityService`
- `MarketDataGateway`

### 1.2 Manager / Write-side Facade

`src/invest_evolution/market_data/manager.py` 当前负责：

- `DataIngestionService`
- `DataManager`

### 1.3 Package-level Public Surface

上层若需要稳定 import surface，应优先依赖 `invest_evolution.market_data` 这个包级 public surface，而不是假设更细的文件拆分长期不变。

说明：

- 当前公开实现不再维护 `market_data/ingestion.py`、`market_data/quality.py`、`market_data/gateway.py` 作为独立公共 owner。
- 若未来再次拆分源码，也应先更新本文档与测试，再暴露新的公共 file owner。

### 1.4 Ingestion / Quality / Gateway 能力

`DataIngestionService` / `DataQualityService` / `MarketDataGateway` 当前负责：

- 股票主数据同步
- 日线同步
- 指数同步
- 交易日历同步
- 财务快照同步
- 资金流同步
- 龙虎榜同步
- 60 分钟线同步

### 1.5 Read-side Builders / Services

`src/invest_evolution/market_data/datasets.py` 当前提供：

- `TrainingDatasetBuilder`
- `WebDatasetService`
- `CapitalFlowDatasetService`
- `EventDatasetService`
- `IntradayDatasetBuilder`
- `T0DatasetBuilder`

### 1.6 Compatibility Facade

`src/invest_evolution/market_data/manager.py` 中的 `DataManager` 负责：

- 训练 readiness 检查
- stock data 加载
- 离线优先 / 在线兜底 / mock 兜底
- 对外提供统一方法入口

说明：

- 当前兼容 facade 仍集中在 `manager.py`，但不再把 ingestion / quality / gateway 重新拆成单独公共 file owner。
- 若未来再次拆分源码，也应先更新本文档与测试，再暴露新的公共 file owner。

## 2. 当前 canonical schema

主要表包括：

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

## 3. 读写分层

### 3.1 写路径

```text
External Source -> DataIngestionService -> MarketDataRepository -> SQLite
```

### 3.2 读路径

```text
SQLite -> Repository Query -> DatasetBuilder / Service -> Controller / Web API
```

## 4. 训练读取方式

训练不再依赖散落的数据拼装逻辑，而是：

1. `DataManager.check_training_readiness()`
2. `DataManager.load_stock_data()`
3. `TrainingDatasetBuilder.get_stocks()`
4. 追加 point-in-time 上下文（财务、因子、状态、资金流等）

## 5. Web 读取方式

公开 Web data surface 已收口为：

- `GET /api/data/status`
- `POST /api/data/download`

`capital_flow`、`dragon_tiger`、`intraday_60m` 等 drill-down 能力仍保留在 data layer / commander runtime 工具链中，但不再作为公开 Web contract 暴露。

## 6. 质量与诊断

`DataQualityService` 提供：

- 数据是否健康
- 主数据/日线/指数/财务/日历等是否齐全
- 日期范围是否有效
- 最新日期、股票数、K 线数等摘要

`DataManager.check_training_readiness()` 则提供训练视角的诊断，例如：

- 是否有足够股票满足最小历史天数
- 截断日是否落在离线覆盖范围内
- 是否需要降低 `min_history_days`
- 是否需要补齐近期日线或指数

## 7. 数据源分工

### 7.1 Baostock

当前主要用于：

- security master
- daily bars
- index bars
- trading calendar
- intraday 60m

### 7.2 Tushare

当前主要用于：

- financial snapshots
- 可选日线补数

### 7.3 Akshare

当前主要用于：

- trading calendar
- financial snapshots
- capital flow
- dragon tiger list

## 8. 默认数据库路径

- `data/stock_history.db`

除非显式传参覆盖，否则训练、Web 和状态诊断都默认读这里。

## 9. 当前架构收益

- 数据模型统一，训练与 Web 口径一致
- 可在 read-side builder 里做 point-in-time enrichment
- 数据质量检查可以复用 repository status
- 后续若需要换 DB，改 repository 和 ingestion 层即可

## 10. 演化边界与禁止事项

### 10.1 允许在边界后方演化

以下内容可以继续演化，只要不破坏当前 public surface：

- `repository.py` 背后的 schema、索引、查询与持久化实现
- `manager.py` 内的 ingestion provider 选择、在线兜底和训练读取策略
- `datasets.py` 内的 read-side 组装、缓存和 point-in-time enrichment
- `MarketDataGateway` 对后台补数和运行时策略的编排

### 10.2 上层必须遵守的边界

上层控制器、Web 路由、训练编排层应通过下列稳定边界访问数据：

- `MarketDataRepository`
- `DataIngestionService`
- `DataQualityService`
- `MarketDataGateway`
- `DataManager`
- `TrainingDatasetBuilder` / `WebDatasetService` 等 read-side builders

### 10.3 当前明确禁止

以下做法应视为架构回退：

- 在 controller / Web route / runtime facade 中直接拼 SQL
- 绕过 repository 与 dataset builder，直接读取或写入新的 SQLite 真相源
- 在运行时表面新增第二份可写事实源
- 不更新本文档和测试就重新引入新的 `market_data/*.py` 公共 owner
