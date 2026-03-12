# 架构图（当前代码实现）

## 1. 系统全景图

```mermaid
flowchart TD
    USER[用户 / 脚本 / Web] --> ENTRY[app/commander.py / app/train.py / app/web_server.py]

    ENTRY --> CMD[CommanderRuntime]
    ENTRY --> TRAIN[SelfLearningController]
    ENTRY --> WEB[Flask API]

    CMD --> BRAIN[brain/]
    CMD --> BODY[InvestmentBodyService]
    WEB --> CMD
    BODY --> TRAIN

    TRAIN --> DATA[market_data/]
    TRAIN --> DOMAIN[invest/]

    DATA --> DB[(data/stock_history.db)]
    DATA --> SRC1[Baostock]
    DATA --> SRC2[Tushare]
    DATA --> SRC3[Akshare]

    DOMAIN --> MODELS[invest/models]
    DOMAIN --> MEET[invest/meetings]
    DOMAIN --> FOUNDATION[invest/foundation]
    DOMAIN --> EVO[invest/evolution]
    DOMAIN --> ALLOC[invest/allocator]
    DOMAIN --> BOARD[invest/leaderboard]

    CMD --> RUNTIME[runtime/]
    WEB --> STATIC[static/index.html]
```

## 2. Commander 运行时图

```mermaid
flowchart LR
    A[CommanderRuntime] --> B[BrainRuntime]
    A --> C[InvestmentBodyService]
    A --> D[MemoryStore]
    A --> E[CronService]
    A --> F[HeartbeatService]
    A --> G[BridgeHub]
    A --> H[StrategyGeneRegistry]
    A --> I[TrainingLabArtifactStore]
    A --> J[EvolutionConfigService]

    B --> K[LLMGateway]
    B --> L[Tool Registry]
    L --> M[invest_status / invest_train / memory / cron / strategies / plugins]
```

## 3. 训练闭环图

```mermaid
flowchart LR
    A[DataManager] --> B[InvestmentModel]
    B --> C[SelectionMeeting]
    C --> D[TradingPlan]
    D --> E[SimulatedTrader]
    E --> F[StrategyEvaluator]
    E --> G[BenchmarkEvaluator]
    F --> H[ReviewMeeting]
    G --> H
    H --> I[参数调整]
    H --> J[Agent 权重调整]
    I --> K[Optimization Events]
    J --> K
    K --> L[Cycle JSON / Leaderboard / Training Lab]
```

## 4. 数据层图

```mermaid
flowchart TD
    SRC[baostock / tushare / akshare] --> ING[DataIngestionService]
    ING --> REPO[MarketDataRepository]
    REPO --> DB[(stock_history.db)]

    REPO --> TRAIN_DS[TrainingDatasetBuilder]
    REPO --> WEB_DS[WebDatasetService]
    REPO --> CF[CapitalFlowDatasetService]
    REPO --> EVT[EventDatasetService]
    REPO --> INTRA[IntradayDatasetBuilder]
    REPO --> T0[T0DatasetBuilder]

    TRAIN_DS --> MANAGER[DataManager]
    WEB_DS --> API[Web API /api/data/*]
    MANAGER --> TRAIN[SelfLearningController]
```

## 5. 运行态工件图

```mermaid
flowchart TD
    TRAIN[训练执行] --> CYCLE[runtime/outputs/training/cycle_*.json]
    TRAIN --> OPT[runtime/outputs/training/optimization_events.jsonl]
    TRAIN --> SEL[runtime/logs/meetings/selection/*.json|md]
    TRAIN --> REV[runtime/logs/meetings/review/*.json|md]
    TRAIN --> SNAP[runtime/state/config_snapshots/*]
    TRAIN --> LAB1[runtime/state/training_plans/*]
    TRAIN --> LAB2[runtime/state/training_runs/*]
    TRAIN --> LAB3[runtime/state/training_evals/*]
    TRAIN --> BOARD[runtime/outputs/leaderboard.json]
    CMD[CommanderRuntime] --> STATE[runtime/outputs/commander/state.json]
    CMD --> MEM[runtime/memory/commander_memory.jsonl]
```

## 6. 设计要点

### 6.1 单进程融合

当前系统不是“Web 一套、训练一套、Agent 一套”的三套运行时，而是：

- Commander 把 Brain 与 Invest Body 融在一个进程里
- Web 只是复用 CommanderRuntime
- 训练入口则直接复用训练控制器

### 6.2 读写分层

- 写数据统一走 `market_data/ingestion.py`
- 读数据统一走 dataset builder / service
- 训练与 Web 都不应直接拼 SQL

### 6.3 审计优先

以下信息都能落盘追溯：

- 周期结果
- 会议记录
- 优化事件
- 配置快照
- 训练计划 / 运行 / 评估
- Commander memory
