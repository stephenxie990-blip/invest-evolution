# Phase 6 Task Plan

## Goal

按 RFC 推进第六阶段结构性重构，采用分波次实施方式，在保持现有 CLI / Web / runtime 契约稳定的前提下，逐步建立 `interface -> application -> domain -> infrastructure` 的清晰边界。

## Phases

| Phase | Status | Scope | Notes |
|---|---|---|---|
| 0 | complete | 建立实施计划、审阅关键模块、确认可落地点 | 已完成 |
| A | complete | 建立结构骨架、兼容导出、最小架构守卫 | 已落地并验证 |
| B | complete | 抽训练编排 service | 已完成控制器主链与尾部 glue 的进一步下沉，service 驱动成为默认实现 |
| C | complete | 抽投资分析与会议编排 service | 已统一 invest facade 调用边界，review / policy / optimization 不再散落依赖底层细节 |
| D | complete | 拆 `market_data/` 子服务 | 已收口上层调用迁移，`market_data/services/` 成为默认 facade 接入面 |
| E | complete | runtime protocol / presentation 解耦，Web 资源化路由 | 已完成 brain receipt presenter 抽离与 web contract/display 接缝下沉 |
| F | complete | 测试分层、架构守卫、兼容层清理 | 已补齐 Wave E/F 守卫与回归，完成全链固化 |

## Current Focus

- 按 `Wave B -> Wave C -> Wave D` 顺序完成本轮结构性收口
- 将 `SelfLearningController` 残余控制器编排继续下沉到 training services
- 统一 invest / meetings / evolution 的 facade 使用边界，减少上层直接依赖细节对象
- 将更多 `market_data` 上层调用迁移到显式 facade，并补充对应测试与验证

## Wave Completion Definition

### Wave B complete when

- `app/train.py` 中训练主链仅保留控制器协调与兼容入口
- 剩余 persistence / report / feedback / freeze 相关包装方法要么进一步下沉，要么明确成为稳定兼容 facade
- 训练 orchestration 的关键路径可通过 service 层单测覆盖

### Wave C complete when

- invest 相关上层入口统一通过 `invest/services/` facade 访问会议与进化能力
- 控制器、优化链、分析链不再散落依赖 meeting / evolution 内部细节
- 兼容导出保持稳定，现有 CLI / runtime / web 契约不变

### Wave D complete when

- `market_data/services/` 成为上层默认接入面
- commander / web / training / 其他读侧调用不再新增对旧聚合实现的直接耦合
- facade 覆盖 query / availability / resolver / benchmark / quality / sync 关键职责，并有测试守卫

## Monitoring Checklist

- 每完成一个 wave，更新 `progress.md` 和 `findings.md`
- 每完成一个 wave，至少执行一次 focused verification
- 全部完成后执行全量验证：`ruff`、`pyright`、`pytest`、`freeze_gate`
- 如出现连续两次同类失败，先记录到 planning files，再切换修复路径

## Wave E Completion Definition

- `brain/runtime.py` 的 human-readable receipt / narration 逻辑已有独立 presenter 接缝
- `app/web_server.py` 不再直接承载 runtime contract 路由实现与 display payload 组装细节
- `app/interfaces/web` 成为 web 资源路由与响应帮助逻辑的默认入口

## Wave F Completion Definition

- 为 `Wave E` 新增的 presentation / contracts 边界补充存在性检查与 import guard
- focused verification 覆盖 runtime contract、web human display、commander unified entry 这些高风险路径
- 全量 `ruff / pyright / pytest / freeze_gate` 全部通过

## Constraints

- 不破坏现有对外契约
- 不回退用户已有未提交修改
- 每个波次结束都做最小验证
- 优先抽职责，不做大规模 rename

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| 过早大迁移导致 diff 噪音过大 | review 难度上升 | 先建立新模块并做薄封装 |
| 主链控制器被过度拆分导致行为漂移 | 训练 / Web 回归 | 每次只抽一类职责，保留兼容 facade |
| 路由重组牵连合同测试 | API 回归 | 先做资源层包装，不立即变更路径 |

