# Invest Evolution / 投资进化系统

> Agent-first investment training / research / runtime platform with governance-first controls.  
> 一个以 Agent 为第一用户、以可控性为核心约束的投资训练 / 研究 / 运行一体化平台。

这不是一个单纯的量化脚本仓库。  
它更像一套面向投资场景的协作底座：让 Agent 在明确边界、统一事实来源和治理约束下参与训练、研究、复盘与运行。

## 发布状态 / Release Status

- 当前仓库是可公开 clone、安装、测试的 OSS baseline，许可证为 `Apache-2.0`
- 本次公开版已经验证 `python -m pip install -e ".[dev]"`、`ruff check .`、`pytest -q`
- 推荐的人类使用路径是 `Commander`：先初始化离线数据，再执行 `status`、`train-once`、`run --interactive`

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

## 当前功能一览 / What Works Today

### 1. 训练闭环

- 支持完整的训练主链：数据加载、模型处理、Agent 协作、模拟交易、评估、复盘、优化与治理判断
- 支持多模型实验、配置演化、训练计划 / 训练运行 / 训练评估三层工件
- 支持 mock 模式，便于验证链路和演示系统能力

### 2. 多 Agent 协作

- 支持市场判断、不同风格的选股角色、复盘分析与统一指挥协作
- 支持角色边界约束、输出结构化约束和协作反馈回路
- 支持通过配置调整角色 prompt、权重和行为边界

### 3. 统一运行与控制

- 支持统一入口管理状态、训练、配置、实验记录与结果查看
- 支持命令行、Web/API 和运行时事件流
- 支持把训练结果、会议记录、配置快照和治理事件持续沉淀下来

### 4. 治理与可控性

- 支持模型路由、候选晋级、部署阶段区分和冻结门控
- 支持对训练结果、候选状态和治理判断进行记录与回放
- 支持在持续进化的同时保持明确边界

## 为什么值得关注 / Why It Matters

这个项目关注的不只是“投资结果”，而是一个更底层的问题：

> 如何让 Agent 在真实、高复杂度、高不确定性的场景中，成为可控、可审计、可进化的工具。

如果这件事成立，那么这套协作方式不只可以用于投资，也可以迁移到更多决策辅助场景。

## 开源说明 / Open-source Edition

这是 `Invest Evolution` 的公开开源版本，面向可 clone、可安装、可测试、可讨论的 GitHub 协作场景。

公开仓库当前有意不包含以下内容：

- 私有或本地长期调优过的 Agent prompt pack
- 个人运行态日志、快照、工作树残留与历史归档
- 先前本地实验中 vendored 的第三方依赖副本

主仓库本身保持自包含：

- 默认提供可公开的内置 Agent prompt baseline
- 支持通过 `agent_settings/` 做本地覆盖或扩展
- 安装、测试与基础运行路径不依赖私有 prompt 包

## 快速开始

推荐使用 Python 3.11+ 与虚拟环境。

```bash
git clone https://github.com/stephenxie990-blip/invest-evolution.git invest-evolution
cd invest-evolution
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -e ".[dev]"
```

### 1. 初始化离线库

```bash
# 初始化股票主数据、日线与指数日线
python3 -m market_data --source baostock --start 20180101

# 查看离线库健康状态 + 指定训练截断日是否可用
python3 -m market_data --status --cutoff 20241231 --stocks 200
```

### 2. 可选补数

```bash
# 财务快照（需要 Tushare Token）
python3 -m market_data --source tushare --financials --stocks 500 --token "$TUSHARE_TOKEN"

# 资金流（akshare）
python3 -m market_data --source akshare --capital-flow --stocks 300

# 龙虎榜（akshare）
python3 -m market_data --source akshare --dragon-tiger --start 20240101

# 60 分钟线（baostock）
python3 -m market_data --source baostock --intraday-60m --start 20230101 --stocks 200
```

### 3. 运行 Commander（推荐主入口）

> 当前推荐把 `Commander` 作为**唯一人类入口**使用：状态查询、训练执行、训练实验室、配置管理、数据查询、运行诊断都优先通过 `Commander` 对话/CLI 完成。Web 控制台更适合作为可选可视化与兼容壳。

```bash
# 推荐先走真实数据 / 离线库路径
python3 commander.py status --detail fast
python3 commander.py strategies --reload
python3 commander.py train-once --rounds 1
python3 commander.py run --interactive

# 显式 smoke / demo 模式（mock 数据 + dry-run LLM）
python3 commander.py train-once --rounds 1 --mock

# 等价 console script
invest-commander status --detail fast
invest-commander train-once --rounds 1
invest-commander train-once --rounds 1 --mock
```

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

