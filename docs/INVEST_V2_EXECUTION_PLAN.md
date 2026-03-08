# 投资进化系统 v2.0 总体升级 Master Plan

## 0. 计划定位

## 0.1 执行状态（2026-03-08）

当前仓库已经完成本方案的第一轮落地，状态为：**v2.0 升级完成并通过验证**。

### 已落地产物
- 新增 `invest/contracts/`：统一 `SignalPacket`、`AgentContext`、`ModelOutput`、`StrategyAdvice`、`TradeRecord`、`EvalReport` 等跨层契约。
- 新增 `invest/foundation/`：将计算逻辑分层为 `compute/`、`engine/`、`risk/`、`metrics/`，并由旧模块兼容委托到该底座。
- 新增 `invest/models/`：落地 `InvestmentModel` 基类、`MomentumModel`、`context_renderer`、模型注册表与 `configs/momentum_v1.yaml`。
- 改造 `invest/agents/` 与 `invest/meetings/`：新增以 `AgentContext` / `EvalReport` 为输入的新入口，使推理层与计算层解耦。
- 改造 `app/train.py`、`app/commander.py`、`app/web_server.py`：新 Pipeline 已可加载模型、执行选股会议、交易、评估、复盘，并对外暴露激活模型配置。
- 改造 `invest/evolution/`：新增 `mutators.py`，进化对象切换到 YAML 配置与叙事变体。

### 已完成验证
- 契约/模型/会议桥接/Web/API/依赖守卫/YAML 变异测试全部已补齐并通过。
- 全量回归：`uv run pytest -q` 通过。
- 语法校验：`uv run python -m compileall app invest config train.py commander.py web_server.py` 通过。
- 训练验证：
  - `INVEST_DISABLE_LIVE_LLM=1 uv run python train.py --cycles 1 --mock --log-level WARNING` 通过。
  - `INVEST_DISABLE_LIVE_LLM=1 INVEST_FORCE_CUTOFF_DATE=20211221 uv run python train.py --cycles 1 --log-level WARNING` 通过。
- 真实训练产物验证：
  - `runtime/logs/meetings/selection/meeting_0001.json` 显示 `model_name=momentum`、`config_name=momentum_v1`。
  - `runtime/outputs/training/cycle_1.json` 已落盘选股、收益、复盘与优化事件。
  - `data/evolution/generations/momentum_v1_cycle_0999.yaml` 已生成，证明 YAML 进化链路可用。

### 质量门结论
### Pure v2-only Cutover（已完成）
- 已删除 legacy 目录：`invest/selection/`、`invest/trading/`、`invest/evaluation/`。
- 已删除兼容壳：`invest/optimization.py`、`invest/core.py`。
- 已移除 `enable_v2_pipeline` 配置与 API 语义，v2 流水线改为唯一正式路径。
- 已将交易与评估核心实现收敛到 `invest/foundation/`，因此纯化后训练与测试仍可完整通过。
- 已用 `tests/test_structure_guards.py` 与 `tests/test_architecture_import_rules.py` 将“旧目录不存在”固化为结构守卫。

- 架构边界门：通过 `tests/test_architecture_import_rules.py` 守卫层间 import 方向。
- 测试门：专项测试与全量回归均通过。
- 运行门：mock 与真实数据 smoke 均通过。
- 文档门：`task_plan.md`、`findings.md`、`progress.md` 与本 master plan 已同步更新。
- 工具链备注：当前环境未安装 `ruff` / `pyright`，因此本轮静态门以 `compileall + pytest` 实施，不阻塞 v2.0 完成认定。


这不是一份“重构建议”，而是一份以 **系统整体升级到 v2.0** 为目标的完整实施方案。

本方案覆盖：
- 升级目标与完成定义
- 目标架构与依赖规则
- 分阶段实施安排
- 每阶段的代码范围、产物、测试收口、质量控制
- 并行编排 / subagent 使用建议
- 迁移期间的新旧兼容策略、回滚策略、风险控制
- 最终切换到 v2.0 的验收口径

一句话定义本次升级：

> 把“写死在代码里的策略判断”拆成三部分：
> **foundation 负责算事实，models 负责定义视角，agents 负责推理判断，orchestration 负责装配执行。**

---

## 1. v2.0 升级目标与 Done Definition

## 1.1 北极星目标

把当前“按功能拆目录但逻辑仍混合”的系统，升级为：

```text
L0 Data          market_data/
L1 Foundation    invest/foundation/
L2 Models        invest/models/
L3 Agents        invest/agents/ + invest/meetings/
L4 Orchestration commander.py / train.py / web_server.py
Cross-layer      invest/contracts/
```

## 1.2 v2.0 必须达成的 8 个结果

