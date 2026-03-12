# 研究一体化融合方案（2026-03-12）

## 1. 结论先行

当前系统的真正问题，不是“训练”和“问股”跑在两个进程里，而是：

- **训练链已经有闭环**，但它的核心知识以 `ModelOutput / SignalPacket / EvalReport / StrategyAdvice` 等训练契约存在；
- **问股链已经有解释能力**，但它的核心知识仍停留在 `YAML + tools + derived_signals + dashboard` 的局部推理上；
- 两条链共享了数据源，却**没有共享研究语义、策略状态和结果归因**。

因此，最优方向不是继续扩 `ask_stock` 的工具数量，也不是直接把训练器塞进问股器，而是：

> 把系统升级为 **统一研究引擎（Unified Research Engine）**，训练与问股只是这个引擎的两个视角。

我认可“`ResearchState / PolicyState / AttributionState` 轻量中间层”的方向，但如果想做得更优雅、更稳，我建议补上一个你当前方案里缺失但最关键的对象：

- `ResearchSnapshot`：时点研究快照
- `PolicySnapshot`：策略/风控/评估快照
- `ResearchHypothesis`：研究结论 / 概率推演 / 可执行规则
- `OutcomeAttribution`：结果归因 / 校准 / 回灌

这四层构成闭环，而不是三层静态快照。

---

## 2. 基于当前代码的现状判断

### 2.1 训练链已经具备“研究内核雏形”

训练主链由 `SelfLearningController` 组织，真实流程是：

1. 用 `DataManager` 按 `cutoff_date` 加载训练数据
2. `investment_model.process(stock_data, cutoff_date)` 产出 `ModelOutput`
3. `SelectionMeeting.run_with_model_output(model_output)` 形成 `TradingPlan`
4. `SimulatedTrader.run_simulation()` 执行后验模拟
5. `StrategyEvaluator` + `BenchmarkEvaluator` 给出评估
6. `ReviewMeeting.run_with_eval_report()` 给出参数/权重调整

其中已经存在一批很好的统一契约：

- `invest/contracts/model_output.py`
- `invest/contracts/signal_packet.py`
- `invest/contracts/eval_report.py`
- `invest/contracts/strategy_advice.py`

这说明训练链不是“没有内核”，而是**内核只在训练域内闭合**。

### 2.2 问股链本质上仍是“工具编排器”

`StockAnalysisService` 当前路径是：

1. 解析股票和 YAML strategy
2. 用 `MarketDataRepository` 直接拉取单标的数据
3. 用 LLM ReAct 或 YAML plan 调工具
4. 汇总成 `derived_signals`
5. 用 `strategy.scoring + algo_score` 生一个 `dashboard`

这条链的优点是：

- 可解释
- 易扩展
- 对话友好

但它的局限也很明显：

- **它没有消费训练侧的 `ModelOutput / Policy / EvalReport`**
- **它分析的是“单标的局部证据”，不是“同一模型下的全市场相对位置”**
- **它的最终结论来自 YAML 评分模板，而不是训练正在学习的策略内核**

因此它更像一个“研究工具面板”，而不是统一研究内核的视图。

### 2.3 当前真正已经统一的是“数据层”，不是“研究层”

仓库文档已经确认：训练与 Web/Commander 侧基本共享统一离线库、统一 repository、统一 `DataManager` 读侧。

这意味着当前最关键的问题不再是“数据源割裂”，而是：

- 因子语义未统一
- 状态抽象未统一
- 后验评分未统一
- 问股没有回灌训练

---

## 3. 当前割裂的根因

### 根因 1：两条链没有共享同一个“研究样本”定义

训练的最小样本其实是：

- 某个 `cutoff_date`
- 某个市场环境
- 某个 universe
- 某个策略参数状态
- 某次选股/交易/评估结果

问股的最小样本却是：

- 某个股票
- 一套 YAML strategy
- 一串工具调用结果
- 一份解释性 dashboard

