# Invest Evolution / 投资进化系统

> Agent-first investment training / research / runtime platform with governance-first controls.  
> 一个以 Agent 为第一用户、以可控性为核心约束的投资训练 / 研究 / 运行一体化平台。

这不是一个单纯的量化脚本仓库。  
它更像一套面向投资场景的协作底座：让 Agent 在明确边界、统一事实来源和治理约束下参与训练、研究、复盘与运行。

## 项目定位 / Positioning

- **Agent-first**：系统的“第一用户”是 Agent 与运行时，而不是 UI。
- **Governance-first**：系统强调 promotion、routing、deployment stage、freeze gate 等治理边界，而不是无约束自治。
- **Scenario-first, but extensible**：当前最完整的是投资场景，但底层已经具备迁移到其他高反馈决策场景的结构雏形。

## 项目理念 / Philosophy

这个项目真正想解决的，不只是“如何做投资决策”，而是更底层的几个问题：

- 如何让 Agent 成为真正可用的工具，而不是不可预测的参与者
- 如何让人和 Agent 的协作关系可控、可审计、可约束
- 如何让多个 Agent 围绕同一个数据底座和任务目标形成稳定的协作范式
- 如何让系统持续进化，但始终在明确边界内生长

投资是这套底层能力当前最完整的验证场。  
因为它天然具备高复杂度、高反馈密度、高不确定性和高价值。

## 这是什么 / What This Is

- 一个面向投资场景的 Agent-first 协作系统
- 一个把训练、研究、运行和治理放进同一条闭环里的平台
- 一个能持续沉淀结果、复盘、候选、晋级和治理记录的实验环境

## 这不是什么 / What This Is Not

- 不是只靠 buzzword 拼起来的“Agent 外壳”。
- 不是直接面向最终交易执行的高频生产系统。
- 不是已经完成效果收敛的自动赚钱机器。
- 不是以人类点击 UI 为核心交互范式的软件。

## 当前对外只讲三件事 / Three External Surfaces

当前对外表达已经收口，原则上只讲下面三件事：

1. **Commander control surface**  
   `Commander` 是唯一推荐的人类入口，承接状态查询、训练执行、训练实验室、配置管理和运行诊断。
2. **Training Lab + governance loop**  
   当前最完整的产品闭环是训练计划、训练运行、训练评估、晋级判断与复盘工件。
3. **Stateless Web/API deploy surface**  
   Web/API 是部署和机器读写界面，不承担新的产品主入口语义。

所有兼容入口、辅助脚本和历史 facade，都应该服务于这三件事，而不是重新长出第四种对外叙事。

## 当前功能一览 / What Works Today

### 1. 训练闭环

- 支持完整的训练主链：数据加载、多经理计划生成、组合合成、模拟交易、评估、双层复盘与治理判断
- 支持多经理实验、配置演化、训练计划 / 训练运行 / 训练评估三层工件
- 支持 mock 模式，便于验证链路和演示系统能力

### 2. 多 Agent 协作

- 支持多投资经理并行运行、共享能力底座和治理层统一收口
- 支持角色边界约束、输出结构化约束和协作反馈回路
- 支持通过配置调整经理行为边界、预算分配和认知辅助能力

### 3. 统一运行与控制

- 支持统一入口管理状态、训练、配置、实验记录与结果查看
- 支持命令行、Web/API 和运行时事件流
- 支持把训练结果、治理工件、配置快照和运行事件持续沉淀下来

### 4. 治理与可控性

- 支持经理激活、预算分配、组合约束、候选晋级、部署阶段区分和冻结门控
- 支持对训练结果、候选状态和治理判断进行记录与回放
- 支持 `research feedback coverage planner`，可直接暴露 requested regime 的样本缺口、补样优先级与当前 cycle 的 evidence 增益
- 支持 runtime `regime_profiles` 统一 contract，并保留旧前缀参数 fallback，便于做分 regime 校准与审计
- 支持在训练评估中下钻 `manager x regime` 质量矩阵，并按需启用更严格的二维晋级门
- 支持在持续进化的同时保持明确边界