1. `market_data/` 继续保持独立稳定，不感知上层策略。
2. `invest/contracts/` 成为跨层统一语言，替代分散 contracts。
3. `invest/foundation/` 只做纯计算，不含策略偏好和上层推理。
4. `invest/models/` 落地，至少有一个可运行的 `MomentumModel`。
5. `invest/agents/` 和 `invest/meetings/` 只消费 `AgentContext` / `EvalReport`，不再直接算指标。
6. `train.py` / `commander.py` / `web_server.py` 切到统一 Pipeline。
7. `invest/evolution/` 变异对象从“代码参数/隐式逻辑”切换为“YAML 配置 + 叙事模板”。
8. 新增策略时，默认路径是“新增 YAML + 新增/复用 Model 类”，而不是在 Agent、Trader、Meeting 里散改。

## 1.3 Done Definition（最终收口标准）

只有满足下面全部条件，才能认为 v2.0 升级完成：

### 架构完成
- `invest/contracts/`、`invest/foundation/`、`invest/models/` 均已落地。
- 层间依赖守卫测试通过。
- `invest/__init__.py` 已收口为稳定导出，而不是继续扁平暴露所有内部实现。

### 行为完成
- 基于 `MomentumModel + momentum_v1.yaml` 的新链路可跑通完整训练。
- Agent 只接收 `AgentContext`，不再直接依赖原始 K 线与技术指标函数。
- Evolution 可以自动生成下一代 YAML 变体并持久化快照。

### 质量完成
- 各 Phase 定义的 capability / regression eval 全部通过。
- 关键路径测试通过：contracts、foundation parity、model output、pipeline、web/config、evolution。
- 至少完成一次 dry run 和一次真实数据训练 smoke。

### 迁移完成
- 旧兼容路径只保留明确标注的兼容出口，或被删除。
- 文档、测试、配置示例与运行入口已经全面更新到 v2.0 语义。

---

## 2. 当前系统现状与核心差距

## 2.1 当前已经具备的基础

当前仓库并不是从 0 开始，已经有若干非常好的基础：

- `market_data/` 边界相对清晰，可作为稳定 L0。
- `invest/` 已按功能拆成 `agents/`、`meetings/`、`trading/`、`evaluation/`、`selection/`、`evolution/`。
- 训练中心、记忆中心、Web API、会议记录、数据库升级等近期工作说明系统具备持续演进能力。
- 测试资产较完整，已有结构守卫、数据流、交易、评估、训练、Web API、会议等测试。

## 2.2 与 v2.0 的关键差距

| 维度 | 当前状态 | v2.0 目标 | 差距性质 |
|---|---|---|---|
| 契约 | 分散在 `shared/contracts.py`、`trading/contracts.py`、评估对象、meeting 输出 | 统一到 `invest/contracts/` | 高 |
| 纯计算 | 指标/风控/评估散落在 Agent、Trader、Selector | 统一进 `foundation/` | 高 |
| 模型层 | 尚未存在独立 `models/` | 用 YAML + Model 类表达策略 | 高 |
| Agent 层 | 仍夹带阈值、统计、部分参数裁剪 | 只做 LLM 推理 | 高 |
| 编排层 | 仍绑定旧对象流转 | 统一围绕 `ModelOutput` Pipeline | 中高 |
| 进化对象 | 仍偏向内部代码参数 | 变异 YAML + narrative | 高 |
| API 导出 | `invest/__init__.py` 过宽 | 稳定且收口 | 中 |
| 迁移治理 | 有蓝图，但缺项目级阶段门控 | 有统一质量门与阶段验收 | 高 |

## 2.3 当前最重要的耦合点

本次升级最危险的不是代码量，而是这些隐藏耦合：

1. **默认阈值散落**：如止损、止盈、风险阈值存在于 Trader、Risk、Agent、Meeting 多处。
2. **契约与逻辑混写**：`TradingPlan` 一类对象和默认策略生成逻辑耦合在一起。
3. **Agent 不纯**：Agent 仍直接参与指标、规则、阈值逻辑。
4. **训练入口绑定旧语义**：`train.py`、`commander.py`、`web_server.py` 尚未围绕新 contracts 编排。
5. **扁平导出**：`invest/__init__.py` 使内部模块很容易被跨层随意调用。

---

## 3. 架构原则与依赖红线

## 3.1 分层原则

### L0 数据层：稳定事实源
- 只负责原始数据的获取、校验、存储、读取。
- 不知道策略，不做衍生判断。

### L1 能力底座：纯计算
- 接收数据 + 参数，返回数值或结构化计算结果。
- 不包含投资偏好、阈值判断、LLM 推理。

### L2 模型层：策略视角
- 决定用哪些因子、参数和叙事模板。
- 调用 foundation，输出 `SignalPacket + AgentContext`。

### L3 Agent 层：纯推理
- 只消费叙事上下文和评估报告。
- 输出建议、辩论、复盘意见和会议决议。

### L4 编排层：装配与驱动
- 管谁先谁后、组件如何装配、事件如何暴露。
- 不承载业务计算和策略判断。

## 3.2 依赖规则

允许依赖：

```text
L4 -> L3/L2/L1/L0/contracts
L3 -> contracts/shared llm
L2 -> L1/L0/contracts
L1 -> L0/contracts
L0 -> 无上层依赖
contracts -> 不依赖业务层
```