这两个“样本单位”天然不一样，所以很难闭环。

### 根因 2：问股缺少横截面对照

训练模型的本质是横截面筛选：

- 它会看全市场摘要
- 会排序
- 会决定 top_n
- 会结合 regime / risk / routing

而问股当前只看单标自身，缺少：

- 当前模型下该股票在全市场的分位/排名
- 是否高于当前入选阈值
- 与同风格候选相比处于什么位置

这会导致口径天然漂移：

> 单独看“像机会”，不代表在当前策略里“值得选”。

### 根因 3：策略状态有两套语言

训练链用的是模型配置语言：

- `params`
- `execution`
- `risk_policy`
- `evaluation_policy`
- `review_policy`
- `agent_weights`

问股链用的是 YAML strategy 语言：

- `required_tools`
- `analysis_steps`
- `entry_conditions`
- `scoring`
- `core_rules`
- `tool_call_plan`

两者都在表达“研究规则”，但没有一个是另一个的投影，所以不可避免会分叉。

### 根因 4：归因层太薄，无法承接问股闭环

训练侧虽然已经有 `StrategyEvaluator`、`BenchmarkEvaluator` 和复盘会议，但当前归因仍偏粗：

- `signal_accuracy` 本质上接近胜率代理
- `timing_score` 近似 `1 - max_drawdown`
- `risk_control_score` 近似止损/止盈原因计数
- `compute_per_stock_contribution()` 也只是简单 PnL 占比

它还不足以回答：

- 当时为什么看多？
- 是因子判断错了，还是执行错了？
- 触发失效条件前后，结论是否仍有效？
- 问股给出的概率/区间是否校准良好？

### 根因 5：问股缺少“可评分对象”

当前 ask_stock 会返回观点，但不会明确注册一条可被未来验证的研究命题：

- `as_of_date`
- `policy_version`
- `horizon`
- `entry_condition`
- `invalidation_condition`
- `de-risk_condition`
- `expected_distribution`

没有这些，后验评分和训练回灌就无从谈起。

---

## 4. 目标架构：统一研究引擎

### 4.1 核心设计原则

1. **同一因果**：任何结论必须绑定 `as_of_date`，只使用当时可见数据
2. **同一语义**：问股与训练共享因子定义、风险协议、评估协议
3. **同一闭环**：问股结论必须能被评分，并反哺训练校准
4. **同一内核，两种视角**：问股是单样本投影，训练是批量样本学习
5. **LLM 退居解释层**：核心研究结论应由结构化内核生成，LLM 只负责解释与补充证据编排

### 4.2 四层核心对象

#### A. `ResearchSnapshot`

不可变、时点化、可复盘的研究快照。

建议字段：

- `as_of_date`
- `scope`：`single_security` / `universe_batch`
- `security` / `universe`
- `market_context`
- `cross_section_context`
- `feature_snapshot`
- `data_lineage`
- `readiness`

其中最重要的是三部分：

- **market_context**：市场 regime、宽度、波动、指数背景
- **feature_snapshot**：单标的 canonical 指标/因子/结构/资金/风险位
- **cross_section_context**：在当前模型 universe 中的排名、分位、入选阈值差距、同组比较

> 这一步是当前 ask_stock 最缺失的。没有横截面位置，就谈不上与训练口径一致。

#### B. `PolicySnapshot`

版本化、可审计的策略状态。

建议字段：

- `policy_id`
- `model_name`
- `config_name`
- `params`
- `risk_policy`
- `execution_policy`
- `evaluation_policy`
- `review_policy`
- `agent_weights`
- `routing_context`
- `version_hash`

`PolicySnapshot` 的语义来源必须是训练侧当前真实正在使用的配置，而不是 ask_stock 自己再维护一套判断标准。

#### C. `ResearchHypothesis`

这是你当前三态方案里缺失的关键对象。它代表：

