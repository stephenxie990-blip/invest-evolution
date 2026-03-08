# Findings

## 2026-03-08 实际操作观察
- 已使用真实浏览器自动化按用户路径操作：打开首页 -> 进入“训练追踪” -> 勾选 Mock -> 点击“开始训练” -> 切到“工作台”观察时间线。
- 页面存在持续连接（SSE），自动化不能使用 `networkidle`，需要用 `load`/`domcontentloaded`。

## 复现到的真实问题
1. `MarketRegime.reason()` 返回 dict，但前端追踪事件按字符串切片，导致历史错误 `slice(None, 200, None)`。
2. 训练在 `no_data` 提前结束时，没有明确终态事件，前端会出现“上面仍在运行、下面却已出结果”的状态错位。
3. 前端把 `status=no_data` 直接当正常训练结果汇总，导致 `+0.00%` / `0 笔交易` 的误导性摘要。
4. Mock 模式默认历史窗口与项目配置 `min_history_days=1095` 不匹配，导致 Web 演示下经常直接 `no_data`。
5. Mock 模式只把主 `llm_caller` 设为 `dry_run`，但各 Agent 仍可能实际调用 LLM，不符合“Mock 演示”预期。
6. 模拟交易阶段追踪日志引用了不存在的 `trader.capital` 属性，导致训练在进入模拟交易时异常。
7. 周期结果写 JSON 时，`numpy.bool_` 落入 `audit_tags` / `optimization_events`，导致持久化失败。

## 本轮修复后观察
- 训练追踪界面现在能正确区分三类终态：完成 / 跳过 / 失败。
- 工作台时间线现在能看到 `cycle_start`、各 Agent 进度、`cycle_skipped` 或 `cycle_complete` 的真实闭环。
- Mock 训练已经能稳定跑通完整一轮，不再默认早早 `no_data`。
- 真实浏览器自动化最终观察到一轮成功完成：
  - `cycle_complete` 已出现
  - 结果摘要显示 `请求轮次=1 / 有效完成=1 / 跳过=0 / 失败=0`
  - 工作台状态回到“空闲”
  - 示例收益率：`+4.25%`
- 非 mock 自动化首次失败：`#train-mock` 为自定义隐藏 checkbox，Playwright 不能直接 `uncheck()`；改为点击其可见 `label.toggle` 容器模拟真人操作。

## 2026-03-08 产品化增强补充
- `static/index.html` 当前 JS 已通过 `node` 语法检查，新增的时间线筛选/Agent 折叠逻辑不存在重复函数定义冲突。
- 记忆详情接口原先仅返回当前训练工件，已补充与“上一条 training_run”的结构化 compare 数据，适合前端直接渲染策略差异卡片。
- 本地环境没有 `python` 命令，需统一使用 `python3` 执行技能脚本和临时校验。
- 端口 `8080` 上存在旧版 Flask 进程时，需要先重启才能加载最新静态页与接口逻辑。

## 2026-03-08 Agent 工作台视觉升级观察
- 原工作台信息足够但视觉层级偏平，新增顶部总览可显著提升“整体掌控感”。
- 将 Agent 卡片按状态优先级排序后，运行中 / 思考中的角色会自然浮到上方，更适合长时间盯盘和训练观察。
- 将阶段、进度、选股数量、过程历史拆成卡片化子模块后，信息密度更高但阅读压力更低。

## 2026-03-08 真实训练监控结论
- 本轮真实训练已完整跑通，关键路径为：数据加载 → 市场状态分析 → Agent 选股会议 → 30 天模拟交易 → 复盘会议 → 参数/权重优化。
- 训练过程中仍出现多次“LLM JSON 解析告警”，但系统具备容错能力，未导致训练中断。
- 本轮选股会议最终选出 `sh.600023`、`sh.600066`、`sh.600004`、`sh.600059`；模拟交易阶段发生 10 次 `all_positions_red` 风险告警。
- 相比上一条训练记忆，本轮收益从 `-2.02%` 改善到 `-1.42%`，但依然未通过 benchmark。

## 2026-03-08 JSON 解析加固验证
- 主解析器现在能覆盖：Markdown fenced JSON、前后说明文本、未闭合 fence、尾逗号、前置“下面是最终JSON”、以及 Python 风格字典字面量。
- 真实训练复跑后，训练链路、会议纪要、复盘决策与优化记录均能正常落库；最新一轮收益从上一轮 `-1.42%` 改善到 `-0.14%`。
- 当前 Web 进程 stdout/stderr 绑定在终端设备 `/dev/ttys018`，本轮无法自动统计精确告警条数；但从结构化结果与训练闭环看，解析失败已不再表现为链路性中断。

## 2026-03-08 复盘一致性修复发现
- `ReviewMeeting._compile_facts()` 产出的 `total_cycles` / `win_rate` / `avg_return` 本来就是正确聚合值，问题出在 `app/train.py` 误把单轮 `cycle_dict` 传给了 `MeetingRecorder.save_review()`。
- `ReviewMeeting._validate_decision()` 会裁剪 `position_size` 等参数，但原始 `reasoning` 不会随之同步，导致“文案说 10%，实际落盘 30%”这种不一致。
- 最稳妥的修法不是改写 LLM 原文，而是补充结构化的 `applied_summary`，让 Markdown 和记忆详情都能展示“最终真正执行的参数/权重”。
- 本地测试需统一使用 `uv run pytest`，因为系统环境没有直接安装 `pytest`。