禁止依赖：

```text
L1 -> L2/L3
L2 -> L3
L3 -> L1 直接计算模块
contracts -> 任何业务层
```

## 3.3 迁移原则

1. **契约先行，代码后迁**。
2. **compat adapter 先行，旧入口后删**。
3. **每个 Phase 结束都必须可运行、可回滚、可验证**。
4. **从 Phase 1 起，任何新增计算逻辑只能进入 `foundation/`**。
5. **从 Phase 2 起，任何新增策略参数只能进入 `models/configs/*.yaml` 或 Model 类**。

---

## 4. 项目治理：skills、工作方式、质量门

## 4.1 主线 skills

| Skill | 用途 | 使用时机 |
|---|---|---|
| `pi-planning-with-files` | 维护主计划、发现、进度文件 | 整个项目全程 |
| `agentic-engineering` | 用 eval-first 和 15 分钟任务单元拆工作 | 每个 Phase |
| `eval-harness` | 为每阶段先定义 capability/regression eval | 每个 Phase 开始前 |
| `verification-loop` | 统一执行 build/test/diff review | 每个 Phase 完成后 |

## 4.2 实施配套 skills

| Skill | 用途 |
|---|---|
| `python-patterns` | 模块/API 设计、类型、纯函数边界 |
| `python-testing` | 单元、集成、fixture、parity、contract 测试 |
| `tdd-workflow` | 核心迁移点采用先测再改 |
| `coding-standards` | 命名、导入、职责边界统一 |
| `backend-patterns` | 编排层/Web/API 切换阶段辅助使用 |

## 4.3 质量门（所有 Phase 通用）

### Gate A：代码边界门
- 不新增违反层间依赖的 import。
- 不把新逻辑继续塞回旧层。
- 不在 Agent 层新增计算与硬编码阈值。

### Gate B：测试门
- 该 Phase 的 capability eval 全过。
- Baseline regression 至少全过。
- 关键受影响目录的定向回归通过。

### Gate C：运行门
- dry run 能跑通。
- 如改动影响训练主链，则至少完成一次端到端 smoke。

### Gate D：文档门
- 文档、配置示例、迁移说明同步更新。
- `task_plan.md` / `findings.md` / `progress.md` 更新。

## 4.4 Baseline 回归门槛

当前确认可作为最小回归门槛的命令：

```bash
uv run pytest tests/test_structure_guards.py tests/test_data_flow.py -q
```

后续每个 Phase 至少都要跑这一条，再叠加该阶段专项测试。

---

## 5. 阶段总览（Program View）

| Phase | 名称 | 目标 | 预计时长 | 并行性 | 风险 |
|---|---|---|---:|---|---|
| 0 | Contracts First | 冻结跨层语言 | 1-2 天 | 不并行 | 低 |
| 0.5 | 架构守卫与 ADR | 锁住依赖方向 | 0.5-1 天 | 不并行 | 低 |
| 1 | Foundation 抽取 | 抽出纯计算底座 | 3-5 天 | 可 3 线并行 | 中 |
| 2 | 第一个 Model | 跑通新链路核心 | 3-5 天 | 部分并行 | 高 |
| 3 | Agent/Meeting 纯化 | 让推理层脱离计算 | 2-4 天 | 部分并行 | 中 |
| 4 | Orchestration 切换 | 统一 Pipeline 与入口 | 3-5 天 | 有限并行 | 中高 |
| 5 | Evolution 改造 | 变异 YAML 与 narrative | 2-4 天 | 可并行 | 中 |
| 6 | 收尾与 Cutover | 删除兼容层、全面收口 | 1-3 天 | 不并行 | 中 |

---

## 6. 分阶段详细方案

## Phase 0：Contracts First

### 6.0.1 目标
建立 v2.0 的统一语言层，让所有后续改造围绕 contracts 进行，而不是直接围绕旧对象和旧模块。

### 6.0.2 本阶段产物
- `invest/contracts/__init__.py`
- `invest/contracts/signal_packet.py`
- `invest/contracts/agent_context.py`
- `invest/contracts/model_output.py`
- `invest/contracts/strategy_advice.py`
- `invest/contracts/trade_contracts.py`
- `invest/contracts/eval_report.py`
- `tests/test_contracts_serialization.py`
- `tests/test_contracts_compatibility.py`

### 6.0.3 工作包（Work Packages）

#### WP0-1：定义核心 contracts
- `SignalPacket`
- `StockSignal`
- `AgentContext`
- `ModelOutput`
- `StrategyAdvice`
- `TradeRecord` / `PositionSnapshot`
- `EvalReport`

#### WP0-2：定义最小字段，不过度设计
- 第一版只保留真实跨层传递所需字段。
- 禁止预埋“未来也许会用”的扩展字段。

#### WP0-3：为旧对象建立 adapter
- 旧 `TradingPlan` / `SimulationResult` / Review 输出与新 contracts 做最小映射。
- 先桥接，不强推全仓库替换。

