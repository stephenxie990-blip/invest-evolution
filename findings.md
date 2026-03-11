# Findings（实施阶段，2026-03-10）

## P0 根因
- 根目录兼容壳是星号导入，不是模块别名；测试和 monkeypatch 无法命中私有状态。
- 训练控制器默认走新签名，但旧测试仍通过实例级 monkeypatch 覆盖 `random_cutoff_date()` 和 `diagnose_training_data()`。
- `_recover_hunter_result()` 收缩签名后，遗留测试和旧调用仍传默认止损/止盈参数。
- 旧 `static/index.html` 已不再是产品化训练中心，测试职责应降级为壳层与迁移入口。

## P0 实施决策
- 根壳统一改为真正的 `sys.modules` 别名导出。
- 训练控制器引入兼容调用 helper，根据函数签名和实例级覆写自动选择调用方式。
- Hunter 恢复函数接受旧参数但保持当前角色边界，不恢复 execution params 输出。
- 旧页新增 `/app` 与 `/api/contracts/frontend-v1` 入口卡片，并把 DOM 测试改为壳层契约。

## 前端升级阶段发现（Sprint 1 已启动）
- 新前端可以在不依赖旧 `static/index.html` 的前提下独立组织页面与状态流。
- `/app` 挂载约定已经足够支撑独立 SPA，不需要再向 Flask 模板层追加业务逻辑。
- 契约驱动最适合先从 `status`、`training_lab`、`runtime_paths`、`evolution_config`、`events` 这几组接口切入。
- 当前最合理的推进顺序仍然是：脚手架/SDK → 训练中心 → 仪表盘 → 配置/数据 → 模型/策略。


## Wave 2 实施结论
- `invest/evolution/analyzers.py` 里的 mock LLM 路径已被移除，避免未来误把“示例返回”当生产逻辑。
- 前端契约已经同时具备机器可读 JSON 和人类可读台账，便于后续按页面与 subagent 分工。
- `frontend/` 已可独立 `npm run build`，说明 `/app` 路线具备继续产品化演进的工程基础。

- 训练中心最适合先做 master-detail：列表层消费 artifactList，详情层按 id 单独拉取，能明显降低大 JSON 一次性渲染的耦合。
- Playwright 冒烟最稳妥的方式是通过 `page.route()` 拦截 `/api/*`，避免被真实运行时和数据状态影响。


## Wave 3 实施结论
- 前端契约现在同时具备主合同、JSON Schema、OpenAPI 三种机器可读形态，适合前端、测试和工具链协作。
- 原先依赖旧训练页 DOM 的“Agent 总览 / 时间线 / speech cards”语义，已转移到 SSE 事件与控制器发射行为的 API 契约测试。
- `/app` 前端开始在运行时入口就校验 SSE 契约，后续产品化页面可以建立在稳定事件模型上。

## 训练数据加载性能专项（2026-03-11）
- 基线复现表明，真实数据库热路径的主要浪费有两段：
  1. `get_stocks()` 会把命中股票的**全历史日线**都读出来，而不是只读训练所需的近期窗口。
  2. `load_stock_data()` 默认每轮都会重新执行 `_ensure_point_in_time_derivatives()`，重复重算并 upsert `trading_calendar / security_status_daily / factor_snapshot`，即使本地库早已完整。
- 在当前 8.6GB 本地库上，固定 `cutoff=20210830` 的实测基线：
  - `baseline`：115.6s～118.1s
  - `仅跳过衍生补数`：52.6s～56.4s
  - `仅裁剪日历窗口`：15.7s～18.5s，但会少载入约 45 只股票
  - `按每股最近 N 个交易日切片 + 向量化增强`：可保留 4188/4188 只股票，并将热路径降到个位数到十几秒
- 最终选定方案：
  - 仓储层新增“训练切片查询”，按**每只股票截止日前最近 N 个交易日 + 未来模拟窗口**读取，而非扫描全历史。
  - 训练侧改为**单次 DataFrame 向量化增强**，优先复用已有 `security_status_daily / factor_snapshot` 字段，缺失时再内联补算。
  - 默认训练热路径不再触发重型 point-in-time 衍生补数；只有显式 `include_capital_flow=True` 才保留该补数入口。

