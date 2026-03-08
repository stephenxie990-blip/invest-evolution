# Web 训练中心与记忆中心实施计划

状态：进行中
开始时间：2026-03-08

## 目标
- 让训练中心的 Agent 工作窗口按 Agent 持续同步状态、进度、阶段与最新产出。
- 让系统模块工作流以带时间戳的瀑布流方式实时滚动具体工作内容、会议发言和阶段成果。
- 让每次训练自动沉淀为一条可检索的训练记忆，并支持详情回放过程日志、策略使用、优化与进化内容。
- 完成一轮 `debate=false` 的真实 Web 训练，对比耗时与收益表现。

## 实施步骤
- [x] 步骤 1：补齐后端实时事件模型
- [x] 步骤 2：重构训练中心实时视图
- [x] 步骤 3：接入训练记忆与详情接口
- [x] 步骤 4：补充回归测试并完成静态检查
- [x] 步骤 5：重启 Web 并完成本轮产品化 Web 验收

## 本轮实现重点
- `app/train.py`：新增结构化事件，区分 Agent 状态、模块日志、会议发言、优化事件。
- `invest/meetings/selection.py` / `invest/meetings/review.py`：将候选、观点、复盘结论通过回调流式上送。
- `app/commander.py`：每次训练自动写入 `training_run` 记忆，并为每轮结果挂载产物路径。
- `app/web_server.py`：新增训练记忆详情接口，回放周期结果、会议纪要、配置快照。
- `static/index.html`：训练中心升级为 Agent 实时视图 + 时间线瀑布流，记忆中心升级为列表 + 详情面板。

## 验收标准
- Agent 状态与进度能实时同步，不再重复堆卡片。
- 系统模块工作流能滚动显示时间戳、工作内容、候选结果、会议发言、优化结论。
- 每次训练自动新增一条训练记忆，点击后可回放详细过程。
- 相关后端与 Web API 回归测试通过。


## 本轮产品化增强（第二版）
- [x] 时间线增加事件类型筛选与关键词筛选。
- [x] Agent 工作视图支持单卡折叠 / 全部折叠。
- [x] 会议发言在系统工作流中使用高亮卡片展示。
- [x] 记忆详情新增“策略差异对比”，对比上一条训练记忆的收益、选股、参数与策略开关变化。
- [x] 补充静态语义测试、接口测试，并重启 Web 服务完成 smoke 验收。


## Agent 工作台可视化增强
- [x] 新增 Agent 总览看板（在线数、活跃数、平均进度、最近更新）。
- [x] 强化 Agent 卡片层级（状态光带、胶囊、迷你指标、当前工作/思考/产出/过程分区）。
- [x] 按状态优先级排序 Agent 卡片，优先显示运行中与思考中的角色。
- [x] 完成语法检查、回归测试与 Web 服务重启验收。


## 真实训练监控
- [x] 轮询训练状态与系统运行状态。
- [x] 跟踪 Agent 工作节点与 LLM 调用阶段。
- [x] 在训练结束后汇总收益、选股、策略变化与异常。

## 2026-03-08 复盘一致性热修复
- [x] 修复复盘会议纪要统计字段误取单轮 `cycle_dict` 的问题，统一改为落盘聚合 `facts`。
- [x] 修复建议仓位文案与最终执行参数脱节的问题，新增“最终执行摘要”并随参数清洗结果同步输出。
- [x] 完成定向回归：`tests/test_meeting_refinement.py`、`tests/test_all_modules.py`。

---

# 数据库升级方案 V2 实施计划

状态：进行中
开始时间：2026-03-08

## 目标
- 将数据库从“个股日线仓库”升级为“训练/回测/风控一体化研究数据库”。
- 先完成 P0：指数数据统一入库与状态暴露。
- 为 P1 财务读侧接入、行业统一事实源和交易日历扩展打基础。

## 实施步骤
- [x] 步骤 1：梳理现状与升级目标
- [x] 步骤 2：编写 `docs/DATABASE_UPGRADE_V2.md`
- [x] 步骤 3：新增 `index_bar` 表与仓储接口
- [x] 步骤 4：接入指数同步与状态暴露
- [ ] 步骤 5：补充测试并完成定向验证
- [ ] 步骤 6：启动财务读侧与行业统一改造

---

# 投资进化系统 v2.0 架构升级实施计划

状态：已完成（代码落地 + 全量回归 + 训练 smoke）
开始时间：2026-03-08

## 目标
- 将 `invest/` 从按功能堆叠的 AI 交易脚本，升级为按变化频率分层的策略实验室。
- 建立 `contracts/ -> foundation/ -> models/ -> agents/ -> orchestration` 的稳定边界。
- 保持现有训练链路可运行，通过兼容层逐步迁移，而不是一次性推倒重来。
- 让“新增/进化策略”主要通过 YAML 配置和模型类完成，而不是散落改代码。

## 实施步骤
- [x] Phase 0：定义 `invest/contracts/` 与序列化/契约测试
- [x] Phase 0.5：补齐层间依赖守卫与迁移 ADR
- [x] Phase 1：提取 `invest/foundation/` 纯计算底座并做结果对齐
- [x] Phase 2：落地首个 `MomentumModel` 与 `configs/momentum_v1.yaml`
- [x] Phase 3：改造 `agents/` 与 `meetings/`，只消费 `AgentContext`
- [x] Phase 4：重构 `train.py` / `commander.py` / `web_server.py` 到标准 Pipeline
- [x] Phase 5：让 `invest/evolution/` 变异 YAML 与叙事模板，而不是变异代码
- [x] Phase 6：收口公开 API、补齐文档与回归矩阵（兼容出口保留最小集合）

