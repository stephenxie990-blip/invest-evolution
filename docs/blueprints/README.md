# Blueprints 文档索引

本目录收录“重构蓝图 / 提案 / 执行方案”，用于指导继续清理和后续阶段重构。

## 推荐阅读顺序

1. `PROJECT_ARCHITECTURE_BLUEPRINT.md`：当前主干结构蓝图与边界约束
2. `INVEST_REFACTOR_BLUEPRINT.md`：投资域进一步收口方案
3. `DATA_LAYER_UNIFICATION_PLAN.md`：数据层统一计划
4. `RESEARCH_ENGINE_UNIFICATION_PROPOSAL_20260312.md` / `RESEARCH_ENGINE_UNIFICATION_EXECUTION_BLUEPRINT_20260312.md`：研究引擎统一的提案与执行蓝图

## 文档分组

### 当前主干蓝图

- `PROJECT_ARCHITECTURE_BLUEPRINT.md`：仓库当前稳定结构、依赖方向与扩展约束
- `ARCHITECTURE_DIAGRAM.md`：结构图与模块关系速览

### 域内重构

- `INVEST_REFACTOR_BLUEPRINT.md`：`invest/` 域的继续拆分与职责收束
- `DATA_LAYER_UNIFICATION_PLAN.md`：`market_data/` 统一读写面的收口计划

### 专题方案

- `NANOBOT_FUSION_ARCHITECTURE.md`：Nanobot 相关结构方案
- `RESEARCH_ENGINE_UNIFICATION_PROPOSAL_20260312.md`：研究引擎统一提案
- `RESEARCH_ENGINE_UNIFICATION_EXECUTION_BLUEPRINT_20260312.md`：研究引擎统一执行蓝图

## 使用建议

- 想做“下一刀怎么砍”，先看本目录
- 想确认“当前代码已经长成什么样”，回到 `docs/architecture/README.md`
