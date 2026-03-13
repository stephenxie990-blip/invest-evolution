# 第六阶段结构性重构 RFC（2026-03-13）

## 1. 状态

- 状态：Proposed
- 阶段：Phase 6
- 范围：`app/`、`brain/`、`invest/`、`market_data/`、`config/`、`tests/`
- 前置条件：阶段 1-5 已完成，静态质量门禁已通过

## 2. 背景

阶段 1-5 已经完成以下收口工作：

- 清理空异常、空实现、重复导入、魔法数字等静态质量债务
- 修复一批类型漂移、契约不一致、日志缺失与隐藏失败问题
- 清掉全仓可见的 `ruff` / `pyright` / `pytest` / freeze gate 阻塞项
- 完成目录归并、兼容壳收口和主链运行验证

这意味着系统已经从“先把坏味道压下去”进入“可以安全做结构性重构”的阶段。

当前仓库已经具备较稳定的业务分区：

- `app/`：入口、Web、训练入口、薄服务
- `brain/`：agent runtime、scheduler、memory、tools
- `invest/`：投资业务域、会议、进化、评估、路由
- `market_data/`：数据仓、读写、同步、质量审计
- `config/`：演化配置、运行路径、控制面配置

但这些包之间的“逻辑边界”仍然没有被完全显式化。很多职责虽然已经移动到更合适的目录里，但仍然以“大文件 + 大对象 + 多种责任混在一起”的形式存在。这会带来三个后果：

1. 代码能跑，但难以继续安全演进。
2. 新功能容易沿着旧耦合路径继续堆叠。
3. 后续如果做更深的 agent 化、服务化或测试分层，成本会越来越高。

因此 Phase 6 的目标不是“重写系统”，而是在不破坏现有能力的前提下，把当前已经形成的结构继续推进到“可长期演进的架构形态”。

## 3. 问题定义

### 3.1 入口层仍然过厚

当前以下对象仍承载过多职责：

- `app/train.py` 中的 `SelfLearningController`
- `app/stock_analysis.py` 中的 `StockAnalysisService`
- `app/commander.py` 中的 `CommanderRuntime`

典型问题：

- 同时承担流程编排、状态管理、工件落盘、错误处理、展示拼装
- 对下游领域对象的调用边界不清晰
- 难以针对单一职责做单元测试

### 3.2 训练编排与投资领域逻辑仍然耦合

训练主链路已经比较完整，但“流程控制”和“领域决策”仍然经常在同一对象中混写，导致：

- 跳过策略、诊断策略、评估汇总策略不容易独立演进
- Selection / Review / Evolution 之间缺少更清晰的 service 边界
- 训练产物生成逻辑与业务语义纠缠

### 3.3 `market_data/` 仍是“数据大包”

`market_data/` 已经完成初步收口，但内部依然混有多类职责：

- 仓储查询
- 数据同步与写入
- 训练数据构造
- benchmark / index 辅助
- 数据可用性与质量检查

这会导致数据域接口过宽，调用方容易直接依赖底层细节。

### 3.4 `brain/` 的运行协议和展示协议混杂

`brain/runtime.py` 等模块既承载：

- tool loop 与 session runtime
- 风险 gate / task bus 协作
- 面向人类的 receipt / summary / event narration

这会让 runtime contract 与 UI/人类可读文案相互牵扯，不利于后续扩展 CLI / Web / automation 多入口。

### 3.5 配置体系职责边界不够清晰

当前配置相关能力散落在：

- `config/evolution.yaml`
- `config/services.py`
- `config/__init__.py`
- `agent_settings/`
- `runtime/state/runtime_paths.json`

问题不是“文件太多”，而是以下几类配置尚未严格分离：

- 静态业务配置
- 运行时路径配置
- LLM / control plane 配置
- Web / 接口侧配置
- 策略与治理类配置

### 3.6 Web 路由按“操作类型”分组，不够资源化

当前路由已经从 `web_server.py` 中拆出，但仍主要按：

- read
- ops
- data
- command

来分组。这对于快速收口是有效的，但长期看会带来：

- 同一资源的 GET / POST / execute 分散在不同文件
- API 行为扩展时需要跨多个 route module 跳转
- 契约、测试、权限和响应结构不容易围绕资源聚合

### 3.7 测试布局仍偏“跟着历史模块跑”

虽然测试已经能通过，但测试目录尚未完全对齐目标分层：

- 单元测试与集成测试边界不够清楚
- 应用层、领域层、接口层的测试意图不够明显
- 架构依赖约束没有自动化防回退

