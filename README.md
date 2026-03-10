# 投资进化系统 v1.0

当前仓库是一套**A 股离线数据驱动的策略训练与运行平台**：它把 `Commander` 指挥运行时、训练主循环、统一数据层、Web 控制台、训练实验室、模型排行榜与 allocator 放在同一个工程里。

当前代码主链已经稳定收敛到以下能力：

- **统一入口**：CLI、训练入口、Web 服务都以 `app/` 下实现为准，根目录同名脚本只保留兼容启动壳。
- **统一数据层**：`market_data/` 负责 SQLite canonical schema、离线同步、质量审计、训练/网页读取构造器。
- **统一训练闭环**：`SelfLearningController` 完成“数据加载 → 模型产出 → Agent 会议 → 模拟交易 → 评估 → 复盘 → 优化/固化”。
- **统一运行时**：`CommanderRuntime` 把 `brain/` 本地 agent loop 与投资训练主体融合到单进程内。
- **统一实验产物**：训练计划、训练运行、训练评估、周期结果、会议记录、配置快照、优化事件都落盘到 `runtime/`。

## 当前功能一览

### 1. 训练与研究

- 支持四个内置投资模型：`momentum`、`mean_reversion`、`value_quality`、`defensive_low_vol`
- 支持模型 YAML 配置、评分权重、风险策略、训练门控与 mutation space
- 支持 mock 数据模式，便于本地验证训练链路
- 支持训练计划 / 训练运行 / 训练评估三层实验工件
- 支持 leaderboard 聚合、市场状态识别与训练前模型路由

### 2. Agent 与会议系统

- 选股侧：`MarketRegimeAgent`、`TrendHunterAgent`、`ContrarianAgent`、`QualityAgent`、`DefensiveAgent`
- 复盘侧：`StrategistAgent`、`EvoJudgeAgent`、`ReviewDecisionAgent`
- 支持 debate 开关、Agent 权重调整、复盘建议回写、反思记忆
- Agent prompt 可通过 `agent_settings/agents_config.json` 与 Web API 修改

### 3. Commander 运行时

- `BrainRuntime` 提供本地多轮对话 + tool calling
- 内置工具覆盖：状态查询、训练执行、训练计划、策略基因、cron、记忆搜索、插件重载
- 支持单实例锁、训练互斥锁、Bridge 收发箱、cron、heartbeat、记忆审计
- 支持将训练结果自动写入 memory 与 training lab 工件目录

### 4. Web 控制台与 API

- Dashboard / Chat / Train / Strategies / Cron / Memory / Agents / Data 等前端面板
- Flask API 覆盖状态、训练、训练实验室、策略、leaderboard、allocator、配置、数据查询与后台下载
- SSE 事件流：`/api/events`
- Web 模式默认关闭 autopilot / heartbeat / bridge，仅保留手动触发与监控

### 5. 数据层

- 默认离线库：`data/stock_history.db`
- 数据源：`baostock`、`tushare`、`akshare`
- 已统一表：`security_master`、`daily_bar`、`index_bar`、`financial_snapshot`、`trading_calendar`、`security_status_daily`、`factor_snapshot`、`capital_flow_daily`、`dragon_tiger_list`、`intraday_bar_60m`、`ingestion_meta`
- 支持数据健康审计、训练 readiness 诊断、资金流 / 龙虎榜 / 60 分钟线读取

## 快速开始

推荐使用 Python 3.11+ 与虚拟环境。

```bash
cd ~/Desktop/投资进化系统v1.0
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

### 3. 运行 Commander

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
pip install -e ".[prod]"
gunicorn -c gunicorn.conf.py wsgi:app
```

- 非回环地址部署时，若未开启 `WEB_API_REQUIRE_AUTH=true` 且未配置 `WEB_API_TOKEN`，服务会拒绝启动。
- 鉴权支持 `Authorization: Bearer <token>` 或 `X-Invest-Token: <token>`。
- 健康检查：`GET /healthz`。
- 旧静态壳：`/legacy`
- 新前端挂载点：`/app`
- 默认 `web_ui_shell_mode=legacy`，即 `/` 仍指向旧壳；设置为 `app` 后 `/` 指向新前端。
- 当 `frontend_canary_enabled=true` 时，可通过 `/?__frontend=app` 或请求头 `X-Invest-Frontend-Canary: app` 做灰度访问。