## Errors Encountered

| Error | Attempt | Resolution |
|---|---|---|
| `python` command unavailable in shell | 1 | 改用 `python3` |

## Completed This Session

- 新增 `app/application/` 与 `app/interfaces/` 骨架目录
- 新增 `invest/services/` 与 `market_data/services/` service facade
- `app/web_server.py` 已切换到 `app.interfaces.web.register_runtime_interface_routes`
- 新增 Phase 6 Wave A 架构守卫与 facade 验证测试
- 从 `SelfLearningController.run_training_cycle()` 抽出 `TrainingCycleDataService`
- 训练主链已开始通过 `SelectionMeetingService` / `ReviewMeetingService` 调用关键编排路径
- 新增 `TrainingReviewService`，将 `EvalReport` 构造与 review decision 应用从主控制器中下沉
- `app/training/optimization.py` 已优先通过 `evolution_service` 调用进化引擎
- `app/commander_support/services.py` 已改为通过 `MarketQueryService` 访问 market data 读侧
- 已将本轮目标收敛为“彻底完成 Wave B / C / D”，并明确完成定义、监控项与验证门槛
- `TrainingLifecycleService` 已优先通过 persistence / freeze services 完成周期收尾，不再以内循环反向依赖 controller 包装方法
- `TrainingExperimentService` 已直接协调 LLM runtime / routing services，`configure_experiment()` 一带的服务边界进一步收紧
- `TrainingPolicyService` 已通过 `SelectionMeetingService.set_agent_weights()` 同步 agent 权重，去除上层对底层 meeting 属性的直接写入
- `app/training/optimization.py` 已统一通过 `EvolutionService` 边界驱动进化链，并保留对 legacy engine 的兼容适配
- `app/commander_support/status.py` 已切换到 `MarketQueryService`，补齐 `Wave D` 的一处剩余直接耦合
- 已完成 `Wave B / C / D` 收口，并补充相应回归测试
- 已进入 `Wave E / F` 并完成：
  - 新增 `app/interfaces/web/presentation.py`，收口 web display / contract 响应帮助逻辑
  - 新增 `app/interfaces/web/contracts.py` 与 `app/interfaces/web/routes/contracts.py`，将 runtime contract 路由下沉到 interface 层
  - 新增 `brain/presentation.py`，将 `brain/runtime.py` 的 human receipt builder 抽成独立 presenter
  - `app/web_server.py` 现仅保留薄适配与兼容 helper，不再直接持有 contract 路由实现
  - `tests/test_architecture_import_rules.py` 已新增 Wave E/F 边界守卫

## Supplementary Planning Outputs

- 已新增 `docs/plans/V1_1_IMPLEMENTATION_BLUEPRINT_20260314.md`
- 蓝图将 `v1.1` 明确收敛为“训练协议硬化 + 最小必要结构解耦 + Instructor + Guardrails”
- `PySR / E2B / Temporal` 被明确留在 `v1.2+`，不进入 `v1.1` 主版本范围

## Verification Snapshot

- `ruff check .` 通过
- `pyright .` 通过，`0 errors`
- `.venv/bin/pytest -q` 通过
- `475 tests collected`
- `python -m app.freeze_gate --mode quick` 通过

## Wave B/C/D Final Verification

- `.venv/bin/ruff check .` 通过
- `.venv/bin/pyright .` 通过，`0 errors`
- `.venv/bin/pytest -q` 通过，`[100%]`
- `.venv/bin/python -m app.freeze_gate --mode quick` 通过

## Wave E/F Final Verification

- `.venv/bin/ruff check .` 通过
- `.venv/bin/pyright .` 通过，`0 errors`
- `.venv/bin/pytest -q` 通过，`[100%]`
- `.venv/bin/python -m app.freeze_gate --mode quick` 通过