## 4. 目标

Phase 6 的目标如下：

1. 建立清晰的逻辑分层：`interface -> application -> domain -> infrastructure`
2. 让训练编排、会议编排、进化编排成为显式服务，而不是分散在超大对象中
3. 让 `market_data/` 内部职责从“包级混合”变成“子服务 + 窄接口”
4. 让 `brain/` 的 runtime protocol 与人类展示协议分离
5. 让配置体系按职责分包，减少调用方对文件与存储位置的感知
6. 让 Web API 从“按模块收口”进化到“按资源组织”
7. 让测试与架构守卫同步升级，避免重构完成后再次回退

## 5. 非目标

本 RFC 明确不做以下事情：

1. 不重写投资策略、评估算法或模型逻辑本身
2. 不改变已稳定的 CLI / Web 对外契约，除非通过兼容层平滑过渡
3. 不在 Phase 6 中引入新的远程服务拆分或微服务化
4. 不大规模修改已有运行工件格式，除非先提供读取兼容层
5. 不追求一次性物理目录大迁移，优先做逻辑分层与兼容重定向

## 6. 核心原则

### 6.1 先做逻辑分层，再做物理迁移

优先把职责、依赖方向、服务边界理顺，再逐步迁移目录。避免一开始就做大规模 rename，导致 diff 噪音过大、回归面失控。

### 6.2 保持外部稳定，内部逐步替换

对外仍保留当前稳定入口：

- CLI 命令
- Web API 路径
- runtime artifact
- 兼容壳模块

内部则通过 façade、adapter、re-export 逐步切换实现。

### 6.3 每次只抽一层责任

每一轮重构尽量只解决一类问题，例如：

- 先抽 orchestration service
- 再抽 persistence / artifact service
- 再抽 presentation / receipt builder

避免一次改完所有责任，导致问题定位困难。

### 6.4 用测试和依赖守卫替代“口头约定”

重构完成后必须引入架构防回退措施，而不是仅靠代码评审记忆边界。

## 7. 目标架构

### 7.1 逻辑分层模型

```text
interface
  ├─ CLI
  ├─ Web API
  ├─ SSE / event stream
  └─ external compatibility shells

application
  ├─ commander orchestration
  ├─ training orchestration
  ├─ selection/review/evolution coordination
  └─ query / command use cases

domain
  ├─ invest domain models, meetings, routing, evolution rules
  ├─ market data domain contracts
  └─ runtime/task semantics

infrastructure
  ├─ sqlite repository
  ├─ file persistence
  ├─ LLM gateway
  ├─ runtime path storage
  └─ config loading / serialization
```

### 7.2 依赖规则

允许依赖：

- `interface -> application`
- `application -> domain`
- `application -> infrastructure`
- `infrastructure -> domain contracts`

禁止依赖：

- `domain -> app/web/cli`
- `domain -> Flask`
- `invest -> app/web_*`
- `market_data -> brain/runtime`
- `brain/runtime core -> human presentation formatter`

推荐理解为：

1. interface 负责收参、鉴权、协议适配、响应编码
2. application 负责 use case 编排
3. domain 负责业务语义、规则、契约、决策
4. infrastructure 负责存储、外部调用、序列化和 IO

## 8. 目标目录形态

本 RFC 推荐的目标目录形态如下。注意这是一份“目标结构图”，不是要求一次性完成全部物理迁移。

```text
app/
  interfaces/
    cli/
    web/
    sse/
  application/
    commander/
    training/
    investment/
  compatibility/

brain/
  runtime/
  protocols/
  scheduling/
  memory/
  tasking/

invest/
  domain/
  services/
  meetings/
  evolution/
  router/
  contracts/
  shared/

market_data/
  repository/
  sync/
  query/
  quality/
  services/
  contracts/

config/
  schema/
  loaders/
  services/
  policy/

tests/
  unit/
    application/
    domain/
    infrastructure/
    interfaces/
  integration/
  contracts/
  regression/
  architecture/
```

## 9. 关键重构流

### 9.1 训练编排服务化

把 `app/train.py` 中过重的控制器拆成更清晰的 application services。

建议新增或抽取的对象：

- `TrainingOrchestrator`
- `TrainingPlanService`
- `TrainingRunPersistence`
- `TrainingEvaluationService`
- `TrainingDiagnosticsPolicy`

职责划分建议：

