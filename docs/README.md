# Docs Index

## Canonical Docs

这些文档最适合当作“现在这套系统怎么工作”的主入口：

- `MAIN_FLOW.md`：系统正式入口、主链路、Web/API 分组
- `TRAINING_FLOW.md`：训练周期主链路
- `DATA_ACCESS_ARCHITECTURE.md`：数据层读写分层与 canonical SQLite
- `CONFIG_GOVERNANCE.md`：配置分层与治理边界
- `RUNTIME_STATE_DESIGN.md`：运行态工件、锁文件、状态目录
- `COMPAT_CLEANUP_REPORT.md`：当前兼容壳保留策略
- `COMPATIBILITY_SURFACE.md`：根目录兼容入口、正式入口与迁移建议

## Architecture / Blueprint

这些文档描述目标架构、演进蓝图或专题设计：

- `blueprints/ARCHITECTURE_DIAGRAM.md`
- `blueprints/PROJECT_ARCHITECTURE_BLUEPRINT.md`
- `blueprints/NANOBOT_FUSION_ARCHITECTURE.md`
- `blueprints/FRONTEND_BACKEND_SPLIT_BLUEPRINT.md`
- `blueprints/INVEST_REFACTOR_BLUEPRINT.md`
- `blueprints/RESEARCH_ENGINE_UNIFICATION_PROPOSAL_20260312.md`
- `blueprints/RESEARCH_ENGINE_UNIFICATION_EXECUTION_BLUEPRINT_20260312.md`
- `blueprints/DATA_LAYER_UNIFICATION_PLAN.md`
- `audits/DATA_LAYER_UNIFICATION_REPORT.md`

## Plans / Boards

这些文档更偏执行清单、任务拆分与阶段推进：

- `plans/PROJECT_REMEDIATION_ACTION_PLAN_20260312.md`
- `plans/REPAIR_IMPLEMENTATION_BOARD_20260310.md`
- `plans/FRONTEND_REFACTOR_EXECUTION_PLAN.md`
- `plans/INVEST_V2_EXECUTION_PLAN.md`
- `plans/OPTIMIZATION_TRIGGER_MATRIX.md`
- `plans/DATABASE_UPGRADE_V2.md`

## Reviews / Audits

这些文档偏阶段性审计、评审与诊断，适合回看历史结论：

- `audits/PROJECT_AUDIT_20260310.md`
- `audits/PROJECT_REVIEW_REPORT_20260312.md`

## Specialized Areas

- `contracts/`：前后端契约、Schema、OpenAPI
- `architecture/`：更细分的架构专题资料
- `frontend/`：前端专题文档
- `research/`：研究引擎相关专题
- `runbooks/`：运维/执行手册

## Reading Order

如果想快速理解当前项目，推荐按这个顺序读：

1. `MAIN_FLOW.md`
2. `TRAINING_FLOW.md`
3. `DATA_ACCESS_ARCHITECTURE.md`
4. `CONFIG_GOVERNANCE.md`
5. `RUNTIME_STATE_DESIGN.md`
6. `audits/PROJECT_REVIEW_REPORT_20260312.md`
7. `plans/PROJECT_REMEDIATION_ACTION_PLAN_20260312.md`
