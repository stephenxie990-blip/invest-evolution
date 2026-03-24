# Security / 安全说明

## Current Security Posture / 当前安全定位

本项目当前定位为：

- 本地研究 / 训练 / 运行平台
- Agent-first 协作系统
- 非生产级实盘资金托管系统

这意味着：

- 任何默认配置都不应被理解为“适合直接用于真实资金自动交易”
- 安全问题不仅包括传统 Web/API 问题，也包括 Agent 系统特有的问题

## Please Report Privately / 请私下反馈漏洞

如果你发现以下类型的问题，请不要先公开披露细节：

- 密钥、令牌、凭据泄露
- 鉴权绕过
- 任意配置修改
- 任意文件读写 / 路径逃逸
- tool calling 越权
- prompt injection 导致的越权行为
- 运行时状态、bridge、memory、artifact 被恶意利用
- dependency / supply chain 风险

请通过私下渠道先反馈，并尽量提供：

- 影响范围
- 复现步骤
- 预期行为
- 实际行为
- 你认为的风险等级

## Security Priorities / 当前安全优先级

当前项目最重要的安全关注点包括：

1. **Web/API auth and config mutation safety**  
   包括控制面、运行时路径、prompt 更新、训练触发等接口。

2. **Agent and tool boundary safety**  
   包括 Commander tool calling、角色越权、结构化输出修复与 guardrail 绕过。

3. **Secrets and runtime artifacts**  
   包括 API key、token、runtime state、runtime event logs、memory、outputs。

4. **Dependency and environment safety**  
   包括本地运行依赖、第三方数据源、可选依赖与导入边界。

## Out of Scope / 暂不作为高优先级处理的内容

以下问题如果不产生实际安全影响，通常不作为高优先级安全漏洞处理：

- 单纯的研究结果差或收益差
- 非安全性质的提示词质量问题
- 不影响越权、泄密或破坏的文档错误
- 仅限本地开发环境、且不涉及敏感资源的轻微易用性问题

## Safe Usage Reminder / 使用提醒

- 不要把真实交易密钥直接提交到仓库
- 不要在未完成鉴权与部署加固的情况下暴露 API 到公网
- 不要把当前仓库默认视为“可直接自动托管真实资金”的系统
- 任何高风险自动化能力都应在明确治理和授权前提下启用

## Disclosure Principle / 披露原则

我们倾向于：

1. 先确认问题
2. 先修复或缓解
3. 再公开必要信息

对于 Agent 系统，安全问题往往和治理问题交织，所以我们会优先看：

- 是否越权
- 是否破坏边界
- 是否难以审计
- 是否可能对真实环境造成不可控影响
