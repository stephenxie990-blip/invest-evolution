# QuantConnect LEAN 指标库 + 订单管理 深度分析与本项目融合评估

- 分析日期：2026-03-11
- 本地快照：`external/lean` @ commit `0c4a121`
- 上游仓库：[QuantConnect/Lean](https://github.com/QuantConnect/Lean)
- 分析范围：
  - LEAN 的 `Indicators`、`Common/Indicators`
  - LEAN 的 `Common/Orders`、`Common/Securities`、`Common/Brokerages`、`Engine/TransactionHandlers`
  - 本项目的 `app/`、`invest/`、`market_data/` 中与信号、交易计划、模拟执行、训练闭环、Commander 调度相关部分
- 结论先行：
  1. LEAN 的**指标库**非常适合融入本项目，但应采用“Python 原生重构 + API 借鉴”方式，而不是直接嵌入 C# 实现。
  2. LEAN 的**订单管理**成熟度远高于本项目当前执行层，但不适合整套移植；最优路径是按分层架构逐步吸收：`OrderRequest → Order → Fill/Fee/Slippage/BuyingPower → Portfolio Update → OrderEvent`。
  3. 若未来希望 Commander 成为自然语言唯一入口，那么 LEAN 的价值主要不在 UI，而在于给 Commander 后端提供**可解释、可审计、可回放**的执行内核。

---

## 1. LEAN 指标库深度

### 1.1 规模与成熟度

本地统计结果：

- `external/lean/Indicators` 文件数：231
- `external/lean/Indicators/CandlestickPatterns` 文件数：64
- `external/lean/Tests/Indicators` 文件数：156

这说明 LEAN 的指标层不是几组常见均线函数，而是一个完整的、经过长期回测框架验证的指标子系统，包含：

- 趋势类：SMA、EMA、WMA、DEMA、TEMA、HMA、KAMA 等
- 动量类：RSI、MACD、ROC、Stochastic、TRIX、Momentum 等
- 波动率类：ATR、STD、BollingerBands、Donchian 等
- 成交量类：OBV、MFI、AccumulationDistribution 等
- 形态类：大量蜡烛图形态
- 复合类 / 派生类：由多个指标相互组合形成的链式指标

### 1.2 指标基础设施的关键设计

LEAN 指标层的核心不是“指标很多”，而是**指标抽象非常统一**。

#### a) `IndicatorBase`

`external/lean/Indicators/IndicatorBase.cs:29` 定义了指标基类，关键能力包括：

- `Current`：当前值
- `Previous`：前一个值
- `Samples`：已处理样本数
- `IsReady`：预热是否完成
- `Window`：滚动窗口
- `Updated`：更新事件
- `Update(IBaseData input)`：统一更新入口
- `Reset()`：状态复位

这意味着 LEAN 的每个指标天然具备：

- 增量更新能力
- 滚动历史可见性
- readiness 管理
- 可组合的流式计算语义

#### b) `RollingWindow<T>`

`external/lean/Common/Indicators/RollingWindow.cs:45` 提供通用滚动窗口容器，指标和策略无需自己管理最近 N 个点的缓存。

这点对本项目特别重要，因为本项目当前技术指标实现更偏“一次性算一个结果”，而不是可持续增量更新。

#### c) `CompositeIndicator`

`external/lean/Indicators/CompositeIndicator.cs:32` 允许多个指标组合为一个复合指标。

这意味着 LEAN 的指标可以不是孤立函数，而是形成计算图。比如：

- EMA 输入到 MACD
- RSI + MA + 波动率组合成二级信号
- 自定义信号器建立在现有指标之上

#### d) 代表性指标实现

`external/lean/Indicators/ExponentialMovingAverage.cs:1` 展示了 LEAN 的典型指标实现方式：

- 通过状态而不是整段回看反复全量计算
- 明确 warm-up / ready 语义
- 与基类一致的 update/reset 行为

### 1.3 LEAN 指标库对本项目的直接启发

本项目当前指标层主要是函数式、离线式、摘要式：

- `invest/foundation/compute/indicators.py:26` 提供 `calc_rsi`
- `invest/foundation/compute/indicators.py:39` 提供 `calc_macd_signal`
- `invest/foundation/compute/indicators.py:59` 提供 `calc_bb_position`
- `invest/foundation/compute/indicators.py:73` 提供 `calc_volume_ratio`
- `invest/foundation/compute/features.py:19` 在股票摘要计算中一次性拼出 `close / RSI / MACD / algo_score`

当前实现的问题不是“不能用”，而是**抽象层级偏低**：

1. 更像最终信号函数，不像可复用指标对象
2. 缺少统一的 warm-up / is_ready / reset 语义
3. 缺少指标链式组合与事件更新能力
4. 对实时 / 增量 / 流式分析不够友好
5. 不利于 Commander 在自然语言工具编排时复用同一套指标对象

**结论：指标层融合价值很高，而且是低风险高收益。**

---

## 2. LEAN 订单管理深度

### 2.1 规模与成熟度

本地统计结果：

- `external/lean/Common/Orders` 文件数：125
- `external/lean/Common/Securities` 文件数：268
- `external/lean/Common/Brokerages` 文件数：44
- 订单相关测试命中数：240

这反映出 LEAN 的订单管理并不是“下单函数 + 成交回调”，而是一整套交易引擎：

- 订单对象模型
- 请求对象模型
- 交易管理器
- 经纪商模型
- 回测撮合模型
- 手续费模型
- 滑点模型
- 购买力 / 保证金模型
- 证券持仓更新模型
- 事件与票据（ticket）

### 2.2 订单域对象层

#### a) `Order`

`external/lean/Common/Orders/Order.cs:31` 是订单基类。LEAN 的订单不是简单 dict，而是具有类型、方向、数量、状态、时间、标签、BrokerId 等统一字段。

#### b) `OrderTicket`

`external/lean/Common/Orders/OrderTicket.cs:29` 提供订单票据抽象，用来追踪：

- 订单是否已提交
- 是否被取消 / 更新
- 请求结果
- 事件流

这给运行中的 Agent / Commander 提供了天然的“可追踪性”。

#### c) `OrderEvent`

`external/lean/Common/Orders/OrderEvent.cs:30` 描述订单生命周期事件，如：

- Submitted
- PartiallyFilled
- Filled
- Canceled
- Invalid

这正是本项目现在相对欠缺的部分：目前更偏“最终 trade_history 结果”，而不是完整“订单生命周期事件流”。

#### d) 请求对象：Submit / Update / Cancel

- `external/lean/Common/Orders/SubmitOrderRequest.cs:24`
- `external/lean/Common/Orders/UpdateOrderRequest.cs:23`
- `external/lean/Common/Orders/CancelOrderRequest.cs:23`

LEAN 将“用户/策略想做什么”与“系统中的订单实体”分离：

- Request = 意图
- Order = 系统接纳后的订单对象

这层抽象对本项目非常关键，因为本项目当前只有一个很薄的 `OrderIntent`：

- `invest/foundation/engine/order.py:8`

但这个 `OrderIntent` 还没有被扩展成完整订单生命周期。

### 2.3 事务管理 / 订单路由层

#### a) `SecurityTransactionManager`

`external/lean/Common/Securities/SecurityTransactionManager.cs:30` 负责证券级交易管理。

#### b) `BrokerageTransactionHandler`

`external/lean/Engine/TransactionHandlers/BrokerageTransactionHandler.cs:844` 是请求处理关键入口。其职责包括：

- 分发 submit/update/cancel
- 检查参数合法性
- 规范化价格与数量
- 检查 buying power
- 检查 brokerage 是否支持该订单
- 正式下单并管理状态

这是一条完整的订单受理链，而不只是“策略直接改仓位”。

### 2.4 回测成交 / 成本 / 约束层

#### a) 回测经纪商撮合

`external/lean/Brokerages/Backtesting/BacktestingBrokerage.cs:233` 到 `external/lean/Brokerages/Backtesting/BacktestingBrokerage.cs:335` 展示了回测撮合的核心链路：

- 扫描挂单
- 再做 buying power 检查
- 调用 `security.FillModel.Fill(...)`
- 调用手续费模型
- 生成 `OrderEvent`
- 推进订单状态与后续组合更新

#### b) `FillModel`

`external/lean/Common/Orders/Fills/FillModel.cs:30` 定义成交模型基类。

这意味着“订单是否成交、以什么价格成交、是否部分成交”是一个独立插件层，而不是写死在回测循环里。

#### c) `FeeModel`

`external/lean/Common/Orders/Fees/FeeModel.cs:25` 定义手续费模型基类。

#### d) `BuyingPowerModel`

`external/lean/Common/Securities/BuyingPowerModel.cs:27` 定义购买力 / 保证金 / 可开仓能力检查。

#### e) `IBrokerageModel`

`external/lean/Common/Brokerages/IBrokerageModel.cs:34` 定义经纪商规则：

- 支持哪些订单类型
- 证券对应哪些 fill/slippage/fee/buying power 模型
- 不同市场规则差异

#### f) Brokerage → Security 模型注入

`external/lean/Common/Securities/BrokerageModelSecurityInitializer.cs:26` 负责把经纪商模型注入到证券实例，使单个证券获得对应的交易规则集。

### 2.5 持仓与组合更新层

`external/lean/Common/Securities/SecurityPortfolioModel.cs:31` 负责成交后：

- 更新持仓数量
- 更新持仓均价
- 处理平仓收益
- 反映到账户 / 组合层

这让 LEAN 的状态推进是**订单事件驱动**的，而非“策略循环内顺手改 cash + shares”。

---

## 3. 本项目当前执行层现状

### 3.1 指标与信号层现状

本项目当前技术指标层主要是轻量函数组合：

- `invest/foundation/compute/indicators.py:26`
- `invest/foundation/compute/indicators.py:39`
- `invest/foundation/compute/indicators.py:59`
- `invest/foundation/compute/indicators.py:73`
- `invest/foundation/compute/features.py:19`

优点：

- 简洁
- 可读
- 低依赖
- 对离线批量选股足够实用

限制：

- 缺少统一指标对象抽象
- 不支持面向 Commander 的细粒度工具编排
- 很难表达多级指标链
- 不适合扩展到“策略 YAML + ReAct 工具调用 + 实时增量更新”架构

### 3.2 交易计划层现状

本项目已经有“计划”概念，但还停留在策略意图层：

- `invest/shared/contracts.py:25` 定义 `TradingPlan`
- `invest/shared/contracts.py:53` 的 `make_simple_plan()` 生成简化交易计划
- `invest/foundation/engine/order.py:8` 定义了很薄的 `OrderIntent`

这说明项目已经具备 LEAN 风格改造的入口，但当前计划层仍缺少：

- 订单状态机
- 订单票据
- 请求/订单分离
- 更新单 / 撤单
- 部分成交
- 明确的订单事件流

### 3.3 模拟成交层现状

当前模拟引擎的核心在 `SimulatedTrader`：

- `invest/foundation/engine/simulator.py:18`
- `invest/foundation/engine/simulator.py:202` `buy()`
- `invest/foundation/engine/simulator.py:288` `sell()`
- `invest/foundation/engine/simulator.py:357` `check_and_close_positions()`
- `invest/foundation/engine/simulator.py:422` `check_portfolio_risk()`
- `invest/foundation/engine/simulator.py:490` `_execute_plan_step()`
- `invest/foundation/engine/simulator.py:549` `step()`
- `invest/foundation/engine/simulator.py:627` `run_simulation()`

当前引擎已经具备：

- 滑点、佣金、印花税
- T+1
- 固定止损 / 止盈 / 跟踪止盈
- ATR 动态止损
- 组合回撤风控
- 异常事件处理

这是一个**不错的轻量回测执行器**，但与 LEAN 相比还明显偏“过程内联”：

- 买卖逻辑直接在 `SimulatedTrader` 内计算
- fee/slippage/buying power 没有独立模型接口
- 没有显式订单簿 / pending order / ticket / order event stream
- 不支持丰富订单类型（目前本质是近似市价执行）
- 计划执行更接近“目标持仓执行”，而不是“订单路由系统”

### 3.4 风控层现状

风控层已经相对独立，这是一个很好的基础：

- `invest/foundation/risk/controller.py:366` `PortfolioRiskManager.check_portfolio_risk()`
- `invest/foundation/risk/controller.py:415` `RiskController`

这意味着未来把 LEAN 风格 `BuyingPowerModel / Risk Gate / Brokerage Rules` 融进来时，不必完全推倒重来。

### 3.5 训练与闭环层现状

训练闭环由 `SelfLearningController` 组织：

- `app/train.py:295` `SelfLearningController`
- `app/train.py:1224` 附近：路由/市场状态/选股会议
- `app/train.py:1368` 创建 `SimulatedTrader`
- `app/train.py:1438` 执行 `run_simulation()`
- `app/train.py:1523` 做策略评估
- `app/train.py:1568` 后进入复盘会议

运行路径大致是：

1. 市场状态识别 / allocator / router
2. SelectionMeeting 产出候选
3. 形成 `TradingPlan`
4. `SimulatedTrader` 跑 30 天模拟
5. `StrategyEvaluator` / benchmark 评估
6. ReviewMeeting 调参数

也就是说，本项目已经有完整训练闭环，但**执行层抽象还不够深**。

### 3.6 Commander 现状

Commander 已经是系统级 orchestration runtime，而不仅是聊天壳：

- `app/commander.py:600` `InvestmentBodyService`
- `app/commander.py:706` `run_cycles()`
- `app/commander.py:945` `CommanderRuntime`
- `app/commander.py:1145` `start()`
- `app/commander.py:1205` `ask()`
- `app/commander.py:1296` `create_training_plan()`
- `app/commander.py:1435` 起：数据状态、资金流、龙虎榜、60m、训练计划、训练运行、配置、控制面工具
- `app/commander.py:1699` `status()`
- `app/commander.py:1776` `_register_fusion_tools()`

Commander 的监控与可观测性也已存在：

- `app/commander_observability.py:246` `build_runtime_diagnostics()`
- `app/commander_observability.py:289` `build_training_lab_overview()`
- `app/commander_observability.py:303` `build_strategy_detail()`
- `app/commander_observability.py:402` `build_training_run_detail()`
- `app/commander_observability.py:468` `build_training_evaluation_detail()`

同时，Commander 已经内置了一版轻量 “问股 + YAML + ReAct-like” 能力：

- `app/stock_analysis.py:77` `StockAnalysisService`
- `app/stock_analysis.py:96` 工具注册：`get_daily_history / get_realtime_quote / analyze_trend`
- `app/stock_analysis.py:321` `_run_react_executor()`
- `app/stock_analysis.py:363` `_execute_plan_deterministic()`
- `app/stock_analysis.py:387` `_execute_plan_with_llm()`
- `app/stock_analysis.py:816` 起的默认策略 YAML 模板

**这说明：Commander 已经具备接入 LEAN 风格执行内核的入口，只是底层执行器还不是 LEAN 级别。**

---

## 4. LEAN 两大能力与本项目的融合适配度

## 4.1 指标库融合适配度：高

### 适合融合的原因

1. **语言边界可控**
   - 不需要把 C# 引擎整套搬进 Python。
   - 可以直接借鉴 LEAN 的抽象设计，在 Python 里重建统一指标接口。

2. **和 Commander / ask stock / 训练体系天然兼容**
   - Commander 需要可调用工具；指标对象可以直接成为工具后端。
   - ask stock 的 YAML + ReAct 流程很适合按“指标工具”细粒度编排。
   - 训练引擎可复用同一套指标定义，避免研究与执行指标不一致。

3. **增量收益很大**
   - 可以快速把当前轻量信号层升级为真正的指标工厂。
   - 为后续实时、分钟级、多资产留出抽象余量。

### 推荐融合方式

不要把 LEAN 指标代码直接嵌入本项目，而是：

- 借鉴 `IndicatorBase + RollingWindow + CompositeIndicator + IsReady + Reset`
- 在 Python 内新增一层 `invest/foundation/indicators_v2/` 或类似目录
- 逐步把当前 `calc_*` 函数升级为：
  - `RSIIndicator`
  - `EMAIndicator`
  - `MACDIndicator`
  - `ATRIndicator`
  - `BollingerBandsIndicator`
  - `VolumeRatioIndicator`
- 再提供适配器，使旧的 `compute_stock_summary()` 仍能调用新层

### 第一批最值得迁移的指标

建议优先级：

1. `EMA / SMA / WMA`
2. `MACD`
3. `RSI`
4. `ATR`
5. `BollingerBands`
6. `ROC / Momentum`
7. `Volume` 派生指标

原因：这些是当前系统选股、风控、问股、训练复盘都会直接用到的公共基础设施。

## 4.2 订单管理融合适配度：中高，但必须分阶段

### 为什么价值高

LEAN 的订单系统能补齐本项目当前执行层的关键短板：

- 显式订单生命周期
- 多订单类型
- 部分成交
- 独立 fill / fee / slippage / buying power 模型
- 经纪商规则抽象
- 可回放、可追踪的订单事件流

### 为什么不能直接整套搬

1. **语言栈不同**
   - LEAN 是 C# 主引擎；本项目主干是 Python。
   - 直接嵌入意味着跨语言边界、部署复杂度、调试成本都会急剧升高。

2. **系统规模不匹配**
   - 本项目当前核心优势是：训练闭环 + Agent 协作 + Commander 调度。
   - 若完整引入 LEAN 交易引擎，会让系统重心从“智能研究与训练”转向“通用量化底座”。

3. **维护成本极高**
   - 直接 sync 上游 LEAN 版本几乎不可控。
   - 本项目后续迭代需要的是“可定制的 Python 执行层”，而不是被大型外部引擎绑死。

### 推荐融合方式

应吸收**架构思想**和**接口分层**，不要整套移植实现。

推荐路线：

#### Phase A：订单域对象化

把当前执行层从 `TradingPlan -> buy()/sell()`，升级为：

- `OrderRequest`
- `Order`
- `OrderTicket`
- `OrderEvent`
- `ExecutionReport`

这样 Commander、训练器、问股器、未来实盘桥接都能使用同一套订单语义。

#### Phase B：执行模型插件化

把 `SimulatedTrader` 中现在内联的执行逻辑拆分成：

- `FillModel`
- `FeeModel`
- `SlippageModel`
- `BuyingPowerModel`
- `TimeInForce / T+1 Rule`

先实现 A 股回测版本即可。

#### Phase C：组合更新事件化

将目前直接修改 `cash / positions / trade_history` 的逻辑，重构为：

- 订单成交 -> 生成 `OrderEvent`
- `PortfolioModel` 消费事件 -> 更新持仓与资金
- `TradeLedger` 记录成交与生命周期

#### Phase D：订单类型扩展

在市价单基础上逐步支持：

- LimitOrder
- StopMarketOrder
- StopLimitOrder
- TrailingStopOrder
- Bracket / OCO（若业务需要）

#### Phase E：实盘桥接

当 Commander 未来接第三方券商 / OMS / Paper Trading 时，再加：

- `BrokerageModel`
- `BrokerAdapter`
- `CapabilityMatrix`

这时才有必要做真正的实盘订单桥接。

---

## 5. 逐项映射：LEAN 能力 vs 本项目现状

| 维度 | LEAN | 本项目现状 | 结论 |
|---|---|---|---|
| 指标抽象 | `IndicatorBase` + `Window` + `IsReady` | 以 `calc_*` 函数为主 | 需要升级 |
| 指标组合 | `CompositeIndicator` | 基本无统一组合层 | 需要升级 |
| 指标增量更新 | 原生支持 | 主要按 DataFrame 全量计算 | 需要升级 |
| 策略调用指标 | 统一接口 | 工具层和训练层部分割裂 | 需要统一 |
| 请求/订单分离 | `SubmitOrderRequest` 等 | `OrderIntent` 很薄 | 明显缺失 |
| 订单票据 | `OrderTicket` | 无 | 缺失 |
| 订单事件 | `OrderEvent` | 只有 `TradeRecord` 结果记录 | 缺失 |
| 多订单类型 | 丰富 | 近似市价单 | 缺失 |
| Fill 模型 | 独立插件 | 内联在 `buy/sell` | 需拆分 |
| Fee 模型 | 独立插件 | 手续费写在执行函数内 | 需拆分 |
| Buying power | 独立模型 | 仅简单现金/仓位判断 | 需升级 |
| Brokerage rules | 独立模型 | 规则零散在执行逻辑和风控逻辑中 | 需统一 |
| Portfolio 更新 | `SecurityPortfolioModel` | `SimulatedTrader` 直接更新 | 需事件化 |
| Commander 统一调度 | 可作为上层整合点 | 已具备很强 runtime 能力 | 可直接承接 |

---

## 6. 对 Commander 的具体意义

如果未来把 Commander 作为自然语言唯一入口，那么 LEAN 的这两部分融入价值如下：

### 6.1 指标库融入后

Commander 可直接支持：

- “用 MACD + ATR + 布林带分析宁德时代”
- “给我看近 90 天 EMA20/EMA60 金叉情况”
- “把这个 YAML 策略里的指标全部跑一遍”
- “把训练和问股都统一到同一套指标定义”

这会显著减少现在 `ask stock` 与训练内核之间的能力鸿沟。

### 6.2 订单管理融入后

Commander 可支持真正的执行级自然语言：

- “给这个训练计划生成订单草案”
- “如果按限价单 + 2% 滑点保护执行，结果会怎样”
- “回放第 18 轮训练的所有订单生命周期”
- “解释为什么这笔交易被拒绝开仓”
- “把止损单改成 trailing stop 再回测”

也就是说，Commander 将不只是训练编排器，而会成为：

- 研究入口
- 交易执行解释器
- 订单监控面板的对话入口
- 回测 / 训练 / 实盘统一控制台

---

## 7. 推荐融合蓝图

## 7.1 建议做的

### 蓝图一：先做指标层统一

新增一层 Python-native 指标框架：

- `BaseIndicator`
- `RollingWindow`
- `CompositeIndicator`
- `IndicatorRegistry`
- `IndicatorToolAdapter`

让以下系统统一依赖它：

- `app/stock_analysis.py`
- `invest/foundation/compute/features.py`
- `invest/models/*`
- `invest/agents/*`
- `app/train.py`

### 蓝图二：再做订单域建模

新增执行域对象：

- `OrderRequest`
- `Order`
- `OrderTicket`
- `OrderEvent`
- `ExecutionContext`
- `ExecutionReport`

先在回测模式跑通，再考虑 Commander 暴露工具。

### 蓝图三：重构 `SimulatedTrader`

把当前 `SimulatedTrader` 从“大而全执行器”改为“协调器”：

- 接受 `TradingPlan`
- 生成 `OrderRequest`
- 交给 `ExecutionEngine`
- `ExecutionEngine` 调用 fill/fee/slippage/buying_power 模型
- `PortfolioModel` 根据 `OrderEvent` 更新状态

### 蓝图四：把 Commander 的 ask stock 升级为真正 YAML + ReAct 工具图

当前 `app/stock_analysis.py:321` 已经有雏形，但工具仍然偏少、偏摘要。融合指标层后，应扩成：

- 数据工具：历史 K 线、实时行情、分钟线、资金流、龙虎榜、财务摘要
- 指标工具：EMA/RSI/MACD/ATR/Bollinger/Volume/ROC
- 结构工具：趋势识别、波动率、支撑阻力、形态
- 执行工具：生成订单草案、估算成交、计算风控阈值、回放订单

这样 Commander 的对话入口才能真正承载“研究 + 训练 + 执行解释”。

## 7.2 不建议做的

### 不建议 1：直接把 LEAN 整个引擎嵌入本项目

原因：

- 语言/部署复杂度过高
- 维护成本过高
- 会削弱当前项目的 Python/Agent/Commander 一致性

### 不建议 2：一开始就追求 LEAN 全量订单类型

建议先聚焦：

- Market
- Limit
- StopMarket
- TrailingStop

先把生命周期、审计、事件流做对，再扩订单族。

### 不建议 3：先接实盘券商，再补执行内核

顺序应当相反：

1. 先规范订单语义
2. 再完善回测执行模型
3. 再做 paper trading
4. 最后才接真实 broker adapter

---

## 8. 最终评估

### 8.1 LEAN 指标库是否值得融合？

**值得，且应优先融合。**

- 适配度：高
- 技术风险：中低
- 收益：高
- 对 Commander 的提升：直接且明显

### 8.2 LEAN 订单管理是否值得融合？

**值得融合其架构，不值得直接移植其整套实现。**

- 适配度：中高
- 技术风险：中高
- 收益：高
- 正确方式：分层吸收、Python 原生重构

### 8.3 对本项目的最佳路线

推荐优先级：

1. **先融合 LEAN 指标抽象**
2. **再重构订单域对象与事件流**
3. **再拆分执行模型（fill/fee/slippage/buying power）**
4. **最后把 ask stock / Commander 升级为真正的 YAML + ReAct 工具编排入口**

### 8.4 一句话结论

本项目最应该借鉴 LEAN 的，不是“完整量化平台外壳”，而是两层底座：

- **指标底座**：统一研究、问股、训练、风控的计算语义
- **订单底座**：统一模拟、执行解释、审计、回放、未来实盘桥接的执行语义

Commander 则负责把这两层底座包装成自然语言唯一入口。

---

## 9. 下一步建议

如果继续推进，我建议下一步直接进入两个交付物之一：

1. **架构设计稿**：输出“本项目 Python 版 LEAN-like 指标层 + 订单层”的目录结构、类图、数据流和 API 契约。
2. **Phase 1 实施**：先落地指标层 v2，并把 `ask stock` 升级为可调用更多指标工具的 YAML + ReAct 编排版本。

