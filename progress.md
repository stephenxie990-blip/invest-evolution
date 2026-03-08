# Progress

- 2026-03-08：恢复上轮上下文，确认 Web 服务在线、训练中心/记忆中心当前缺口与改造目标。
- 2026-03-08：使用 `pi-planning-with-files` 更新文件化实施计划，按 5 步完整推进。
- 2026-03-08：增强 `brain/memory.py`，支持 metadata 检索与按 id 读取记忆详情。
- 2026-03-08：增强 `app/commander.py`，每次训练自动写入 `training_run` 记忆，并为单轮结果增加产物引用。
- 2026-03-08：增强 `app/train.py`，新增 `agent_progress`、`module_log`、`meeting_speech` 等结构化事件，并补齐数据加载、选股、模拟交易、复盘、优化各阶段日志。
- 2026-03-08：增强 `invest/meetings/selection.py`，输出候选推荐、观点和辩论摘要。
- 2026-03-08：增强 `invest/meetings/review.py`，输出 Strategist / EvoJudge / Commander 的阶段性结论。
- 2026-03-08：重构 `static/index.html` 训练中心，改为 Agent 固定卡片实时更新 + 系统工作流瀑布流自动滚动/手动回看。
- 2026-03-08：重构 `static/index.html` 记忆中心，新增训练记忆详情面板，支持查看训练摘要、周期结果、会议纪要、优化记录、配置快照。
- 2026-03-08：新增 `tests/test_web_server_memory_api.py`，验证训练记忆自动写入、metadata 检索和记忆详情接口。
- 2026-03-08：通过静态检查与针对性回归测试，下一步执行真实 Web 训练验证。

- 2026-03-08：补齐训练中心产品化交互，新增时间线筛选、按 Agent 折叠、会议发言高亮与记忆详情“策略差异对比”。
- 2026-03-08：扩展 `/api/memory/<id>` 返回当前训练与上一条训练记忆的收益、选股、参数、模式和关键布尔标记差异。
- 2026-03-08：新增前端静态语义测试，完成 Python / JS 语法校验，并通过 5 组针对性回归测试。
- 2026-03-08：重启 Web 服务至最新代码，确认首页已包含新的训练中心与记忆详情产品化标记。

- 2026-03-08：升级 Agent 工作台可视化界面，新增顶部总览看板、状态优先排序、脉冲状态点、阶段胶囊和迷你指标卡。
- 2026-03-08：通过前端 JS 语法检查与针对性回归测试，并重启 Web 服务验证最新工作台界面已生效。

- 2026-03-08：启动真实训练过程监控，持续轮询 `/api/status`、`/api/memory` 并跟踪 Web 服务日志，准备同步关键阶段和结果。

- 2026-03-08：真实训练于 13:08:24 启动、13:14:08 完成，耗时约 5 分 44 秒；已监控到选股会议、模拟交易、复盘会议与策略调整全流程。
- 2026-03-08：本轮真实训练收益约 -1.42%，选出 4 只股票，交易 8 笔，复盘会议给出保守调整并落库到训练记忆。

- 2026-03-08：重写 `invest/shared/llm.py` 的 JSON 解析流程，新增前后缀剥离、尾逗号修复、缺失闭合补全、Python 字典字面量兼容，并收敛到统一解析入口。
- 2026-03-08：将 `invest/evolution/analyzers.py` 与 `invest/evolution/llm_optimizer.py` 改为复用统一解析器，避免各模块各自脆弱解析。
- 2026-03-08：完成解析器 smoke、`tests/test_train_ui_semantics.py`、`tests/test_web_server_memory_api.py`、`tests/test_all_modules.py` 回归，并再跑 1 轮真实训练验证；本轮收益改善到 -0.14%。

## 2026-03-08 复盘一致性热修复进展
- 已修改 `/Users/zhangsan/Desktop/投资进化系统v1.0/app/train.py`：复盘会议落盘时优先使用 `ReviewMeeting.last_facts`。
- 已修改 `/Users/zhangsan/Desktop/投资进化系统v1.0/invest/meetings/review.py`：缓存聚合事实，并在决策校验后生成 `applied_summary`。
- 已修改 `/Users/zhangsan/Desktop/投资进化系统v1.0/invest/meetings/recorder.py`：Markdown 新增“最终执行摘要”展示。
- 已修改 `/Users/zhangsan/Desktop/投资进化系统v1.0/tests/test_meeting_refinement.py`：补充统计口径和执行摘要一致性测试。
- 验证结果：`uv run pytest tests/test_meeting_refinement.py -q` 通过；`uv run pytest tests/test_meeting_refinement.py tests/test_all_modules.py -q` 通过。

