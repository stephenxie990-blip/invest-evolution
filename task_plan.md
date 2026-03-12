# Task Plan: 研究一体化融合方案

## Goal
基于当前仓库真实实现，先完成统一研究引擎方案与执行蓝图，再按 Phase 0-4 顺序完成最小可运行实现、测试与验收工件。

## Current Phase
Phase 9

## Phases
### Phase 1: Requirements & Discovery
- [x] Understand user intent
- [x] Identify constraints and requirements
- [x] Document findings in findings.md
- **Status:** complete

### Phase 2: Current Architecture Analysis
- [x] Locate training and ask-stock pipelines
- [x] Identify shared/duplicated state and semantics
- [x] Map dataflow and responsibility boundaries
- **Status:** complete

### Phase 3: Unified Engine Design
- [x] Define target domain model
- [x] Design unified execution flow
- [x] Define evaluation and feedback loop
- **Status:** complete

### Phase 4: Migration Planning
- [x] Propose phased migration path
- [x] Define rollout risks and compatibility strategy
- [x] Identify short-term high-leverage changes
- **Status:** complete

### Phase 5: Delivery
- [x] Deliver research proposal to user
- [x] Deliver executable blueprint with acceptance and scheduling
- [x] Reference current code locations
- **Status:** complete

### Phase 6: Phase 0-4 Implementation
- [x] 落盘 `invest/research/*` domain contracts / bridge / case / attribution / scenario / renderer
- [x] `ask_stock(as_of_date=...)` 接入 research bridge
- [x] `CommanderRuntime` / `invest_ask_stock` 透传 `as_of_date`
- [x] 持久化 case / attribution / calibration report
- [x] 新增 research tests 与 phase eval markdown
- **Status:** complete


### Phase 7: Training Feedback Loop
- [x] 将 ask 侧 calibration feedback 注入训练 cycle / report / snapshot
- [x] 让 `ReviewMeeting` 消费 `research_feedback` 并影响 fallback / prompt
- [x] 补充 training feedback / review feedback 回归测试
- **Status:** complete


### Phase 8: Feedback-Driven Optimizer & Freeze Gate
- [x] 让 optimizer 直接消费 multi-horizon `research_feedback` 生成自动调参 plan
- [x] 让 freeze gate 直接消费 calibration / bias / horizon 指标
- [x] 将 gate evaluation 透传到 training report / snapshot
- [x] 补充 multi-horizon feedback 回归测试
- **Status:** complete


### Phase 9: Promotion Gate Calibration
- [x] 让 training evaluation / promotion gate 消费 `research_feedback`
- [x] 将校准门并入 candidate promote/reject verdict
- [x] 透传 `research_feedback` 到 training run result dict
- [x] 补充 commander 晋升门回归测试
- **Status:** complete

## Key Questions
1. 训练链与问股链分别由哪些模块主导，边界如何划分？
2. 当前系统缺失的“统一语义层/状态层/归因层”具体体现在哪些代码断点？
3. 如何在不做大爆炸重构的前提下，演进到统一研究引擎？
4. 如何定义可执行的实施路径、验收标准与阶段退出门？
5. 如何安排 subagent / skills，使升级过程可持续推进并受控验收？

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| 先做仓库级结构摸底再出方案 | 方案必须绑定现有实现，避免空中楼阁 |
| 使用文件化 planning 记录研究 | 任务跨度较大，便于持续收敛 |
| 将执行蓝图独立成文 | 便于直接指导后续实施 |
| 在蓝图中强化 `version_hash` 与评分时钟 | 防止后期归因与校准漂移 |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| `python` command not found when running session-catchup | 1 | 改用 shell 原生命令完成 planning 初始化 |
| Large file output truncated during exploration | 1 | 分块读取关键方法与契约对象 |
| shell `apply_patch` warning | 1 | 后续改为 here-doc 直接写文件 |

## Notes
- 重点不是叠加新功能，而是统一研究语义、时序因果和验证闭环
- 优先寻找最小破坏式演进路径
- 当前已完成 Phase 0-4 的最小可运行实现与核心测试
- 当前已完成 ask→train 的最小闭环：case / attribution / calibration → training feedback → review meeting / commander snapshot

### Phase 10: Training Plan Default Calibration Gate
- [x] 在 training plan 生成阶段自动注入 `optimization.promotion_gate.research_feedback` 默认模板
- [x] 保持用户自定义 `promotion_gate` / `research_feedback` 的深度合并能力
- [x] 补充 artifact store 与 commander create plan 回归测试
- [x] 运行 targeted / broader pytest 验证默认 gate 不破坏训练链
- **Status:** complete

### Phase 11: Calibration Gate Visibility
- [x] 将默认 `research_feedback` 校准门摘要暴露到 training plan 响应
- [x] 将 training execute / train API 响应补充 promotion 校准摘要与失败原因
- [x] 补充 commander / web / artifact 可见性回归测试
- [x] 运行 targeted / broader pytest 验证响应扩展不破坏现有链路
- **Status:** complete

### Phase 12: Daily Review & Closure
- [x] 盘点今日变更范围与阶段成果
- [x] 复核核心 Python 文件编译通过
- [x] 复跑今日关键回归套件并确认稳定通过
- [x] 识别收口阻塞与非阻塞事项
- [x] 形成明日续接前的退出门与收口清单
- **Status:** complete