> 在某个 `ResearchSnapshot + PolicySnapshot` 下，系统对一个标的或一组标的形成的结构化判断。

建议字段：

- `stance`：候选买入 / 观察 / 回避 / 减仓
- `score` / `rank` / `percentile`
- `selected_by_policy`
- `scenario_distribution`
- `expected_return_interval`
- `max_adverse_excursion_estimate`
- `entry_rule`
- `invalidation_rule`
- `de_risk_rule`
- `supporting_factors`
- `contradicting_factors`
- `evaluation_protocol`
- `confidence`

这里要注意：

- **问股输出的核心不是一个“结论句子”**，而是一份 `ResearchHypothesis`
- 训练链也不应该直接从原始特征跳到交易结果，中间同样应形成批量 `ResearchHypothesis`

#### D. `OutcomeAttribution`

后验结果、归因、校准和回灌的统一对象。

建议字段：

- `hypothesis_id`
- `realized_return`
- `benchmark_excess_return`
- `mfe` / `mae`
- `entry_triggered`
- `invalidation_triggered`
- `de_risk_triggered`
- `thesis_result`：hit / miss / invalidated / timeout
- `factor_attribution`
- `timing_attribution`
- `execution_attribution`
- `risk_attribution`
- `calibration_metrics`
- `policy_update_candidates`

这一步承接训练与问股共用的复盘语言。

---

## 5. 最优雅的统一方式：不是把 ask_stock 做成训练器，而是让 ask_stock 读取训练内核

### 5.1 短期最关键的一步

**让 ask_stock 默认先跑一遍当前 active/routed investment model，再提取目标股票在该模型下的真实位置。**

这一步的价值极高，因为它立即统一了：

- 因子定义
- 市场状态判断
- 选股排序逻辑
- 风控参数
- 当前策略版本

具体来说，问股应先拿到：

- 当前 routed model 是谁
- 该 model 在当前 `as_of_date` 下的 `ModelOutput`
- 用户问的股票是否进入 `selected_codes`
- 若未入选，其 rank / percentile / threshold gap 是多少
- 当前 `SignalPacket.factor_values` 与 `evidence` 是什么

这样问股才真正是在回答：

> “这只股票在当前正在训练/实战使用的策略内核里，处于什么位置？”

而不是：

> “按一套独立 YAML 模板，它看起来像不像机会？”

### 5.2 YAML strategy 不应再当“决策内核”，而应降级为“视图/探针 DSL”

当前 YAML strategy 仍然有价值，但应改变定位：

- **保留** `required_tools / tool_call_plan / analysis_steps`
- **弱化** `scoring` 对最终结论的主导权
- **改造为**：问股的“证据探针”和“解释视图模板”

也就是说：

- 决策口径来自 `PolicySnapshot + ResearchHypothesis`
- YAML 决定“额外取哪些证据、如何展示、偏哪个研究视角”

这样既能保住现在的问股灵活性，又不会和训练内核打架。

---

## 6. 概率推演与情景预判应该怎么落地

你要求的方向非常正确：

- 概率推演，而不是单点预测
- 情景化预判，而不是拍脑袋
- 可执行规则，而不是口号

我建议不要一开始就上复杂黑盒预测器，而是先用 **历史相似样本分布 + 条件统计** 这条路线。

### 6.1 V1：相似样本分布引擎

训练过程中持续沉淀历史样本库：

- `ResearchSnapshot`
- `PolicySnapshot`
- 后续 `OutcomeAttribution`

问股时：

1. 取当前标的的 `ResearchSnapshot`
2. 在相同模型 / 相近 regime / 相近因子 bucket 中检索历史相似样本
3. 统计未来 5/10/20/60 日收益分布、回撤分布、失效率
4. 输出：
   - `P(正收益)`
   - `P(超越基准)`
   - `P(先触发失效)`
   - `P25/P50/P75` 收益区间

