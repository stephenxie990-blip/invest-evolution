# 项目总体评审报告（忽略前端）

日期：2026-03-12  
范围：后端运行时、研究引擎、训练流程、数据链路、交互治理、测试与文档一致性  
不包含：前端 UI 体验、页面交互与视觉层

---

## 1. 总体结论

这是一个**已经跨过“脚本集合”阶段、进入“本地研究与训练平台”阶段**的项目。

如果用一句话评价当前状态：

> **项目的骨架已经成立，主链路已经打通，最主要的问题不再是“功能有没有”，而是“核心对象过重、跨层字典过多、状态治理还没有完全产品化”。**

从工程成熟度看，我会给出如下判断：

- **架构成熟度：7.5/10**
- **训练闭环成熟度：8/10**
- **研究一体化成熟度：7.5/10**
- **数据链路成熟度：8/10**
- **交互治理成熟度：8/10**
- **可维护性：6.5/10**

结论不是“需要推倒重写”，而是：

- 当前项目**值得继续在现有骨架上演进**；
- 最优策略是**继续收敛边界、拆轻核心类、强化 typed contract 与状态治理**；
- 不建议再回到“快速堆功能”的路径。

---

## 2. 评审范围与方法

本次评审重点审阅了以下模块：

- 统一运行时：`app/commander.py`
- 问股链路：`app/stock_analysis.py`
- 训练链路：`app/train.py`
- 训练评估与优化：`app/training/reporting.py`、`app/training/optimization.py`
- 研究域模型：`invest/research/*`
- 复盘与裁判：`invest/meetings/review.py`、`invest/agents/reviewers.py`
- 数据链路：`market_data/manager.py`、`market_data/datasets.py`
- 交互治理协议：`brain/task_bus.py`、`brain/schema_contract.py`、`brain/runtime.py`
- 设计文档：`docs/MAIN_FLOW.md`、`docs/TRAINING_FLOW.md`、`docs/DATA_ACCESS_ARCHITECTURE.md`、`docs/RESEARCH_ENGINE_UNIFICATION_EXECUTION_BLUEPRINT_20260312.md`

评审方法以“**代码主链 + 文档交叉验证 + 当前测试覆盖面**”为准，而不是只看目录命名。

---

## 3. 架构评审

## 3.1 当前架构的真实分层

从实现上看，项目已经形成 5 个相对清晰的层：

1. **入口与运行时层**
   - `CommanderRuntime` 统一承接 CLI / Web / tool calling / training lab / runtime state
   - `BrainRuntime` 承担自然语言交互与工具编排

2. **业务编排层**
   - `StockAnalysisService` 负责问股分析编排
   - `SelfLearningController` 负责训练周期编排
   - `ReviewMeeting` 负责复盘讨论编排

3. **研究域层**
   - `invest/research/contracts.py`
   - `snapshot_builder.py`
   - `policy_resolver.py`
   - `hypothesis_engine.py`
   - `scenario_engine.py`
   - `attribution_engine.py`
   - `case_store.py`

4. **数据访问层**
   - `MarketDataRepository` + canonical SQLite
   - `DataManager` façade
   - `TrainingDatasetBuilder / WebDatasetService / IntradayDatasetBuilder` 等 read-side builder

5. **治理与协议层**
   - `brain/task_bus.py`
   - `brain/schema_contract.py`
   - runtime feedback / confirmation / next_action contract

这个分层是当前项目最值得肯定的地方：**虽然核心类仍偏重，但方向是对的，系统不是散的。**

## 3.2 架构优势

### 优势 A：运行时已经统一

`CommanderRuntime` 已经不是包装壳，而是统一运行时核心，负责：

- 状态统一
- training plan / run / evaluation 工件管理
- tool 暴露与 bounded workflow 包装
- memory / cron / bridge / strategy registry 协同

这使得系统具备了“单进程统一运行时”的稳定骨架。

### 优势 B：研究语义开始独立成层

`invest/research/*` 的引入是本项目今天最关键的架构升级。

现在问股与训练之间不再只靠“隐式共享模型结果”连接，而是开始围绕以下对象收敛：

- `ResearchSnapshot`
- `PolicySnapshot`
- `ResearchHypothesis`
- `OutcomeAttribution`

这使“个股研究”和“策略研究”第一次有了统一语义支点。

### 优势 C：治理协议已经制度化