### 6.0.4 影响文件范围
- 新增 `invest/contracts/*`
- 允许轻微修改：`invest/shared/contracts.py`、`invest/trading/contracts.py`、`invest/evaluation/*`（仅 adapter）
- 不允许大改：`train.py`、`commander.py`、`web_server.py`

### 6.0.5 测试收口

#### capability eval
- 每个 contract 可实例化。
- 每个 contract 可 `asdict` / JSON round-trip。
- `ModelOutput` 可同时装载 `SignalPacket` 和 `AgentContext`。

#### regression eval
- `uv run pytest tests/test_structure_guards.py tests/test_data_flow.py -q`
- 旧训练主流程行为不变。

#### 新增测试建议
- `tests/test_contracts_serialization.py`
- `tests/test_contracts_compatibility.py`

### 6.0.6 质量控制
- contract 文件不 import 任何业务模块。
- 字段命名统一英文，解释在 docstring 写清。
- 所有 contract 带类型注解。
- 第一版 contract 设计必须通过一次 diff review，确认没有把策略逻辑塞进去。

### 6.0.7 完成标准
- contracts 全部落地。
- 兼容 adapter 可用。
- 没有动主流程行为。

### 6.0.8 回滚策略
- 仅删除 `invest/contracts/` 和 adapter 改动即可回滚，不触碰业务链路。

---

## Phase 0.5：架构守卫与 ADR

### 6.1.1 目标
在大规模迁移前，锁住依赖方向和工程规则，避免“刚拆完又被回流污染”。

### 6.1.2 本阶段产物
- `docs/adr/ADR-invest-v2-layering.md`
- `tests/test_architecture_import_rules.py`
- `tests/test_agent_purity_guards.py`（第一版可先是 guard scaffold）

### 6.1.3 工作包
- 写清目标分层、允许依赖、禁止依赖、迁移原则。
- 增加 AST/import 层面的守卫测试。
- 规定：从此阶段开始新增计算逻辑不得进入 Agent/Meeting。

### 6.1.4 测试收口

#### capability eval
- 检测 `foundation/` 不可 import `models/` / `agents/`。
- 检测 `models/` 不可 import `agents/`。
- 检测 `contracts/` 不 import 业务层。

#### regression eval
- `uv run pytest tests/test_structure_guards.py tests/test_architecture_import_rules.py -q`

### 6.1.5 质量控制
- ADR 成为后续所有迁移的裁决依据。
- 新增模块前，先对照 ADR 判断归属层级。

### 6.1.6 完成标准
- 有规则、有测试、有文档，后续改造不再“边想边拆”。

---

## Phase 1：Foundation 抽取

### 6.2.1 目标
把所有纯计算能力抽到 `invest/foundation/`，并让旧模块改为调用 foundation，从而实现“行为不变，边界先变”。

### 6.2.2 本阶段产物
- `invest/foundation/compute/{indicators,factors,features}.py`
- `invest/foundation/risk/controller.py`
- `invest/foundation/engine/{simulator,order}.py`
- `invest/foundation/metrics/{returns,benchmark,attribution}.py`
- `tests/test_foundation_indicator_parity.py`
- `tests/test_foundation_risk_parity.py`
- `tests/test_foundation_metrics_parity.py`

### 6.2.3 子泳道划分

#### Lane A：Compute
来源：
- `invest/shared/indicators.py`
- `invest/selection/factors.py`
- `invest/agents/regime.py` / `invest/agents/hunters.py` 中的指标与特征逻辑

目标：
- 抽到 `foundation/compute/`
- 统一命名和参数接口

#### Lane B：Risk + Engine
来源：
- `invest/trading/risk.py`
- `invest/trading/engine.py`

目标：
- 抽出纯风险计算与纯执行撮合能力
- 把默认阈值迁移到模型配置层

#### Lane C：Metrics
来源：
- `invest/evaluation/cycle.py`
- `invest/evaluation/benchmark.py`
- `invest/evaluation/reports.py` 中的可计算部分

目标：
- 把“指标计算”和“文字报告”分离

### 6.2.4 工作包
- 为每个旧函数创建对应 foundation 实现。
- 编写 parity test：同输入，新旧输出一致。
- 旧模块内部改为调用 foundation，而不是继续保留双实现。
- 标记旧实现为 deprecated adapter 或 wrapper。

### 6.2.5 测试收口

#### capability eval
- foundation 中每个核心函数都可独立单测。
- 对同一输入，新旧输出一致或差异有明确说明。

#### regression eval
- `uv run pytest tests/test_structure_guards.py tests/test_data_flow.py -q`
- `uv run pytest tests/test_trading_critical_fixes.py tests/test_evaluation.py tests/test_all_modules.py -q`

#### 新增测试建议
- `tests/test_foundation_indicator_parity.py`
- `tests/test_foundation_risk_parity.py`
- `tests/test_foundation_metrics_parity.py`