## 第二轮真实压测结论（2026-03-11）
- 多截断日对比（真实库，`min_history_days=150`，`future_days=30`）：
  - `20190411`：新路径 `13.96s`，旧热路径模拟 `20.52s`，提速 `1.47x`
  - `20210830`：新路径 `24.60s`，旧热路径模拟 `55.00s`，提速 `2.24x`
  - `20250613`：新路径 `49.10s`，旧热路径模拟 `151.47s`，提速 `3.08x`
- 三轮真实 `run_training_cycle()` dry-run 阶段剖析显示：
  - `data_loading` 平均 `17.96s`，仍是最大瓶颈。
  - `investment_model.process` 平均 `2.65s`，是第二热点，但量级已明显低于数据加载。
  - `simulation / review_meeting / benchmark_series` 基本可忽略。
- 对 `20250613` 的微基准表明，当前热点进一步收敛到 `query_training_bars()` 里的 **SQLite 窗口查询 + 状态/因子左连接**：
  - 当前联表方案：`27.90s`
  - 仅查日线切片、状态/因子完全走内联补算：`21.77s`
- 因此，下一步若要继续压缩到更低时延，最有价值的方向是：
  - 让训练热路径默认跳过 `security_status_daily / factor_snapshot` 联表，完全以内联向量化补算为主；
  - 但该方案会改变“优先复用库内预计算值”的语义，需要先做一致性验收，避免回归到与历史快照不一致的特征值。


## 统一控制面专项发现（2026-03-11）
- 当前 LLM 使用点已经具备统一网关 `app/llm_gateway.py`，但装配层仍分散在 `config`、`agent_settings`、`commander` 三处。
- 训练主链路中的 `SelectionMeeting` / `ReviewMeeting` / `LLMOptimizer` 仍在不同层次持有 caller，适合统一到启动装配阶段收口。
- `InvestAgent` 当前已支持 `fast/deep/显式模型` 解析，是接入控制面的良好兼容桥。
- 运行时市场数据的真正外网出口主要有两处：`EvolutionDataLoader` 在线兜底和 `include_capital_flow=True` 时的 akshare 资金流同步；而 `sync_trading_calendar` / `sync_security_status_daily` / `sync_factor_snapshots` 本身是本地派生，不是外网访问。
- 因此本轮“内部运行环境干净”的关键不是重写全部数据服务，而是禁止训练运行时触发在线兜底，并把外部抓取限定到显式同步命令。

- 已新增 `config/control_plane.py` 作为统一控制面 loader / resolver / service，采用“control_plane.yaml + control_plane.local.yaml + 兼容 legacy defaults”三层策略。
- 已确认“重启生效”路径最适合本项目：控制面 API 不再尝试局部热刷新，而是明确返回 `restart_required=true`。
- `SelfLearningController`、`LLMOptimizer`、`SelectionMeeting`、`ReviewMeeting` 与 `CommanderConfig` 默认值已接入统一控制面绑定。
- 运行时市场数据策略已收口为控制面 `data.runtime_policy`，本轮先禁止两类外部访问：在线 baostock 兜底、运行时 akshare 资金流同步；本地派生补数逻辑继续保留。


## 控制面第二阶段结论（2026-03-11）
- 旧 `/api/evolution_config` 现已成为兼容壳：非 LLM 字段仍写入演化配置，LLM 字段自动转写到 `/api/control_plane`。
- 旧 `/api/agent_configs` 现已成为兼容壳：prompt 仍保存在 `agent_settings/agents_config.json`，`llm_model` 改为转写 control plane binding。
- 新增 `market_data/gateway.py` 后，Web 后台下载、CLI 同步、训练运行时在线兜底/资金流外部同步都经过统一网关。
- 数据链路仍是“外部源 -> 清洗/规范化入库 -> 训练时从本地库加载到内存”；因此严格说不是运行中热更新，但数据库更新会在下一次训练/下一轮装载时自然生效。
- 真实训练暴露的复盘阶段脆弱点为 `agent_weight_adjustments` 偶发返回 list；已在 review agent / review meeting 两层加入正规化容错。


