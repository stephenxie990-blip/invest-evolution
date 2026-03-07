# 投资进化系统 v1.0

A股量化交易策略的自我进化训练平台（融合版 Commander）。

## 快速开始

推荐安装方式：`python3 -m pip install -e ".[dev]"`。

```bash
cd ~/Desktop/投资进化系统v1.0
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -e ".[dev]"

# 初始化统一离线库（推荐首次执行）
python -m market_data --source baostock --start 20180101

# CLI 状态检查
python commander.py status

# 查看并热重载策略基因
python commander.py strategies --reload

# 单轮训练（mock）
python commander.py train-once --rounds 1 --mock

# 常驻运行
python commander.py run

# Web 前端
python web_server.py --mock
```

## 主链路说明

- `app/commander.py`：统一 CLI 与守护入口的真实实现，负责拼装 `CommanderRuntime`、调度、Bridge、策略基因与训练执行；根目录 `commander.py` 仅保留兼容启动壳。
- `brain/runtime.py` + `brain/tools.py`：提供多轮推理与 tool-calling 外壳，把训练、状态、策略、记忆、定时任务暴露给 Commander。
- `app/train.py`：训练主控制器 `SelfLearningController` 的真实实现，负责“加载数据 → 市场判断 → 选股会议 → 模拟交易 → 评估 → 优化”；根目录 `train.py` 为兼容壳。
- `market_data/repository.py` + `market_data/ingestion.py` + `market_data/datasets.py`：统一数据仓储、同步服务、训练/T0/Web 读取构造器。
- `market_data/manager.py`：保留对外 façade，统一转发到 canonical 数据层。
- `invest/meetings/`：`SelectionMeeting` 生成交易计划，`ReviewMeeting` 进行复盘与权重调整，`MeetingRecorder` 负责会议审计落盘。
- `invest/trading/`：`SimulatedTrader`、风险控制、候选调度与交易执行。
- `invest/evaluation/`：收益、基准、冻结与策略管理评估。
- `invest/selection/` + `invest/evolution/`：多因子选股、LLM 亏损分析、遗传进化、参数优化与交易分析。
- `app/web_server.py`：Flask Web API/前端入口的真实实现，复用 `CommanderRuntime` 提供状态、训练、策略与配置操作；根目录 `web_server.py` 为兼容壳。
- 详细说明见 `docs/MAIN_FLOW.md`。

## 当前目录结构（与代码一致）

## 目录收口说明

- `app/`：顶层应用实现包，收纳原先散落在根目录的 `commander.py`、`train.py`、`web_server.py`、`llm_gateway.py`、`llm_router.py` 实现。
- 根目录同名文件现为兼容启动壳，保留 `python commander.py ...`、`python train.py ...`、`python web_server.py ...` 等既有用法。
- 业务包继续保留为 `invest/`、`market_data/`、`brain/`、`config/`，避免把业务逻辑重新散回根目录。

- `app/commander.py`: 融合主入口真实实现（守护进程、调度、工具编排）
- `brain/runtime.py`: 指挥官多轮推理 + tool-calling 运行时
- `brain/scheduler.py`: 本地 heartbeat + interval job 调度
- `brain/tools.py`: 投资工具注册（status/train/strategies/cron/memory/plugins）
- `brain/memory.py`: 持久记忆存储（jsonl）
- `brain/bridge.py`: 多通道桥接总线（file inbox/outbox）
- `brain/plugins.py`: 插件工具加载（plugins/*.json）
- `app/llm_gateway.py`: 全系统唯一外部 LLM 通道（训练与指挥官共用）；根目录 `llm_gateway.py` 为兼容转发模块
- `invest/core.py`: `invest.shared` 的兼容入口，保留公共能力旧导入路径
- `market_data/repository.py`: SQLite canonical schema 与查询仓储
- `market_data/ingestion.py`: Baostock/Tushare 同步写入服务
- `market_data/datasets.py`: 训练集、T0 数据集、Web 状态读取
- `market_data/quality.py`: 数据质量巡检
- `market_data/manager.py`: 向后兼容 façade 与命令行同步入口
- `invest/shared/`: 公共数据结构、LLMCaller、技术指标、摘要与追踪器
- `invest/agents/`: 多 Agent 定义（regime/trend/contrarian/commander 等）
- `invest/meetings/`: 选股会议、复盘会议与会议记录
- `invest/trading/`: 交易执行、风险控制与调度
- `invest/evaluation/`: 评估、冻结与策略管理
- `invest/selection/` / `invest/evolution/`: 选股、优化、进化与分析
- `app/train.py`: 训练流程控制器真实实现

## 融合运行模型

- 单进程：Brain (nanobot风格)+ Body (投资训练引擎)
- 多通道桥接：`runtime/sessions/inbox` 输入，`runtime/sessions/outbox` 输出（24h 守护）
- 插件能力：`agent_settings/plugins/*.json` 声明式工具热加载
- 长期记忆：`runtime/memory/commander_memory.jsonl` 检索与审计
- 策略基因：`strategies/*.md|*.json|*.py`（可编辑、可替换、可热重载）
- 数据路径：统一默认到项目内 `data/stock_history.db`
- 输出路径：`runtime/outputs/`，日志路径：`runtime/logs/`

## 测试

```bash
pytest -q
```

## 依赖

运行依赖和开发依赖统一由 `pyproject.toml` 管理，推荐使用 `python3 -m pip install -e ".[dev]"`。

## 配置

- 示例配置：`config/evolution.yaml.example`
- 复制为 `config/evolution.yaml` 后按需修改
- 也可通过环境变量覆盖（如 `LLM_API_KEY`）

## 打包

- 已提供 `pyproject.toml`
- CLI 入口：`invest-commander`、`invest-train`