## 为什么值得关注 / Why It Matters

这个项目关注的不只是“投资结果”，而是一个更底层的问题：

> 如何让 Agent 在真实、高复杂度、高不确定性的场景中，成为可控、可审计、可进化的工具。

如果这件事成立，那么这套协作方式不只可以用于投资，也可以迁移到更多决策辅助场景。

## 快速开始

推荐使用 Python 3.11+ 与 `uv`。当前官方 bootstrap 路径统一为：`uv.lock` + 项目内 `.venv`。

```bash
git clone https://github.com/stephenxie990-blip/invest-evolution.git invest-evolution
cd invest-evolution

# 官方环境构建路径（dev + prod extras）
python3 scripts/bootstrap_env.py

# 若需要修复坏掉的 .venv shebang / console scripts
python3 scripts/bootstrap_env.py --reinstall
```

- 日常命令推荐统一走 `uv run python -m ...`，避免直接依赖 `.venv/bin/pytest` 这类可能被历史路径污染的 console script。
- 若需要显式使用项目托管解释器，推荐 `./.venv/bin/python -m ...`。
- 若必须直接调用 `.venv/bin/pytest`，先执行一次 `python3 scripts/bootstrap_env.py --reinstall` 确保 shebang 被刷新。
- 普通系统解释器下的裸 `python3 -m invest_evolution...` 不属于源码 checkout 的稳定契约；请使用 `uv run python -m ...`、`./.venv/bin/python -m ...` 或对应的 console script。
- 新成员上手与工程交接请先看 `docs/ONBOARDING_HANDOFF.md`；release 前的 active checklist 统一看 `docs/RELEASE_READINESS.md`。

### 1. 初始化离线库

```bash
# 初始化股票主数据、日线与指数日线
uv run python -m invest_evolution.interfaces.cli.market_data --source baostock --start 20180101

# 查看离线库健康状态 + 指定训练截断日是否可用
uv run python -m invest_evolution.interfaces.cli.market_data --status --cutoff 20241231 --stocks 200
```

### 2. 可选补数

```bash
# 财务快照（需要 Tushare Token）
uv run python -m invest_evolution.interfaces.cli.market_data --source tushare --financials --stocks 500 --token "$TUSHARE_TOKEN"

# 资金流（akshare）
uv run python -m invest_evolution.interfaces.cli.market_data --source akshare --capital-flow --stocks 300

# 龙虎榜（akshare）
uv run python -m invest_evolution.interfaces.cli.market_data --source akshare --dragon-tiger --start 20240101

# 60 分钟线（baostock）
uv run python -m invest_evolution.interfaces.cli.market_data --source baostock --intraday-60m --start 20230101 --stocks 200
```

### 3. 运行 Commander（推荐主入口）

> 当前推荐把 `Commander` 作为**唯一人类入口**使用：状态查询、训练执行、训练实验室、配置管理、数据查询、运行诊断都优先通过 `Commander` 对话/CLI 完成。Web 控制台更适合作为可选可视化界面。

```bash
# 推荐先走真实数据 / 离线库路径
uv run python -m invest_evolution.interfaces.cli.commander status --detail fast
uv run python -m invest_evolution.interfaces.cli.commander strategies --reload
uv run python -m invest_evolution.interfaces.cli.commander train-once --rounds 1
uv run python -m invest_evolution.interfaces.cli.commander run --interactive

# 显式 smoke / demo 模式（mock 数据 + dry-run LLM）
uv run python -m invest_evolution.interfaces.cli.commander train-once --rounds 1 --mock

# 等价 console script
invest-commander status --detail fast
invest-commander train-once --rounds 1
invest-commander train-once --rounds 1 --mock
```

#### 3.0 入口分层（收口后的正式约定）

- `invest-commander` / `uv run python -m invest_evolution.interfaces.cli.commander`
  - 唯一推荐的人类主入口
