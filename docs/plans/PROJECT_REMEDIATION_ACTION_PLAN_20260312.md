# 项目可执行整改清单（2026-03-12）

范围：后端、研究引擎、数据链路、训练流程、交互治理  
不包含：前端页面与视觉交互  
依据：`docs/audits/PROJECT_REVIEW_REPORT_20260312.md`、`docs/blueprints/RESEARCH_ENGINE_UNIFICATION_EXECUTION_BLUEPRINT_20260312.md`

---

## 1. 整改目标

本整改计划的目标不是继续加功能，而是把当前已经打通的“统一研究闭环”收敛成**更稳定、更可维护、更可分工推进**的工程结构。

一句话目标：

> 在不打断当前可运行主链的前提下，完成“核心编排减重、研究对象固化、校准协议统一、状态/工件治理清晰化”。

---

## 2. 总体优先级

### P0：必须优先完成，属于下一阶段启动门

1. 拆轻 `SelfLearningController`
2. 拆轻 `CommanderRuntime`
3. 固化 calibration / research feedback policy schema
4. 减少训练/评估/工件链路中的裸 `dict` 传递
5. 明确 artifact / runtime state / research asset 生命周期边界

### P1：高价值增强，建议紧随 P0 推进

6. 让 `invest/research/*` 升格为真正中间层
7. 统一训练报告、promotion 摘要、guardrails 摘要的 DTO
8. 收敛问股 legacy dashboard 与新 research payload 的双轨
9. 建立 research asset 的查询/检索与重放约定
10. 补强数据 schema migration 与 calibration table 演进约定

### P2：中期治理与长期演进项

11. 统一日志 / memory / artifact 的观测面
12. 为大文件建立包级拆分与模块边界规范
13. 梳理 legacy 兼容层退出节奏
14. 建立更正式的 release / regression / contract freeze 机制

---

## 3. Owner 设计

本项目当前不适合按“技术层”切 owner，而适合按“闭环职责”切 owner。

### Owner A：Research Kernel Owner

负责：

- `invest/research/*`
- 问股与训练共享对象
- hypothesis / attribution / calibration 语义
- `ResearchCaseStore` 生命周期

目标：

- 保证研究对象是系统唯一语义源，而不是 ask/train 各自维护解释字段

对应模块：

- `invest/research/contracts.py`
- `invest/research/snapshot_builder.py`
- `invest/research/case_store.py`
- `invest/research/attribution_engine.py`
- `invest/research/hypothesis_engine.py`
- `invest/research/scenario_engine.py`
- `invest/research/policy_resolver.py`

### Owner B：Training Runtime Owner

负责：

- `SelfLearningController`
- optimization / freeze / promotion gate
- training cycle orchestration
- training lab artifacts

目标：

- 让训练主链更轻、更稳定、更可审计

对应模块：

- `app/train.py`
- `app/training/optimization.py`
- `app/training/reporting.py`
- `app/lab/artifacts.py`
- `app/lab/evaluation.py`

### Owner C：Runtime & Interaction Owner

负责：

- `CommanderRuntime`
- `BrainRuntime`
- task bus / bounded workflow / feedback / next_action
- 工具边界与运行时响应包装

目标：

- 保证所有交互输出的一致性、可解释性、可治理性

对应模块：

- `app/commander.py`
- `brain/runtime.py`
- `brain/task_bus.py`
- `brain/schema_contract.py`
- `brain/tools.py`

### Owner D：Data & Lineage Owner

负责：

- canonical SQLite schema
- ingestion / dataset builders / readiness diagnostics
- point-in-time 数据口径
- 数据 lineage 与 migration 规范

目标：

- 保证 ask/train/research 对数据的时序语义一致

对应模块：

- `market_data/repository.py`
- `market_data/manager.py`
- `market_data/datasets.py`
- `market_data/ingestion.py`
- `market_data/quality.py`

### Owner E：Quality & Governance Owner

负责：

- 契约测试
- golden tests
- regression suite
- 文档同步
- release gate / freeze gate / contract freeze 流程

目标：

- 保证项目在快速演进时不发生隐性语义漂移

对应模块：