### 6.2.6 质量控制
- foundation 不 import Agent/Meeting。
- foundation 中任何阈值都必须来自参数，不允许隐式默认偏好。
- 纯计算函数优先写成 side-effect free。
- 每个泳道完成后先做局部回归，再合并。

### 6.2.7 完成标准
- 新增计算逻辑有统一归宿。
- 旧系统行为保持一致。
- 纯计算边界已清晰可复用。

### 6.2.8 回滚策略
- 保留旧模块 wrapper；若发现偏差，可快速切回旧实现调用路径。

---

## Phase 2：第一个 InvestmentModel

### 6.3.1 目标
创建 `invest/models/`，用一个真实可跑的 `MomentumModel` 打通新架构的核心主链。

### 6.3.2 本阶段产物
- `invest/models/__init__.py`
- `invest/models/base.py`
- `invest/models/momentum.py`
- `invest/models/context_renderer.py`
- `invest/models/configs/momentum_v1.yaml`
- `tests/test_model_config_loading.py`
- `tests/test_momentum_model_output.py`
- `tests/test_model_signal_parity.py`

### 6.3.3 工作包

#### WP2-1：定义 `InvestmentModel` 基类
至少包含：
- `load_config()`
- `extract_signals()`
- `build_context()`
- `process()`

#### WP2-2：实现 `MomentumModel`
- 用 foundation 的指标/因子计算结果生成 `SignalPacket`
- 生成适合 Agent 推理的 `AgentContext`

#### WP2-3：YAML 化参数
- 把周期、权重、止损/仓位上界、排序规则、叙事模板参数外置到 YAML

#### WP2-4：做新旧路径对齐
- 对同一输入数据，`SignalPacket` 与旧逻辑输出对齐
- 偏差必须可量化并记录原因

### 6.3.4 测试收口

#### capability eval
- `MomentumModel.process(data)` 返回完整 `ModelOutput`
- `ModelOutput.signal_packet` 非空且结构完整
- `ModelOutput.agent_context` 可读且包含关键证据、候选、风险提示

#### regression eval
- 旧 `SelectionMeeting` 在 adapter 模式仍可运行
- `uv run pytest tests/test_data_flow.py tests/test_comparison.py -q`

#### 新增测试建议
- `tests/test_model_config_loading.py`
- `tests/test_momentum_model_output.py`
- `tests/test_model_signal_parity.py`

### 6.3.5 质量控制
- Model 只能调用 foundation + contracts，不直接 import Agent。
- Model 中不能重新实现指标。
- YAML 参数要有 schema/校验逻辑，防止无效配置。
- 叙事模板和计算逻辑分开组织，避免耦死在类里。

### 6.3.6 完成标准
- 单个 `MomentumModel` 已成为新系统可运行核心。
- 新增策略的路径第一次被“证明可行”。

### 6.3.7 回滚策略
- `train.py` 仍可走旧 meeting/selection 路径；新 model 作为 feature-flag/adapter 路径引入。

---

## Phase 3：Agent / Meeting 纯化

### 6.4.1 目标
把 `agents/` 与 `meetings/` 从“半计算半推理”改造成“纯推理/纯协作”层。

### 6.4.2 本阶段产物
- 精简后的 `invest/agents/{base,regime,hunters,reviewers}.py`
- 精简后的 `invest/meetings/{selection,review}.py`
- `tests/test_agent_context_consumption.py`
- `tests/test_agent_purity_guards.py`
- `tests/test_meeting_contract_bridge.py`

### 6.4.3 工作包
- 改 Agent 输入：从原始数据/统计 dict 改为 `AgentContext`
- 改 Agent 输出：统一到 `StrategyAdvice` 或结构化会议建议
- 从 Agent 和 Meeting 中清理指标函数、硬编码阈值、策略参数裁剪逻辑
- 把保留的“参数清洗”迁回模型层或编排层的配置校验阶段

### 6.4.4 测试收口

#### capability eval
- Agent 输入为 `AgentContext`
- Agent 输出为结构化建议
- Agent 代码不再调用 `calc_*` / `compute_*` 之类计算函数

#### regression eval
- `uv run pytest tests/test_meeting_refinement.py tests/test_agent_prompt_contracts.py tests/test_data_flow.py -q`
- 训练追踪、会议纪要、记忆落库继续可用

#### 新增测试建议
- `tests/test_agent_context_consumption.py`
- `tests/test_agent_purity_guards.py`
- `tests/test_meeting_contract_bridge.py`

### 6.4.5 质量控制
- Agent 文件中禁止新增技术指标计算。
- Agent prompt 合同保持稳定，不能因输入切换而破坏 JSON 输出约束。
- 对比旧路径和新路径的会议摘要质量，至少做一次 A/B 评估。

### 6.4.6 完成标准
- Agent/Meeting 的职责被彻底收窄到“理解、推理、协作”。

### 6.4.7 回滚策略
- 通过 feature flag 保留旧输入路径，直到新 `AgentContext` 路径稳定。

