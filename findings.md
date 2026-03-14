# Findings

## 2026-03-13

### Structural observations

- `app/train.py` 仍以 `SelfLearningController` 为主枢纽，但项目已经引入 `app/training/controller_services.py` 和 `app/training/optimization.py`，适合继续抽 application service。
- `app/stock_analysis.py` 已经有 `_init_strategy_dir()`、`_init_runtime_state_dir()` 等局部拆分，说明该文件适合继续做 service facade 化。
- `app/commander.py` 已完成大量 support 模块下沉，当前更适合做 `application / interface` 包装，而不是直接深拆 runtime 核心。
- `market_data/` 仍未形成显式子服务目录，最适合作为 Wave D 目标。
- 现有测试中已存在 `tests/test_architecture_import_rules.py`、`tests/test_structure_guards.py`，可以在 Wave A 追加 Phase 6 守卫。

### Likely Wave A landing zones

- 新增 `app/application/` 与 `app/interfaces/` 作为骨架目录
- 将 `app/train.py` / `app/stock_analysis.py` 对应的 orchestrator facade 先在新包里落壳
- 为 Web 路由提供资源化包级入口，但先维持旧 `web_*_routes.py` 注册函数
- 为 `market_data/` 增加 services 层 facade，不立即迁移底层实现

### Implemented in this session

- `app/interfaces/web/registry.py` 已成为 Web 路由统一注册入口，`app/web_server.py` 已切换到该入口。
- `invest/services/` 已建立 `SelectionMeetingService`、`ReviewMeetingService`、`EvolutionService` 三个显式 service facade。
- `market_data/services/` 已建立 availability / resolver / benchmark / quality / sync 这批显式 facade。
- `SelfLearningController` 已引入 `TrainingCycleDataService`，将训练周期的随机种子、cutoff 生成、数据诊断、数据加载与 resolution 解包从主方法中抽离。
- `SelfLearningController` 的关键会议路径已改为走 `selection_meeting_service` / `review_meeting_service`。
- `TrainingReviewService` 已落地，`EvalReport` 构造与 review decision 应用不再直接散落在 `run_training_cycle()` 主体中。
- `MarketQueryService` 已落地，`app/commander_support/services.py` 已开始脱离对 `WebDatasetService` 的直接依赖。
- `trigger_loss_optimization()` 已开始优先使用 `evolution_service`，而不是默认直连 `evolution_engine`。

### Constraints observed during implementation

- `brain/` 暂不适合直接做物理目录迁移，因为当前存在 `brain/runtime.py` 等同名模块，若直接改成包目录会引入较高 import 风险。
- `market_data/manager.py` 目前仍是兼容 facade 和聚合入口，Wave D 应继续做内部责任迁移，而不是急于删除。
- `StockAnalysisService` 已有局部初始化拆分，但尚未真正做到“编排层 vs 领域层”分离，适合作为后续 Wave C 的下一刀。
- `app/train.py` 仍然保留较厚的 simulation / evaluation / optimization 主链，后续继续拆时应优先抽“simulation/evaluation/result assembly”，而不是急于大规模改动会议对象本身。

## 2026-03-14

### v1.1 blueprint findings

- 当前系统的真实优势在运行时骨架，而不是训练科学。`control_plane -> runtime contract -> BrainRuntime -> CommanderRuntime/InvestmentBodyService -> SelfLearningController` 这条主链值得保留。
- 当前系统的首要短板不是“Agent 不够强”，而是训练协议不够硬：随机 cutoff、review 输入窗口过短、candidate config 与 active config 边界不硬、GA fitness 与个体评估未严格绑定。
- `ReviewMeeting.run_with_eval_report()` 当前主路径只消费单周期 `EvalReport`，这意味着文档中的“跨周期复盘学习”在实现上仍未真正成立。
- `SimulatedTrader` 已经具备佣金、印花税、滑点、T+1、ATR 和组合风控，因此它不是“零摩擦玩具”；更准确的结论是“有基础摩擦，但缺少微观结构 realism”。
- `autoresearch` 最值得借鉴的是“冻结评估面、缩小可变面、明确 keep/discard 规则”，而不是“无限自主的 agent 研究叙事”。