### 3.2 执行 Freeze Gate

```bash
# 契约漂移 + focused protocol/golden 回归
invest-freeze-gate --mode quick

# 完整发布门（推荐 release 前执行）
invest-freeze-gate --mode full

# 只列出将执行的命令
invest-freeze-gate --mode full --list
```

### 4. 直接跑训练

```bash
# 正式训练默认使用真实数据链路
python3 train.py --cycles 5

# 显式 smoke / demo 模式
python3 train.py --cycles 1 --mock

# 等价 console script
invest-train --cycles 5
invest-train --cycles 1 --mock
```

### 5. 模型路由

- 系统默认启用 `rule` 路由模式，会在每轮训练前先识别市场状态，再决定主模型。
- 可通过 `config/evolution.yaml` 或 `POST /api/evolution_config` 调整：
  - `model_routing_enabled`
  - `model_routing_mode`
  - `model_switch_cooldown_cycles`
  - `model_switch_min_confidence`
  - `model_switch_hysteresis_margin`
- 可通过 `GET /api/model-routing/preview` 预览某个截断日的路由决策。

### 6. 启动 Web 控制台

> Web 控制台当前定位为**可选观测/展示层**，不是推荐主入口。若你更偏向自然语言交互与低前端维护成本，可仅保留 `Commander` 入口，Web 只在需要图表、SSE 回放和只读可视化时启用。

```bash
# 本地调试：默认绑定回环地址，未配置鉴权也可启动
python3 web_server.py

# 本地 smoke / demo / health-check 模式
python3 web_server.py --mock
# 默认地址: http://127.0.0.1:8080
```

```bash
# 生产部署：必须开启 Web API 鉴权
export WEB_API_TOKEN="<strong-random-token>"
export WEB_API_REQUIRE_AUTH=true
export WEB_API_PUBLIC_READ_ENABLED=false
export GUNICORN_WORKERS=1
pip install -e ".[prod]"
gunicorn -c gunicorn.conf.py wsgi:app
```

- 非回环地址部署时，若未开启 `WEB_API_REQUIRE_AUTH=true` 且未配置 `WEB_API_TOKEN`，服务会拒绝启动。
- `wsgi:app` 会在导入时自动 bootstrap Commander runtime，因此 `GUNICORN_WORKERS` 必须保持为 `1`。
- 鉴权支持 `Authorization: Bearer <token>` 或 `X-Invest-Token: <token>`。
- 内置简单应用级限流，默认按窗口限制读 / 写 / 重型接口；可通过 `WEB_RATE_LIMIT_*` 环境变量调整。
- 反向代理必须向 `/api/*` 转发可信的 `X-Real-IP`；应用不再信任客户端自带的 `X-Forwarded-For` 进行限流识别。
- 健康检查：`GET /healthz`。
- 自然语言交互入口：`POST /api/chat`。
- 运行状态与事件流入口：`GET /api/status`、`GET /api/events`。
- 部署示例文件：`deploy/nginx/invest-evolution.conf`、`deploy/systemd/invest-evolution.service`、`deploy/systemd/invest-evolution.env.example`。
- 根路径 `/` 现在返回 API 入口说明；`/app` 与 `/legacy` 仅保留为已移除 UI 的 tombstone 提示。

> `mock` 现在是显式的 smoke / demo / health-check 模式，不再作为真实训练失败时的隐式兜底。

## 当前正式入口

### 入口脚本

- `app/commander.py`：统一 Commander CLI / daemon / runtime 装配入口
- `app/train.py`：训练/研究入口
- `app/web_server.py`：Flask API / SSE / 自然语言交互入口
- `market_data/__main__.py`：统一数据同步与状态诊断入口

### 兼容壳

以下根目录文件仍可继续使用，但真实实现都在 `app/`：

- `commander.py`
- `train.py`
- `web_server.py`

## 项目结构（与当前代码一致）