- `TrainingOrchestrator`：只负责训练流程编排
- `TrainingPlanService`：只负责计划解析、加载、校验
- `TrainingRunPersistence`：只负责工件持久化与状态落盘
- `TrainingEvaluationService`：只负责评估结果汇总与导出
- `TrainingDiagnosticsPolicy`：只负责 skip、warning、degrade 策略

### 9.2 投资分析与会议编排服务化

把 `app/stock_analysis.py` 与 `invest/meetings/*` 的编排责任进一步显式化。

建议目标：

- `StockAnalysisService` 收敛为 facade，而非承载全部细节
- 新增 `SelectionMeetingService`
- 新增 `ReviewMeetingService`
- 新增 `EvolutionService`

推荐边界：

- `SelectionMeetingService`：组织候选生成、会议输入、决策输出
- `ReviewMeetingService`：组织复盘输入、结果裁判、改进建议
- `EvolutionService`：统一 mutation / optimizer / prompt evolution 调度

### 9.3 `market_data/` 子服务化

建议把当前“数据大包”明确拆成以下责任：

- `DataAvailabilityService`：数据是否齐备、区间是否可训练
- `MarketSyncService`：数据抓取、回填、同步
- `TrainingDatasetResolver`：训练集与读侧数据拼装
- `BenchmarkService`：指数、基准、对比口径
- `QualityAuditService`：质量检查、缓存校验、告警输出

这类服务可以先以类或模块函数存在，不要求一开始就改成复杂 IOC。

### 9.4 `brain/` runtime protocol 与 presentation 解耦

建议把当前 runtime 相关实现拆为三层：

- `runtime core`：session、tool loop、state transition
- `runtime protocol`：事件、task bus、schema contract、receipt contract
- `presentation`：给人类看的 summary、narration、status text

目标是让：

- CLI / Web / automation 共用同一 runtime core
- 展示格式变化不影响 runtime 协议
- 事件对象成为一等公民，而不是到处拼字符串

### 9.5 配置体系分层

建议把配置能力拆成五类：

- `StaticConfigLoader`
- `RuntimePathService`
- `ControlPlaneConfigService`
- `WebConfigService`
- `PolicyLoader`

目标：

1. 调用方只依赖服务，不感知底层 YAML / JSON 文件布局
2. 配置变更路径统一
3. 不同配置的校验逻辑分开维护

### 9.6 Web API 资源化

建议把当前 route modules 从“read / ops / data / command”逐步迁移为“按资源组织”：

- `routes/runtime.py`
- `routes/training.py`
- `routes/training_lab.py`
- `routes/strategies.py`
- `routes/leaderboard.py`
- `routes/allocator.py`
- `routes/memory.py`
- `routes/cron.py`
- `routes/configuration.py`
- `routes/data.py`
- `routes/contracts.py`

迁移原则：

1. 先保留原注册函数签名
2. 内部逐步把 handler 移到资源模块
3. 最终由统一 router registry 注册

### 9.7 测试分层与架构守卫

Phase 6 完成时，测试体系应新增以下能力：

- application 层单元测试
- domain 层纯逻辑测试
- Web contract 测试
- architecture import rule 测试

建议新增的守卫：

- 禁止 `invest/` 导入 `app.interfaces.web`
- 禁止 `market_data/` 导入 `brain.runtime`
- 禁止 domain 层直接依赖 Flask / request / Response
- 关键 façade 文件行数和 public method 数量设置预算预警

## 10. 渐进迁移方案

### 10.1 Wave A：建立骨架，不改行为

目标：

- 新建 application / interface / infrastructure 对应子目录
- 引入 façade 与 service skeleton
- 把旧入口继续指向新骨架

产出：

- 新目录
- 空壳服务或薄实现
- 兼容导出
- 架构说明文档

### 10.2 Wave B：抽训练编排

目标：

- 从 `SelfLearningController` 中抽出 training orchestration 与 persistence
- 保持现有 CLI / Web training 行为不变

产出：

- `TrainingOrchestrator`
- `TrainingRunPersistence`
- 对应测试迁移

### 10.3 Wave C：抽投资分析与会议编排

目标：

- 把 selection / review / evolution 编排从大对象中抽出
- 统一会议输入输出契约

产出：

- `SelectionMeetingService`
- `ReviewMeetingService`
- `EvolutionService`

### 10.4 Wave D：拆 `market_data/`

目标：

- 引入数据可用性、同步、训练集解析、基准服务
- 让上层不再直接感知 repository 细节

产出：

- 数据域服务
- repository façade 收窄
- 读写边界测试

