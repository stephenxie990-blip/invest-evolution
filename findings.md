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
