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
