# Docs Index / 文档索引

## Start Here / 从这里开始

如果你第一次打开这个仓库，推荐按这个顺序阅读：

1. `../README.md`
2. `audits/PROJECT_INTERPRETATION_REPORT_20260315.md`
3. `MAIN_FLOW.md`
4. `TRAINING_FLOW.md`
5. `AGENT_INTERACTION.md`

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

## Public Docs Policy / 公开文档边界

- GitHub 仓库优先公开项目定位、核心能力、架构概览、使用方式与审计结论。
- 内部执行路线图、阶段性优先级和工作计划不作为公开文档入口维护。

## GitHub Community Docs / GitHub 社区文档

- `../CONTRIBUTING.md`：贡献方式、开发与验证约定
- `../SECURITY.md`：安全边界、漏洞反馈与公开披露原则

## Specialized Areas / 专题目录

- `contracts/`：前后端契约、Schema、OpenAPI
- `architecture/`：当前实现层架构专题资料
- `blueprints/`：蓝图、提案和执行方案
- `research/`：研究引擎与实验专题
- `runbooks/`：运维、发布、安全与回滚手册
- `archive/`：历史计划、旧评审和会话文档归档

## Archive Policy / 归档说明

- `docs/` 只保留当前主入口和仍有效的计划 / 审计。
- 已过期、已完成或仅用于某次会话的材料统一移入 `archive/`。
- 若文档与代码冲突，以代码和最新审计结果为准，再回写文档修正。
