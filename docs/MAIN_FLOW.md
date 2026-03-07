# 主链路说明

## 入口分工

- `commander.py`：推荐的统一 CLI 入口，适合状态检查、守护运行、策略热重载和单轮训练。
- `train.py`：面向训练流程本身的专用入口，适合批量 cycle 和研究型实验。
- `web_server.py`：Flask Web 前端/API 入口，适合手动触发训练、查看状态、编辑配置。

## 从入口到执行的主链路

1. `commander.py`
   - 构建 `CommanderConfig`
   - 初始化 `CommanderRuntime`
   - 装配 `BrainRuntime`、`CronService`、`HeartbeatService`、Bridge、Memory、Plugins
   - 通过 `InvestmentBodyService` 驱动 `SelfLearningController`
2. `train.py`
   - `SelfLearningController.run_training_cycle()` 执行单轮训练
   - 从 `DataManager` 获取历史或模拟数据
   - 使用 `compute_market_stats()` 与 Agent/算法判断市场状态
   - 调用 `SelectionMeeting.run_with_data()` 生成 `TradingPlan`
   - 交给 `SimulatedTrader.run_simulation()` 执行模拟交易
   - 用 `StrategyEvaluator` / `BenchmarkEvaluator` / `FreezeEvaluator` 做评估
   - 在亏损或触发条件下调用 `LLMOptimizer` 与 `EvolutionEngine` 做优化
3. `web_server.py`
   - 复用 `CommanderRuntime`
   - 暴露 `/api/status`、`/api/train`、`/api/strategies`、`/api/evolution_config` 等接口

## 模块映射

- `core.py`：公共数据结构、指标计算、市场统计、追踪器
- `agents.py`：各类 Agent 定义与兼容别名
- `brain_runtime.py`：tool-calling 运行时
- `brain_tools.py`：Commander 工具注册
- `brain_scheduler.py`：本地心跳与 interval 调度
- `brain_memory.py`：长期记忆
- `brain_bridge.py`：文件桥接通道
- `brain_plugins.py`：插件加载
- `llm_gateway.py` / `llm_router.py`：统一 LLM 出口与快/慢模型路由
- `data.py` / `meetings.py` / `trading.py` / `evaluation.py` / `optimization.py`：训练与交易主业务链

## `src/` 兼容层说明

- `src/` 目录当前只包含对根模块的兼容 re-export，例如 `src/commander.py -> from commander import *`。
- 保留它的价值在于兼容旧命令、旧导入路径和现有兼容测试。
- 如果计划删除 `src/`，需要同步完成三类收口：
  - 删除或改写所有 `src.*` 兼容测试
  - 删除 `agents.py`、`core.py`、`__init__.py` 中的 `src.*` 别名兼容逻辑
  - 全量确认外部脚本/文档/自动化不再使用 `python -m src.*`
- 因此当前建议是：`src/` 暂时保留，作为低成本兼容层；等确认无外部依赖后，再在单独变更中删除。
