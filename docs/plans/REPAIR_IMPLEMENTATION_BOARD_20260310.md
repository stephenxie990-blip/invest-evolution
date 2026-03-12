# 修复实施总控板（2026-03-10）

## 目标
- 以 P0/P1 分波次收口审查发现，先修兼容层和契约漂移，再推进前端独立演进。
- 保持后端 Headless：CLI、CommanderRuntime、Flask API 持续可用。
- 将旧 `static/index.html` 明确降级为过渡壳层，把产品化训练中心迁移到 `/app` + `frontend-api-contract.v1.json`。

## 当前波次：Wave 1 / P0
| 泳道 | 负责人 | 任务 | 验收标准 | 状态 |
| --- | --- | --- | --- | --- |
| Compat | 主调度 | 根目录 `web_server.py` / `train.py` / `commander.py` 改为真实模块别名 | monkeypatch 可命中 `_runtime` / `_event_buffer` 等私有状态；CLI 启动不退化 | 已完成 |
| Training Contract | 主调度 | `SelfLearningController` 兼容 `random_cutoff_date` / `diagnose_training_data` 旧签名 | `tests/test_train_cycle.py`、`tests/test_train_event_stream.py` 通过 | 已完成 |
| Hunter Compat | 主调度 | `_recover_hunter_result()` 兼容旧调用参数 | `tests/test_hunter_code_normalization.py` 通过 | 已完成 |
| Legacy UI Shell | 主调度 + 前端接口 | 旧页明确为壳层，暴露 `/app` 与契约入口 | 旧页测试改为壳层职责；不再要求产品化训练中心控件 | 已完成 |
| Verification | 主调度 | 先 targeted tests，再全量 `pytest` | 定向回归通过，且 `./.venv/bin/python -m pytest -q` 全量通过 | 已完成 |

## P1（下一波）
1. 清理 `invest/evolution/analyzers.py` 残留 mock LLM 路径。
2. 增加前端 mock 数据和 OpenAPI/JSON Schema 派生文档。
3. 为 `/app` 挂载补充 smoke test 与构建产物检查。
4. 建立 Agent 观测面板的 API 契约测试，替代旧页 DOM 断言。

## 协作编制
- 主调度：控制范围、变更合并、回归和验收。
- Compat 子代理：只处理根壳别名与兼容导入，不碰业务逻辑。
- Training 子代理：只处理训练数据诊断、事件顺序、回退兼容。
- Frontend Contract 子代理：只维护旧页壳层职责、`/app` 入口和契约文档。
- Review 子代理：做 diff review、失败归因、回归清单和发布建议。

## 门禁
- 每个泳道必须有单独的 targeted tests。
- 合并前至少通过一次全量 `./.venv/bin/python -m pytest -q`。
- 不修无关失败；若发现新增非目标风险，记录到下一波待办。


## Wave 2（已完成）
- 已清理 `invest/evolution/analyzers.py` 的内置 mock LLM 路径，改为“外部注入调用器 + 安全降级默认结果”。
- 已新增 `docs/contracts/frontend-interface-ledger.v1.md`，作为前端团队按页面/接口/事件流拆分的可读台账。
- 已更新 `frontend/README.md`，反映当前脚手架、契约入口与开发命令。
- 已新增 `tests/test_evolution_analyzers.py`，覆盖无调用器降级与注入调用器两条路径。
- 已验证 `cd frontend && npm run build` 通过。


## Wave 3（已完成）
- 已新增派生契约文档：`frontend-api-contract.v1.schema.json` 与 `frontend-api-contract.v1.openapi.json`。
- 已暴露 `/api/contracts/frontend-v1/schema` 与 `/api/contracts/frontend-v1/openapi`，并纳入 `/api/contracts` 目录索引。
- 已补 `tests/test_agent_observability_contract.py`，将 Agent 时间线 / speech / module log 语义迁移为 API 契约测试。
- 已在前端 `useEventStream()` 中接入 Zod 事件契约校验，避免无结构 SSE 数据直接进入页面。
- 已验证全量 `pytest`、`compileall`、`frontend npm run build` 通过。
