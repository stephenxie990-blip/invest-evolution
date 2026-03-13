# Phase 6 Task Plan

## Goal

按 RFC 推进第六阶段结构性重构，采用分波次实施方式，在保持现有 CLI / Web / runtime 契约稳定的前提下，逐步建立 `interface -> application -> domain -> infrastructure` 的清晰边界。

## Phases

| Phase | Status | Scope | Notes |
|---|---|---|---|
| 0 | complete | 建立实施计划、审阅关键模块、确认可落地点 | 已完成 |
| A | complete | 建立结构骨架、兼容导出、最小架构守卫 | 已落地并验证 |
| B | in_progress | 抽训练编排 service | 已完成 cycle bootstrap、data loading、review report/apply 下沉 |
| C | in_progress | 抽投资分析与会议编排 service | 已完成 selection/review/evolution service façade 接线第一步 |
| D | in_progress | 拆 `market_data/` 子服务 | 已新增 query/availability/resolver/benchmark/quality/sync facades，并开始接入 commander/web 读侧 |
| E | pending | runtime protocol / presentation 解耦，Web 资源化路由 | 目标 `brain/` / `app/web_*` |
| F | pending | 测试分层、架构守卫、兼容层清理 | 收尾与固化 |

## Current Focus

- 继续削薄 `app/train.py` 剩余的大段编排逻辑
- 让更多上层调用从 `market_data` 旧读侧实现迁移到显式 facade
- 为 Wave E 的 runtime protocol / presentation 解耦准备边界

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

## Verification Snapshot

- `ruff check .` 通过
- `pyright .` 通过，`0 errors`
- `.venv/bin/pytest -q` 通过
- `475 tests collected`
- `python -m app.freeze_gate --mode quick` 通过
