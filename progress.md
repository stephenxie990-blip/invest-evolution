# Progress（实施阶段，2026-03-10）

- 建立 `docs/REPAIR_IMPLEMENTATION_BOARD_20260310.md` 作为实施总控板。
- 修复 `web_server.py`、`train.py`、`commander.py` 根壳兼容模式。
- 在 `app/train.py` 增加兼容签名调用 helper，收口 cutoff / diagnostics 漂移。
- 在 `invest/agents/hunters.py` 恢复旧参数兼容签名。
- 在 `static/index.html` 增加前后端分离迁移卡片。
- 将 `tests/test_train_ui_semantics.py` 从“产品化训练中心”降级为“壳层契约”验证。
- 待执行：定向回归与全量 pytest，记录剩余风险。

- 定向回归通过：兼容壳 / 训练事件 / Hunter / 旧页壳层测试全部通过。
- 全量 `./.venv/bin/python -m pytest -q` 通过。
- 编译检查 `./.venv/bin/python -m compileall app brain invest market_data config web_server.py train.py commander.py` 通过。

- 启动前端升级实施，完成 `frontend/` 独立工程脚手架。
- 新增前端路由、应用壳、基础 UI 组件和全局样式。
- 新增契约驱动 API Client、错误归一化、状态接口访问层、训练实验室访问层、SSE 封装。
- 首批页面已接入：`dashboard`、`training-lab`、`settings`；`models`、`data` 先挂契约占位页。
- 运行过一次 `frontend` 构建验证，并在清理构建产物后确认 `tests/test_frontend_api_contract.py` 继续通过。

- Wave 2：清理 `invest/evolution/analyzers.py` 内置 mock LLM 路径，改为外部注入调用器。
- 新增 `docs/contracts/frontend-interface-ledger.v1.md`，补齐前端页面/接口/SSE 台账。
- 更新 `frontend/README.md`，同步当前前端脚手架与构建方式。
- 新增 `tests/test_evolution_analyzers.py`。
- 验证 `cd frontend && npm install && npm run build` 通过。
- 再次验证全量 `./.venv/bin/python -m pytest -q` 通过。

- 继续推进 Sprint 2，补齐训练中心 `plans/runs/evaluations` 的详情查询 hooks 与 master-detail 布局。
- 新增 Playwright 配置、页面对象和训练中心冒烟测试，并验证通过。
- 调整 Vite dev proxy，移除错误的 `/app` 代理，避免开发态把 SPA 路由误转发回 Flask。

- Wave 3：新增 `scripts/generate_frontend_contract_derivatives.py` 生成派生契约文档。
- 新增 `docs/contracts/frontend-api-contract.v1.schema.json` 与 `docs/contracts/frontend-api-contract.v1.openapi.json`。
- `app/web_server.py` 新增派生契约路由与目录索引项。
- 新增 `tests/test_agent_observability_contract.py`，将 selection/review 时间线、speech、module log 改为契约测试。
- `frontend/src/shared/realtime/events.ts` 已在接收 SSE 时做 Zod 契约校验。
- 验证 `./.venv/bin/python -m pytest -q`、`./.venv/bin/python -m compileall ...`、`cd frontend && npm run build` 全部通过。