`task_bus`、`feedback`、`next_action`、`bounded_workflow` 这套协议，使系统不仅能返回结果，还能返回：

- 为什么这么做
- 风险等级是什么
- 是否需要确认
- 下一步建议是什么

这对 agent 化、工具化、长期可维护性都非常重要。

## 3.3 架构问题

### 问题 A：核心类仍然过重

关键文件规模非常能说明问题：

- `app/commander.py`：3310 行
- `app/train.py`：2323 行
- `app/stock_analysis.py`：2028 行
- `app/web_server.py`：1543 行

这意味着当前系统虽然“逻辑上分层”，但**实现上仍然存在超大编排类**：

- `CommanderRuntime`
- `SelfLearningController`
- `StockAnalysisService`

这些类承担了：

- 编排
- 状态落盘
- DTO 组装
- 错误处理
- 契约包装
- 业务策略选择

它们是当前最主要的维护成本来源。

### 问题 B：跨层仍以 `dict` 为主

虽然研究域已经有 dataclass contract，但系统绝大多数跨层交互仍大量使用 `dict[str, Any]`。

典型表现：

- `CommanderRuntime` 大量构造/改写 payload
- `SelfLearningController` 大量拼装 cycle/report/snapshot dict
- `ReviewMeeting` 与 `ReviewDecisionAgent` 之间也主要传 dict
- API 返回体中 contract、artifact、feedback 混合嵌套

这会带来三个风险：

1. 字段漂移难发现
2. 重构成本高
3. 类型边界不清晰

### 问题 C：状态治理多轨并存

当前系统同时存在：

- SQLite canonical data
- runtime state JSON
- training artifacts JSON
- config YAML
- memory JSONL
- eval markdown / planning markdown

这不一定是错，但说明项目现在已经进入**“多状态容器时代”**。如果没有更明确的生命周期边界，后续维护会越来越重。

---

## 4. 功能实现评审

## 4.1 问股体系

问股链路在 `app/stock_analysis.py`，当前已经不再只是“策略 YAML + 指标解释”，而是：

1. 解析问题与策略
2. 解析股票与时间边界
3. 构建工具执行计划
4. 运行分析工具链
5. 从分析结果中抽取信号
6. 通过 `_build_research_bridge()` 接入真实研究内核
7. 构造 hypothesis / scenario / dashboard
8. 持久化 case，并尝试 attribution / calibration

这是一次质变：

- 旧 ask_stock 更像“分析器集合入口”
- 新 ask_stock 开始变成“单标的研究入口”

### 评价

- **方向非常正确**
- 时间因果控制做得不错，`as_of_date` 已是一等语义
- live 模式与 replay 模式已经显式区分
- 已能复用 live controller 的 active/routed model 输出

### 仍存问题

- `StockAnalysisService` 仍然过重，工具执行、研究桥接、dashboard 渲染、case 持久化都在一个类里
- ask 结果兼顾 legacy dashboard 与新 research payload，两套语义并存，后续还需继续收敛

## 4.2 训练体系

训练主线仍然以 `SelfLearningController.run_training_cycle()` 为核心，这条链路已经非常完整：

- 随机截断日
- readiness / diagnostics
- stock data load
- model process
- selection meeting
- simulated trading
- benchmark / strategy evaluation
- review meeting
- optimization / freeze
- result / report / snapshot persist

### 评价

- **训练闭环已经具备“实验平台”特征**，而不是单轮回测脚本
- 训练结果对象 `TrainingResult` 已覆盖数据模式、路由、benchmark、strategy score、research feedback 等元信息
- training lab 三工件（plan / run / evaluation）设计合理

### 仍存问题

- `SelfLearningController` 仍像“训练总线 + 协调器 + 状态机 + 报表器 + 调参器”的合体
- 某些 fallback / reload / mutation 逻辑混在主流程里，理解成本较高
- 训练流程虽然强，但“过程对象”偏少，“过程字典”偏多

## 4.3 研究一体化实现

这是今天项目最亮眼的部分。

已实现的统一闭环可以概括为：

```text
ask_stock
→ ResearchSnapshot / PolicySnapshot / Hypothesis
→ ResearchCaseStore
→ OutcomeAttribution / calibration
→ research_feedback
→ ReviewMeeting / optimization / freeze gate
→ promotion gate
→ training plan default calibration gate
→ CLI/API visibility
```

### 评价

