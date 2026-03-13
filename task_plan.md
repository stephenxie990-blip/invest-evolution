# Task Plan: Agent Foundation Phased Implementation

## Goal
按照既定顺序，为投资进化系统制定一份可直接执行的五阶段实施方案，明确每阶段的改动范围、接入点、风险控制、验收标准和推进节奏。

## Current Phase
Phase 5

## Phases

### Phase 1: Requirements & Discovery
- [x] 理解用户希望按既定五阶段顺序推进
- [x] 复核本项目现有 runtime、contract、training、freeze gate 接入点
- [x] 汇总前一轮研究结论并筛掉不适合立刻落地的方向
- **Status:** complete

### Phase 2: Planning & Structure
- [x] 明确每个阶段的目标、边界与非目标
- [x] 标注每个阶段的核心改动文件与模块
- [x] 给出 feature flag、测试、回滚和验收策略
- [x] 将规划结果整理到正式方案文档
- **Status:** complete

### Phase 3: Execution Backlog Definition
- [x] 将五阶段拆分为可排期的工作包
- [x] 为每个工作包定义前置依赖、输出物与阻塞条件
- [x] 标注哪些阶段适合直接编码实现
- **Status:** complete

### Phase 4: Verification & Rollout Design
- [x] 定义每阶段的单测、golden、freeze gate 扩展项
- [x] 定义灰度开关、默认关闭策略和回滚路径
- [x] 定义上线前检查清单
- **Status:** complete

### Phase 5: Delivery
- [x] 完成正式实施方案文档
- [ ] 更新 findings.md 与 progress.md
- [ ] 向用户交付简明结论与下一步建议
- **Status:** in_progress

## Key Questions
1. 哪些能力可以在不重构单进程 Commander runtime 的前提下直接接入？
2. 哪些阶段必须先做抽象层，才能避免未来返工？
3. 每个阶段最小可交付版本应该是什么，怎样验证它真的带来收益？

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| 先做实施方案，不直接进入编码 | 用户当前要求是规划与制定更务实的方案，先把路径定清楚能减少后续返工 |
| 继续沿用 `Instructor -> Guardrails -> PySR -> E2B -> Temporal` 顺序 | 这是当前系统下收益/成本比最优的路径 |
| 以现有 `control_plane -> runtime contract -> brain runtime -> freeze gate` 作为接入骨架 | 项目已经有治理和协议基础，没必要先换掉主架构 |
| 所有新能力默认走 feature flag / optional dependency | 降低主链风险，保留回滚能力 |
| `Temporal` 先做 workflow 抽象和单流程 PoC | 当前系统仍是单进程内嵌 runtime，直接平台化改造成本过高 |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| `python` 命令不存在，session-catchup 无法执行 | 1 | 改用 `python3` 重新执行 |
| 项目根目录缺少本轮规划文件 | 1 | 确认已有文件仅存在于备份目录，决定在根目录新建本轮专用文件 |

## Notes
- 本轮只制定实施方案，不修改业务代码。
- 方案必须覆盖：改动入口、测试策略、回滚策略、阶段验收。
- 正式方案文档路径：`docs/plans/AGENT_FOUNDATION_PHASED_IMPLEMENTATION_PLAN_20260313.md`