## 2026-03-08 数据库升级 V2 发现
- 当前库里 `security_master` 已有完整行业字段，但运行时行业判断仍主要依赖 `data/industry_map.json`；后者当前仅 13 条映射，已成为明显瓶颈。
- 当前库 `financial_snapshot` 为 0 行，且仓储层仅有 upsert，没有读侧查询接口，导致价值/质量策略无法真正消费财务数据。
- 大盘/基准数据此前没有进入统一数据库，`invest/evaluation/freeze.py` 仍在运行时直接抓取沪深300，削弱了离线复现能力。
- 最适合作为 P0 的切入口是 `index_bar`：改动集中、风险低、能立刻提升 benchmark 与市场状态一致性。
- `config.index_codes` 原默认只含上证/深成/创业板三大指数，不含 `000300.SH`，会导致 benchmark 使用的沪深300未被同步；本轮已补入默认配置并在同步逻辑中强制兜底追加。

## 2026-03-08 投资进化系统 v2.0 升级差距研判
- 已根据用户反馈将执行文档升级为项目级 master plan，新增每阶段工作包、测试收口、质量门、回滚策略与 cutover 标准。
- 当前仓库已经完成第一层按功能拆分：`agents/`、`meetings/`、`trading/`、`evaluation/`、`selection/`、`evolution/` 已独立成目录，但仍未形成按变化频率分层的 L0-L4 架构。
- `invest/shared/contracts.py` 仍承载策略默认值与计划生成逻辑，说明“契约”和“模型偏好”尚未分离。
- `invest/trading/engine.py` 内含 `default_stop_loss_pct` / `default_take_profit_pct` 等策略默认参数，`invest/trading/risk.py` 也保留硬编码阈值，尚未完成 foundation 与 model 分层。
- `invest/agents/regime.py`、`invest/agents/hunters.py`、`invest/meetings/selection.py` 仍直接依赖统计/打分/阈值逻辑，Agent 还未纯化为只消费叙事上下文的推理层。
- `invest/__init__.py` 继续用扁平大导出的方式暴露整个投资域 API，这利于兼容，但会让后续分层迁移产生大量隐性耦合；迁移期应保留 re-export，收尾期再统一收口。
- 现有测试资产可直接复用为迁移护栏，尤其是 `tests/test_structure_guards.py` 与 `tests/test_data_flow.py`；本轮已验证 `uv run pytest tests/test_structure_guards.py tests/test_data_flow.py -q` 通过，可作为后续 Phase baseline。
- 最适合并行的不是“大面积改同一层”，而是“在契约冻结后按目录泳道并行”：`foundation/compute`、`foundation/risk+engine`、`foundation/metrics` 可独立推进；`models/` 与 `agents/` 需要等待 `ModelOutput` 契约冻结后再并行。
- 如果要调度 subagent，建议上限为 3 个：架构/契约、底座提取、编排/测试；继续增加并行度会在 `train.py`、`commander.py`、`invest/__init__.py` 上显著放大合并冲突。

## 2026-03-08 投资进化系统 v2.0 升级完成态发现
- 本轮采用“兼容优先”的分层升级：先新增 v2 目录和契约，再让旧模块逐步委托新底座，避免一次性推倒重来造成训练主链中断。
- `invest/contracts/` 已成为跨层统一语言；`SignalPacket`、`AgentContext`、`ModelOutput`、`StrategyAdvice`、`TradeRecord`、`EvalReport` 已具备可序列化与可测试的稳定边界。
- `invest/foundation/` 已承接计算职责：指标/因子/特征进入 `compute/`，撮合进入 `engine/`，风控进入 `risk/`，收益评估进入 `metrics/`；旧共享入口通过委托方式复用这些底座能力。
- `MomentumModel + momentum_v1.yaml` 已跑通新链路，模型负责参数和上下文生产，Agent/Meeting 负责推理与协作，训练编排负责按标准 Pipeline 装配执行。
- 训练日志已证明 Agent 不再直接吃原始行情：`runtime/logs/meetings/selection/meeting_0001.json` 中记录了 `model_name=momentum`、`config_name=momentum_v1` 和可读的 `agent_context_summary`。
- 进化对象已切换到配置层：`data/evolution/generations/momentum_v1_cycle_0999.yaml` 的生成说明系统已经能对 YAML 做变异并保留代际快照。
- 为了测试稳定性，`invest/shared/llm.py` 增加了 pytest 场景短路和 `INVEST_DISABLE_LIVE_LLM=1` 开关；这让回归测试和训练 smoke 不依赖外部 LLM 即可稳定收口。
- 当前唯一明确的工具链缺口是本地未安装 `ruff`/`pyright`，不影响 v2.0 升级完成，但建议后续补齐为标准质量门的一部分。

## 2026-03-08 纯 v2-only Cutover 发现
- 真正阻碍纯 v2-only 的不是模型层，而是训练入口、顶层导出和旧测试矩阵；只要这些兼容口不清，旧目录就会持续被“保活”。
- 将旧交易/评估实现平移到 `invest/foundation/` 比从零重写风险更低：既保住行为稳定，也能在物理删除旧目录后保持训练链路可用。
- `enable_v2_pipeline` 一旦存在，就会导致配置层、Web API、Commander snapshot 和测试语义全部双轨；删除该开关后，系统语义明显简化。
- 选股算法降级路径删除后，`selection_mode` 不再需要表达 `algorithm_fallback`，这让训练记忆、对比视图和审计标签都回归单一语义。
- 大而全的 `test_all_modules.py`、`test_optimization.py`、`test_comparison.py` 等旧测试文件本质上是 legacy 架构的回归护栏；进入 pure v2-only 后应删除或重写，而不是继续维持。