- `invest-train` / `uv run python -m invest_evolution.interfaces.cli.train`
  - 批处理 / CI / 兼容训练入口，不作为默认人类入口
- `invest-runtime`
  - 独立 Commander runtime daemon 入口
- `invest_evolution.interfaces.web.wsgi:app` / Web API
  - 无状态部署与机器读写入口，不承担人类主交互职责

### 3.1 刷新 / 校验前端 API 契约

```bash
# 重建主契约 + JSON Schema + OpenAPI + transcript snapshots
python3 scripts/generate_runtime_contract_derivatives.py

# 或使用统一 console script
invest-refresh-contracts

# 只校验当前文档是否与生成结果一致（适合 CI / release gate）
python3 scripts/generate_runtime_contract_derivatives.py --check
invest-refresh-contracts --check
```

- 当前 canonical runtime contract：`/api/contracts/runtime-v2`

### 3.2 执行 Freeze Gate

```bash
# 契约漂移 + focused protocol/golden 回归
uv run python -m invest_evolution.application.freeze_gate --mode quick

# 完整 freeze gate 回归门（release 前仍推荐再跑 release-readiness 主链）
uv run python -m invest_evolution.application.freeze_gate --mode full

# 只列出将执行的命令
uv run python -m invest_evolution.application.freeze_gate --mode full --list

# fresh env smoke gate
uv run python scripts/run_verification_smoke.py

# 已完成 bootstrap 后，也可使用 console script
invest-freeze-gate --mode quick
```

- `invest-freeze-gate` 通过当前解释器执行 `python -m pytest/ruff/pyright`；推荐在 `uv sync` 完成后使用，或直接使用 `uv run python -m invest_evolution.application.freeze_gate ...`。
- `scripts/run_verification_smoke.py` 是当前最小环境恢复 smoke suite，会先检查环境是否与 `uv.lock` 同步，并验证 `requests` / `rank_bm25` 等关键运行依赖，再跑 focused pytest / ruff / pyright。

### 4. 兼容 / 批处理方式跑训练（非推荐人类入口）

> `invest-train` 仍然是正式支持的训练 facade，但当前定位是**批处理 / CI / 自动化兼容入口**。人类日常操作优先走 `Commander`，只有需要脚本化单独训练、回归实验或 release gate 时再直接调用训练入口。

```bash
# 正式训练默认使用真实数据链路
uv run python -m invest_evolution.interfaces.cli.train --cycles 5

# 显式 smoke / demo 模式
uv run python -m invest_evolution.interfaces.cli.train --cycles 1 --mock

# 等价 console script
invest-train --cycles 5
invest-train --cycles 1 --mock
```

### 5. 组合治理预览

- 系统默认启用 `rule` 治理模式，会在每轮训练前先识别市场状态，再决定激活经理集合、预算分配和组合约束。
- 可通过 `config/evolution.yaml` 或 `POST /api/evolution_config` 调整：
  - `governance_enabled`
  - `governance_mode`
  - `governance_cooldown_cycles`
  - `governance_min_confidence`
  - `governance_hysteresis_margin`
- 治理预览、leaderboard 与 allocator 当前收回到 Commander / 内部运行面，不再作为公开 Web/API surface 暴露。

### 6. 启动 Web 控制台

> Web 控制台当前定位为**可选观测/展示层**，不是推荐主入口。若你更偏向自然语言交互与低前端维护成本，可仅保留 `Commander` 入口，Web 只在需要图表、SSE 回放和只读可视化时启用。

```bash
# 本地调试：默认绑定回环地址，未配置鉴权也可启动
# 默认仅启动无状态 Web/API，不会内嵌 Commander runtime
uv run python -m invest_evolution.interfaces.web.server

# 本地 smoke / demo / health-check 模式
uv run python -m invest_evolution.interfaces.web.server --mock
# 默认地址: http://127.0.0.1:8080
```

