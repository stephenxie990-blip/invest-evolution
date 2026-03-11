# 配置治理说明

当前项目已经把“配置读取”“在线修改”“落盘审计”“运行路径注入”拆成了明确的配置面。

## 1. 配置来源优先级

### 1.1 Evolution 配置

从高到低：

1. 环境变量
2. `config/evolution.yaml`
3. `config/__init__.py` 默认值

### 1.2 Commander 运行时配置

由 `CommanderConfig` 负责，来源包括：

- 环境变量 `COMMANDER_*`
- CLI 参数
- `runtime/state/runtime_paths.json` 中的路径覆盖

## 2. 当前三类可编辑配置

### 2.1 训练/模型配置

服务：`EvolutionConfigService`

可编辑字段包括：

- LLM 模型与 API 连接参数
- debate 开关与轮数
- 数据源
- `max_stocks`、`simulation_days`、`min_history_days`
- `initial_capital`、`max_positions`、`position_size_pct`
- `investment_model`、`investment_model_config`
- `allocator_enabled`、`allocator_top_n`
- `stop_on_freeze`

### 2.2 运行路径配置

服务：`RuntimePathConfigService`

可编辑字段包括：

- `training_output_dir`
- `meeting_log_dir`
- `config_audit_log_path`
- `config_snapshot_dir`

持久化位置：`runtime/state/runtime_paths.json`

### 2.3 Agent 配置

注册表：`AgentConfigRegistry`

持久化位置：`agent_settings/agents_config.json`

当前可由 Web 修改：

- `llm_model`
- `system_prompt`

## 3. 在线修改入口

### 3.1 Web API

- `GET/POST /api/evolution_config`（仅训练参数与发布开关）
- `GET/POST /api/runtime_paths`
- `GET/POST /api/agent_prompts`（Prompt 专用）

### 3.2 运行时效果

- `evolution_config` 修改后会更新 live config，并写审计/快照
- `runtime_paths` 修改后，若 Commander 已启动，会同步更新 live runtime paths
- `agent_prompts` 修改后写回 JSON 文件，仅影响 Agent prompt；模型绑定统一走 `/api/control_plane`

## 4. 审计与快照

### 4.1 变更审计

`EvolutionConfigService.apply_patch()` 会写入：

- `runtime/state/config_changes.jsonl`

### 4.2 配置快照

同时生成：

- `runtime/state/config_snapshots/config_<ts>.json`

### 4.3 周期级快照

训练周期中还会追加：

- `runtime/state/config_snapshots/cycle_<id>.json`
- 以及输出目录内的 `cycle_<id>_config_snapshot.json`

## 5. 环境变量分层

### 5.1 LLM 级

- `LLM_API_KEY`
- `LLM_API_BASE`
- `LLM_MODEL`
- `LLM_DEEP_MODEL`
- `LLM_TIMEOUT`
- `LLM_MAX_RETRIES`

### 5.2 Commander 级

- `COMMANDER_MODEL`
- `COMMANDER_API_KEY`
- `COMMANDER_API_BASE`
- `COMMANDER_TEMP`
- `COMMANDER_MAX_TOKENS`
- `COMMANDER_MAX_TOOL_ITER`
- `COMMANDER_MEMORY_WINDOW`
- `COMMANDER_TRAIN_INTERVAL_SEC`
- `COMMANDER_HEARTBEAT_INTERVAL_SEC`
- `COMMANDER_AUTOPILOT`
- `COMMANDER_HEARTBEAT`
- `COMMANDER_BRIDGE`
- `COMMANDER_MOCK`

## 6. 治理原则

### 6.1 在线改配置只改“运行参数”，不改结构代码

Web 端适合修改阈值、路径、prompt，不适合修改领域代码与 schema。

### 6.2 敏感信息只展示脱敏值

例如：

- `llm_api_key` 在查询接口中只返回 masked 版本

### 6.3 路径覆盖必须标准化

`RuntimePathConfigService` 会：

- 解析相对路径
- 转成绝对路径
- 持久化标准结果

### 6.4 模型配置仍以 YAML 为主

模型行为细节仍建议保留在：

- `invest/models/configs/*.yaml`

Web `evolution_config` 更偏运行层，而不是替代模型 YAML。

## 7. 当前建议的使用方式

- 需要改训练阈值或发布开关：走 `/api/evolution_config`；需要改模型/provider/key：走 `/api/control_plane`
- 需要把训练输出切到其他目录：走 `/api/runtime_paths`
- 需要改 Agent prompt：走 `/api/agent_prompts`
- 需要版本化关键模型策略：修改 `invest/models/configs/*.yaml` 并纳入 Git