这样天然满足：

- 只用历史样本
- 可解释
- 可校准
- 易与训练闭环对接

### 6.2 V2：规则化情景引擎

在分布基础上，进一步输出三个情景：

- **Bull case**：触发条件 / 目标区间 / 失败前提
- **Base case**：中性演化路径
- **Bear case**：失效条件 / 风险扩散路径

每个情景都必须绑定触发条件，而不是靠语言描述模糊兜底。

### 6.3 V3：校准优先于复杂化

如果后续再做更复杂的概率模型，也应该优先优化：

- Brier score
- 区间命中率
- 方向准确率
- 失效条件及时性

而不是先追求更复杂的预测器。

---

## 7. 归因闭环该怎么升级

当前 `StrategyEvaluator` 可以保留，但只能算 V0。

我建议在 `OutcomeAttribution` 中拆成四层归因：

1. **Factor Attribution**
   - 当时看多/看空的主因子是谁
   - 事后是哪些因子失灵了

2. **Timing Attribution**
   - 结论方向对，但入场太早/太晚？
   - 是否在有效区间外追价？

3. **Risk Attribution**
   - 是 thesis 错了，还是风控没执行？
   - 失效后是否及时退出？

4. **Execution Attribution**
   - 若后续引入订单域对象，可继续追踪滑点/成交约束/仓位限制影响

这样训练和问股都能复用同一套复盘语言。

---

## 8. 建议的演进路线（避免大爆炸重构）

### Phase 0：冻结术语，先统一契约

新增统一研究域对象，但先不动大流程：

- `ResearchSnapshot`
- `PolicySnapshot`
- `ResearchHypothesis`
- `OutcomeAttribution`

同时明确旧对象与新对象的映射：

- `SignalPacket / AgentContext` → `ResearchSnapshot` 的一部分
- 训练模型 config sections → `PolicySnapshot`
- `dashboard + StrategyAdvice` → `ResearchHypothesis` 的不同视图
- `EvalReport + strategy_scores + benchmark + review` → `OutcomeAttribution`

### Phase 1：先让 ask_stock 接入训练内核

这是最值得优先做的一步。

改造原则：

- ask_stock 先解析 `as_of_date`
- 通过同一数据入口构造当前 universe
- 复用同一 routed model / active policy
- 读取 `ModelOutput`，提取目标股票的 rank、factor_values、evidence
- 保留现有 YAML tools 作为补充证据层

这个阶段就能立刻统一：

- 因子口径
- 风险口径
- regime 判断
- 模型版本

### Phase 2：把问股输出改为 `ResearchHypothesis`

替换当前 `dashboard` 的主地位，输出：

- stance
- percentiles
- probability / interval
- entry / invalidation / de-risk rules
- supporting / contradicting factors

`dashboard` 可以继续存在，但变成 UI projection。

### Phase 3：持久化研究 case，建立可证伪机制

每次 ask_stock 都落一条 case：

- `research_case_id`
- `ResearchSnapshot`
- `PolicySnapshot`
- `ResearchHypothesis`
- `evaluation_protocol`

等 horizon 到期或失效条件触发后，生成 `OutcomeAttribution`。

### Phase 4：训练吸收问股 case，形成统一校准集

训练集不再只有“训练周期结果”，还应包含：

- 实时问股样本
- 历史回放样本
- 模型主动选股样本
- 模型拒绝样本（很重要）

统一进入校准层后，再决定：

- 是否调整参数阈值
- 是否调整风险协议
- 是否调整情景概率估计

### Phase 5：最后再考虑更深的执行层统一

当研究层打通后，再推进：

- 订单语义对象化
- 执行事件流
- slippage / fee / buying power 插件化
- 实盘桥接

顺序不能反。

---

## 9. 具体到当前项目，我建议的模块切分

建议新增一个独立研究域，而不是把逻辑继续堆在 `app/train.py` 和 `app/stock_analysis.py` 里。