- 第一波清理已完成：旧前端 Agent 配置不再依赖 `/api/agent_configs`，改为 prompt 走 `/api/agent_prompts`、模型走 `/api/control_plane`。
- `/api/agent_configs` 已可删除，因为仓内已无运行时引用；剩余旧接口重点只剩 `/api/evolution_config` 的训练参数壳层职责。

## 全盘审计补充（2026-03-11 / 后端）
- 后端是“单仓单进程、双运行面”架构：`SelfLearningController` 负责训练闭环，`CommanderRuntime` 负责常驻调度与对话，两者通过同一进程内对象组合而不是微服务 RPC 连接。
- 统一控制面已经把 LLM 绑定与运行时外部数据策略收口到 `config/control_plane.py`，训练会议、review、optimizer、commander brain 都通过 component binding 解析模型。
- 数据热路径是 `DataManager -> TrainingDatasetBuilder -> MarketDataRepository.query_training_bars()`：先挑股票池，再按每股窗口裁剪 SQL，最后在内存里做点时特征补齐，避免全历史扫描。
- Agent 编排分为两层：训练内的投资 agent 会议（selection/review/model routing）和 commander 内的本地 brain tool-calling；两套 agent 不同职责、同仓运行、共享部分状态与产物目录。
- Web 服务本质是控制面 + 可观测层，API 面比较厚：状态、训练、训练实验室、策略、cron、memory、配置、数据查询都在 `app/web_server.py` 单文件路由中。
- 测试层比较完整，78 个测试文件覆盖架构边界、控制面、安全、训练事件流、数据策略、agent 合约与 Web API，说明当前系统偏“契约驱动 + 回归守护”。


- 第二、三波清理已完成：`/api/evolution_config` 不再暴露/接受 `llm_fast_model` / `llm_deep_model` / `llm_api_base` / `llm_api_key`。
- 设置页安全面板已切到 `/api/control_plane`；`/api/evolution_config` 只保留训练参数与发布开关。
- 删除的是 API 兼容层，不是底层 `config` 中的 fallback 字段；底层字段仍保留给启动阶段 fallback 与环境变量兼容。


- 底层瘦身已完成：`EvolutionConfigService` 不再输出 `llm_fast_model` / `llm_deep_model` / `llm_api_base` / `llm_api_key_masked` / `llm_api_key_source`，也不再持久化 `llm_api_key`。
- LLM 的底层 fallback 字段仍保留在 `config` dataclass 和启动逻辑中，用于环境变量兼容与 control plane fallback；这属于启动层依赖，不再属于对外配置 API。
- 设置页安全信息现在完全来自 `/api/control_plane`，而不是 `/api/evolution_config`。

## Commander 统一入口升级规划结论（2026-03-11）
- Commander 当前已覆盖核心训练执行面，但未覆盖完整的配置域、数据域、分析查询域和统一观测域。
- 最合理的产品路径不是立刻删除 Web，而是先把 Commander 升级为唯一人类入口，再把 Web 降级为可选可视化与兼容壳。
- 技术上最关键的改造不是继续堆 `app/commander.py`，而是从 `web_server.py` 抽共享 service，再让 Commander Tool 与 Web API 共用。
- 升级顺序建议为：管理能力缺口 -> 数据能力缺口 -> 统一观测层 -> 自然语言任务层 -> 前端降级 -> 问股 DSL。
- 真正的目标是让 Commander 变成“统一控制平面代理”，而不只是“训练控制台 + 对话壳”。



- 启动层瘦身已完成：`SelfLearningController`、`CommanderConfig`、`LLMCaller()` 空参构造已优先从 control plane 的 `defaults.fast/deep` 解析默认模型与 provider。
- 仍保留 `AgentConfig.llm_model` 与 `LLMRouter.from_config(cfg)` 对显式传入 config 的尊重，这样测试语义和局部调用语义更稳定。

