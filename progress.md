# Progress（2026-03-11）

- 已读取 `pi-planning-with-files` 技能并完成会话同步检查。
- 已确认本次审计范围聚焦后端/训练/agent/数据/调度，忽略 `frontend/` 实现细节。
- 已完成首轮结构盘点：`app/` 统一入口、`brain/` 运行时、`invest/` 训练/会议/优化、`market_data/` 数据层、`config/` 控制面。
- 下一步：深入训练闭环、Commander 运行时、Web API 与数据/agent 编排链路。


- 第二、三波清理完成：移除 evolution LLM 兼容壳，前端设置页改读 `/api/control_plane`，Playwright 设置页测试通过。


- 完成底层瘦身：`config/services.py` 去掉 evolution LLM 暴露逻辑；前端 `settings` 改接 control plane；Playwright 设置页与相关 pytest 回归通过。

- 已完成 Commander 统一入口升级总方案编制，新增 `docs/architecture/COMMANDER_UNIFIED_ENTRY_UPGRADE_PLAN_20260311.md`。
- 已明确升级目标、分阶段实施路径、subagent 工作单元、skills 使用规划与总体验收标准。
- 下一步若进入实施，应从 Phase 1 的“Lab 列表 + 分析查询域 + 配置域”三类能力接入 Commander 开始。
- 已新增 `docs/architecture/COMMANDER_CAPABILITY_MATRIX_20260311.md`，用于按功能域追踪 Commander 覆盖缺口。


- 新增 `resolve_default_llm()` / `build_default_llm_caller()`；训练、commander、LLMCaller 默认装配已切 control plane；相关 pytest 与前端构建通过。
