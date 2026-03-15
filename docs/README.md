# Docs Index / 文档索引

## Canonical Docs / 当前主入口文档

这些文档代表“当前系统的真相”，优先阅读：

- `../README.md`：项目定位、当前能力、快速开始
- `MAIN_FLOW.md`：系统主链路、正式入口、运行时分层
- `TRAINING_FLOW.md`：训练协议、单周期闭环、治理对象与工件
- `AGENT_INTERACTION.md`：Agent-first 协作逻辑、角色边界、会议链路
- `DATA_ACCESS_ARCHITECTURE.md`：数据底座、读写路径、canonical SQLite
- `CONFIG_GOVERNANCE.md`：配置层级、控制面与高风险修改边界
- `RUNTIME_STATE_DESIGN.md`：运行态目录、锁文件、状态快照与工件

## Latest Reviews / 最新评审与审计

- `audits/PROJECT_INTERPRETATION_REPORT_20260315.md`：本轮升级后的项目解读报告
- `audits/PROJECT_REVIEW_REPORT_20260315.md`：工程视角正式评审
- `audits/MODEL_GOVERNANCE_RERUN_COMPARISON_20260315.md`：治理复跑对比结论
- `audits/DATA_LAYER_UNIFICATION_REPORT.md`：数据层统一专题报告

## Active Plans / 当前有效计划

- `plans/V1_1_EXECUTION_FREEZE_20260315.md`
- `plans/MODEL_OPTIMIZATION_REMEDIATION_BLUEPRINT_20260315.md`
- `plans/V1_1_IMPLEMENTATION_BLUEPRINT_20260314.md`
- `plans/OPTIMIZATION_TRIGGER_MATRIX.md`
- `plans/DATABASE_UPGRADE_V2.md`

## Legacy Index Snapshot

这些文档最适合当作“当前系统怎么工作”的主入口：

- `MAIN_FLOW.md`：系统正式入口、主链路、Web/API 分组
- `TRAINING_FLOW.md`：训练周期主链路与训练实验室工件
- `DATA_ACCESS_ARCHITECTURE.md`：数据层读写分层与 canonical SQLite
- `CONFIG_GOVERNANCE.md`：配置分层、控制面与高风险修改边界
- `RUNTIME_STATE_DESIGN.md`：运行态工件、锁文件、状态目录
- `COMPAT_CLEANUP_REPORT.md`：当前兼容壳保留策略
- `COMPATIBILITY_SURFACE.md`：正式入口、兼容入口与迁移建议

## Legacy Architecture / Blueprints Snapshot

这些文档描述当前主干架构、专题设计和继续演进方向：

- `architecture/README.md`
- `blueprints/README.md`
- `blueprints/ARCHITECTURE_DIAGRAM.md`
- `blueprints/PROJECT_ARCHITECTURE_BLUEPRINT.md`
- `blueprints/INVEST_REFACTOR_BLUEPRINT.md`
- `blueprints/RESEARCH_ENGINE_UNIFICATION_PROPOSAL_20260312.md`
- `blueprints/RESEARCH_ENGINE_UNIFICATION_EXECUTION_BLUEPRINT_20260312.md`
- `blueprints/DATA_LAYER_UNIFICATION_PLAN.md`
- `audits/DATA_LAYER_UNIFICATION_REPORT.md`

## Legacy Plans Snapshot

这些文档对应上一轮索引快照；其中已迁移到 `archive/` 的文件仅保留追溯价值：

- `plans/V1_1_IMPLEMENTATION_BLUEPRINT_20260314.md`
- `archive/plans/AGENT_FOUNDATION_PHASED_IMPLEMENTATION_PLAN_20260313.md`
- `archive/plans/PHASE6_IMPLEMENTATION_PLAN_20260313.md`
- `archive/plans/PROJECT_REMEDIATION_ACTION_PLAN_20260312.md`
- `archive/plans/INVEST_V2_EXECUTION_PLAN.md`
- `plans/OPTIMIZATION_TRIGGER_MATRIX.md`
- `plans/DATABASE_UPGRADE_V2.md`

## Legacy Reviews / Audits Snapshot

这些文档偏阶段性审计、评审与诊断，适合回看历史结论：

- `archive/audits/PROJECT_AUDIT_20260310.md`
- `archive/audits/PROJECT_REVIEW_REPORT_20260312.md`

## Specialized Areas / 专题目录

- `contracts/`：前后端契约、Schema、OpenAPI
- `architecture/`：当前实现层架构专题资料
- `blueprints/`：重构蓝图、提案和执行方案
- `research/`：研究引擎与执行模型专题
- `runbooks/`：运维与执行手册
- `archive/`：历史会话记录、已完成的阶段性方案与归档材料

## Reading Order

如果想快速理解当前项目，推荐按这个顺序读：

1. `../README.md`
2. `audits/PROJECT_INTERPRETATION_REPORT_20260315.md`
3. `MAIN_FLOW.md`
4. `TRAINING_FLOW.md`
5. `AGENT_INTERACTION.md`
6. `DATA_ACCESS_ARCHITECTURE.md`
7. `CONFIG_GOVERNANCE.md`
8. `RUNTIME_STATE_DESIGN.md`
9. `plans/V1_1_EXECUTION_FREEZE_20260315.md`
10. `plans/MODEL_OPTIMIZATION_REMEDIATION_BLUEPRINT_20260315.md`

## Hygiene / 文档治理说明

- `docs/` 只保留当前主入口和仍有效的计划 / 审计。
- 已过期、已完成或仅用于某次会话的材料统一移入 `archive/`。
- 若文档与代码冲突，以代码和最新审计结果为准，再回写文档修正。