---

## Phase 4：Orchestration 切换

### 6.5.1 目标
把主入口切换到新 Pipeline，让 CLI / Web / Train 都通过统一装配路径运行 v2.0。

### 6.5.2 本阶段产物
- 改造后的 `train.py`
- 改造后的 `commander.py`
- 改造后的 `web_server.py`
- 必要时补 `app/train.py`、`app/commander.py`、`app/web_server.py` 的桥接更新
- `tests/test_pipeline_v2_smoke.py`
- `tests/test_web_model_selection_api.py`
- `tests/test_train_v2_events.py`

### 6.5.3 标准 Pipeline

```text
Load Data (L0)
  -> Model.process (L1 + L2)
  -> Agents / Meetings (L3)
  -> Engine / Risk (L1)
  -> Metrics / EvalReport (L1)
  -> Review / Evolution (L3 + Phase5)
```

### 6.5.4 工作包
- 在 `train.py` 中引入 model registry / config loader
- 在 `commander.py` 中允许选择 model/config
- 在 `web_server.py` 中暴露 model/config 切换与对比入口
- 统一训练事件流：事件语义围绕 model / agent / engine / eval 组织
- 保证训练中心、记忆中心、复盘产物仍可对接

### 6.5.5 测试收口

#### capability eval
- 切换 YAML 可切换策略
- CLI / Web / Train 三条入口走同一装配路径
- 新 Pipeline 能输出完整训练产物

#### regression eval
- `uv run pytest tests/test_train_cycle.py tests/test_train_event_stream.py tests/test_commander.py tests/test_web_server_config_api.py tests/test_web_server_runtime_and_bool.py -q`
- 视情况追加 `tests/test_web_server_memory_api.py tests/test_train_ui_semantics.py -q`

#### 新增测试建议
- `tests/test_pipeline_v2_smoke.py`
- `tests/test_web_model_selection_api.py`
- `tests/test_train_v2_events.py`

### 6.5.6 质量控制
- 编排层只装配，不新增业务判断。
- `train.py`、`commander.py`、`web_server.py` 由单 owner/单 workstream 负责，避免并行冲突。
- 所有入口变更都必须经过 dry run + Web smoke。

### 6.5.7 完成标准
- 用户从外部入口已经能“感知到” v2.0 架构。
- 统一 Pipeline 成立，旧流程只剩兼容入口。

### 6.5.8 回滚策略
- 编排层保留旧 dispatcher / 旧 route adapter，在新 Pipeline 不稳定时可临时回退。

---

## Phase 5：Evolution 改造

### 6.6.1 目标
把进化引擎改造成“对配置和叙事做变异”的系统，而不是继续在代码逻辑上隐式漂移。

### 6.6.2 本阶段产物
- `invest/evolution/mutators.py`
- 改造后的 `invest/evolution/engine.py`
- 必要时改造 `invest/evolution/orchestrator.py`
- `data/evolution/generations/` 快照规范
- `tests/test_yaml_mutation.py`
- `tests/test_evolution_generation_roundtrip.py`
- `tests/test_evolution_config_constraints.py`

### 6.6.3 工作包
- 定义 YAML 参数空间、边界、合法值
- 定义叙事模板变异维度
- 实现 generation snapshot 持久化
- 排行榜记录“配置 -> 结果”的可追溯关系

### 6.6.4 测试收口

#### capability eval
- 能从上一代 YAML 生成新一代 YAML
- 变异结果合法、可加载、可运行
- 结果可回溯到快照与评估记录

#### regression eval
- `uv run pytest tests/test_optimization.py tests/test_strategy_gene_validation.py -q`

#### 新增测试建议
- `tests/test_yaml_mutation.py`
- `tests/test_evolution_generation_roundtrip.py`
- `tests/test_evolution_config_constraints.py`

### 6.6.5 质量控制
- 所有变异必须受约束，不允许生成“能写出来但不能运行”的配置。
- 演化输出必须可复现：保存父代、变异规则、随机种子、结果摘要。

### 6.6.6 完成标准
- 进化目标已经从代码迁移到配置。
- “策略实验室”的核心闭环成立。

### 6.6.7 回滚策略
- 保留旧 evolution path 作为 fallback，直到 YAML 变异路径稳定。

---

## Phase 6：兼容收尾与 Cutover

### 6.7.1 目标
去掉临时兼容分支，完成 v2.0 正式切换。

### 6.7.2 本阶段产物
- 收口后的 `invest/__init__.py`
- 迁移说明文档
- 过时路径删除清单
- 版本升级说明（README / docs）

### 6.7.3 工作包
- 删除不再需要的 compat adapter
- 收口 `invest/__init__.py`
- 更新 README、架构图、训练流说明、配置文档
- 扫描仓库旧引用路径并清理

### 6.7.4 测试收口

#### capability eval
- 所有核心功能都走新架构
- 旧路径只有明确兼容出口或已删除

