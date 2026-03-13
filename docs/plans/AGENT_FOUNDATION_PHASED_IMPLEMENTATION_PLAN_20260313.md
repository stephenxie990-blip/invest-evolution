# Agent Foundation Phased Implementation Plan

## 1. Goal

在不破坏当前单进程 `CommanderRuntime` 主链的前提下，按以下顺序分阶段增强系统：

1. `Instructor`
2. `Guardrails AI`
3. `PySR`
4. `E2B`
5. `Temporal`

目标不是“换框架”，而是围绕现有的：

- `config/control_plane.py`
- `app/runtime_contract_tools.py`
- `brain/runtime.py`
- `brain/tools.py`
- `app/commander.py`
- `app/freeze_gate.py`

逐层加上结构化输出、语义治理、可解释研究引擎、隔离执行与耐久工作流能力。

## 2. Guiding Principles

### 2.1 Keep the Spine, Strengthen the Layers

保留现有 `control_plane -> runtime contract -> runtime tool loop -> freeze gate` 主骨架，不引入一次性平台替换。

### 2.2 Opt-In First

所有新能力先走 feature flag、可选依赖、可选执行路径，默认关闭。

### 2.3 Contract-First

优先从现有 contract/schema 派生结构，而不是在各业务点重复定义校验规则。

### 2.4 Research and Runtime Stay Decoupled

`PySR` 进入 research/training lab 支线，不能先污染实时 `BrainRuntime` 主 loop。

### 2.5 Every Phase Must Be Reversible

每一阶段都必须有：

- 单独开关
- 明确测试集
- 回滚方式
- 退出标准

## 3. Shared Preconditions

这些工作不单独算阶段，但应该在 Phase 1 开始前先完成。

### 3.1 Dependency Strategy

建议在 [pyproject.toml](/Users/zhangsan/Desktop/投资进化系统v1.0/pyproject.toml) 中增加 optional extras，而不是直接塞进默认依赖：

- `agent-structured`: `pydantic`, `instructor`
- `agent-guardrails`: `guardrails-ai`
- `research-symbolic`: `pysr`
- `agent-sandbox`: `e2b`
- `workflow-durable`: `temporalio`

这样本地轻量开发和生产最小部署不会被一次性拖重。

### 3.2 Feature Flags

建议在 [control_plane.py](/Users/zhangsan/Desktop/投资进化系统v1.0/config/control_plane.py) 的 payload 中增加新配置段，例如：

- `llm.structured_output.enabled`
- `llm.structured_output.mode`
- `governance.guardrails.enabled`
- `research.pysr.enabled`
- `runtime.sandbox_backend`
- `workflow.engine`

初期全部默认关闭，按 intent 或 tool 名做白名单。

### 3.3 Observability and Artifacts

建议统一增加：

- 结构化输出成功率
- 校验失败率
- guardrails 拦截率
- sandbox 调用次数 / 失败率 / 平均时长
- workflow resume 次数

并将每一类新能力的工件写入 `runtime/outputs/...` 下独立子目录。

### 3.4 Freeze Gate Expansion Rule

每一阶段接入后，都必须同步扩展 [freeze_gate.py](/Users/zhangsan/Desktop/投资进化系统v1.0/app/freeze_gate.py) 或其关联测试集，至少覆盖：

- 正常路径
- 校验失败路径
- 回退路径
- golden snapshot 更新

## 4. Phase 1: Instructor

### 4.1 Objective

为关键 intent 增加强结构输出、自动校验和可控重试，优先解决：

- LLM 输出格式漂移
- reply / protocol payload 结构不稳定
- stock analysis / training planning 场景中的半结构化回复难测问题

### 4.2 Scope

首批只覆盖 3 到 5 条高价值路径：

- `invest_ask_stock`
- `invest_training_plan_create`
- `invest_training_plan_execute` 的总结输出
- `invest_control_plane_update` 的确认前说明
- `BrainRuntime` 的最终 human-readable receipt / bounded summary

### 4.3 Primary Touchpoints

- [app/llm_gateway.py](/Users/zhangsan/Desktop/投资进化系统v1.0/app/llm_gateway.py)
- [brain/runtime.py](/Users/zhangsan/Desktop/投资进化系统v1.0/brain/runtime.py)
- [app/stock_analysis.py](/Users/zhangsan/Desktop/投资进化系统v1.0/app/stock_analysis.py)
- [app/runtime_contract_tools.py](/Users/zhangsan/Desktop/投资进化系统v1.0/app/runtime_contract_tools.py)
- [tests/test_brain_runtime.py](/Users/zhangsan/Desktop/投资进化系统v1.0/tests/test_brain_runtime.py)

### 4.4 Implementation Approach

1. 先新增一层 “structured output adapter”，不要直接把 `Instructor` 散落到业务方法里。
2. 由 adapter 接收：
   - prompt/messages
   - 目标 schema/model
   - retry policy
   - fallback policy
