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

- `commander.py`：统一 CLI 与守护入口，负责拼装 `CommanderRuntime`、调度、Bridge、策略基因与训练执行。
- `brain_runtime.py` + `brain_tools.py`：提供多轮推理与 tool-calling 外壳，把训练、状态、策略、记忆、定时任务暴露给 Commander。
- `train.py`：训练主控制器 `SelfLearningController`，负责“加载数据 → 市场判断 → 选股会议 → 模拟交易 → 评估 → 优化”。
- `data.py`：离线数据缓存、下载、模拟数据和统一 `DataManager`。
- `meetings.py`：`SelectionMeeting` 生成交易计划，`ReviewMeeting` 进行复盘与权重调整。
- `trading.py`：`SimulatedTrader`、风险控制、调度执行。
- `evaluation.py`：收益、基准、冻结与策略管理评估。
- `optimization.py`：LLM 亏损分析、遗传进化、参数优化与交易分析。
- `web_server.py`：Flask Web API/前端入口，复用 `CommanderRuntime` 提供状态、训练、策略与配置操作。
- 详细说明见 `docs/MAIN_FLOW.md`。

## 当前目录结构（与代码一致）

- `commander.py`: 融合主入口（守护进程、调度、工具编排）
- `brain_runtime.py`: 指挥官多轮推理 + tool-calling 运行时
- `brain_scheduler.py`: 本地 heartbeat + interval job 调度
- `brain_tools.py`: 投资工具注册（status/train/strategies/cron/memory/plugins）
- `brain_memory.py`: 持久记忆存储（jsonl）
- `brain_bridge.py`: 多通道桥接总线（file inbox/outbox）
- `brain_plugins.py`: 插件工具加载（plugins/*.json）
- `llm_gateway.py`: 全系统唯一外部 LLM 通道（训练与指挥官共用）
- `core.py`: 基础模型、LLMCaller、技术指标与公共能力
- `data.py`: 数据下载/离线加载/T0约束/模拟数据
- `agents.py`: 多 Agent 定义（regime/trend/contrarian/commander 等）
- `meetings.py`: 选股会议与复盘会议编排
- `trading.py`: 交易执行与风控
- `optimization.py`: 参数优化、进化与策略库
- `train.py`: 训练流程控制器

## 融合运行模型

- 单进程：Brain (nanobot风格)+ Body (投资训练引擎)
- 多通道桥接：`sessions/inbox` 输入，`sessions/outbox` 输出（24h 守护）
- 插件能力：`agent_settings/plugins/*.json` 声明式工具热加载
- 长期记忆：`memory/commander_memory.jsonl` 检索与审计
- 策略基因：`strategies/*.md|*.json|*.py`（可编辑、可替换、可热重载）
- 数据路径：统一默认到项目内 `data/stock_history.db`
- 输出路径：`outputs/`，日志路径：`logs/`

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
