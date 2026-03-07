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
- `agents.py`：各类 Agent 定义
- `brain_runtime.py`：tool-calling 运行时
- `brain_tools.py`：Commander 工具注册
- `brain_scheduler.py`：本地心跳与 interval 调度
- `brain_memory.py`：长期记忆
- `brain_bridge.py`：文件桥接通道
- `brain_plugins.py`：插件加载
- `llm_gateway.py` / `llm_router.py`：统一 LLM 出口与快/慢模型路由
- `data.py` / `meetings.py` / `trading.py` / `evaluation.py` / `optimization.py`：训练与交易主业务链

## 代码结构约束

- 根目录模块是唯一真实源码入口。
- 代码与导入路径统一使用根目录模块。
- 安装与运行统一以 `pyproject.toml` 为单一依赖来源。
- 训练与运行依赖显式 `TradingPlan`、显式配置字段和明确入口。