3. 首批 schema 不追求覆盖所有 payload，先覆盖最不稳定、最常被消费的字段。
4. 如果 `Instructor` 在当前 `litellm` 路径下存在适配摩擦，优先保住“schema 校验 + retry + fallback”的行为一致性，不强求一次性全量替换现有 gateway。

### 4.5 Deliverables

- 一个统一的 structured output adapter
- 一组首批 response models
- 若干关键 intent 的结构化输出接入
- 对应单测与 golden 更新

### 4.6 Acceptance Criteria

- 被纳入范围的 intent 在测试环境中结构化输出成功率显著高于当前自由文本模式
- 校验失败能够回退到明确错误，而不是 silently degrade
- freeze gate 中新增相关测试全部通过
- 默认关闭时，旧行为不回归

### 4.7 Rollback

- 关闭 `llm.structured_output.enabled`
- adapter 保留但不参与执行
- 保持原 `LLMGateway` 路径可独立运行

### 4.8 Estimated Effort

约 1.5 到 2.5 周。

## 5. Phase 2: Guardrails AI

### 5.1 Objective

在高风险写操作前增加语义与策略校验，补齐当前仅有参数类型校验、缺少业务逻辑校验的缺口。

### 5.2 Scope

只覆盖 mutating 且高风险的工具：

- `invest_control_plane_update`
- `invest_runtime_paths_update`
- `invest_evolution_config_update`
- `invest_training_plan_create`
- `invest_training_plan_execute`
- `invest_data_download`

### 5.3 Primary Touchpoints

- [brain/runtime.py](/Users/zhangsan/Desktop/投资进化系统v1.0/brain/runtime.py)
- [brain/tools.py](/Users/zhangsan/Desktop/投资进化系统v1.0/brain/tools.py)
- [app/commander.py](/Users/zhangsan/Desktop/投资进化系统v1.0/app/commander.py)
- [brain/task_bus.py](/Users/zhangsan/Desktop/投资进化系统v1.0/brain/task_bus.py)
- `tests/test_commander_mutating_workflow_golden.py`

### 5.4 Implementation Approach

1. 在工具参数通过 schema 校验后、实际执行前插入 guardrails hook。
2. guardrails 的输入应包含：
   - tool name
   - normalized args
   - risk level
   - confirmation state
   - current config snapshot
3. 初期 validators 只做三类判断：
   - 危险 patch 拦截
   - 缺失 confirm 的危险执行拦截
   - 不完整训练计划 / 非法参数组合拦截
4. guardrails 失败不应抛裸异常，而应返回标准 protocol payload，状态建议使用 `blocked` 或 `confirmation_required`。

### 5.5 Deliverables

- guardrails registry / policy module
- 高风险工具 validator 集合
- task bus / receipt 中的拦截原因可读化
- 对应 golden 和 protocol 测试

### 5.6 Acceptance Criteria

- 明显危险或不完整的写操作在执行前被阻断
- 阻断原因在 response 中清晰可读
- 不影响只读工具执行时延和输出
- 已有 confirmation gate 语义不被破坏

### 5.7 Rollback

- 关闭 `governance.guardrails.enabled`
- 保留参数 schema 与 confirmation gate，不启用额外语义校验

### 5.8 Estimated Effort

约 1.5 到 2 周。

## 6. Phase 3: PySR

### 6.1 Objective

在 training lab 中建立符号回归支线，用于：

- 因子组合发现
- 评分公式压缩
- 复杂模型蒸馏为可解释规则

### 6.2 Scope

只在离线研究路径中运行，不进入实时 `BrainRuntime` 问答主链。

优先数据来源：

- `factor_snapshot`
- `daily_bar`
- 训练评估 summary
- leaderboard 表现标签

### 6.3 Primary Touchpoints

- `invest/research/`
- `invest/foundation/compute/`
- [app/commander.py](/Users/zhangsan/Desktop/投资进化系统v1.0/app/commander.py)
- training lab artifact 目录
- 未来可新增 `invest_research_symbolic_*` 工具，但第一版不强制暴露给所有用户

### 6.4 Implementation Approach

1. 先做 dataset builder，把已有 factor / label 对齐成离线样本。
2. 把符号回归运行做成 batch job，不阻塞主线程交互。
3. 结果工件至少包括：
   - 公式文本
   - complexity
   - train / validation / holdout 指标
   - 输入特征清单
   - 运行参数
4. 第一版只做“发现与记录”，不自动回写策略配置。
5. 第二版再考虑把候选公式变成 mutation space 的输入。

### 6.5 Deliverables

- PySR dataset builder
- symbolic regression runner
- research artifact writer
- 结果浏览 / 列表接口或只读工具

### 6.6 Acceptance Criteria

- 至少能稳定生成可复现公式工件
- 公式在 holdout 上不显著塌陷
- 工件可回溯到输入数据版本和运行参数
- 不影响默认训练链路