### v1.1 implementation decisions

- `v1.1` 应先做训练协议硬化与 lineage/promotion discipline，再接 `Instructor`。
- `Phase 6` 只推进对 `v1.1` 直接有价值的 Wave B / Wave C 最小必要部分，不做大规模目录迁移。
- `Instructor` 首批只接入 3 到 5 条高价值 intent，不进行全局替换。
- `Guardrails` 只保护高风险 mutating tools，不扩大到全量只读路径。
- `PySR / E2B / Temporal` 延后到 `v1.2+`。

### Wave B/C/D closeout findings

- `Wave B` 已经完成大部分主链下沉，剩余问题更多是控制器尾部的薄包装聚集与少量 glue 逻辑；这类代码不一定危险，但会持续抬高 `app/train.py` 认知负担。
- `Wave C` 当前更像“已建立 facade，但仍有部分调用习惯没有完全统一”；收口重点不是新增抽象，而是让上层入口稳定依赖显式 facade。
- `Wave D` 当前最适合继续做“调用迁移 + 测试守卫”，而不是大规模改写 `market_data/manager.py`；兼容聚合入口仍然需要保留一段时间。
- 本轮判断标准应从“是否还能继续拆”切换为“是否已经形成默认接入面、是否足够稳定、是否有测试兜底”。

### Wave B/C/D implemented outcomes

- `Wave B` 的关键收口并不在“删掉所有 wrapper”，而在于把内部默认调用链切到 service：`lifecycle -> persistence/freeze`、`experiment -> llm runtime/routing` 已完成这一步。
- `FreezeGateService` 需要同时满足“service 内部可组合”和“外部测试/兼容层可 monkeypatch controller seam”这两个目标，因此最终采用了“优先尊重覆写 hook，否则走内部实现”的折中方案。
- `Wave C` 的核心收益来自“显式 facade 成为默认依赖面”，不是 facade 数量继续增加；`SelectionMeetingService.set_agent_weights()` 和 `EvolutionService` 统一适配 legacy engine 是两处关键收口点。
- `Wave D` 的剩余缺口确实集中在上层状态读取路径，`app/commander_support/status.py` 改为 `MarketQueryService` 后，应用层对 `WebDatasetService` 的直接依赖已经不再是默认路径。
- 对这轮重构最重要的质量信号不是局部 case 通过，而是全量 `ruff / pyright / pytest / freeze_gate` 全绿；这意味着 `Wave B / C / D` 已具备阶段性结项条件。

### Wave E/F implemented outcomes

- `Wave E` 最有价值的切口不是继续拆更多 route 文件，而是把 `web_server.py` 中“contract 路由实现 + display payload 组装”这两类横切逻辑正式下沉到 `app/interfaces/web/`。
- `brain/runtime.py` 中最值得优先抽离的不是 tool loop，而是 human receipt builder；因为它是明显的 presentation 语义，抽到 `brain/presentation.py` 后，runtime orchestration 与 human narration 的边界清晰很多。
- 在抽 presenter 时，必须保留 `latest_event.kind/label/detail/broadcast_text` 和“只有内部事件时的解释文本”这些细节字段，否则 `commander unified entry` 的 human receipt 会立刻回归；这一点已经通过 focused 回归验证。
- `Wave F` 的守卫不应只检查“文件存在”，还要检查新 helper 不反向依赖 entrypoint；因此这轮新增了针对 `app/interfaces/web/*` 与 `brain/presentation.py` 的 import guard。
- 当前 `app/web_server.py` 仍然保留少量兼容 helper 和 bootstrap 逻辑，但已经不再承担 contract/document serving 的具体实现；这满足“thin adapter”目标且风险可控。

### Pre-v1.1 cleanup findings