## 2026-03-08 数据库升级 V2 进展
- 已启用 `pi-planning-with-files` 接管本轮数据库升级任务，并将方案落盘到 `docs/DATABASE_UPGRADE_V2.md`。
- 已在 `market_data/repository.py` 增加 `index_bar` 表、状态字段和查询接口。
- 已在 `market_data/ingestion.py` 增加 `sync_index_bars()`，支持按配置指数或默认指数同步。
- 已在 `app/web_server.py` 的后台数据下载链路中接入指数同步。
- 已在 `market_data/quality.py` 增加指数覆盖状态输出，准备进行定向测试验证。
- 2026-03-08：完成 `uv run pytest tests/test_data_unification.py tests/test_governance_phase_a_f.py -q` 定向回归，相关数据层改造通过。
- 2026-03-08：已将 `sh.000001`、`sz.399001`、`sz.399006`、`sh.000300` 的指数日线补入当前库，覆盖 `20150105` 至 `20260306`。

## 2026-03-08 投资进化系统 v2.0 规划进展
- 已将执行方案升级为完整的项目级 master plan，并补齐各阶段目标、详细工作包、测试矩阵、质量控制与 subagent 编排方案。
- 已使用 `pi-planning-with-files`、`agentic-engineering`、`eval-harness`、`verification-loop` 组织本轮架构升级规划。
- 已扫描仓库当前结构、投资域模块、导出边界、历史规划文件与近期提交，完成现状 -> 目标架构差距映射。
- 已识别当前升级主风险：契约分散、策略参数硬编码、Agent/Meeting 夹带计算逻辑、训练编排绑定旧流程、扁平导出导致边界松散。
- 已执行 baseline smoke：`uv run pytest tests/test_structure_guards.py tests/test_data_flow.py -q` 通过，可作为后续迁移的最小回归门槛。
- 已将 v2.0 分解为 7 个可验收阶段，并定义每阶段的 skill 组合、并行策略与退出条件。
- 已生成 `docs/INVEST_V2_EXECUTION_PLAN.md` 作为后续升级主执行文档。

## 2026-03-08 投资进化系统 v2.0 升级完成
- 已完成 `invest/contracts/`、`invest/foundation/`、`invest/models/` 三层落地，并将 `app/train.py`、`app/commander.py`、`app/web_server.py` 接到新的 v2 Pipeline。
- 已新增 `tests/test_v2_contracts.py`、`tests/test_v2_momentum_model.py`、`tests/test_v2_selection_bridge.py`、`tests/test_v2_web_models_api.py`、`tests/test_architecture_import_rules.py`、`tests/test_yaml_mutation.py` 作为本轮架构升级护栏。
- 已执行 `uv run pytest tests/test_yaml_mutation.py -q`，结果通过。
- 已执行 `uv run pytest -q`，全量回归通过。
- 已执行 `uv run python -m compileall app invest config train.py commander.py web_server.py`，Python 语法校验通过。
- 仓库当前未安装 `ruff` 与 `pyright` 可执行文件，本轮静态门以 `compileall + pytest` 收口，并记录为后续工具链补齐项。
- 已执行 `INVEST_DISABLE_LIVE_LLM=1 uv run python train.py --cycles 1 --mock --log-level WARNING`，mock 训练通过。
- 已执行 `INVEST_DISABLE_LIVE_LLM=1 INVEST_FORCE_CUTOFF_DATE=20211221 uv run python train.py --cycles 1 --log-level WARNING`，真实数据 smoke 训练通过。
- 已核对 `runtime/logs/meetings/selection/meeting_0001.json`，确认本轮使用 `model_name=momentum`、`config_name=momentum_v1`，并由 `agent_context_summary` 驱动 Agent 会议。
- 已核对 `runtime/outputs/training/cycle_1.json`，确认真实训练完成选股、交易、复盘与优化事件落盘。
- 已核对 `data/evolution/generations/momentum_v1_cycle_0999.yaml`，确认 YAML 变异输出可生成并持久化。

## 2026-03-08 纯 v2-only Cutover 完成
- 已将 `SimulatedTrader`、风控对象与量化评估器迁入 `invest/foundation/`，训练与测试不再 import `invest/trading` / `invest/evaluation`。
- 已从 `app/train.py` 中移除 `enable_v2_pipeline` 与算法选股降级分支，训练流程固定为模型驱动。
- 已删除旧目录与兼容壳：`invest/selection/`、`invest/trading/`、`invest/evaluation/`、`invest/optimization.py`、`invest/core.py`。
- 已删除不再适用的 legacy 测试，并将保留测试迁移到 `foundation` / `shared` / `v2 contracts` 语义。
- 已执行 `uv run pytest -q`，全量回归通过。
- 已执行 `INVEST_DISABLE_LIVE_LLM=1 uv run python train.py --cycles 1 --mock --log-level WARNING`，mock 训练通过。
- 已执行 `INVEST_DISABLE_LIVE_LLM=1 INVEST_FORCE_CUTOFF_DATE=20211221 uv run python train.py --cycles 1 --log-level WARNING`，真实数据 smoke 训练通过。
- 已核对 `runtime/logs/meetings/selection/meeting_0001.json`，确认 `model_name=momentum`、`config_name=momentum_v1`，纯 v2 模型链路生效。