> `mock` 现在是显式的 smoke / demo / health-check 模式，不再作为真实训练失败时的隐式兜底。

## 当前正式入口

### 入口脚本

- `app/commander.py`：统一 Commander CLI / daemon / runtime 装配入口
- `app/train.py`：训练/研究入口
- `app/web_server.py`：Flask API + 静态控制台入口
- `market_data/__main__.py`：统一数据同步与状态诊断入口

### 兼容壳

以下根目录文件仍可继续使用，但真实实现都在 `app/`：

- `commander.py`
- `train.py`
- `web_server.py`
- `llm_gateway.py`
- `llm_router.py`

## 项目结构（与当前代码一致）

```text
app/                 顶层应用实现（commander/train/web_server/lab/training）
brain/               本地 agent loop、工具、cron、bridge、memory、plugin loader
market_data/         canonical SQLite 数据层、同步服务、读侧 dataset builder
invest/              投资域模型：模型、Agent、会议、交易模拟、评估、进化、allocator
config/              全局配置、可编辑配置服务、Agent 配置注册表
strategies/          可插拔策略基因（md/json/py）
static/              Web 控制台静态资源
runtime/             运行态输出、锁文件、记忆、会话、日志、训练实验室工件
agent_settings/      Agent prompt / model 配置与插件模板
tests/               当前实现对应的回归测试
历史归档区/          已退出主链但保留追溯价值的历史资料
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
4. `INVEST_CONFIG_PATH` 指向的额外覆盖文件
5. 环境变量

建议约定：

- `config/evolution.yaml`：共享、可审阅、非敏感配置
- `config/evolution.local.yaml`：本地敏感项与个人覆盖项
- 环境变量：线上密钥与部署平台注入项

推荐从示例文件开始：

```bash
cp config/evolution.yaml.example config/evolution.yaml
cp config/evolution.local.yaml.example config/evolution.local.yaml
export LLM_API_KEY="<your-key>"
```

### 常用环境变量

- `LLM_API_KEY`
- `LLM_API_BASE`
- `LLM_MODEL`
- `LLM_DEEP_MODEL`
- `COMMANDER_MODEL`
- `COMMANDER_AUTOPILOT`
- `COMMANDER_HEARTBEAT`
- `COMMANDER_BRIDGE`
- `COMMANDER_MOCK`

### Web 可改配置

- `/api/evolution_config`：训练与模型级运行参数
- `/api/runtime_paths`：训练输出、会议日志、配置审计与快照路径
- `/api/agent_configs`：Agent prompt / model 配置

## 测试

```bash
pytest -q
```

当前测试覆盖的主题包括：

- Commander / Brain / Web API 主链
- 数据层统一与状态审计
- 训练计划 / 训练运行 / 训练实验室工件
- 模型配置校验、mutation、策略评分与 allocator
- Agent prompt 边界、导入约束与结构守卫

## 相关文档

- `docs/MAIN_FLOW.md`：系统主链路
- `docs/TRAINING_FLOW.md`：训练周期细节
- `docs/AGENT_INTERACTION.md`：Agent 与会议协作
- `docs/ARCHITECTURE_DIAGRAM.md`：当前架构图
- `docs/DATA_ACCESS_ARCHITECTURE.md`：数据层架构
- `docs/CONFIG_GOVERNANCE.md`：配置治理与审计
- `docs/RUNTIME_STATE_DESIGN.md`：运行态文件设计
- `docs/PROJECT_AUDIT_20260310.md`：当前实现审计摘要

## 现阶段建议的阅读顺序

1. 先读 `README.md`
2. 再看 `docs/MAIN_FLOW.md`
3. 需要训练细节时看 `docs/TRAINING_FLOW.md`
4. 需要数据层时看 `docs/DATA_ACCESS_ARCHITECTURE.md`
5. 需要运行时/配置排障时看 `docs/RUNTIME_STATE_DESIGN.md` 与 `docs/CONFIG_GOVERNANCE.md`


## 发布与安全手册

- 前端 Jira 级任务单：`docs/frontend/frontend-implementation-task-sheet-v2.md`
- 安全与发布前清理：`docs/runbooks/security-release-preflight.md`
- 模型路由灰度 / 回滚：`docs/runbooks/router-rollout.md`