- 在进入 `v1.1` 训练协议硬化前，仓库更需要一个“清洁闸门”，因为当前主要风险已不是结构缺口，而是历史兼容层累积下来的静默失败与不可观测降级。
- 首轮量化扫描显示，`app/ brain/ invest/ market_data/` 里仍有 47 处高价值静态异味，其中 `late import` 最多，但并非都应立即消灭；很多是可选依赖或循环依赖缓冲带，需要按语义分治。
- 第一批最值得先收的不是 `late import`，而是运行链路中的静默吞错：event callback、artifact reader、runtime event JSONL、LiteLLM 初始化、cycle artifact 拼装。这些问题修复收益高且兼容风险低。
- `PLW0603 global-statement` 目前主要集中在 `app/web_server.py` 和 `app/train.py` 的 bootstrap 单例/事件桥接，不属于“垃圾代码”，但确实是后续结构治理的重点。
- `market_data/ingestion.py`、`market_data/manager.py`、`web_ops_routes.py` 中的大量 `late import` 不能简单按 lint 全量上提；其中不少是为了隔离 `akshare/baostock/tushare` 可选依赖或避免启动时副作用，需要单独设计 provider seam。
- 第二批清理后，`S110 / S112` 已经在核心目录清零，说明“先收静默失败，再谈结构重构”这条顺序是正确的。
- 第三批清理把低风险 `late import` 从 32 处压到 26 处，但剩余 26 处里相当一部分已经靠近真正的架构边界问题，而不再只是代码风格问题。
- 现阶段最值得继续推进的结构债务，不再是零散 `except/pass`，而是两类：
  - `app/web_server.py` / `app/train.py` 的 `global state` 与 bootstrap 单例
  - `market_data/*` / `web_ops_routes.py` 中围绕可选依赖和 runtime fallback 的 `late import`
- 下一批如果继续清 `PLC0415`，应该优先做“无副作用 import 上提”和“抽 provider seam”，而不是机械式把所有 import 提到文件顶部。
- `app/train.py` 的事件回调状态容器化证明了一个低风险收口模式：保留模块级 API，内部改成显式 state object，就能在不破坏上层契约的前提下清掉一部分全局状态异味。
- `app/web_server.py` 的 `PLW0603` 已可在不改 monkeypatch 表面的前提下清零，说明“先去掉函数内 global 写法，再考虑物理状态容器化”是更稳的路径。
- `market_data.manager` 这轮提供了一个明确反例：看似安全的 service import 上提实际会触发 `market_data.services -> market_data.manager` 循环依赖，所以剩余 `PLC0415` 不能按 lint 机械消除。
- 继续收口 `PLC0415` 的优先顺序应调整为：
  - 业务内部纯模块依赖：继续上提
  - `market_data` 与 `web_ops_routes`：先设计 provider seam / runtime accessor，再动 import
- 后续实践证明，上面的判断是对的：`web_ops_routes` 适合直接上提，而 `market_data` 则更适合抽成显式 loader/helper，而不是强行提前导入可选依赖。
- `app/training/runtime_hooks.py` 的引入为 training 子系统提供了一个干净的 runtime seam，既消除了 `lifecycle_services -> app.train` 的反向依赖，又保留了 `app.train` 的兼容导出面。
- `market_data` 的剩余 `PLC0415` 全部清零后，说明这批债务已经从“代码风格问题”升级为了“依赖加载策略问题”；用显式 loader 表达可选依赖，比散落局部 import 更清晰也更可测试。

### Protocol convergence findings

- 目前真正还在漂移的，不再是 `SignalPacket.metadata` 对 `raw_summaries` 的直接消费，而是“稳定字段仍通过隐式 metadata 传递”的尾巴；最明确的一处是 `AgentContext.metadata["confidence"]`。
- 模型层已经把 `market_stats / stock_summaries / raw_summaries` 写入 `SignalPacket.context`，但不少调用仍以“先构造裸 dict，再让契约对象被动归一化”的方式工作，语义上还不够显式。
- 会议层与 Agent 层已经能消费 `StockSummaryView`，但对“显式置信度字段”的依赖还没建起来，所以仍存在 `metadata.get("confidence")` 这种弱协议读取。
- `ask_stock` 当前最大问题不是结构缺失，而是“稳定研究协议”和“兼容顶层字段”混在一起导出；需要保留兼容，但要把 canonical payload 讲清楚。

