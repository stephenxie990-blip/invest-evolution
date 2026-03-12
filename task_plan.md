# Task Plan: 研究一体化融合方案

## Goal
基于当前仓库真实实现，研究“训练链 + 问股链”割裂问题，提出统一研究引擎的架构方案、迁移路径与验证闭环。

## Current Phase
Phase 1

## Phases
### Phase 1: Requirements & Discovery
- [x] Understand user intent
- [x] Identify constraints and requirements
- [ ] Document findings in findings.md
- **Status:** in_progress

### Phase 2: Current Architecture Analysis
- [ ] Locate training and ask-stock pipelines
- [ ] Identify shared/duplicated state and semantics
- [ ] Map dataflow and responsibility boundaries
- **Status:** pending

### Phase 3: Unified Engine Design
- [ ] Define target domain model
- [ ] Design unified execution flow
- [ ] Define evaluation and feedback loop
- **Status:** pending

### Phase 4: Migration Planning
- [ ] Propose phased migration path
- [ ] Define rollout risks and compatibility strategy
- [ ] Identify short-term high-leverage changes
- **Status:** pending

### Phase 5: Delivery
- [ ] Deliver research proposal to user
- [ ] Include milestones and next actions
- [ ] Reference current code locations
- **Status:** pending

## Key Questions
1. 训练链与问股链分别由哪些模块主导，边界如何划分？
2. 当前系统缺失的“统一语义层/状态层/归因层”具体体现在哪些代码断点？
3. 如何在不做大爆炸重构的前提下，演进到统一研究引擎？

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| 先做仓库级结构摸底再出方案 | 方案必须绑定现有实现，避免空中楼阁 |
| 使用文件化 planning 记录研究 | 任务跨度较大，便于持续收敛 |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| `python` command not found when running session-catchup | 1 | 改用 `python3` 或直接继续手工初始化 planning files |

## Notes
- 重点不是叠加新功能，而是统一研究语义、时序因果和验证闭环
- 优先寻找最小破坏式演进路径