- `tests/test_schema_contracts.py`
- `tests/test_commander*_golden.py`
- `tests/test_runtime_api_contract.py`
- `docs/contracts/*`
- `docs/*.md`

---

## 4. P0 整改清单

## P0-1 拆轻 `SelfLearningController`

### 问题

当前 `app/train.py` 过重，`run_training_cycle()` 承担了：

- 数据准备
- 训练编排
- 评估与复盘
- 优化触发
- freeze gate
- 结果落盘
- snapshot 汇总

### 目标

把 `SelfLearningController` 从“超级编排类”拆成一个**协调器 + 多个 service**。

### 交付物

建议拆出以下服务：

1. `TrainingCycleOrchestrator`
2. `TrainingResultAssembler`
3. `TrainingFeedbackService`
4. `FreezePromotionService`
5. `TrainingPersistenceService`

### Owner

- 主 Owner：Training Runtime Owner
- 协同 Owner：Research Kernel Owner

### 工作任务

1. 盘点 `run_training_cycle()` 的阶段边界
2. 抽离“数据准备与诊断”服务
3. 抽离“feedback / freeze / optimization”服务
4. 抽离“结果组装与落盘”服务
5. 保持 `SelfLearningController` 只做 orchestration
6. 补齐服务级单测

### 验收标准

- `app/train.py` 文件行数明显下降
- `run_training_cycle()` 主流程长度缩短到当前的一半左右
- freeze / optimization / persistence 可以单独单测
- 现有训练回归套件不退化

### Subagent 分配

- `subagent-training-orchestrator`
- `subagent-training-feedback-gates`
- `subagent-training-persistence`

### Skills 使用

- `python-patterns`：服务拆分与 dataclass/typing 收敛
- `python-testing`：服务级测试补充
- `verification-loop`：拆分后做回归
- `tdd-workflow`：若采用先测后拆方式

---

## P0-2 拆轻 `CommanderRuntime`

### 问题

`app/commander.py` 已成为统一运行时核心，但当前职责过于集中：

- runtime state
- training lab
- bounded workflow wrapping
- ask / train entrypoint
- artifact paths
- response envelope
- governance metadata

### 目标

把 `CommanderRuntime` 变为**总协调器**，而不是“所有事情都亲手做”的中枢。

### 交付物

建议拆出：

1. `TrainingLabService`
2. `RuntimeStatusService`
3. `WorkflowEnvelopeService`
4. `CommanderResearchFacade`

### Owner

- 主 Owner：Runtime & Interaction Owner
- 协同 Owner：Training Runtime Owner

### 工作任务

1. 抽离 training plan/run/eval 工件管理
2. 抽离 bounded workflow / feedback / next_action 包装
3. 抽离 runtime status snapshot 组装
4. 抽离 ask/train 领域 facade
5. 降低 `CommanderRuntime` 对细节 payload 的直接改写次数

### 验收标准

- `CommanderRuntime` 中 helper 数量明显减少
- `training_lab` 相关逻辑集中到独立 service
- `feedback/next_action` 不再在多个入口重复组装
- `status()` / `train_once()` / `execute_training_plan()` 行为保持兼容

### Subagent 分配

- `subagent-runtime-envelope`
- `subagent-training-lab`
- `subagent-status-snapshot`

### Skills 使用

- `agentic-engineering`：大类拆分与执行顺序控制
- `backend-patterns`：service/facade 分层抽象
- `python-testing`：保持 commander 回归不破
- `verification-loop`：golden + regression 复核

---

## P0-3 固化 calibration / research feedback policy schema

### 问题

目前 optimization、freeze、promotion、default gate 都在使用 `research_feedback`，但 policy schema 还偏分散，未来很容易阈值漂移。

### 目标

定义统一的 `calibration_policy` / `research_feedback_policy` schema，并明确：

- horizon 协议
- blocked bias 协议
- brier-like score 协议
- availability 行为
- default gate / override 合并规则

### 交付物

1. 统一 schema 文档
2. policy dataclass / validator
3. 配置归一化器
4. freeze / promotion / optimization 共用解析入口

### Owner

- 主 Owner：Research Kernel Owner
- 协同 Owner：Training Runtime Owner