## 并行编排建议
- 先串行完成 Phase 0 与 Phase 0.5；这两个阶段决定所有后续工作的公共语言与边界。
- Phase 1 可拆成 3 条并行泳道：`compute/`、`risk+engine/`、`metrics/`。
- Phase 2 与 Phase 3 仅在 `ModelOutput`/`AgentContext` 契约冻结后并行推进。
- Phase 4 与 Phase 5 可部分并行，但都依赖已有 `ModelOutput` 和标准 Pipeline 事件。
- 建议最多 3 个并行执行单元；超过 3 个会集中冲突在 `train.py`、`commander.py`、`invest/__init__.py`。

## 验收主线
- Baseline：`uv run pytest tests/test_structure_guards.py tests/test_data_flow.py -q`
- 每个阶段必须先定义 capability eval + regression eval，再开始改代码。
- 迁移期间保留兼容 re-export，直到 Phase 6 再删除旧入口。

## 详细方案文档
- 详细 master plan 已升级到 `docs/INVEST_V2_EXECUTION_PLAN.md`，包含阶段目标、工作包、测试收口、质量门、并行编排、回滚和 cutover 标准。

## 本轮规划结论
- `invest/` 现状已经完成“按功能拆目录”的第一步，但还没有达到“按变化频率分层”的目标。
- 当前最大风险不是代码量，而是隐藏耦合：默认阈值、扁平导出、数据合同分散、训练入口直接绑死旧流程。
- 最稳的执行方式不是直接大搬家，而是“契约先行 + 底座抽取 + 单模型落地 + Agent 纯化 + 编排切换”。

## 执行结果
- 已新增 `invest/contracts/`、`invest/foundation/`、`invest/models/` 三层，并通过 `invest/contracts/*` 建立跨层统一数据契约。
- 已将指标/因子/特征、模拟撮合、风控、收益评估拆入 `invest/foundation/compute|engine|risk|metrics/`，旧接口改为复用底座以保持兼容。
- 已落地 `MomentumModel`、`context_renderer`、`registry` 与 `invest/models/configs/momentum_v1.yaml`，模型输出统一为 `ModelOutput = SignalPacket + AgentContext`。
- 已改造 `invest/agents/` 与 `invest/meetings/`，新增 `analyze_context` / `run_with_model_output` / `run_with_eval_report` 等入口，LLM 层改为消费叙事上下文和评估报告。
- 已完成 `app/train.py`、`app/commander.py`、`app/web_server.py` 的 v2 编排接线，新增 `/api/investment-models` 并在训练过程中暴露激活模型与配置。
- 已完成 `invest/evolution/mutators.py`，进化对象切到 YAML；当前仓库已生成 `data/evolution/generations/momentum_v1_cycle_0999.yaml` 作为验证产物。

## 测试与收口
- 全量测试：`uv run pytest -q` 通过。
- 关键专项：`uv run pytest tests/test_yaml_mutation.py -q` 通过。
- 语法校验：`uv run python -m compileall app invest config train.py commander.py web_server.py` 通过。
- 端到端训练：
  - `INVEST_DISABLE_LIVE_LLM=1 uv run python train.py --cycles 1 --mock --log-level WARNING` 通过。
  - `INVEST_DISABLE_LIVE_LLM=1 INVEST_FORCE_CUTOFF_DATE=20211221 uv run python train.py --cycles 1 --log-level WARNING` 通过。
- 真实训练产物确认走新链路：`runtime/logs/meetings/selection/meeting_0001.json` 中 `model_name=momentum`、`config_name=momentum_v1`，并附带 `agent_context_summary`。

## 纯 v2-only Cutover
- 已物理删除旧架构目录：`invest/selection/`、`invest/trading/`、`invest/evaluation/`，以及兼容壳 `invest/optimization.py`、`invest/core.py`。
- `app/train.py` 已去除 `enable_v2_pipeline` 开关与算法降级路径，训练主链只允许 `InvestmentModel -> SelectionMeeting -> Foundation Engine -> Foundation Metrics -> ReviewMeeting`。
- `config/__init__.py`、`config/services.py`、`app/commander.py`、`app/web_server.py` 已移除 `enable_v2_pipeline` 兼容配置和 API 暴露。
- `invest/foundation/` 已承接交易与评估实现：新增 `engine/contracts.py`、`engine/helpers.py`、`metrics/cycle.py`，并将 `SimulatedTrader`、`BenchmarkEvaluator`、`StrategyEvaluator` 等迁入 v2 官方层。
- `SelectionMeeting.run_with_data()` 和公开 fallback 接口已清理；Agent fallback 保留为私有兜底实现，不再作为旧架构公共 API 暴露。

## 质量控制结论
- 架构边界：新增 `tests/test_architecture_import_rules.py` 作为层间依赖守卫。
- 行为一致性：foundation 通过兼容委托接入旧入口，降低迁移期间行为漂移。
- 测试确定性：`invest/shared/llm.py` 增加 pytest 短路与 `INVEST_DISABLE_LIVE_LLM=1` 运行时开关，避免测试/训练因外部 LLM 抖动失稳。
- 工具现状：仓库当前未安装 `ruff` / `pyright` 可执行文件，因此本轮静态门控以 `compileall` + 全量 pytest 为准。
