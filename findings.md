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