### 6.7 Rollback

- 关闭 `research.pysr.enabled`
- 删除或忽略 PySR 支线工件，不影响主训练入口

### 6.8 Estimated Effort

约 2.5 到 4 周。

## 7. Phase 4: E2B

### 7.1 Objective

增加一个隔离执行后端，让 Agent 可以在不污染主机运行态的前提下执行临时研究脚本、数据处理脚本和实验代码。

### 7.2 Scope

第一版限制为只读 / 弱副作用任务：

- 临时数据分析
- CSV/JSON 转换
- 研究脚本执行
- 报告型产物生成

不允许第一版直接做：

- 本地配置修改
- 主链 artifact 覆盖写
- 主数据库直接写入

### 7.3 Primary Touchpoints

- `brain/` 下新增执行后端抽象
- [brain/tools.py](/Users/zhangsan/Desktop/投资进化系统v1.0/brain/tools.py) 中新增 sandbox tool
- [brain/runtime.py](/Users/zhangsan/Desktop/投资进化系统v1.0/brain/runtime.py) 中新增风险分类与 receipt
- `config/control_plane.py` 或 runtime config 中新增 backend 选择项

### 7.4 Implementation Approach

1. 先定义统一执行接口：本地执行与沙箱执行都实现同一抽象。
2. 默认 backend 保持本地；只有显式声明时才走 E2B。
3. 输入输出边界必须先定义清楚：
   - 允许上传哪些文件
   - 允许输出哪些工件
   - 是否允许联网
   - 密钥怎样隔离
4. 第一版只新增专用 sandbox tool，不改现有工具默认执行方式。

### 7.5 Deliverables

- execution backend abstraction
- E2B adapter
- 新 sandbox tool
- 审计日志与执行工件记录

### 7.6 Acceptance Criteria

- 沙箱执行结果可回溯、可审计
- 主项目文件系统不会被未授权修改
- 网络、文件上传、输出目录策略明确
- 默认关闭时零行为变化

### 7.7 Rollback

- `runtime.sandbox_backend=local`
- 禁用 sandbox tool 注册

### 7.8 Estimated Effort

约 2 到 3 周。

## 8. Phase 5: Temporal

### 8.1 Objective

验证是否需要把部分长时任务升级为 durable workflow，并以最小 PoC 方式验证恢复能力。

### 8.2 Scope

第一版只选一个流程做 PoC：

- `invest_training_plan_execute`

不做全系统迁移，不改所有 cron / runtime 行为。

### 8.3 Primary Touchpoints

- [app/commander.py](/Users/zhangsan/Desktop/投资进化系统v1.0/app/commander.py)
- training lab run/evaluation 流程
- 未来可新增 `app/workflows/` 抽象层
- [app/freeze_gate.py](/Users/zhangsan/Desktop/投资进化系统v1.0/app/freeze_gate.py) 的耐久性回归扩展

### 8.4 Implementation Approach

1. 先把当前训练执行流程拆成显式步骤：
   - load plan
   - resolve experiment spec
   - run cycles
   - record artifacts
   - append memory
2. 再抽象 workflow interface，让当前本地流程和未来 Temporal workflow 能共用同一域逻辑。
3. 只对单个流程做 crash / restart / resume 模拟。
4. 若恢复收益不明显，则停止在抽象层，不强行平台化。

### 8.5 Deliverables

- workflow abstraction
- single-flow Temporal PoC
- crash recovery test scenario
- go/no-go decision memo

### 8.6 Acceptance Criteria

- PoC 流程能在中断后恢复到明确阶段
- 工件不重复、不丢失、状态可解释
- 不要求替换现有默认运行方式

### 8.7 Rollback

- `workflow.engine=local`
- Temporal PoC 独立保留，不参与默认生产路径

### 8.8 Estimated Effort

约 3 到 5 周，且应在前四阶段稳定后才启动。

## 9. Recommended Delivery Order

### Sprint A

- shared preconditions
- Phase 1 `Instructor`

### Sprint B

- Phase 2 `Guardrails AI`

### Sprint C

- Phase 3 `PySR`

### Sprint D

- Phase 4 `E2B`

### Sprint E

- Phase 5 `Temporal PoC`

## 10. What We Should Not Do

- 不要在第一阶段就重写 `LLMGateway`
- 不要在第二阶段把 guardrails 铺到所有只读路径
- 不要让 `PySR` 第一版自动改写策略配置
- 不要让 `E2B` 第一版拥有主系统写权限
- 不要在没有 workflow 抽象的前提下直接把训练主链搬进 `Temporal`

## 11. Immediate Next Step

如果下一步开始编码，最务实的顺序是：

1. 为 `structured output` 建一层 adapter
2. 选定首批 3 个 intent 做 `Instructor` 接入
3. 补充对应测试与 freeze gate 扩展
4. 等 Phase 1 稳定后，再开始 Phase 2 的 guardrails hook
