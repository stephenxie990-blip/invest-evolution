# 项目架构蓝图（当前状态）

本文不是未来理想图，而是**当前仓库已经形成的稳定结构**。

## 1. 总体原则

### 1.1 入口收口到 `app/`

所有正式实现都放在 `app/`：

- `app/commander.py`
- `app/train.py`
- `app/web_server.py`
- `app/llm_gateway.py`
- `app/llm_router.py`

根目录同名文件只做兼容转发。

### 1.2 数据层收口到 `market_data/`

所有离线库 schema、同步与读侧构造都集中到 `market_data/`，避免训练、Web、脚本分别维护一套数据逻辑。

### 1.3 投资域能力收口到 `invest/`

`invest/` 是核心业务域，包含：

- 模型
- Agent
- 会议
- 模拟交易与风险控制
- 评估
- 进化优化
- allocator / leaderboard

### 1.4 运行时编排收口到 `brain/`

`brain/` 提供本地 agent loop 能力，但不直接承担投资业务本身。

## 2. 当前包职责

### 2.1 `app/`

- 负责入口、编排、薄服务层、Training Lab 与 Web API
- 不应沉淀大量领域算法

### 2.2 `brain/`

- `runtime.py`：tool calling + session 管理
- `tools.py`：把投资能力包装成 BrainTool
- `scheduler.py`：cron / heartbeat
- `bridge.py`：文件消息桥
- `memory.py`：Commander memory
- `plugins.py`：声明式插件加载

### 2.3 `market_data/`

- `repository.py`：canonical SQLite schema 与查询
- `ingestion.py`：数据同步写路径
- `datasets.py`：训练 / Web / T0 / 事件读路径
- `manager.py`：兼容 façade
- `quality.py`：健康检查与审计

### 2.4 `invest/`

- `models/`：投资模型与 YAML 配置
- `agents/`：市场、猎手、复盘裁判等角色
- `meetings/`：Selection / Review / Recorder
- `foundation/`：模拟交易、指标、风险控制、评估基础设施
- `evolution/`：LLM 优化、遗传优化、mutation
- `allocator/`：基于 leaderboard 与 regime 的模型分配
- `leaderboard/`：训练结果聚合排行
- `contracts/`：跨模块数据契约
- `shared/`：共享指标、摘要、跟踪、LLM caller

## 3. 依赖方向

推荐理解为：

```text
entry(app) -> orchestration(brain/app service) -> domain(invest) -> data(market_data)
```

但在当前实现里，训练控制器同时会用到 `invest/` 与 `market_data/`，所以更准确的方向是：

```text
app/*
  ├─> brain/*
  ├─> market_data/*
  └─> invest/*

invest/*
  ├─> invest/contracts + invest/shared
  └─> config

market_data/*
  └─> config
```

## 4. 当前稳定接口

### 4.1 CLI

- `python3 commander.py ...`
- `python3 train.py ...`
- `python3 web_server.py ...`
- `python3 -m market_data ...`
- `invest-commander`
- `invest-train`
- `invest-data`

### 4.2 Web API

当前已经形成稳定资源面：

- runtime status
- training
- training lab
- strategies
- leaderboard / allocator
- cron / memory
- agent configs / evolution config / runtime paths
- data status / data query / download

### 4.3 文件工件接口

- `runtime/outputs/training/cycle_*.json`
- `runtime/outputs/leaderboard.json`
- `runtime/state/training_plans/*.json`
- `runtime/state/training_runs/*.json`
- `runtime/state/training_evals/*.json`

## 5. 目前仍然保留的兼容层

### 5.1 根目录启动壳

- `commander.py`
- `train.py`
- `web_server.py`
- `llm_gateway.py`
- `llm_router.py`

### 5.2 独立工具脚本

- `scripts/cli/allocator.py`
- `scripts/cli/leaderboard.py`


## 6. 当前扩展点

### 6.1 可插拔策略基因

`strategies/` 支持三类文件：

- `.md`
- `.json`
- `.py`

并由 `StrategyGeneRegistry` 统一加载、排序和热重载。

### 6.2 可编辑 Agent Prompt

- 存储：`agent_settings/agents_config.json`
- 接口：`/api/agent_prompts`（Prompt 专用）

### 6.3 可编辑运行路径

- 存储：`runtime/state/runtime_paths.json`
- 接口：`/api/runtime_paths`

### 6.4 可编辑训练主配置

- 主文件：`config/evolution.yaml`
- 服务：`EvolutionConfigService`
- 接口：`/api/evolution_config`（训练参数） + `/api/control_plane`（LLM 控制面）

## 7. 当前架构上的约束与建议

### 7.1 不要再新增根目录真实实现

如果新增入口，应继续放到 `app/`，根目录仅保留兼容壳。

### 7.2 不要让 Web 直接调用底层细节

优先通过：

- `CommanderRuntime`
- `RuntimePathConfigService`
- `EvolutionConfigService`
- `WebDatasetService`

### 7.3 不要在训练链路里直接写 SQL

训练链路应继续依赖 `DataManager` / dataset builder。

### 7.4 不要绕过 `LLMGateway`

所有外部 LLM 请求都应统一从 `app/llm_gateway.py` 出口出去，保证：

- timeout
- retry
- provider error 处理
- future 可观测性