- `--embedded-runtime` 只保留给 `compat/dev` 场景，本地联调或兼容模式可显式启用；正式拆分部署时应保持 Web/API 无状态。
- 若不使用 `uv run`，请改用项目托管解释器 `./.venv/bin/python -m invest_evolution.interfaces.web.server ...`；不要假设系统 `python3` 在普通源码 checkout 下能直接发现 `src/` 包。

```bash
# 生产部署目标：Web/API 无状态化，Commander runtime 独立运行
export WEB_API_TOKEN="<strong-random-token>"
export WEB_API_REQUIRE_AUTH=true
export WEB_API_PUBLIC_READ_ENABLED=false
export GUNICORN_WORKERS=2
pip install -e ".[prod]"
gunicorn -c gunicorn.conf.py invest_evolution.interfaces.web.wsgi:app
```

```bash
# 独立 Commander runtime 服务
invest-runtime
```

- `invest_evolution.interfaces.web.wsgi:app` 是纯 Flask 入口，导入不会再隐式 bootstrap Commander runtime。
- 无状态 Web/API 部署下，`GUNICORN_WORKERS` 可按机器容量调整；Gunicorn worker 生命周期不再承担 runtime 启停。
- 生产建议使用 `deploy/systemd/invest-evolution.service` + `deploy/systemd/invest-evolution-runtime.service` + Nginx 的拆分拓扑。

- 非回环地址部署时，若未开启 `WEB_API_REQUIRE_AUTH=true` 且未配置 `WEB_API_TOKEN`，服务会拒绝启动。
- 鉴权支持 `Authorization: Bearer <token>` 或 `X-Invest-Token: <token>`。
- 内置简单应用级限流，默认按窗口限制读 / 写 / 重型接口；可通过 `WEB_RATE_LIMIT_*` 环境变量调整。
- 反向代理必须向 `/api/*` 转发可信的 `X-Real-IP`；应用不再信任客户端自带的 `X-Forwarded-For` 进行限流识别。
- 健康检查：`GET /healthz`。
- 自然语言交互入口：`POST /api/chat`。
- 运行状态与事件流入口：`GET /api/status`、`GET /api/events`。
- 当前公开 Web/API surface 只保留 status、events、chat、training lab、config、direct runtime-v2 contract，以及 `/api/data/status` 与 `/api/data/download`；`/api/managers`、`/api/playbooks`、`/api/cron`、`/api/memory`、status alias 与 contract catalog 路由已退出公开面。
- 部署示例文件：`deploy/nginx/invest-evolution.conf`、`deploy/systemd/invest-evolution.service`、`deploy/systemd/invest-evolution-runtime.service`、`deploy/systemd/invest-evolution.env.example`。
- 根路径 `/` 现在只返回当前 API / CLI 入口说明；已移除的 Web UI 不再保留 tombstone route。

#### 6.1 生产启动顺序与运行手册

- 推荐启动顺序：先启动 `invest-evolution-runtime.service`，确认 runtime 已持有 `runtime/state/commander.lock` 并写出 `runtime/outputs/commander/state.json`，再启动 `invest-evolution.service`。
- `GET /healthz` 只表示 Web/API 进程和反向代理链路健康；是否存在活跃 runtime、当前锁状态、最近快照与事件窗口，请看 `GET /api/status`。
- Web/API 是 `runtime/state`、事件目录、工件目录的只读消费者；runtime service 是唯一写方，目录所有权应统一为运行用户（例如 `invest:invest`）。

#### 6.2 Clean Boot / Restart / Stale Lock