#### regression eval
- 运行完整回归矩阵
- 至少一次完整 dry run + 一次真实训练 smoke

### 6.7.5 质量控制
- 删除兼容层前必须先确认没有外部入口依赖它。
- `invest/__init__.py` 不再继续扁平暴露内部实现细节。

### 6.7.6 完成标准
- 架构、文档、入口、测试、配置全部切到 v2.0。

---

## 7. 测试收口总矩阵（项目级）

## 7.1 测试分层

### A. Contract Tests
目标：保证 contracts 稳定、可序列化、可兼容。

建议文件：
- `tests/test_contracts_serialization.py`
- `tests/test_contracts_compatibility.py`

### B. Architecture Guard Tests
目标：保证层间依赖方向不会回流污染。

建议文件：
- `tests/test_architecture_import_rules.py`
- `tests/test_agent_purity_guards.py`

### C. Parity Tests
目标：保证 foundation 抽取不改变结果。

建议文件：
- `tests/test_foundation_indicator_parity.py`
- `tests/test_foundation_risk_parity.py`
- `tests/test_foundation_metrics_parity.py`
- `tests/test_model_signal_parity.py`

### D. Integration Tests
目标：保证 pipeline 和跨层协作完整。

建议文件：
- `tests/test_data_flow.py`
- `tests/test_pipeline_v2_smoke.py`
- `tests/test_meeting_contract_bridge.py`

### E. Entry / Product Tests
目标：保证 CLI / Web / Train 外部体验不坏。

建议文件：
- `tests/test_train_cycle.py`
- `tests/test_train_event_stream.py`
- `tests/test_commander.py`
- `tests/test_web_server_config_api.py`
- `tests/test_web_model_selection_api.py`
- `tests/test_web_server_memory_api.py`
- `tests/test_train_ui_semantics.py`

### F. Evolution Tests
目标：保证配置变异合法、可追溯、可回放。

建议文件：
- `tests/test_yaml_mutation.py`
- `tests/test_evolution_generation_roundtrip.py`
- `tests/test_evolution_config_constraints.py`

## 7.2 各阶段统一测试命令模板

### 每阶段最小门槛
```bash
uv run pytest tests/test_structure_guards.py tests/test_data_flow.py -q
```

### contracts 阶段
```bash
uv run pytest tests/test_contracts_serialization.py tests/test_contracts_compatibility.py tests/test_structure_guards.py -q
```

### foundation 阶段
```bash
uv run pytest tests/test_foundation_indicator_parity.py tests/test_foundation_risk_parity.py tests/test_foundation_metrics_parity.py tests/test_trading_critical_fixes.py tests/test_evaluation.py -q
```

### models 阶段
```bash
uv run pytest tests/test_model_config_loading.py tests/test_momentum_model_output.py tests/test_model_signal_parity.py tests/test_comparison.py -q
```

### agents/meetings 阶段
```bash
uv run pytest tests/test_agent_context_consumption.py tests/test_agent_purity_guards.py tests/test_meeting_refinement.py tests/test_agent_prompt_contracts.py -q
```

### orchestration 阶段
```bash
uv run pytest tests/test_pipeline_v2_smoke.py tests/test_train_cycle.py tests/test_train_event_stream.py tests/test_commander.py tests/test_web_server_config_api.py tests/test_web_server_runtime_and_bool.py -q
```

### evolution 阶段
```bash
uv run pytest tests/test_yaml_mutation.py tests/test_evolution_generation_roundtrip.py tests/test_evolution_config_constraints.py tests/test_optimization.py tests/test_strategy_gene_validation.py -q
```

### cutover 阶段
```bash
uv run pytest -q
```

## 7.3 手工 smoke 清单

每个会影响入口层的 Phase，至少执行以下手工 smoke：

1. 启动一次 mock / dry-run 训练。
2. 观察训练事件是否正常流转。
3. 检查会议纪要、记忆详情、参数快照是否仍可落盘。
4. 如影响 Web，检查前端是否仍能显示训练状态、模型选择、训练结果。

---

## 8. 质量控制（项目级）

## 8.1 代码质量规则
- 所有新增模块必须带类型注解。
- 新增跨层数据结构必须放进 `invest/contracts/`。
- 新增纯计算函数必须放进 `invest/foundation/`。
- 新增策略参数默认值必须放进 `invest/models/configs/*.yaml` 或模型配置 schema。
- Agent 层不允许新增计算函数与交易规则阈值。

## 8.2 设计质量规则
- 每个模块先明确层级归属，再写代码。
- 每个文件只承载一个主要职责。
- 编排层不能“偷偷补业务逻辑”。
- 进化层只能变异配置和模板，不得绕过模型层直接改业务逻辑。

## 8.3 交付质量规则
- 每个 Phase 都必须产出：代码、测试、文档、进度记录。
- 每个 Phase 都必须保留回滚点。
- 没有 capability eval / regression eval 的改动，不进入下一阶段。

