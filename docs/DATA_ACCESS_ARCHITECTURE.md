# 数据访问架构

当前系统的数据访问已经统一到 `market_data/`，核心原则是：

- **写入统一入库**
- **读取统一经由 dataset builder / service**
- **训练与 Web 不直接拼 SQL**

## 1. 核心对象

### 1.1 Repository

`market_data/repository.py` 中的 `MarketDataRepository` 负责：

- 初始化 canonical schema
- 提供查询接口
- 提供 upsert 接口
- 维护 ingestion meta 与状态摘要

### 1.2 Ingestion

`market_data/ingestion.py` 中的 `DataIngestionService` 负责：

- 股票主数据同步
- 日线同步
- 指数同步
- 交易日历同步
- 财务快照同步
- 资金流同步
- 龙虎榜同步
- 60 分钟线同步

### 1.3 Read-side Builders / Services

`market_data/datasets.py` 当前提供：

- `TrainingDatasetBuilder`
- `WebDatasetService`
- `CapitalFlowDatasetService`
- `EventDatasetService`
- `IntradayDatasetBuilder`
- `T0DatasetBuilder`

### 1.4 Compatibility Facade

`market_data/manager.py` 中的 `DataManager` 负责：

- 训练 readiness 检查
- stock data 加载
- 离线优先 / 在线兜底 / mock 兜底
- 对外提供统一方法入口

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

Web API 的 `/api/data/*` 路由统一走 `WebDatasetService`，典型能力包括：

- status summary
- capital flow
- dragon tiger events
- intraday 60m

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