- `systemctl restart invest-evolution.service` 只影响无状态 Web/API；不会触发 Commander runtime 启停，也不会清理训练工件。
- `systemctl restart invest-evolution-runtime.service` 会走有序 shutdown / restart；正常停止后应释放 `runtime/state/commander.lock`，历史工件保留。
- 若 crash 后残留 `runtime/state/commander.lock`，runtime service 对“可读取且 PID 已失活”的 stale lock 可以自愈；若 lock 文件损坏、不可解析或 ownership 异常，需人工清理后再重启。
- `runtime/state/training.lock` 目前不做 stale-lock 自愈。只有在确认没有活跃训练进程、最近一次训练已结束或已放弃后，才允许人工移除。
- 推荐 clean boot 清单：`systemctl stop invest-evolution-runtime invest-evolution` -> 确认无 `invest-runtime` 活跃进程 -> 检查 `commander.lock` / `training.lock` -> 必要时清理 stale lock -> 先起 runtime，再起 web -> 用 `/healthz` + `/api/status` 双检查。

> `mock` 现在是显式的 smoke / demo / health-check 模式，不再作为真实训练失败时的隐式兜底。

## 当前正式入口

### 人类入口与辅助入口

- `Commander` 是当前唯一推荐的人类入口：状态查询、训练执行、训练实验室、配置管理、数据查询、运行诊断优先都从这里进入。
- Web/API 只保留可视化、状态读取、SSE 与 API 命令路由，不再承担新的产品主入口语义。
- `invest-train` 保留为协议化训练 / 调试入口，适合批量实验、CI、脚本化验证。
- `invest-data` 保留为数据底座维护入口，负责补数、状态诊断与离线库治理。

### 入口 facade 与装配层

- `src/invest_evolution/application/commander_main.py`：Commander facade owner，保留 `CommanderConfig`、`CommanderRuntime`、`main`
- `src/invest_evolution/application/commander/bootstrap.py`：Commander runtime bootstrap、路径同步、配置 wiring
- `src/invest_evolution/application/commander/runtime.py`：Commander runtime 公共 response / lifecycle facade
- `src/invest_evolution/application/commander/status.py`：Commander 状态/诊断与 Training Lab 读侧汇总
- `src/invest_evolution/application/commander/workflow.py`：Commander mutating/readonly workflow 收口
- `src/invest_evolution/application/commander/ops.py`：Commander control surface、治理/配置/数据操作入口
- `src/invest_evolution/application/commander/presentation.py`：Commander 表现层格式化与响应拼装
- `src/invest_evolution/application/train.py`：训练 facade owner，保留 `SelfLearningController`、`TrainingResult`、`train_main`
- `src/invest_evolution/application/training/bootstrap.py`：训练依赖装配、运行时默认值与 service wiring
- `src/invest_evolution/application/training/controller.py`：训练生命周期与周期 orchestration
- `src/invest_evolution/application/training/execution.py`：经理执行、选股、模拟、结果主链
- `src/invest_evolution/application/training/review.py`：复盘输入归一化与 review 服务
- `src/invest_evolution/application/training/review_contracts/__init__.py`：训练阶段 envelopes、TypedDict payload、snapshot builder 与 contract projection owner
- `src/invest_evolution/application/training/policy.py`：实验协议、治理/范围/策略归一化
- `src/invest_evolution/application/training/research.py`：validation / peer comparison / judge / feedback
- `src/invest_evolution/application/training/persistence.py`：工件落盘与周期结果持久化
- `src/invest_evolution/application/training/observability.py`：冻结门、事件、训练可观测性
- `src/invest_evolution/interfaces/web/server.py`：Flask API / SSE / 自然语言交互入口
- `src/invest_evolution/market_data/__main__.py`：统一数据同步与状态诊断入口

### 平台核心 vs 投资域核心

- 平台核心：`interfaces/`、`application/`、`agent_runtime/`、`config/`、`common/`
- 投资域核心：`investment/`、`market_data/`
- 平台核心负责入口、编排、可观测性、工具协议、配置治理与运行时桥接；投资域核心负责经理体系、治理决策、研究/进化、组合计划与数据事实。
- `agent_runtime` 是平台侧的 tool loop / planner / memory 基础设施，不等同于投资域里的 `ManagerAgent`。

## 项目结构（与当前代码一致）

