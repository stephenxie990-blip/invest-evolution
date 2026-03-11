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

- 已完成 Commander 统一入口 Phase 0~5 实施，核心新增文件为 `app/commander_services.py`、`app/commander_observability.py`、`app/stock_analysis.py`、`tests/test_commander_unified_entry.py`。
- 已修复 `app/commander_services.py` 中 `list_agent_prompts_payload()` 与 `/api/agent_prompts` 兼容壳之间的签名回归，相关旧兼容测试恢复通过。
- 全量回归完成：`./.venv/bin/python -m pytest -q` 通过，仅剩一个 pandas deprecation warning。
- Commander 入口验证完成：
  - `./.venv/bin/python commander.py ask -m '/tool invest_train {"rounds":1,"mock":true}'` 通过
  - `./.venv/bin/python commander.py ask -m '/tool invest_train {"rounds":1,"mock":false}'` 通过
- 真实 ask 训练完成后生成实验工件：
  - plan: `runtime/state/training_plans/plan_20260311_191203_527362.json`
  - run: `runtime/state/training_runs/run_20260311_191629_472472.json`
  - evaluation: `runtime/state/training_evals/run_20260311_191629_472472.json`
- 当前系统状态：Commander 已成为推荐主入口；Web 保留为兼容/可视化层；若继续演进，可进一步把更多 API 壳收缩为 Commander 专属对话任务。

- 已完成 Commander 自然语言入口稳定性专项审计，并对 `brain/runtime.py` 的 builtin intent 路由做了收紧与补强。
- 新增自然语言回归场景：数据状态优先级、配置问题不误入问股、状态+最近训练组合响应、控制面配置概览。
- 定向测试通过：`./.venv/bin/python -m pytest tests/test_commander_unified_entry.py tests/test_brain_runtime.py -q`
- 全量测试再次通过：`./.venv/bin/python -m pytest -q`
- CLI 真实入口仿真通过：`请看看系统状态`、`请帮我刷新数据状态`、`我想看看配置有没有问题`、`分析一下系统状态和最近训练`、`看看控制面配置`。

- 已完成 Commander 全功能用户仿真补充：临时 workspace + 临时 DB 下跑通 41 项自然语言/显式工具/门控/观测调用。
- 已修复多轮真实训练的确认语义：`真实/实盘` 不再等同于确认；只有 `确认/confirm` 才能越过高风险门控。
- 新增测试：`test_runtime_ask_multi_round_real_training_requires_explicit_confirmation`，位置 `/Users/zhangsan/Desktop/投资进化系统v1.0/tests/test_commander_unified_entry.py`。
- 全量测试再次通过：`./.venv/bin/python -m pytest -q`。

- 已完成第二阶段首版：`ask_stock` 升级为 YAML + 工具编排 + 执行轨迹模式，核心文件为 `app/stock_analysis.py`。
- 已升级策略文件：`stock_strategies/chan_theory.yaml`、`stock_strategies/trend_following.yaml`，新增 `aliases` 与 `tool_call_plan`。
- 已新增测试文件 `tests/test_stock_analysis_react.py`，并增强 `tests/test_commander_unified_entry.py` 对新 orchestration 结构的断言。
- 定向测试通过：`./.venv/bin/python -m pytest tests/test_stock_analysis_react.py tests/test_commander_unified_entry.py tests/test_brain_runtime.py -q`
- CLI 实测通过：
  - `./.venv/bin/python commander.py ask -m '用缠论分析 600031'`
  - `./.venv/bin/python commander.py ask -m '用趋势跟随分析 600031'`
- 编译与全量回归通过：`python3 -m compileall ...`、`./.venv/bin/python -m pytest -q`

- 已完成第三阶段增强：`ask_stock` 支持真正的 stock LLM ReAct loop，并保留 `yaml_react_like` fallback。
- `app/commander.py` 已将 Commander 当前 LLM 配置传给 `StockAnalysisService`，同时在 `mock_mode=True` 时自动关闭 stock planner 的 LLM 路径。
- 新增测试覆盖：
  - `llm_react` 正常执行工具链
  - `llm` 不产出工具调用时自动 fallback
- 真实 CLI 验证通过：`./.venv/bin/python commander.py ask -m '用缠论分析 600031'` 返回 `orchestration.mode = llm_react`。
- 编译与全量测试再次通过：`python3 -m compileall ...`、`./.venv/bin/python -m pytest -q`
