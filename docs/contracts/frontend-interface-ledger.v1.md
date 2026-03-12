# Frontend Interface Ledger V1

## 目的
- 这是 `docs/contracts/frontend-api-contract.v1.json` 的人类可读补充版。
- 用于前端团队按页面、模块、接口、事件流拆分开发，不直接阅读 Python 内部实现。
- 后端继续保持 CLI + API 双形态；新前端只消费 `/api/*` 与 `/api/events`。

## 挂载与运行
- 旧页面：`/`，仅保留过渡壳层与命令触发。
- 新前端：`/app`，对应 `frontend/` 独立工作区。
- 契约目录：`GET /api/contracts`。
- 主契约：`GET /api/contracts/frontend-v1`。
- JSON Schema：`GET /api/contracts/frontend-v1/schema`。
- OpenAPI：`GET /api/contracts/frontend-v1/openapi`。
- 契约刷新命令：`python3 scripts/generate_frontend_contract_derivatives.py` 或 `invest-refresh-contracts`。
- 契约漂移校验：`python3 scripts/generate_frontend_contract_derivatives.py --check` 或 `invest-refresh-contracts --check`。
- Freeze gate：`invest-freeze-gate --mode quick|full`，用于把 contract / transcript / regression 一起冻结。
- 标准响应片段：`responseFeedback`，用于承载 Commander/ask-stock 的用户可读 gate + audit 摘要。
- 标准后续动作片段：`responseNextAction`，用于表达建议用户下一步动作。
- 标准 transcript 快照：主契约中的 `transcript_snapshots` 与 OpenAPI 中的 `x-transcript-snapshots`，由 shared transcript snapshot builder 自动导出，用于前端 mock、回放与回归比对。
- 事件流：`GET /api/events`（SSE）。

## 页面分工
| 页面/模块 | 前端路由 | 核心 hooks / 模块 | 依赖接口 | 说明 |
| --- | --- | --- | --- | --- |
| 仪表盘 | `/dashboard` | `useQuickStatus` / `useDeepStatus` | `GET /api/lab/status/quick`、`GET /api/lab/status/deep` | 系统状态、训练摘要、深度诊断 |
| 训练实验室 | `/training-lab` | `useTrainingPlans`、`useTrainingRuns`、`useTrainingEvaluations`、`useCreateTrainingPlan`、`useExecuteTrainingPlan`、`useEventStream` | `GET/POST /api/lab/training/plans`、`POST /api/lab/training/plans/{plan_id}/execute`、`GET /api/lab/training/runs`、`GET /api/lab/training/evaluations`、`GET /api/events` | 计划创建、执行、结果流式观察 |
| 模型策略 | `/models` | `shared/api/contracts` + 模型 API hooks | `GET /api/investment-models`、`GET /api/leaderboard`、`GET /api/allocator` | 模型列表、排行、分配建议 |
| 数据控制台 | `/data` | 数据 API hooks | `GET /api/data/status`、`POST /api/data/download`、`GET /api/data/capital_flow`、`GET /api/data/dragon_tiger`、`GET /api/data/intraday_60m` | 数据状态、下载、深查 |
| 配置中心 | `/settings` | 设置 API hooks | `GET/POST /api/runtime_paths`、`GET/POST /api/evolution_config`、`GET /api/control_plane`、`GET /api/contracts`、`GET /api/contracts/frontend-v1` | 运行时路径、训练参数、LLM 控制面、契约查看 |

## 训练实验室接口
| 方法 | 路径 | 请求字段 | 返回摘要 | 备注 |
| --- | --- | --- | --- | --- |
| `GET` | `/api/lab/training/plans?limit=10` | `limit` | `{ count, items[] }` | `items` 为 artifact 行；按时间倒序展示 |
| `POST` | `/api/lab/training/plans` | `{ rounds, mock=false, goal, notes, tags[], detail_mode? }` | 单条训练计划对象 | `mock` 默认为 `false`，仅用于显式 smoke/demo |
| `POST` | `/api/lab/training/plans/{plan_id}/execute` | 无 | 执行结果对象 | 慢接口，建议按钮 loading + timeout 300s |
| `GET` | `/api/lab/training/runs?limit=10` | `limit` | `{ count, items[] }` | 展示最近运行记录 |
| `GET` | `/api/lab/training/evaluations?limit=10` | `limit` | `{ count, items[] }` | 展示最近评估记录 |

## SSE 事件约定
| 事件类型 | 关键字段 | 展示语义 |
| --- | --- | --- |
| `connected` | `ts`, `message` | 初次连接成功提示 |
| `cycle_start` | `cycle_id`, `cutoff_date`, `requested_data_mode`, `llm_mode`, `timestamp` | 训练周期开始，前端据此标识 live/mock 与 dry-run |
| `cycle_complete` | `cycle_id`, `return_pct`, `requested_data_mode`, `effective_data_mode`, `llm_mode`, `degraded`, `timestamp` | 训练周期完成，并明确请求/实际模式与是否降级 |
| `cycle_skipped` | `cycle_id`, `cutoff_date`, `reason`, `stage` | 因数据不足等原因跳过 |
| `agent_status` | `agent`, `status`, `message`, `stage`, `progress_pct` | Agent 状态卡片 / 时间线摘要 |
| `agent_progress` | `agent`, `status`, `message`, `details` | Agent 执行细节 |
| `module_log` | `module`, `title`, `message`, `kind`, `level` | 模块日志流 |
| `meeting_speech` | `meeting`, `agent`, `speech`, `role`, `confidence` | 会议发言卡片 |

## 错误与空态
- Web API 以“HTTP 状态码 + 扁平错误体”为主；训练数据链路不可用时返回 `503` + 结构化 `{ error_code: "data_source_unavailable", ... }`。
- 前端统一通过 `frontend/src/shared/api/errors.ts` 归一化为 `ApiError`。
- 对慢接口、空列表、契约 404 均要有明确空态文案。

## 前端编制建议
1. `Dashboard / Status`：负责状态汇总、深度诊断切换。
2. `Training Lab + SSE`：负责训练计划、执行、事件流。
3. `Models + Data`：负责模型、排行、数据深查。
4. `Settings + Contracts`：负责路径配置、参数配置、契约查看。
5. `QA / Types`：负责 Zod schema、错误归一化、mock 数据和构建验证。

## 交付门禁
- 所有前端请求必须经过 `apiRequest()`。
- 新页面只引用契约文档和 `frontend/src/shared/contracts/types.ts`，不直接依赖 Python 代码。
- 每次新增接口时，同时更新 JSON 契约和本台账。
