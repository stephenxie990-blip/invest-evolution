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