```text
src/invest_evolution/application/       应用编排；`train.py` 与 `commander_main.py` 是稳定 facade
src/invest_evolution/application/training/ 训练主链、bootstrap、controller、execution、review、policy 等稳定子模块
src/invest_evolution/application/commander/ Commander bootstrap/runtime/status/workflow/ops/presentation 稳定子模块
src/invest_evolution/interfaces/web/    Flask API、WSGI、route 注册、runtime facade
src/invest_evolution/interfaces/cli/    console scripts 与 `python -m` 入口
src/invest_evolution/agent_runtime/     本地 agent loop、工具、planner、memory、plugin loader
src/invest_evolution/market_data/       canonical SQLite 数据层、同步服务、读侧 dataset builder
src/invest_evolution/investment/        投资域模型：agents、contracts、governance、research、evolution、runtimes、shared
src/invest_evolution/config/            全局配置、可编辑配置服务、Agent 配置注册表
src/invest_evolution/common/            共享工具与基础设施薄层
scripts/cli/         独立 CLI 工具脚本（allocator / leaderboard）
scripts/data/        数据回填与修复脚本
strategies/          可插拔策略基因（md/json/py）
.workspace/          本地工作态、临时输出、agent 协作文件（默认忽略）
runtime/             运行态目录（默认忽略，不再作为版本资产）
tests/               当前实现对应的回归测试
docs/                面向开源协作的公开文档与说明
```

## 运行时产物

默认运行态目录都在 `runtime/`：

- `runtime/outputs/training/`：周期结果、冻结报告、优化事件等
- `runtime/outputs/leaderboard.json`：模型排行榜
- `runtime/outputs/commander/state.json`：运行时状态快照
- `runtime/logs/artifacts/`：selection / manager_review / allocation_review 工件
- `runtime/memory/commander_memory.jsonl`：Commander 长期记忆
- `runtime/state/`：锁文件、训练计划、训练运行、训练评估、配置快照、路径配置
- `runtime/sessions/inbox` / `runtime/sessions/outbox`：Bridge 收发目录
- 最小 explainability 工件集：`cycle_result_path`、`selection_artifact_json_path`、`manager_review_artifact_json_path`、`allocation_review_artifact_json_path`

## 配置说明

### 核心配置来源

当前配置支持分层加载，优先级从低到高如下：

1. `config/__init__.py` 中的默认值
2. `config/evolution.yaml.example`
3. `config/evolution.yaml`（本地 materialized working copy）
4. `config/evolution.local.yaml`
5. `runtime/state/evolution.runtime.yaml`
6. `INVEST_CONFIG_PATH` 指向的额外覆盖文件
7. 环境变量

建议约定：

- `config/evolution.yaml.example`：版本化 canonical baseline
- `config/evolution.yaml`：由 example 复制出的本地工作副本，只作为本地覆盖层，不再代表共享 baseline
- `config/control_plane.yaml`：版本化的 provider/model/binding baseline
- `config/control_plane.local.yaml`：本地敏感 provider key 覆盖层
- `config/evolution.local.yaml`：本地敏感项与个人覆盖项
- `runtime/state/evolution.runtime.yaml`：由 `/api/evolution_config` 维护的运行时覆盖层，不手工编辑、不纳入版本控制
- 环境变量：线上密钥与部署平台注入项

推荐从示例文件开始：

```bash
cp config/evolution.yaml.example config/evolution.yaml
cp config/evolution.local.yaml.example config/evolution.local.yaml
cp config/control_plane.local.yaml.example config/control_plane.local.yaml
export OPENAI_API_KEY="<your-openai-key>"
export MINIMAX_API_KEY="<your-minimax-key>"
```

### 常用环境变量

- `OPENAI_API_KEY`
- `MINIMAX_API_KEY`
- `LLM_TIMEOUT`
- `LLM_MAX_RETRIES`
- `COMMANDER_MODEL`
- `COMMANDER_AUTOPILOT`
- `COMMANDER_HEARTBEAT`
- `COMMANDER_BRIDGE`
- `COMMANDER_MOCK`

说明：