## Commander 统一入口升级实施结论（2026-03-11）
- 已新增共享服务层 `app/commander_services.py`，将配置域、数据域、实验室域、记忆域、观测域能力抽离为 Commander / Web 共用 payload 逻辑。
- 已新增 `app/commander_observability.py`，统一事件 tail、聚合摘要、运行诊断等观测接口，支持 Commander 成为运维与训练观察主入口。
- 已在 `brain/tools.py` 中补齐投资域工具集，包括训练实验室、控制面、运行路径、evolution config、agent prompts、数据状态、memory、事件、问股和策略列表。
- `brain/runtime.py` 已增加显式 `/tool ...` 执行与 no-LLM 内建 intent fallback，使 Commander 在无 API Key 时仍可承担基础自然语言入口。
- `app/web_server.py` 已改为优先复用 Commander/runtime/service 能力；Web 更接近兼容壳而不是独立控制层。
- 已补充 `app/stock_analysis.py` 与 `stock_strategies/`，让 Commander 具备本地 YAML 策略目录 + 问股分析能力，可对标另一项目中的“策略 + 工具调用”模式，但当前实现以本地数据分析与工具封装为主，不是完全等价的自由 ReAct 股票 agent。
- `agent_settings/agents_config.json` 已恢复为多 agent 完整配置，保证 prompt 合约、角色边界与运行时行为一致。
- 回归中唯一新增缺陷是 `list_agent_prompts_payload()` 签名与 Web 调用不一致，已修复并验证。
- 实际入口验证结果：
  - Commander mock ask 训练：成功，`data_mode=mock`，`llm_mode=dry_run`，产出 plan/run/eval 工件。
  - Commander 真实 ask 训练：成功，`requested_data_mode=live`、`effective_data_mode=offline`、`llm_mode=live`，说明入口已能驱动真实本地数据 + 实时 LLM 推理的完整闭环。
- 真实训练结果显示：单轮收益为正，但 `benchmark_passed=false`，说明“入口升级成功”不等于“策略已经达成生产级超额收益”；系统升级目标已完成，策略优化仍是后续课题。

## Commander 自然语言入口稳定性审计（2026-03-11）
- 审计结论：Commander 与系统功能已基本适配，但不能宣称“形式化完美适配”。原因在于：
  - 自然语言入口仍然分为两条路径：高置信度 builtin intent（确定性）与通用 LLM tool-calling（概率性）。
  - 因此对开放式、复合式、超出模板的新问题，仍存在少量语义漂移的理论可能性；只是高频操作已通过规则收紧和测试显著降低风险。
- 发现并修复的关键入口问题：
  - `请帮我刷新数据状态` 之前会被错误路由到通用状态；现已优先命中 `invest_data_status`。
  - `我想看看配置有没有问题` 之前会被错误路由到 `invest_ask_stock`；现已改为 `invest_runtime_diagnostics`。
  - `分析一下系统状态和最近训练` 之前只返回单一 quick status；现已返回组合响应 `status_and_recent_training`，同时包含 quick status 与 training lab 概览。
  - 新增 `看看控制面配置` 等配置类自然语言入口，返回 `config_overview` 组合载荷。
- 当前 builtin 路由策略已从“宽泛关键词包含”收紧为“高置信度优先 + 领域冲突排除 + 复合请求组合返回”，明显减少了指令含糊和误翻译风险。
- 保留风险：
  - 复杂多目标对话若不在 builtin 高置信度规则内，仍由 LLM 自主决策工具链；这是可接受的第二层弹性，不是完全形式化编排。
  - `ask_stock` 仍是本地分析服务，不是 YAML + ReAct 多工具自治股票 agent；这是第二阶段要继续升级的内容。

## Commander 全功能用户仿真补充（2026-03-11）
- 本轮新增覆盖面：不仅验证自然语言高频入口，还验证了显式工具路径、训练实验室、模型路由、allocator、memory、cron、配置读接口、风险门控接口。
- 在临时 workspace + 临时 DB 的端到端仿真矩阵中，共验证 41 项调用：
  - 自然语言入口：状态、深度状态、事件、诊断、训练实验室、排行榜、策略列表、快速测试、数据状态、组合请求、配置风险、控制面配置、运行路径、agent prompts、问股、多轮真实训练确认门控。
  - 显式工具入口：investment models、allocator、routing preview、training plan create/list、runs/evals list、events tail/summary、memory list/search/get、control plane get、runtime paths get、evolution config get、stock strategies、data download status/trigger gate、control plane/runtime paths/evolution config update gate、cron add/list/remove。
