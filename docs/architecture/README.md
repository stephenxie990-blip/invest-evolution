# Architecture 文档索引

本目录收录“当前实现层”的架构说明，优先服务于理解现状、排障和继续收口。

## 推荐阅读顺序

1. `CONTROL_PLANE_IMPLEMENTATION_20260311.md`：控制面实现、配置分层与运行约束
2. `COMMANDER_CAPABILITY_MATRIX_20260311.md`：`commander` 当前能力面与职责边界
3. `COMMANDER_UNIFIED_ENTRY_UPGRADE_PLAN_20260311.md`：统一入口演进方案与待补齐项
4. `model-routing-rfc.md`：模型路由策略与 LLM 出口设计

## 文档分组

### 当前实现

- `CONTROL_PLANE_IMPLEMENTATION_20260311.md`：描述 LLM 控制面、配置装配与运行时落点
- `COMMANDER_CAPABILITY_MATRIX_20260311.md`：盘点 `commander` 已接管的能力与剩余空白

### 演进计划

- `COMMANDER_UNIFIED_ENTRY_UPGRADE_PLAN_20260311.md`：统一 `commander` 入口的收口路线
- `model-routing-rfc.md`：围绕模型选择、路由和统一出口的设计约束

## 使用建议

- 想看“代码今天怎么组织”，先读本目录
- 想看“未来要怎么继续拆/并/重构”，再去 `docs/blueprints/README.md`
