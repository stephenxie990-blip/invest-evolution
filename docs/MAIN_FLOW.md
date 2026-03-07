# 主链路说明

## 入口分工

- `commander.py`：推荐的统一 CLI 入口，适合状态检查、守护运行、策略热重载和单轮训练。
- `train.py`：面向训练流程本身的专用入口，适合批量 cycle 和研究型实验。
- `web_server.py`：Flask Web 前端/API 入口，适合手动触发训练、查看状态、编辑配置和数据同步。
- `data.py`：统一数据同步命令入口；内部已收口到 canonical 数据层。

## 从入口到执行的主链路

1. `commander.py`
   - 构建 `CommanderConfig`
   - 初始化 `CommanderRuntime`
   - 装配 `BrainRuntime`、`CronService`、Bridge、Memory、Plugins
   - 通过 `InvestmentBodyService` 驱动 `SelfLearningController`
2. `train.py`
   - `SelfLearningController.run_training_cycle()` 执行单轮训练
   - `DataManager` 优先从离线 canonical 库读取数据；离线不可用时才降级到在线抓取或 mock
   - 使用 `compute_market_stats()` 与 Agent/算法判断市场状态
   - 调用 `SelectionMeeting.run_with_data()` 生成 `TradingPlan`
   - 交给 `SimulatedTrader.run_simulation()` 执行模拟交易
   - 用 `StrategyEvaluator` / `BenchmarkEvaluator` / `FreezeEvaluator` 做评估
   - 在亏损或触发条件下调用 `LLMOptimizer` 与 `EvolutionEngine` 做优化
3. `web_server.py`
   - 复用 `CommanderRuntime`
   - 暴露 `/api/status`、`/api/train`、`/api/strategies`、`/api/evolution_config`
   - 通过 `WebDatasetService` 提供 `/api/data/status`
   - 通过 `DataIngestionService` 提供 `/api/data/download`
4. `data.py`
   - `DataCache` façade → `DataIngestionService` / `WebDatasetService`
   - `OfflineDataLoader` façade → `TrainingDatasetBuilder`
   - `T0DataLoader` façade → `T0DatasetBuilder`
   - `DataDownloader` façade → `DataIngestionService.sync_daily_bars_from_tushare()`

## 数据主链路

1. 写入链路
   - `DataIngestionService.sync_security_master()` 将股票主数据写入 `security_master`
   - `DataIngestionService.sync_daily_bars()` / `sync_daily_bars_from_tushare()` 将行情写入 `daily_bar`
   - 历史旧表会先导入 canonical schema；新写入不再落到 `stock_daily` / `daily_kline`
2. 读取链路
   - `TrainingDatasetBuilder` 负责训练/回测 cutoff、最小历史长度和未来窗口规则
   - `T0DatasetBuilder` 负责 T0 股票池与幸存者偏差修正
   - `WebDatasetService` 负责 Web 状态聚合
3. 质量链路
   - `DataQualityService` 输出结构化巡检结果
   - Agent 只消费巡检结果做解释，不参与下载、清洗、裁切和落库

## 模块映射

- `data_repository.py`：SQLite canonical schema、迁移、查询与旧表清理
- `data_ingestion.py`：Baostock/Tushare 接入与统一写入
- `data_datasets.py`：训练集、T0 数据集、Web 读取构造器
- `data_quality.py`：数据覆盖率和健康检查
- `data.py`：对外 façade、mock 数据、在线兜底和命令行同步入口
- `core.py`：公共数据结构、指标计算、市场统计、追踪器
- `agents.py`：各类 Agent 定义
- `meetings.py`：选股会议与复盘会议编排
- `trading.py`：交易执行与风控
- `evaluation.py` / `optimization.py`：评估、优化、进化

## 代码结构约束

- 根目录模块是唯一真实源码入口。
- 数据库 canonical schema 只有一套：`security_master`、`daily_bar`、`financial_snapshot`、`ingestion_meta`。
- 训练、Web、T0 统一从 canonical schema 读数据。
- 安装与运行统一以 `pyproject.toml` 为单一依赖来源。
- Agent 不参与数据下载、清洗、落库和 cutoff 裁切。