这条链说明你们已经把“训练负责学规律，问股负责用规律”的抽象，落实到了代码主链，而不只是文档层共识。

### 当前完成度

- 统一语义：已初步成立
- 统一因果：已基本成立
- 统一验证闭环：已成立
- 统一对象模型：已建立雏形，但仍需继续去 `dict` 化

---

## 5. 数据链路评审

## 5.1 当前数据架构是稳定器

`market_data/` 已经形成比较清晰的数据架构：

- 写入统一走 `DataIngestionService -> MarketDataRepository -> SQLite`
- 读取通过 builder/service 分流
- `DataManager` 作为兼容 façade 暴露给训练与问股

这是整个项目最稳定的一层。

## 5.2 数据链路优势

### 优势 A：训练和读取口径基本统一

训练、状态诊断、Web 数据读取，基本都在同一个 canonical schema 上工作。

### 优势 B：point-in-time 约束开始显式化

`cutoff_date` / `as_of_date` 贯穿问股和训练主线，这是量化研究系统最重要的约束之一。

### 优势 C：readiness / diagnostics 设计成熟

在训练前先做 diagnostics，再决定是否跳过，这是很好的工程实践。

## 5.3 数据链路问题

### 问题 A：字符串日期主导，缺少强类型时间对象

系统大量使用 `YYYYMMDD` 字符串比较，这在当前项目里可行，但长期看容易产生：

- 细粒度时点语义不足
- 不同模块日期归一化不一致
- intraday / multi-horizon 扩展时边界复杂化

### 问题 B：数据降级策略分散

`DataManager` 已有离线优先 / 在线兜底 / mock 兜底，但降级语义同时出现在：

- 数据层
- 训练层
- 问股层
- runtime 结果层

建议未来进一步把“降级策略”抽到更显式的 resolution contract 中。

### 问题 C：尚无正式 migration 治理

canonical schema 已经稳定，但如果未来继续扩 factor / attribution / calibration 表，没有更正式的 migration 机制会成为风险。

---

## 6. 训练流程评审

## 6.1 训练流程的优点

### 优点 A：训练、评估、复盘、优化是闭合的

不是简单地“跑完就结束”，而是会进入：

- strategy evaluation
- benchmark evaluation
- review meeting
- optimization trigger
- freeze / promote judgment

这是真正的平台化训练流程。

### 优点 B：引入了多层反馈门

当前训练链至少有三层门控：

- `research_feedback` optimization trigger
- `freeze_gate`
- `promotion_gate`

并且 promotion gate 与 training plan default gate 现在已经共享校准语义。

### 优点 C：训练工件化做得好

plan / run / evaluation 三层工件非常适合：

- 审计
- 重放
- 排障
- 比较不同实验

## 6.2 训练流程问题

### 问题 A：训练周期函数过长

`run_training_cycle()` 是现在整个系统最典型的“高价值但高风险方法”。

它的问题不在于逻辑错，而在于：

- 责任密度过高
- 阅读负荷过大
- 局部修改容易牵动全局

### 问题 B：优化与冻结门虽然接入了 research_feedback，但策略仍偏 rule-heavy

当前优点是稳，但也意味着：

- 规则越来越多
- 不同门之间阈值可能漂移
- 配置治理压力会不断变大

后续需要把“门的协议”收成一套明确的 calibration policy schema。

### 问题 C：报告与状态有一定重复

很多信息同时出现在：

- cycle dict
- controller snapshot
- eval report metadata
- training report
- training_lab evaluation

这提升了可观察性，但也带来了一定重复与维护成本。

---

## 7. 交互逻辑评审（忽略前端）

## 7.1 交互层的优点

### 优点 A：Brain/Commander 模式已经很清晰

项目已经把“自然语言代理”与“投资业务运行时”分开：

- `BrainRuntime` 负责 reasoning / tool calling
- `CommanderRuntime` 负责业务边界与状态控制

这是正确的代理系统设计。

### 优点 B：task_bus / feedback / next_action 很成熟

这套协议让系统输出不再只是“答复内容”，而是带治理语义的响应。

对于复杂任务系统，这是高级能力。

### 优点 C：mutating / readonly 边界感强

确认机制、覆盖率、reason code、next action 等设计，都说明项目已经开始把“安全执行”当作一等问题。

## 7.2 交互层问题

### 问题 A：CommanderRuntime 过于中心化

几乎所有业务能力最后都会汇总到 `CommanderRuntime`。