### 工作任务

1. 盘点当前所有 `research_feedback` policy 使用点
2. 抽象统一 schema
3. 定义 defaults / merge / strict validation
4. 替换各处 ad-hoc dict 读取逻辑
5. 补 contract tests

### 验收标准

- 所有 `research_feedback` policy 解析都走统一入口
- `app/lab/artifacts.py`、`app/lab/evaluation.py`、`app/training/reporting.py`、`app/train.py` 不再各自定义局部协议
- schema tests / regression 通过

### Subagent 分配

- `subagent-calibration-schema`
- `subagent-policy-normalizer`
- `subagent-gate-regression`

### Skills 使用

- `api-design`：如果需要把 schema 对外暴露为稳定 contract
- `python-patterns`：schema dataclass / validator 实现
- `python-testing`：contract tests
- `verification-loop`：回归验证

---

## P0-4 降低主链裸 `dict` 传递比例

### 问题

项目已有 dataclass contract，但主链仍然大量靠裸 `dict` 传播复杂对象。

### 目标

优先收敛以下链路：

- `research_feedback`
- `promotion`
- `guardrails`
- `training evaluation summary`
- `review meeting facts`

### 交付物

1. DTO / typed payload 层
2. 关键 payload 的构造器
3. 明确的 `to_dict()` 边界

### Owner

- 主 Owner：Runtime & Interaction Owner
- 协同 Owner：Research Kernel Owner

### 工作任务

1. 选定 3~5 条关键 payload 链路
2. 定义 typed object
3. 保留 `to_dict()` 兼容接口
4. 将跨层 dict 改为“typed in / dict out”

### 验收标准

- 关键跨层对象有明确类型
- 字段漂移减少
- commander / training / review 不再反复直接 patch 深层 dict

### Subagent 分配

- `subagent-typed-promotion`
- `subagent-typed-feedback`
- `subagent-typed-review-facts`

### Skills 使用

- `python-patterns`
- `python-testing`
- `coding-standards`

---

## P0-5 明确状态/工件生命周期边界

### 问题

当前项目已经同时维护：

- runtime state
- training artifacts
- research assets
- memory
- config snapshots
- eval docs

但“谁是什么、活多久、在哪里查”还没有彻底制度化。

### 目标

建立统一生命周期与目录治理约定。

### 交付物

1. `artifact taxonomy` 扩展文档
2. 生命周期矩阵
3. 清理/归档规则
4. runtime state 与 research asset 的边界说明

### Owner

- 主 Owner：Quality & Governance Owner
- 协同 Owner：Runtime & Interaction Owner
- 协同 Owner：Research Kernel Owner

### 工作任务

1. 列出现有状态容器
2. 按“短期状态 / 实验工件 / 长期研究资产 / 文档记录”分类
3. 定义命名规范、目录规范、保留周期
4. 定义哪些对象可以被 API 直接返回，哪些只应通过 artifact path 查看

### 验收标准

- 每类工件都有 owner、生命周期、读取入口
- 不再出现“同一对象在多个目录落盘但无明确主副本”的情况

### Subagent 分配

- `subagent-artifact-taxonomy`
- `subagent-state-lifecycle`

### Skills 使用

- `agentic-engineering`
- `verification-loop`
- `content-hash-cache-pattern`（若后续对工件缓存做优化）

---

## 5. P1 整改清单

## P1-1 让 `invest/research/*` 真正升格为中间层

### 目标

让 ask/train/review 使用 research object 作为默认中间态，而不是“新增了一组 helper 但主链仍主要靠 legacy payload”。

### 工作任务

1. 扩大 `ResearchSnapshot` / `PolicySnapshot` / `ResearchHypothesis` / `OutcomeAttribution` 的直接使用范围
2. 收敛 legacy dashboard 与新 research payload 的并行期
3. 让 review meeting facts 尽可能由 research object 驱动

### Owner

- 主 Owner：Research Kernel Owner

### Subagent

- `subagent-research-adoption-ask`
- `subagent-research-adoption-train`
- `subagent-research-adoption-review`

### Skills

- `python-patterns`
- `agentic-engineering`
- `verification-loop`

