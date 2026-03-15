# Archive Docs Index

本目录存放不再作为“当前主入口”使用、但仍保留追溯价值的历史文档。

## Archive Rules

- 已完成且被新蓝图/新索引替代的阶段性方案，移入 `archive/`
- 会话级 planning / findings / progress 记录，统一移入 `archive/session/`
- 历史评估、RFC、状态盘点如果不再属于当前实施入口，也移入 `archive/`
- 归档文档默认只保留追溯价值，不再承诺是“当前真相”

## Current Archive Layout

- `architecture/`
  - `COMMANDER_STREAMING_STATUS_20260313.md`
  - `PHASE6_STRUCTURAL_REFACTOR_RFC_20260313.md`
- `audits/`
  - 历史评审、阶段性审计、已被新报告替代的结论
- `plans/`
  - `BACKEND_CLEANUP_CHECKLIST_20260312.md`
  - `FILESYSTEM_REORG_PLAN_20260313.md`
- `session/`
  - `findings_20260313.md`
  - `findings_20260315.md`
  - `progress_20260313.md`
  - `progress_20260315.md`
  - `task_plan_20260313.md`
  - `task_plan_20260315.md`

## Usage

- 想理解“系统现在怎么工作”，回到 `docs/README.md`
- 想查看当前实施主线，优先看 `docs/plans/V1_1_EXECUTION_FREEZE_20260315.md` 与 `docs/plans/MODEL_OPTIMIZATION_REMEDIATION_BLUEPRINT_20260315.md`
- 只有在需要追溯某次阶段决策、清理过程或历史判断时，再进入本目录
