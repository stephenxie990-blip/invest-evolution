# invest/ 重构蓝图

## 1. 目标

本蓝图面向 `invest/` 模块的下一阶段治理，目标不是改变当前训练/选股/交易/评估语义，而是将现有“功能完整但文件偏大”的业务内核进一步收口为：

- 更清晰的目录层次
- 更稳定的公开 API
- 更可解释的策略演化链
- 更低的维护成本
- 可分批实施的迁移路径

## 2. 当前问题总结

### 2.1 大文件过多

当前 `invest/` 主要痛点：

- `core.py`：公共协议、LLM 包装、指标、摘要、追踪混在一起
- `agents.py`：所有业务 Agent 混在一个文件
- `trading.py`：交易引擎、风险控制、异常检测都在同一文件
- `optimization.py`：选股、因子、风险模型、LLM 优化、遗传进化都在一起
- `evaluation.py`：评估、冻结、综合分析聚合较重

### 2.2 边界已经清楚，但粒度还不够细

当前已经形成以下业务层次：

- 基础协议与共享工具
- Agent 认知层
- 会议编排层
- 执行层
- 评估层
- 优化/进化层
- 增强层（辩论/记忆）

问题不是方向不对，而是这些层次还没有完全体现在目录结构上。

### 2.3 策略演化链分散

当前“策略演化”能力分散在：

- `optimization.py`：优化器与进化引擎
- `meetings.py`：ReviewMeeting 复盘建议
- `app/train.py`：触发与应用逻辑

这导致理解成本较高：

- 谁建议改参数？
- 谁真正应用参数？
- 谁只做门控而不做调整？

## 3. 重构原则

1. 不改变当前训练主链路语义
2. 不改变现有对外入口
3. 优先重构内部目录与导入边界
4. 每一步都允许兼容导出，避免一次性爆炸迁移
5. 先拆共享协议和大文件，再考虑更细颗粒优化

## 4. 目标目录结构

建议将 `invest/` 从“平铺大文件”演进为如下结构：

```text
invest/
  __init__.py

  shared/
    __init__.py
    contracts.py        # TradingPlan / PositionPlan / 共享数据合同
    llm.py              # LLMCaller
    indicators.py       # RSI / MACD / BB / pct change
    summaries.py        # summarize_stocks / compute_market_stats / table formatting
    tracking.py         # AgentTracker / TraceLog

  agents/
    __init__.py
    base.py             # InvestAgent / AgentConfig / Belief
    regime.py           # MarketRegimeAgent
    hunters.py          # TrendHunterAgent / ContrarianAgent
    reviewers.py        # StrategistAgent / ReviewDecisionAgent / EvoJudgeAgent

  meetings/
    __init__.py
    selection.py        # SelectionMeeting
    review.py           # ReviewMeeting
    recorder.py         # MeetingRecorder

  trading/
    __init__.py
    contracts.py        # Position / TradeRecord / SimulationResult / RiskMetrics
    engine.py           # SimulatedTrader
    risk.py             # RiskController / DynamicStopLoss / EmergencyDetector
    helpers.py          # 价格/日行情/历史窗口辅助函数

  evaluation/
    __init__.py
    cycle.py            # EvaluationResult / StrategyEvaluator
    benchmark.py        # BenchmarkMetrics / BenchmarkEvaluator
    freeze.py           # FreezeEvaluator
    reports.py          # 文本报告 / 汇总报告

  selection/
    __init__.py
    selectors.py        # StockSelector / AdaptiveSelector
    factors.py          # AlphaFactorModel / DynamicFactorWeight
    risk_models.py      # RiskFactorModel

  evolution/
    __init__.py
    llm_optimizer.py    # LLMOptimizer / AnalysisResult
    engine.py           # EvolutionEngine / Individual
    orchestrator.py     # StrategyEvolutionOptimizer / 统一演化事件模型

  extensions/
    __init__.py
    debate.py           # DebateOrchestrator / RiskDebateOrchestrator
    memory.py           # MarketSituationMemory
```

## 5. 模块边界说明

### 5.1 `shared/`

职责：提供纯共享协议与纯函数工具。

应包含：

- `TradingPlan` 及相关合同
- 技术指标函数
- 市场摘要与股票摘要函数
- `LLMCaller`
- 跟踪与日志对象

不应包含：

- 会议编排
- 训练主循环
- 交易执行状态机
- 优化器行为

### 5.2 `agents/`

职责：只负责“认知与判断”。

应包含：

- 市场状态 Agent
- 候选猎手 Agent
- 复盘分析 Agent

不应包含：

- 计划聚合
- 交易执行
- 文件落盘

### 5.3 `meetings/`

职责：把多个 Agent 的意见收敛成统一决策。

- `selection.py`：产出 `TradingPlan`
- `review.py`：产出复盘建议、参数调整建议、权重调整建议
- `recorder.py`：负责会议审计与记录持久化

### 5.4 `trading/`

职责：执行已知计划，不产生策略判断。

- `engine.py`：只做模拟主循环
- `risk.py`：只做风控与异常检测
- `contracts.py`：持仓、成交、结果数据结构

### 5.5 `selection/`

职责：算法型选股与因子排序。

这部分与“策略进化”不同，不应该继续放在 `optimization.py` 里。

### 5.6 `evolution/`

职责：统一策略演化链。

建议后续将当前散落逻辑统一归到这里：