---

## P1-2 统一报告 DTO 与摘要对象

### 目标

把 `promotion summary`、`guardrails summary`、`evaluation brief`、`feedback summary` 做成统一 DTO 层。

### Owner

- 主 Owner：Runtime & Interaction Owner
- 协同 Owner：Training Runtime Owner

### 工作任务

1. 抽象 summary DTO
2. 统一 `to_dict()` 输出
3. 收敛 commander / API / training_lab 各自的摘要拼装逻辑

### Skills

- `backend-patterns`
- `python-testing`

---

## P1-3 收敛 ask 双轨输出

### 目标

减少 `legacy_dashboard` 与 `research_payload` 双轨长期并存的成本。

### Owner

- 主 Owner：Research Kernel Owner
- 协同 Owner：Runtime & Interaction Owner

### 工作任务

1. 标记 legacy 字段与新字段
2. 给出弃用路线
3. 保留必要兼容层，但明确退出门

### Skills

- `agentic-engineering`
- `verification-loop`

---

## P1-4 research asset 检索与重放约定

### 目标

让 case / attribution / calibration 不只是落盘，还能更容易被查询、比较、重放。

### Owner

- 主 Owner：Research Kernel Owner
- 协同 Owner：Data & Lineage Owner

### 工作任务

1. 定义检索索引字段
2. 增加按 policy/horizon/bias 的查询入口
3. 定义回放协议

### Skills

- `python-patterns`
- `clickhouse-io`（若未来迁移到分析型存储）
- `verification-loop`

---

## P1-5 建立数据 migration 约定

### 目标

为 canonical SQLite 与未来 research/calibration 表扩展建立迁移规范。

### Owner

- 主 Owner：Data & Lineage Owner

### 工作任务

1. 梳理当前 schema 初始化点
2. 建立 migration 目录与约定
3. 为新增 research 表字段提供版本升级脚本

### Skills

- `database-migrations`
- `postgres-patterns`（若未来迁移关系型主库时参考）
- `verification-loop`

---

## 6. P2 整改清单

## P2-1 统一观测面

### 目标

整合：

- event logs
- memory
- training reports
- runtime snapshot
- research assets

形成统一诊断视角。

### Owner

- 主 Owner：Quality & Governance Owner
- 协同 Owner：Runtime & Interaction Owner

### Skills

- `content-hash-cache-pattern`
- `verification-loop`

## P2-2 建立大文件拆分规范

### 目标

为超过 1000~1500 行的关键文件设定拆分阈值与边界模板。

### Owner

- 主 Owner：Quality & Governance Owner

### Skills

- `coding-standards`
- `python-patterns`

## P2-3 Legacy 兼容层退出节奏

### 目标

建立弃用计划，而不是无限保留兼容层。

### Owner

- 主 Owner：Runtime & Interaction Owner
- 协同 Owner：Research Kernel Owner

### Skills

- `agentic-engineering`
- `verification-loop`

## P2-4 Release / regression / contract freeze 机制

### 目标

把当前较强的测试能力制度化为发布门。

### Owner

- 主 Owner：Quality & Governance Owner

### Skills

- `verification-loop`
- `release-skills`

---

## 7. 工作任务分配方案

## 7.1 推荐组织方式：五轨并行，单周节拍

建议不要按文件零散分配，而按工作流分 5 条工作轨：

1. **Research Track**
   - 负责 research object、case store、attribution、policy schema
2. **Training Track**
   - 负责 controller 拆分、optimization/freeze/promotion 统一
3. **Runtime Track**
   - 负责 commander/runtime/task_bus/envelope
4. **Data Track**
   - 负责 readiness、point-in-time、migration、data lineage
5. **Governance Track**
   - 负责 contracts、golden tests、docs、review gates

## 7.2 推荐周节拍

### 周一

- 拆本周 work units
- 冻结接口边界
- 明确 owner 和评审人

### 周二~周三

- subagent 并行开发
- owner 同步 contract 变化

### 周四

- 集成
- 回归
- 文档同步

### 周五

- 评审
- freeze contract
- 更新 blueprint / findings / progress

---

## 8. Subagent 调度方案