## 8.4 观测质量规则
- 训练中心事件模型要跟随新 Pipeline 更新。
- 关键产物必须可落盘：`ModelOutput` 摘要、会议决议、配置快照、评估报告、evolution 快照。
- 任一阶段引入的新结构如果不进入观测链，就算功能写成也不算完成。

---

## 9. subagent / 并行编排方案

## 9.1 原则
这次升级 **应该并行，但不能全程并行**。

最合理的方式是：
- Phase 0 与 Phase 0.5 串行完成
- 从 Phase 1 开始进入受控并行
- 入口层由单 owner 统一收口

## 9.2 推荐的 3 个并行角色

### Role A：Contracts Architect
负责：
- `invest/contracts/`
- adapter
- ADR
- architecture guards

输入：
- 当前 contracts 分散点清单

输出：
- 统一 contracts + import guards + adapter contract

### Role B：Foundation Extractor
负责：
- `foundation/compute`
- `foundation/risk`
- `foundation/engine`
- `foundation/metrics`
- parity tests

输入：
- 现有指标、风控、评估、引擎逻辑

输出：
- 纯计算底座 + 新旧对齐报告

### Role C：Pipeline Migrator
负责：
- `train.py`
- `commander.py`
- `web_server.py`
- Web/UI/entry smoke

输入：
- 已稳定的 contracts/model 接口

输出：
- 新旧入口统一接线 + smoke 验证

## 9.3 不建议超过 3 个并行单元的原因
当前仓库的天然冲突热点是：
- `invest/__init__.py`
- `train.py`
- `commander.py`
- 部分测试基线文件

并行角色超过 3 个之后，冲突成本会急速上升，反而拖慢升级。

## 9.4 并行执行时的交付契约
每个 subagent/workstream 的提交内容必须包含：
- 本工作单元修改了哪些文件
- 依赖哪个上游 contract/interface
- 新增了哪些测试
- 本工作单元的 done condition
- 不允许改动的文件名单

---

## 10. 时间安排（现实版）

## 第 1-2 天
- 完成 Phase 0：contracts
- 完成 Phase 0.5：ADR + import guards

## 第 3-6 天
- 完成 Phase 1：foundation 抽取
- 并行 3 泳道推进 compute / risk+engine / metrics

## 第 7-10 天
- 完成 Phase 2：`MomentumModel`
- 跑通 `ModelOutput`
- 完成 YAML 配置与 schema

## 第 11-13 天
- 完成 Phase 3：Agent / Meeting 纯化
- 做新旧输入路径 A/B 对照

## 第 14-17 天
- 完成 Phase 4：编排层切换
- Web / CLI / Train 统一走新 Pipeline

## 第 18-20 天
- 完成 Phase 5：Evolution YAML 化
- 完成 Phase 6：兼容层清理、全面 cutover
- 跑项目级回归与真实 smoke

---

## 11. 风险与应对（升级版）

| 风险 | 影响 | 预防措施 | 兜底方案 |
|---|---|---|---|
| foundation 抽取导致结果漂移 | 回测结果异常 | parity tests + golden fixtures | 保留旧 wrapper 回退 |
| AgentContext 质量不如直喂数据 | 推理效果下降 | 保留 A/B 开关 + 对照评估 | 临时回退旧输入路径 |
| YAML 空间过大 | 进化效率下降 | schema + bounds + mutator constraints | 缩窄变异范围 |
| 并行改入口导致冲突 | 合并混乱 | 单 owner 负责入口层 | 暂停并行、串行合入 |
| compat 层长期不删 | 认知负担上升 | Phase 6 强制清理 | 建立 deprecated 清单 |
| 测试覆盖不准 | 漏掉行为回归 | 建立分层测试矩阵 | 追加手工 smoke |

---

## 12. 建议的立即启动顺序

如果马上开始执行，不应该从“搬代码”开始，而应该从下面顺序开始：

### Step 1
冻结 contracts 草案，列出当前所有分散 contract 源头。

### Step 2
补 architecture guards 和 ADR，把依赖红线先钉死。

### Step 3
选择一条最小主链：
`market_data -> momentum model -> agent context -> selection advice -> simulation -> eval`

### Step 4
围绕这条最小主链做 foundation 抽取和 first model 落地。

### Step 5
等最小主链跑通后，再扩展到 review/evolution/web 切换。

---

## 13. 我对整个升级项目的最终判断

这次升级成功与否，不取决于你能不能把目录改成五层，而取决于你能不能真正做到下面三件事：

1. **底座只算事实，不做判断**
2. **模型只定义视角，不做推理**
3. **Agent 只做推理，不直接碰原始数据和指标计算**

只要这三件事真正落地，v2.0 就不是“目录更漂亮”，而是系统从“交易脚本”升级成“策略实验室”。

而实现这三件事的唯一正确路径，不是一次性大重写，而是：

> **contracts 先行 -> foundation 抽取 -> first model 跑通 -> agent 纯化 -> orchestration 切换 -> evolution 配置化 -> compat 清理**

这就是我建议你采用的完整升级路径。