- LLM 亏损分析
- EvolutionEngine 参数进化
- 统一的 `EvolutionEvent`
- 建议/决策/已应用变更统一表达

### 5.7 `extensions/`

职责：增强件，不是主链路必需模块。

包括：

- 辩论能力
- 市场情境记忆

## 6. 公开 API 设计建议

当前 `invest/__init__.py` 使用 `import *` 暴露全部内容，不利于后续迁移。

建议调整为显式导出稳定接口，例如：

- `TradingPlan`
- `PositionPlan`
- `SelectionMeeting`
- `ReviewMeeting`
- `SimulatedTrader`
- `StrategyEvaluator`
- `BenchmarkEvaluator`
- `AdaptiveSelector`
- `LLMOptimizer`
- `EvolutionEngine`

并在迁移期间通过兼容 re-export 保持旧导入路径可用。

## 7. 演化/进化链重构建议

### 7.1 当前问题

当前演化链分散：

- `optimization.py`：定义优化器与进化引擎
- `meetings.py`：复盘建议
- `app/train.py`：触发与应用

### 7.2 目标状态

建议形成清晰分层：

- `review.py`：产生“策略调整建议”
- `evolution/orchestrator.py`：统一接收触发条件、调用 LLM 优化与进化引擎、返回结构化事件
- `app/train.py`：只负责触发与应用，不直接承载进化细节

### 7.3 统一事件模型

建议统一为：

- `trigger`
- `stage`
- `status`
- `suggestions`
- `decision`
- `applied_change`
- `notes`
- `ts`

## 8. 分阶段执行方案

## Phase 1：共享层拆分（低风险，高收益）

### 内容

- 从 `core.py` 中拆出：
  - `contracts.py`
  - `llm.py`
  - `indicators.py`
  - `summaries.py`
  - `tracking.py`

### 方法

- 保留 `invest/core.py` 作为兼容层
- `core.py` 内部改为 re-export 新模块
- 先不改业务行为，只改导入组织

### 风险

- 低

### 验证

- 现有测试全绿
- `app/train.py` 与 `invest/` 内部导入不报错

## Phase 2：会议与 Agent 子包化（中风险，高收益）

### 内容

- 拆 `agents.py`
- 拆 `meetings.py`

### 方法

- 先建立 `agents/`、`meetings/` 子包
- 原文件保留兼容 re-export
- 分文件迁移类定义

### 风险

- 中
- 容易引入循环依赖，需要谨慎调整 `shared/` 引用层

### 验证

- 训练主循环可导入
- SelectionMeeting / ReviewMeeting 行为一致

## Phase 3：交易层拆分（中风险）

### 内容

- 拆 `trading.py` 为 `contracts.py`、`engine.py`、`risk.py`

### 方法

- 优先抽数据结构与风控组件
- 最后保留 `SimulatedTrader` 在 `engine.py`
- 原 `trading.py` 改兼容层

### 风险

- 中
- 涉及内部状态与 helper 调用较多

### 验证

- 交易回归测试
- 关键风险控制测试

## Phase 4：选股/进化分离（最高价值）

### 内容

- 将 `optimization.py` 拆成：
  - `selection/`
  - `evolution/`

### 方法

- 先抽 `StockSelector` / `AdaptiveSelector` / 因子模型
- 再抽 `LLMOptimizer` / `EvolutionEngine`
- 最后新增统一 `orchestrator.py`

### 风险

- 中高
- 这是当前最复杂但最值得做的一步

### 验证

- 训练回归
- 审计事件仍可正确写出
- 演化触发条件不变

## Phase 5：收口公开 API 与文档（低风险）

### 内容

- 缩小 `invest/__init__.py` 暴露面
- 更新 README / 主链路说明 / 架构文档
- 明确主链路模块与增强模块

### 风险

- 低

### 验证

- 导入兼容测试
- 文档与代码结构一致

## 9. 兼容策略

为避免一次性改动过大，建议整个重构期采用“新模块落地 + 旧文件兼容转发”模式：

- 原 `invest/core.py`、`agents.py`、`meetings.py`、`trading.py`、`optimization.py` 暂时保留
- 原文件中仅保留兼容导出与 deprecation 注释
- 待一到两轮回归后再决定是否物理删除旧兼容层

## 10. 风险控制

### 重点风险

1. 循环依赖
2. 导入路径回归
3. 训练主循环行为偏移
4. 审计事件/训练输出字段丢失

### 控制手段

- 每个 Phase 完成后都跑全量测试
- 保持一轮只迁移一层，不同时拆多个大文件
- 先抽纯数据结构和纯函数，再抽有状态类
- 继续保留兼容壳，直到所有入口与测试稳定

## 11. 推荐实施顺序

推荐顺序如下：

1. Phase 1：拆 `core.py`
2. Phase 2：拆 `agents.py` + `meetings.py`
3. Phase 3：拆 `trading.py`
4. Phase 4：拆 `optimization.py`
5. Phase 5：缩小 `__init__.py` 暴露面并更新文档

## 12. 最终建议

如果只选一个最值得优先动的点：

- 先拆 `core.py`，因为它是全模块共享中心，拆完后其余重构都会更顺。

如果只选一个最值得重点治理的高价值目标：

- 拆 `optimization.py`，因为它目前最像未来的超级文件，且直接决定策略演化链的可解释性。