示意目录：

```text
invest/research/
├─ contracts.py              # ResearchSnapshot / PolicySnapshot / ResearchHypothesis / OutcomeAttribution
├─ snapshot_builder.py       # 从数据层与模型层构建 canonical snapshot
├─ policy_resolver.py        # 从 active model / routed model / runtime overrides 生成 PolicySnapshot
├─ hypothesis_engine.py      # 生成结构化研究结论（非 UI）
├─ scenario_engine.py        # 相似样本检索、收益分布、情景输出
├─ attribution_engine.py     # 后验归因、校准、回灌建议
├─ case_store.py             # 研究 case 持久化与读取
└─ renderers.py              # ask view / train view / web view
```

现有模块的定位建议：

- `app/train.py`
  - 保持 orchestration
  - 逐步把研究逻辑下沉到 `invest/research/`

- `app/stock_analysis.py`
  - 保留为问股入口
  - 逐步从“决策器”变成“研究引擎视图 + 证据探针编排器”

- `invest/models/*`
  - 保持策略家族定义
  - 继续承担 signal packet 生成，但未来要更自然地映射到 `ResearchSnapshot`

- `invest/meetings/*`
  - 继续承担多 agent 讨论与复盘
  - 但输入输出应逐步标准化为研究域对象

---

## 10. 你当前设想里最对的地方，和我会调整的地方

### 你最对的地方

- 不做大重构，先补中间层
- 先统一状态，再谈功能
- 训练更新 policy，问股读取 research + policy，结果再写 attribution

这个方向本质是对的。

### 我会调整的地方

#### 调整 1：`ResearchState` 最好改成 `ResearchSnapshot`

因为它应该是：

- 不可变
- 时点化
- 可复盘

“State” 容易让人误解成可变运行态。

#### 调整 2：必须补一个 `ResearchHypothesis`

如果只有三层状态，没有统一“研究结论对象”，问股和训练最后还是会各自产生自己的输出格式，割裂会复发。

#### 调整 3：问股要默认接入 active/routed model，而不是继续让 YAML 决定核心判断

YAML 更适合作为：

- 研究 lens
- 工具探针配置
- 展示模板

而不是策略真相来源。

#### 调整 4：归因层必须包含“校准”，不只是“复盘解释”

因为你明确要概率推演，那就必须回答：

- 这个概率准不准？
- 这个区间命中率如何？
- 哪些情景常常高估/低估？

否则问股只是换了个更好听的说法。

---

## 11. 最终建议：项目级优先级排序

### P0（立刻做）

1. 定义研究域统一契约
2. 给 ask_stock 增加 `as_of_date`
3. 让 ask_stock 读取 active/routed model 的 `ModelOutput`
4. 输出 queried symbol 的 rank / percentile / threshold gap / selected_by_policy

### P1（高价值）

1. 用统一 `PolicySnapshot` 替代 ask_stock 独立评分口径
2. 把 ask_stock 输出升级为 `ResearchHypothesis`
3. 保留 YAML，但降级为视图/探针 DSL

### P2（闭环成型）

1. 持久化 ask_stock case
2. 到期后自动生成 `OutcomeAttribution`
3. 把问股 case 纳入训练校准集

### P3（能力跃迁）

1. 引入相似样本分布引擎
2. 输出概率、区间和三情景结果
3. 形成真正的可证伪概率研究体系

---

## 12. 一句话总结

**训练负责学习策略，问股负责投影策略；但二者必须共享同一份 `ResearchSnapshot + PolicySnapshot + ResearchHypothesis + OutcomeAttribution`。**

如果只能给一个最重要的落地点，我的答案是：

> **先让 ask_stock 默认读取当前训练/路由中的真实 model output，再把 YAML 从“判断内核”降级成“视图 DSL”。**

这是当前项目里最小改动、最大统一收益、也最不容易走偏的一步。