这带来的问题是：

- 优点：统一出口
- 缺点：成为超级中枢，后续越来越重

### 问题 B：API response envelope 与业务 payload 有时耦合过深

现在很多 payload 既承载业务数据，又承载：

- bounded workflow
- task_bus audit
- feedback
- next_action
- artifact taxonomy

这对 agent/CLI 很好，但会让业务对象和协议对象耦合较深。

### 问题 C：legacy 兼容层仍然较多

项目为了保持兼容做了很多保留层，这是务实的；但从长期维护看，需要逐步设定“弃用窗口”，否则复杂度会继续上升。

---

## 8. 测试与文档评审

## 8.1 测试优点

当前项目的测试广度是一个明显强项：

- research contracts
- case store
- attribution engine
- ask bridge
- training feedback
- commander
- web API contract
- schema contracts
- golden tests

这说明项目已经具备“改动后可快速回归”的基本能力。

## 8.2 文档优点

当前文档已经不是摆设，而是开始能和实现对齐：

- 主流程文档
- 数据架构文档
- 训练流程文档
- 研究统一蓝图
- planning / findings / progress

## 8.3 风险

- 文档数量很多，未来可能再次出现“文档滞后于实现”
- golden tests 与 schema contract 需要持续同步，否则容易在快速迭代时产生误报

---

## 9. 关键问题清单

### P0：必须持续处理

1. **拆轻 `SelfLearningController`**
   - 至少拆成：训练编排、训练状态、结果落盘、优化门控 四块

2. **拆轻 `CommanderRuntime`**
   - 至少拆出：training lab service、runtime status service、response envelope service

3. **继续减少跨层裸 `dict`**
   - 尤其是 training evaluation / promotion / guardrails / research feedback 这一链

4. **把 calibration policy 做成统一 schema**
   - 避免 optimization / freeze / promotion / default gate 逐渐各走各的阈值

### P1：下一阶段高价值项

5. **研究对象继续固化为真正的中间层**
   - snapshot / policy / hypothesis / attribution 已有雏形，下一步是扩大覆盖面并减少 legacy payload 依赖

6. **统一 artifact/state 生命周期**
   - 需要明确哪些是实验工件，哪些是运行时状态，哪些是长期知识资产

7. **建立更正式的数据 migration 约束**
   - 尤其是 canonical SQLite 与后续 research/calibration 表扩展

### P2：中期优化项

8. **观测性统一**
   - event、memory、report、artifact 的查询与诊断体验仍可进一步统一

9. **逐步压缩 legacy 兼容层**
   - 为 ask legacy dashboard、旧接口字段、旧 contract 保留层设立退出节奏

---

## 10. 推荐演进路径

### 阶段 A：稳定边界

目标：让当前闭环“更稳”，不是“更大”。

建议动作：

- 固化 calibration policy schema
- 拆轻 `CommanderRuntime`
- 拆轻 `SelfLearningController`
- 把 `promotion` / `guardrails` / `feedback` 的中间 DTO 收紧

### 阶段 B：研究层升格

目标：让 `invest/research/*` 真正成为中间层，而不是“新增的一组帮助函数”。

建议动作：

- 扩大 research object 在 ask/train/review 中的直接使用比例
- 缩减 legacy dashboard 与中间 dict 的桥接复杂度
- 将 case / attribution / calibration 发展为正式研究资产

### 阶段 C：平台化治理

目标：把系统从“可运行”推进到“可长期稳定演进”。

建议动作：

- 增强 migration 机制
- 明确 artifact 生命周期
- 对大文件做包级拆分
- 对协议和工件做版本治理

---

## 11. 最终评价

这个项目当前最值得肯定的，不是某一个功能，而是它已经具备了三个很难得的特征：

1. **统一运行时已经成立**
2. **训练闭环已经平台化**
3. **研究一体化已经从概念走到代码主链**

当前真正的主要矛盾不是“缺功能”，而是：

- 核心类过重
- 跨层 payload 过多
- 状态/工件/协议治理开始复杂化

因此，项目的最佳下一步不是继续横向加能力，而是：

> **围绕已形成的研究闭环，继续做边界收敛、对象固化、状态治理和核心编排减重。**

如果保持这条路线，我认为这个项目完全可以继续演进成一个**结构稳定、研究闭环清晰、可审计可迭代的本地化策略研究与训练平台**。
