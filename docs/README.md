# Docs Index / 文档索引

## Start Here / 从这里开始

如果你第一次打开这个仓库，推荐按这个顺序阅读：

1. `../README.md`
2. `ONBOARDING_HANDOFF.md`
3. `MAIN_FLOW.md`
4. `TRAINING_FLOW.md`
5. `AGENT_INTERACTION.md`
6. `CONFIG_GOVERNANCE.md`
7. `DATA_ACCESS_ARCHITECTURE.md`

## Canonical Docs / 当前主入口文档

这些文档构成当前公开仓库的主文档白名单：

- `../README.md`：项目定位、当前能力、快速开始
- `ONBOARDING_HANDOFF.md`：第一小时阅读路径、验证阶梯、owner 视角与 handoff 清单
- `MAIN_FLOW.md`：系统主链路、正式入口、运行时分层
- `TRAINING_FLOW.md`：训练协议、单周期闭环、治理对象与工件
- `AGENT_INTERACTION.md`：Agent-first 协作逻辑、多经理角色边界与认知辅助
- `DATA_ACCESS_ARCHITECTURE.md`：数据底座、读写路径、canonical SQLite
- `CONFIG_GOVERNANCE.md`：配置层级、控制面与高风险修改边界
- `RUNTIME_STATE_DESIGN.md`：运行态目录、锁文件、状态快照与工件
- `COMPATIBILITY_SURFACE.md`：当前 canonical public surface 与正式实现边界
- `RELEASE_READINESS.md`：当前 active release readiness / manual sign-off 清单

## Recent Change Notes / 最近变更摘要

- `V1_5_PHASE3_CHANGE_SUMMARY_2026-03-24.md`：第三阶段 3 个 P0 能力的正式变更摘要、落点、验证结果与剩余观察项
- `V1_5_PHASE3_IMPLEMENTATION_BLUEPRINT_2026-03-24.md`：第三阶段实施蓝图、数据契约、测试矩阵与风险控制
- `V1_5_PHASE3_CANDIDATES_2026-03-24.md`：第三阶段候选项清单；当前已完成全部 P0，P1/P2 仍为后续候选
- `V1_5_PHASE2_CHANGE_SUMMARY_2026-03-24.md`：第二阶段优化与 hardening 的正式变更摘要
- `GOVERNANCE_RECOVERY_CHANGE_SUMMARY_2026-03-24.md`：`v1.5` 针对 `v1.0` 治理主线缺口的恢复摘要、owner map 与验证结果
- `GOVERNANCE_RECOVERY_BLUEPRINT_2026-03-24.md`：治理恢复实施蓝图、恢复范围、owner 挂载点与质量门
- `CHANGE_NOTES_2026-03-23_training_contract_rollout.md`：训练 contract rollout 两次连续提交的简短摘要与验证结果

## Public Docs Policy / 公开文档边界

- GitHub 仓库当前只保留帮助外部理解项目定位、功能形态、运行主链和协作方式的文档。
- 内部评审、一次性执行计划、研究草案与会话归档不在公开仓库长期维护。
- 但只要 runbook 或 onboarding 文档被当前脚本、验证入口或贡献流程直接引用，就应以 active 文档形式保留在 `docs/`。

## Historical Assets / 历史资产

- `archive/`：仅保留仍有引用价值的最小历史资料与归档说明

历史执行计划、会话 handoff、阶段性 closeout 与一次性证据稿原则上不再在主仓库长期保留；
若确需保留，应先证明它仍被当前脚本、流程或审计入口直接引用。

## GitHub Community Docs / GitHub 社区文档

- `../CONTRIBUTING.md`：贡献方式、开发与验证约定
- `../SECURITY.md`：安全边界、漏洞反馈与公开披露原则

## Machine-readable Assets / 机器可读资产

- `contracts/`：前后端契约、Schema、OpenAPI 导出
- `contracts/runtime-api-contract.v2*.json`：当前 canonical runtime contract 及其衍生物

## Note / 说明

- 若文档与代码冲突，以代码为准，再回写文档修正。