- provider / model / api_key 只通过 `config/control_plane.yaml` 与 `config/control_plane.local.yaml` 管理
- 如需从环境注入 provider key，请在 control-plane 文件里通过 `${ENV:...}` 占位符显式声明
- 裸 `LLM_API_KEY` / `LLM_MODEL` / `LLM_DEEP_MODEL` / `LLM_API_BASE` 运行时 fallback 已移除

### Web 可改配置

- `/api/evolution_config`：训练与 Web 运行参数；写入 `runtime/state/evolution.runtime.yaml`
- `/api/control_plane`：LLM provider / model / API key 绑定
- `/api/runtime_paths`：训练输出与工件目录
- `/api/agent_prompts`：角色 prompt baseline

## 测试

```bash
# 最小 smoke suite
uv run python scripts/run_verification_smoke.py

# release-oriented verification bundles
uv run python -m invest_evolution.application.release verify --bundle p0
uv run python -m invest_evolution.application.release verify --bundle p1
uv run python -m invest_evolution.application.release verify --bundle commander-brain

# readiness orchestration (Stage 0 -> Stage 3 / optional Stage 4)
uv run python scripts/run_release_readiness_gate.py --include-commander-brain
uv run python scripts/run_release_readiness_gate.py --include-commander-brain --include-shadow-gate --shadow-output-dir outputs/release_shadow_gate_manual

# focused / repo tests
uv run python -m pytest -q
```

当前测试覆盖的主题包括：

- Commander / Brain / Web API 主链
- 数据层统一与状态审计
- 训练计划 / 训练运行 / 训练实验室工件
- 模型配置校验、mutation、策略评分与 allocator
- Agent prompt 边界、导入约束与结构守卫
- deploy public surface 的 `200/404` smoke、WSGI import smoke、Gunicorn 配置 smoke

## 相关文档

- `docs/README.md`：文档索引与分层导航
- `docs/GOVERNANCE_RECOVERY_CHANGE_SUMMARY_2026-03-24.md`：本轮治理恢复提交摘要、owner map 与验证结果
- `docs/GOVERNANCE_RECOVERY_BLUEPRINT_2026-03-24.md`：治理恢复实施蓝图与挂载边界
- `docs/MAIN_FLOW.md`：系统主链路
- `docs/TRAINING_FLOW.md`：训练周期细节
- `docs/AGENT_INTERACTION.md`：Agent 与会议协作
- `docs/DATA_ACCESS_ARCHITECTURE.md`：数据层架构
- `docs/CONFIG_GOVERNANCE.md`：配置治理与审计
- `docs/RUNTIME_STATE_DESIGN.md`：运行态文件设计
- `docs/COMPATIBILITY_SURFACE.md`：当前公共入口与非目标面
- `docs/STRICT_TRAINING_READINESS_CHECKLIST_2026-03-24.md`：strict readiness 当前证据、剩余质量门与收尾路径
- `docs/V1_5_NEXT_ROUND_CANDIDATE_CONVERGENCE_2026-03-24.md`：第三阶段后下一轮候选收敛，明确 strict readiness 下一轮只保留的 3 个主线项
- `CONTRIBUTING.md`：贡献方式与协作约定
- `SECURITY.md`：安全边界与漏洞反馈方式

## 现阶段建议的阅读顺序

1. 先读 `README.md`
2. 再看 `docs/MAIN_FLOW.md`
3. 需要理解 Agent 角色与协作时看 `docs/AGENT_INTERACTION.md`
4. 需要训练细节时看 `docs/TRAINING_FLOW.md`
5. 需要数据层时看 `docs/DATA_ACCESS_ARCHITECTURE.md`
6. 需要运行时/配置排障时看 `docs/RUNTIME_STATE_DESIGN.md` 与 `docs/CONFIG_GOVERNANCE.md`


## 社区与安全

- 贡献方式与协作约定：`CONTRIBUTING.md`
- 安全边界与漏洞反馈方式：`SECURITY.md`
