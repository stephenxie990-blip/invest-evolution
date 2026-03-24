# 配置治理说明

当前项目已经把“配置读取”“在线修改”“落盘审计”“运行路径注入”拆成了明确的配置面。

## 1. 配置来源优先级

### 1.1 Evolution 配置

从高到低：

1. 环境变量
2. `INVEST_CONFIG_PATH` 指向的额外覆盖文件
3. `runtime/state/evolution.runtime.yaml`
4. `config/evolution.local.yaml`
5. `config/evolution.yaml`（本地 materialized working copy）
6. `config/evolution.yaml.example`（版本化 canonical baseline）
7. `config/__init__.py` 默认值

说明：

- `config/evolution.yaml.example` 是唯一版本化 canonical baseline。
- `config/evolution.yaml` 是从 example materialize 出来的本地工作副本，只允许作为本地覆盖层，不再承担共享基线语义。
- 生产与新环境初始化应以 `config/evolution.yaml.example` 为事实源，再叠加 `config/evolution.local.yaml` 与 runtime override。

### 1.2 Commander 运行时配置

由 `CommanderConfig` 负责，来源包括：

- 环境变量 `COMMANDER_*`
- CLI 参数
- `runtime/state/runtime_paths.json` 中的路径覆盖

### 1.3 Control Plane 配置

建议固定为两层：

1. `config/control_plane.yaml`
   - 版本化 provider/model/binding baseline
2. `config/control_plane.local.yaml`
   - 本地敏感 provider key 覆盖层

## 2. 当前四类可编辑配置面

当前最小可写配置面只有四类：

- `/api/evolution_config`：训练与 Web 运行参数
- `/api/control_plane`：LLM provider / model / api_key 绑定
- `/api/runtime_paths`：训练输出与工件目录
- `/api/agent_prompts`：角色 prompt baseline

为了避免“所有东西都能从一个入口改”的失控状态，不属于这四个面的修改，不应伪装成在线配置变更。

### 2.1 训练/治理配置

服务：`EvolutionConfigService`

可编辑字段包括：

- debate 开关与轮数
- 数据源
- `max_stocks`、`simulation_days`、`min_history_days`
- `initial_capital`、`max_positions`、`position_size_pct`
- `default_manager_id`、`default_manager_config_ref`
- `allocator_enabled`、`allocator_top_n`
- `stop_on_freeze`
- Web API 鉴权与限流相关运行参数

持久化位置：`runtime/state/evolution.runtime.yaml`

不再由此服务负责的内容：

- LLM provider / model / API key
- `control_plane` 级别的组件绑定

### 2.2 运行路径配置

服务：`RuntimePathConfigService`

可编辑字段包括：

- `training_output_dir`
- `artifact_log_dir`

内部仍由 `RuntimePathConfigService` 持有但不再属于 public Web/API contract 的字段：

- `config_audit_log_path`
- `config_snapshot_dir`

持久化位置：`runtime/state/runtime_paths.json`

历史 `meeting_log_dir` 键已退休，runtime 不再自动接管或改写它。
若本地状态文件仍使用旧键，请显式改写 `runtime/state/runtime_paths.json`，或通过当前入口重新落盘：

```bash
GET /api/runtime_paths
POST /api/runtime_paths
```

### 2.3 角色 Prompt 配置（兼容 API 名称仍为 `agent_prompts`）

注册表：`AgentConfigRegistry`

持久化位置：`agent_settings/agents_config.json`

说明：

- `agent_settings/agents_config.json` 现在同时承担“仓库内可复现基线”与“运行时持久化文件”两种角色。
- fresh clone / 新环境默认应直接从该文件获得核心 Agent prompt 与模型设置，不再把它视为纯本地缓存。
- 通过 Web / Commander 在线修改 Agent prompt 后，会直接改写这个文件；这属于显式配置变更，应按配置治理流程审查与提交。
- `agent_prompts` 当前表达的是**角色 prompt baseline**，不会改变 manager / capability / governance 的架构分层。

当前可由 Web 修改：

- `system_prompt`

### 2.4 Control Plane 配置

服务：`ControlPlaneConfigService`

持久化位置：

- `config/control_plane.yaml`
- `config/control_plane.local.yaml`

负责内容：

- provider
- model profile
- component binding
- data runtime policy

不再由 `evolution_config` 负责的内容：

- provider / model / api_key 绑定
- LLM ownership fallback 决策

## 3. 在线修改入口

### 3.1 Web API

- `GET/POST /api/evolution_config`（仅训练参数与发布开关）
- `GET/POST /api/runtime_paths`
- `GET/POST /api/control_plane`
- `GET/POST /api/agent_prompts`（仅 `system_prompt`）

