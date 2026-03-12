# Invest 域重构蓝图（按当前实现回写）

本文不再描述早期的“理想拆分方案”，而是总结当前 `invest/` 域已经落地的结构，并给出后续可继续优化的方向。

## 1. 当前 `invest/` 已形成的结构

```text
invest/
├─ agents/          市场判断、猎手、复盘裁判
├─ allocator/       模型分配器
├─ contracts/       跨模块结构化契约
├─ evolution/       LLM 优化、进化引擎、mutation
├─ foundation/      交易模拟、指标、风控、评估基础设施
├─ leaderboard/     周期结果聚合排行
├─ meetings/        Selection / Review / Recorder
├─ models/          投资模型与 YAML 配置
├─ shared/          指标、摘要、跟踪、LLM caller 等共享能力
└─ memory.py        域内记忆相关结构
```

## 2. 已实现的重构目标

### 2.1 模型与会议分离

当前已经明确区分：

- `models/`：只负责策略模型与信号/上下文产出
- `meetings/`：只负责多 Agent 协作与计划决策

### 2.2 执行与评估下沉到 `foundation/`

交易模拟、风险控制、收益评估、benchmark 评估已经收口到：

- `invest/foundation/engine`
- `invest/foundation/risk`
- `invest/foundation/metrics`
- `invest/foundation/compute`

### 2.3 结构化契约独立

跨模块传递的数据结构已经集中到：

- `invest/contracts/`

这降低了训练主链里“字典满天飞”的程度。

### 2.4 多模型训练成为一等公民

模型 registry、leaderboard、allocator 已经形成闭环，说明重构不再只服务单策略训练。

## 3. 当前仍值得继续优化的点

### 3.1 `app/train.py` 仍然偏重

虽然主链已经清晰，但 `SelfLearningController` 仍承担了大量流程编排与状态落盘逻辑。后续可考虑进一步拆出：

- cycle orchestration
- artifact persistence
- evaluation aggregation
- skip / diagnostics policy

### 3.2 `shared/` 与 `contracts/` 的边界仍可继续收敛

目前共享工具和结构化契约已经分开，但未来仍可继续减少跨目录相互感知。

### 3.3 `memory.py` 的定位仍偏弱

当前项目里“Commander memory”和“投资域内反思/记忆”还不是同一套体系。若未来做更深的学习闭环，可考虑统一抽象。

## 4. 当前推荐的开发原则

- 新增交易规则，优先放进 `models/` 或 `foundation/`
- 新增会议角色，优先放进 `agents/` 与 `meetings/`
- 新增评估口径，优先放进 `foundation/metrics/`
- 新增进化逻辑，优先放进 `evolution/`
- 不要把新业务逻辑重新散回根目录或 `app/` 入口层