## 8.1 调度原则

1. **按闭环分，不按文件分**
2. **每个 subagent 只负责一个清晰 work unit**
3. **所有 subagent 都必须围绕 contract 工作**
4. **owner 负责合并，不让多个 subagent 交叉改同一核心入口过久**

## 8.2 推荐 subagent 清单

### Research 方向

- `subagent-research-contracts`
- `subagent-research-snapshot-policy`
- `subagent-research-attribution-calibration`
- `subagent-research-asset-indexing`

### Training 方向

- `subagent-training-orchestrator`
- `subagent-training-feedback-gates`
- `subagent-training-persistence`
- `subagent-training-promotion-summary`

### Runtime 方向

- `subagent-runtime-envelope`
- `subagent-runtime-status`
- `subagent-runtime-training-lab`
- `subagent-runtime-ask-facade`

### Data 方向

- `subagent-data-readiness-lineage`
- `subagent-data-dataset-builder`
- `subagent-data-migration`

### Governance 方向

- `subagent-contract-golden-tests`
- `subagent-regression-suite`
- `subagent-doc-sync`

## 8.3 调度顺序建议

### 第一批并行

- `subagent-calibration-schema`
- `subagent-training-orchestrator`
- `subagent-runtime-envelope`
- `subagent-artifact-taxonomy`

### 第二批并行

- `subagent-typed-promotion`
- `subagent-typed-feedback`
- `subagent-research-adoption-review`
- `subagent-data-migration`

### 第三批并行

- `subagent-contract-golden-tests`
- `subagent-regression-suite`
- `subagent-doc-sync`

---

## 9. Skills 使用规划

## 9.1 核心技能矩阵

### 研究域整改

- `python-patterns`
- `python-testing`
- `agentic-engineering`
- `verification-loop`

### 运行时与交互治理整改

- `backend-patterns`
- `agentic-engineering`
- `python-testing`
- `verification-loop`

### 数据链路整改

- `database-migrations`
- `python-patterns`
- `verification-loop`

### 文档与契约同步

- `article-writing`（仅用于形成正式方案或说明文档时）
- `verification-loop`
- `pi-planning-with-files`

## 9.2 按阶段推荐 skills

### P0 阶段

- 必用：`pi-planning-with-files`
- 必用：`python-testing`
- 必用：`verification-loop`
- 推荐：`agentic-engineering`

### P1 阶段

- 必用：`python-testing`
- 必用：`verification-loop`
- 推荐：`backend-patterns`
- 推荐：`database-migrations`

### P2 阶段

- 必用：`verification-loop`
- 推荐：`article-writing`
- 推荐：`release-skills`

---

## 10. 推荐执行顺序

### Sprint 1（必须完成）

- P0-3 calibration schema 统一
- P0-1 `SelfLearningController` 拆分第一轮
- P0-2 `CommanderRuntime` 拆分第一轮
- P0-5 生命周期矩阵

### Sprint 2（紧随其后）

- P0-4 typed DTO 收敛
- P1-1 research middle layer 升格
- P1-2 summary DTO 统一

### Sprint 3（结构稳定化）

- P1-3 ask 双轨收敛
- P1-4 research asset 查询/重放
- P1-5 数据 migration 约定

### Sprint 4（治理固化）

- P2-1 统一观测面
- P2-2 大文件拆分规范
- P2-3 legacy 退出计划
- P2-4 发布门与 contract freeze 机制

---

## 11. 退出门（Definition of Done）

一个阶段要算完成，必须同时满足：

1. 代码主链已落地
2. contract / golden tests 已同步
3. 文档已更新
4. planning 记录已同步
5. regression suite 通过
6. 没有新增未归类状态容器

---

## 12. 最终建议

当前项目最适合的推进方式不是“大兵团无序推进”，而是：

- **Owner 负责边界与验收**
- **Subagent 负责清晰 work unit**
- **Skills 负责保证每条工作流都按统一方法执行**

如果严格按这份整改计划推进，项目下一阶段最有希望达成的结果不是“功能更多”，而是：

> **统一研究闭环会从“已经打通”升级为“结构稳定、可持续演进、可多人协作维护”。**