- 结论：
  - 40/41 项在首轮通过；剩余 1 项是 `memory_get` 返回结构与测试判定口径不一致，并非功能缺陷。
  - 修正判定后，功能层面可视为 41/41 通过。
- 本轮发现并修复的唯一真实逻辑问题：
  - `请帮我真实训练2轮` 之前会因把“真实/实盘”当作 confirm 而直接越过高风险确认门控；现已改为只有明确出现“确认/confirm”才算授权执行多轮真实训练。
- 当前仍建议保留的优化项：
  - `evolution_config_update` 只有影响主训练链路的 patch 才要求确认，轻量参数 patch 会即时写入；这属于当前设计，并非 bug，但后续如果要极致安全，可以把所有写配置都统一升级为双确认模式。
  - 自然语言 builtin 已大幅收紧，但仍主要覆盖高频模板；开放式复合表达仍会进入 LLM tool-calling 路径，因此第二阶段的 ask-stock ReAct 化仍需单独做 eval。

## 第二阶段：Ask Stock YAML + ReAct 化（2026-03-11）
- `app/stock_analysis.py` 已从“单函数直出分析”升级为“策略加载器 + 工具计划器 + 执行器 + 评分器”的 stock workflow。
- 当前 `ask_stock` 执行路径：
  - 读取 YAML 策略；
  - 根据问题推断策略（或使用显式策略）；
  - 根据 `tool_call_plan` 构建工具计划；
  - 顺序执行 `get_daily_history` / `analyze_trend` / `get_realtime_quote`；
  - 生成可审计的 `orchestration.tool_calls` 轨迹；
  - 基于策略 scoring 与衍生信号生成 `dashboard`。
- 虽然这还不是“LLM 自治 ReAct 股票 agent”，但已经接近你给出的另一项目模式：
  - 有 YAML 策略文件；
  - 有声明式 required_tools / tool_call_plan；
  - 有 Thought / Action / Observation 风格的执行轨迹；
  - 有最终 dashboard 输出；
  - 且比完全依赖 LLM 更稳定、可测、可回归。
- 两个现有 stock 策略文件 `stock_strategies/chan_theory.yaml` 与 `stock_strategies/trend_following.yaml` 已升级为显式携带 `aliases` 与 `tool_call_plan`。
- CLI 实测表明：
  - `用缠论分析 600031` 会推断到 `chan_theory`，并输出 3 步工具轨迹。
  - `用趋势跟随分析 600031` 会推断到 `trend_following`，并输出对应中期趋势计划。
- 下一步若继续逼近“完整 LLM ReAct”，可以在这个基础上把 `tool_call_plan` 变为 `planner_prompt + allowed_tools + max_steps`，再引入专用 stock-agent loop；但当前阶段先确保稳定比先放大自治更合适。

## 第三阶段增强：Stock LLM ReAct Loop（2026-03-11）
- `ask_stock` 已从“确定性 YAML tool_call_plan 执行器”升级为“混合式 stock agent”:
  - 有 LLM 且非 mock 模式时，走 `llm_react`：LLM 根据策略说明和工具定义决定下一步 stock tool 调用，形成真正的循环式 Thought / Action / Observation 轨迹。
  - 无 LLM、mock 模式、或 LLM planner 失败时，自动回退到 `yaml_react_like` 的确定性计划执行，保证入口稳定。
- 这次升级实现了 agent harness 的关键要素：
  - 窄而明确的 stock tool action space；
  - 带 `status/summary/next_actions/artifacts` 的 observation；
  - planner 失败自动回退；
  - 可在同一个输出中同时看到 recommended plan 与 actual executed plan。
- Commander 在非 mock CLI 实测中，`用缠论分析 600031` 已返回 `orchestration.mode=llm_react`，说明真实入口已经跑到 LLM 规划环节。
- Commander 在 mock / 测试环境中仍默认禁用 stock LLM planner，避免回归测试受真实外部模型波动影响。