```text
app/                 顶层应用实现（commander/train/web_server/lab/training）
app/commander_support/ Commander 入口的查询/写入/生命周期/工作流支撑模块
brain/               本地 agent loop、工具、cron、bridge、memory、plugin loader
market_data/         canonical SQLite 数据层、同步服务、读侧 dataset builder
invest/              投资域模型：模型、Agent、会议、交易模拟、评估、进化、allocator
config/              全局配置、可编辑配置服务、Agent 配置注册表
scripts/cli/         独立 CLI 工具脚本（allocator / leaderboard）
scripts/             数据回填、契约生成与维护脚本
strategies/          可插拔策略基因（md/json/py）
runtime/             运行态输出、锁文件、记忆、会话、日志、训练实验室工件
agent_settings/      Agent prompt / plugin 的本地覆盖与扩展入口（可选）
tests/               当前实现对应的回归测试
docs/                面向开源协作的公开文档与说明
```

## 运行时产物

默认运行态目录都在 `runtime/`：

- `runtime/outputs/training/`：周期结果、冻结报告、优化事件等
- `runtime/outputs/leaderboard.json`：模型排行榜
- `runtime/outputs/commander/state.json`：运行时状态快照
- `runtime/logs/meetings/`：选股会议 / 复盘会议 JSON 与 Markdown
- `runtime/memory/commander_memory.jsonl`：Commander 长期记忆
- `runtime/state/`：锁文件、训练计划、训练运行、训练评估、配置快照、路径配置
- `runtime/sessions/inbox` / `runtime/sessions/outbox`：Bridge 收发目录

## 配置说明

### 核心配置来源

当前配置支持分层加载，优先级从低到高如下：

1. `config/__init__.py` 中的默认值
2. `config/evolution.yaml`
3. `config/evolution.local.yaml`
4. `runtime/state/evolution.runtime.yaml`
5. `INVEST_CONFIG_PATH` 指向的额外覆盖文件
6. 环境变量

建议约定：

- `config/evolution.yaml`：共享、可审阅、非敏感配置
- `config/evolution.local.yaml`：本地敏感项与个人覆盖项（可选，自行从 example 创建）
- `runtime/state/evolution.runtime.yaml`：由 `/api/evolution_config` 维护的运行时覆盖层，不手工编辑、不纳入版本控制
- 环境变量：线上密钥与部署平台注入项

控制面默认共享配置位于 `config/control_plane.yaml`，本地敏感覆盖建议放到 `config/control_plane.local.yaml`。

推荐本地启动前先准备可选覆盖层：

```bash
cp config/evolution.local.yaml.example config/evolution.local.yaml
cp config/control_plane.local.yaml.example config/control_plane.local.yaml
export OPENAI_API_KEY="<your-key>"
```

如果你希望自定义 Agent prompt / plugin 配置：

- 主仓库内置了一套公开可用的 baseline prompt
- 可直接编辑 `agent_settings/agents_config.json` 覆盖默认 prompt / llm_model
- 可在 `agent_settings/plugins/` 下添加你自己的 `*.json` 插件定义
- 后续若需要拆分独立 prompt extension pack，可复用同一 JSON schema 和覆盖机制

### 常用环境变量

- `OPENAI_API_KEY`
- `MINIMAX_API_KEY`
- `LLM_API_KEY`
- `COMMANDER_MODEL`
- `COMMANDER_AUTOPILOT`
- `COMMANDER_HEARTBEAT`
- `COMMANDER_BRIDGE`
- `COMMANDER_MOCK`

### Web 可改配置

- `/api/evolution_config`：训练与 Web 运行参数；写入 `runtime/state/evolution.runtime.yaml`
- `/api/control_plane`：LLM provider / model / API key 绑定
- `/api/runtime_paths`：训练输出、会议日志、配置审计与快照路径
- `/api/agent_prompts`：Agent prompt 配置

## 测试

```bash
ruff check .
pytest -q
```

当前测试覆盖的主题包括：

- Commander / Brain / Web API 主链
- 数据层统一与状态审计
- 训练计划 / 训练运行 / 训练实验室工件
- 模型配置校验、mutation、策略评分与 allocator
- Agent prompt 边界、导入约束与结构守卫

## 相关文档

- `docs/README.md`：文档索引与分层导航
- `docs/MAIN_FLOW.md`：系统主链路
- `docs/TRAINING_FLOW.md`：训练周期细节
- `docs/AGENT_INTERACTION.md`：Agent 与会议协作
- `docs/DATA_ACCESS_ARCHITECTURE.md`：数据层架构
- `docs/CONFIG_GOVERNANCE.md`：配置治理与审计
- `docs/RUNTIME_STATE_DESIGN.md`：运行态文件设计
- `docs/COMPATIBILITY_SURFACE.md`：兼容入口与正式实现边界
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