### 3.2 运行时效果

- `evolution_config` 修改后会更新 live config，并写入 `runtime/state/evolution.runtime.yaml`、审计日志与快照
- `runtime_paths` 修改后，若 Commander 已启动，会同步更新 live runtime paths；public surface 只承诺 `training_output_dir` 与 `artifact_log_dir`
- `agent_prompts` 修改后写回 `agent_settings/agents_config.json`，仅影响 Agent prompt；模型绑定统一走 `/api/control_plane`
- `evolution_config` 不接受任何 `llm` 相关 patch，包括嵌套 `llm.*`；这类修改统一迁移到 `/api/control_plane`

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

- `OPENAI_API_KEY`
- `MINIMAX_API_KEY`
- `LLM_TIMEOUT`
- `LLM_MAX_RETRIES`

当前建议：

- provider/model/api_key 的 canonical ownership 统一走 `config/control_plane.yaml` + `config/control_plane.local.yaml`
- `OPENAI_API_KEY` / `MINIMAX_API_KEY` 只作为 `control_plane.local.yaml` 的占位值来源，不再作为 `EvolutionConfig` import-time 默认 API key 来源
- 裸 `LLM_API_KEY` / `LLM_API_BASE` / `LLM_MODEL` / `LLM_DEEP_MODEL` 运行时 fallback 已移除
- 如需从环境注入 provider/model/api_key，必须通过 control-plane / evolution 配置文件中的 `${ENV:...}` 占位符显式声明
- 若 `control_plane` 已存在但组件 binding 缺失，运行时返回的 `issue` / `ownership_mode` 应视为治理告警，不能把这类 fallback 状态当成稳定终态
- `GET /api/control_plane` 返回的 `llm_resolution.*.ownership_mode` / `fallback_active` 可用于确认当前是否仍在使用 fallback 值；发布前应收敛到 `control_plane`
- `GET /api/control_plane` 的 public response 只承诺 masked binding/config 与 `llm_resolution` 治理诊断，不承诺暴露本地配置文件、审计日志或快照目录路径

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

Web 端适合修改阈值、路径、role prompt 与 control-plane binding，不适合修改领域代码与 schema。

### 6.2 敏感信息只展示脱敏值

例如：

- `web_api_token`、`/api/control_plane` 中的 `api_key` 只返回 masked 版本

### 6.3 路径覆盖必须标准化

`RuntimePathConfigService` 会：

- 解析相对路径
- 转成绝对路径
- 持久化标准结果

### 6.4 运行策略配置仍以 YAML 为主

模型行为细节仍建议保留在：

- `src/invest_evolution/investment/runtimes/configs/*.yaml`

Web `evolution_config` 更偏运行层，而不是替代运行策略 YAML。

第三阶段后，runtime YAML 额外承载以下治理契约：

- `regime_profiles.<regime>.params`
- `regime_profiles.<regime>.risk`
- `regime_profiles.<regime>.filters`

说明：

- `regime_profiles` 是当前 runtime 分 regime 校准的统一 contract。
- 旧 `oscillation_*` / `bear_*` 前缀参数仍保留为兼容 fallback，但新增策略应优先写入 `regime_profiles`。
- 运行时会把实际应用的 profile 来源与参数回显到 `SignalPacketContext.debug_metadata`，便于审计“这轮为什么被收紧/放松”。

### 6.5 Promotion / Freeze 治理可配置项补充

当前与第三阶段直接相关的治理配置包括：

- `train.promotion_gate.research_feedback`
- `train.promotion_gate.regime_validation`
- `train.promotion_gate.manager_regime_validation`
- `train.freeze_gate.research_feedback`

说明：

- `manager_regime_validation` 已进入默认 promotion gate policy，但默认 `enabled: false`，只在显式开启时参与 verdict。
- `research_feedback` 在 freeze / promotion 之外，还会补充 `coverage_plan` 到训练报告和 freeze report，用于暴露 requested regime 的 evidence 缺口。

## 7. 当前建议的使用方式

- 需要改训练阈值、路由策略、Web 鉴权/限流：走 `/api/evolution_config`
- 需要改 LLM/provider/key：走 `/api/control_plane`
- 需要落地敏感 provider key：修改 `config/control_plane.local.yaml` 或由部署平台注入 `OPENAI_API_KEY` / `MINIMAX_API_KEY`
- 需要把训练输出切到其他目录：走 `/api/runtime_paths`
- 需要改角色 prompt baseline：走 `/api/agent_prompts`
- 需要版本化关键运行策略：修改 `src/invest_evolution/investment/runtimes/configs/*.yaml` 并纳入 Git
