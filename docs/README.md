# Docs Index / 文档索引

## Start Here / 从这里开始

如果你第一次打开这个仓库，推荐按这个顺序阅读：

1. `../README.md`
2. `MAIN_FLOW.md`
3. `AGENT_INTERACTION.md`
4. `TRAINING_FLOW.md`
5. `DATA_ACCESS_ARCHITECTURE.md`

## Canonical Docs / 当前主入口文档

这些文档构成当前公开仓库的主文档白名单：

- `../README.md`：项目定位、当前能力、快速开始
- `MAIN_FLOW.md`：系统主链路、正式入口、运行时分层
- `TRAINING_FLOW.md`：训练协议、单周期闭环、治理对象与工件
- `AGENT_INTERACTION.md`：Agent-first 协作逻辑、角色边界、会议链路
- `DATA_ACCESS_ARCHITECTURE.md`：数据底座、读写路径、canonical SQLite
- `CONFIG_GOVERNANCE.md`：配置层级、控制面与高风险修改边界
- `RUNTIME_STATE_DESIGN.md`：运行态目录、锁文件、状态快照与工件
- `COMPATIBILITY_SURFACE.md`：兼容入口与正式实现边界

## Public Docs Policy / 公开文档边界

- GitHub 仓库当前只保留帮助外部理解项目定位、功能形态、运行主链和协作方式的文档。
- 内部评审、执行计划、蓝图、runbook、研究草案与会话归档不在公开仓库维护。

## GitHub Community Docs / GitHub 社区文档

- `../CONTRIBUTING.md`：贡献方式、开发与验证约定
- `../SECURITY.md`：安全边界、漏洞反馈与公开披露原则

## Machine-readable Assets / 机器可读资产

- `contracts/`：前后端契约、Schema、OpenAPI 导出

## Note / 说明

- 若文档与代码冲突，以代码为准，再回写文档修正。