### Contract hardening findings

- `AgentContext.effective_confidence()` 这种“对象自带兼容解析”的方法，比在 selection/training 里散落 `metadata.get("confidence")` 更稳，也更适合后续继续退役 metadata fallback。
- `ask_stock` 这类外部 payload 的收口不能只停在“字段存在”，还需要测试 canonical section 的 shape；否则后续很容易在无意中把顶层兼容字段重新当主协议使用。
- 当前顶层 `policy_id / research_case_id / attribution_id / resolved_security` 仍值得保留，但已经应该被视为“兼容镜像”，不是未来新增调用方的默认入口。

### Repo scan to blueprint recalibration

- 最近提交与 planning files 交叉核对后，可以把 `Phase 6` Wave A-F 视为已完成基线，而不是 `v1.1` 的未完前置条件。
- `brain/presentation.py` 的出现说明 runtime human receipt/presentation 已经从 `brain/runtime.py` 中抽出，后续 `Instructor` 不应再围绕旧 receipt builder 设计。
- `app/interfaces/web/presentation.py` 与 `app/interfaces/web/contracts.py` 已把 web display payload 和 contract helper 下沉到 interface 层，说明 `Guardrails` 与 structured output 都应该直接对接这些 seam。
- `app/stock_analysis_services.py` 已成为 stock-analysis 主链的重要编排面，`v1.1` 不应继续把旧 `app/stock_analysis.py` 当作主要接入点。
- `invest/contracts/agent_context.py` 的 `confidence/effective_confidence()` 与 `invest/contracts/stock_summary.py` 的 `StockSummaryView` 表明协议收敛已经开始落在显式契约对象上，后续更适合做 tail hardening，而不是再开一轮 schema 漫游式重构。
- `pre-v1.1 cleanup gate` 已经推进到“只清阻塞项”更合理的阶段；如果继续把 cleanup 当主线，会稀释训练协议和治理工作的优先级。
- 因此，`v1.1` 蓝图应从“训练协议硬化 + 最小结构解耦 + Instructor + Guardrails”修正为“训练协议硬化 + protocol tail hardening + Instructor + Guardrails”，并明确采用 `Week 0 + Week 1-5` 的推进节奏。

### Module A slice 1 findings

- 训练主链原先的 `experiment_spec` 只是未经规整的松散 dict，`TrainingExperimentService.configure_experiment()` 缺少 canonicalization，因此 protocol、dataset、optimization 这些边界难以复用和持久化。
- 优化链已经隐含了“candidate config generated; active config unchanged” 与 “candidate auto-applied” 两种不同语义，但在 `TrainingResult`、`cycle_*.json` 和 commander body result 里没有被正式建模，导致 active/candidate 边界不可审计。
- 新增 `app/training/experiment_protocol.py` 后，最小可用的协议层已经建立：
  - `ExperimentSpec` 负责把实验输入规整为 canonical payload
  - `build_cycle_run_context()` 负责把 `active_config_ref / candidate_config_ref / runtime_overrides / review_basis_window / fitness_source_cycles / promotion_decision` 收束成统一上下文
- 这次实现故意把 `review_basis_window` 默认保守表达成当前可证明的事实窗口，而不是虚构“已经实现的滚动复盘”；这能让后续 Week 2 继续演进时更真实、更可审计。

### v1.1 remaining cuts findings