### 10.5 Wave E：重构 runtime protocol 与 Web 资源路由

目标：

- runtime 事件协议与展示解耦
- Web 路由按资源重组

产出：

- protocol contracts
- presentation builders
- 资源化 route modules

### 10.6 Wave F：清理兼容层并加守卫

目标：

- 删除阶段性过渡代码
- 固化 import rule 与分层测试

产出：

- 兼容壳最小化
- architecture tests
- 文档与运行图更新

## 11. 兼容与回退策略

### 11.1 兼容策略

重构期间保留以下兼容面：

- 现有 CLI 命令名不变
- 现有 Web API 路径不变
- 现有 runtime artifact 路径与主要字段不变
- 现有 `app/*.py` 入口仍可调用

### 11.2 回退策略

每个 Wave 都应满足：

1. 结构新增优先，逻辑替换次之
2. 旧 façade 在一段时间内同时保留
3. 每次迁移后都能独立执行 `pytest`
4. 如果新 service 出现行为漂移，可快速切回旧实现

建议做法：

- 先抽函数，再抽类，再改调用入口
- 高风险路径保留 shadow adapter
- 关键响应结构用 contract tests 锁定

## 12. 风险分析

### 12.1 行为漂移风险

当 orchestrator 被拆开后，最容易出现：

- 工件字段变化
- 默认值变化
- skip / degrade 条件变化
- 日志与事件顺序变化

应对方式：

- 对训练输出、会议输出、runtime event 建回归测试

### 12.2 隐式依赖断裂风险

当前部分对象可能依赖未显式声明的共享状态或 helper。拆分后容易暴露。

应对方式：

- 先用窄 façade 包裹旧行为
- 不在第一轮直接深改底层算法

### 12.3 目录迁移噪音风险

大规模 rename 会降低 review 质量。

应对方式：

- 单次 PR 只做一类职责提取
- 先复制并导出，再收缩旧文件

### 12.4 测试误报风险

如果测试还停留在“黑盒全跑通”，则无法证明分层真的建立。

应对方式：

- 增加 architecture tests 和 layer-specific tests

## 13. 验证门禁

Phase 6 每个 Wave 结束时至少应满足：

- `ruff check .`
- `pyright .`
- `pytest`
- `python -m app.freeze_gate --mode quick`

Phase 6 全部完成后，建议新增：

- architecture import tests
- Web contract regression tests
- training artifact regression tests
- runtime event contract tests

## 14. 完成判定

当以下条件同时满足时，可认为 Phase 6 完成：

1. `SelfLearningController`、`StockAnalysisService`、`CommanderRuntime` 的主要流程责任已被显式 service 吸收
2. `market_data/` 已形成可识别的数据域子服务边界
3. `brain/` 已实现 runtime protocol 与 presentation 分离
4. 配置体系已分成静态配置、运行路径、控制面、策略治理等明确服务
5. Web 路由已基本按资源聚合，而不是按 read/ops/data/command 人工分组
6. 测试目录已出现分层意图，且有自动化架构守卫
7. 全量验证继续通过，且无对外契约回归

## 15. 建议优先级

推荐实施顺序：

1. 训练编排服务化
2. 投资分析与会议编排服务化
3. `market_data/` 子服务化
4. runtime protocol / presentation 解耦
5. 配置分层
6. Web 资源化路由
7. 测试分层与架构守卫

这样安排的原因：

- 先抽主链核心对象，收益最大
- 再抽数据与 runtime 边界，减少后续耦合反复
- 最后再重组 Web 与测试，能以更稳定的内部结构为基础

## 16. 开放问题

当前仍需在实施前或实施中确认的点：

1. `brain/` 是否继续作为独立顶层包保留，还是逐步被 `app/application/commander` 吸收一部分 use case
2. `invest/shared/` 是否要继续保留为共享工具区，还是进一步拆到 `contracts/`、`services/`、`foundation/`
3. `market_data/manager.py` 是作为兼容 façade 长期保留，还是在 Phase 6 末期退化成纯 re-export
4. Web API 是否需要在资源化重组时同步生成更正式的 OpenAPI 资源分组文档

## 17. 结论

Phase 6 的本质不是“换目录”，而是把当前已经清理干净的系统，推进到一个更清晰的分层架构上。

如果阶段 1-5 解决的是“代码质量能不能站住”，那么 Phase 6 解决的是“这套系统未来还能不能继续长大”。

建议按本 RFC 的 Wave A-F 渐进执行，每一轮都以兼容、可回退、可验证为第一原则。