- `review_meeting` 的真实缺口不是 LLM prompt，而是输入事实长期只有单轮 `EvalReport`；现在通过 `build_review_input()` 已把滚动窗口事实正式接入 service 边界。
- `promotion_decision` 只挂在 `run_context` 里时，调用方很难区分“上下文记录”与“治理记录”；拆出 `promotion_record` / `lineage_record` 后，candidate pending / auto-applied 语义终于可以被直接审计。
- `build_cycle_run_context()` 在 auto-apply 场景下原本可能继续暴露旧 `model_output.config_name`；这个 active ref 漂移已经被修正，否则 lineage 会天然失真。
- `selection` 链路里最危险的 tail 不是字段缺失，而是 `confidence` 在多个点位以弱类型进入聚合逻辑；统一 clamp 到 0-1 后，会议得分和 top-level confidence 都更稳定。
- `structured output` 这轮最值得做的不是引完整第三方框架，而是在 runtime wrap 前加一层最小 normalize；这样能用极低改动把 `invest_ask_stock` 和 training plan 两条关键协议面先固定下来。
- `guardrails` 的第一批高价值拦截点已经足够明确：占位符参数、空 patch、缺失 `plan_id`。这些都是 LLM/tool-calling 场景里高频且低成本可防的事故源。

### Instructor expansion and observability findings

- `Instructor` 路线继续往前走时，最有性价比的不是全量接管所有 tool，而是继续扩高价值、强约束、易失真的响应面；这轮把 config update 系列也纳入了 normalize 范围。
- `promotion_record` / `lineage_record` 如果只存在于训练结果明细里，运营与调试场景还是要翻深层 JSON；补到 `training_lab.run.latest_result` 之后，API、web human view、runtime receipt 三个入口终于能一致看到它们。
- human 展示层的真实问题不是“没有字段”，而是“摘要只显示前 4 条事实会把最关键的治理信息挤掉”；把现状摘要窗口放宽后，promotion / lineage 才真正进入可观察面。

## 2026-03-15

### System re-audit findings after the larger refactor

- 旧版升级方案中的两个核心前提已经失效：
  - “先接 `Instructor`” 已不准确，因为 `brain/runtime.py` 已经在主链中初始化并消费 `StructuredOutputAdapter`。
  - “先接 `Guardrails`” 也已不准确，因为 `RuntimeGuardrails` 已经在 mutating workflow 前承担规则阻断。
- 当前系统的主矛盾已经从“缺少框架接入”转为“现有协议化与治理能力是否足够深、足够一致、足够可审计”。
- 训练链路最重要的结构变化，不是 service 数量增加，而是 `experiment_spec -> run_context -> promotion_record/lineage_record -> lab evaluation/web presentation` 这一条事实链已经闭合。
- `ReviewMeeting` 的输入面已经实质升级：虽然核心仍是当前周期评估，但 service 边界已能接收 `recent_results`、`review_basis_window`、`similar_results`、`similarity_summary`、`causal_diagnosis`，这意味着“跨周期复盘”从叙述进入了真实实现。
- `brain/presentation.py` 与 `app/interfaces/web/presentation.py` 的稳定，意味着未来的结构化输出、治理摘要、ops 卡片都应围绕 presentation seam 继续扩，而不是回写 `brain/runtime.py` 或 `app/web_server.py`。
- `freeze_gate` 的 quick 模式已经把协议层回归测试纳入正式门槛，因此后续任何升级如果不能进入这些 focused suites，就不应算真正落地。

### Upgrade-plan recalibration findings

- `Instructor` 应从“框架接入项目”改名为“内建 structured-output 层的第二阶段深化”；第三方框架是否引入，应该降级为后续决策。
- `Guardrails AI` 应从“P0 接入目标”改成“内建 runtime guardrails 的策略化升级参考”；先扩现有规则层，再决定是否外接。
- `PySR`、`E2B`、`Temporal` 依然有借鉴价值，但对当前代码主链不是第一性矛盾，放在 `v1.2+` 更合理。
- `v1.1` 后续更值得继续投资的地方是：
  - protocol tail hardening
  - training protocol completion
  - promotion/lineage discipline
  - structured output / guardrails coverage and policy depth
