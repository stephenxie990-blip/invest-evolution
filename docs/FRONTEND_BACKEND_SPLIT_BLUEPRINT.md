# 前后端分离蓝图（当前实现版）

更新时间：2026-03-10

## 1. 当前结论

当前仓库已经具备“后端继续稳定提供能力、前端可独立演进”的基础设施，但默认主页面仍然是：

- `/` -> `static/index.html`

同时，仓库也已经为独立前端预留了：

- `/app` -> 托管 `frontend/dist`
- `/api/contracts`
- `/api/contracts/frontend-v1`
- `docs/contracts/frontend-api-contract.v1.json`

这意味着：

- **旧页面**继续承担当前内置控制台职责
- **新前端**可以在不改 Python 主链的前提下独立开发与挂载

## 2. 后端与前端边界

### 后端负责

- Commander Runtime
- 训练与实验工件
- 数据访问与质量检查
- 配置治理
- SSE 事件流
- 契约文档输出

### 前端负责

- 页面路由、交互、图表与表单
- 调用 `/api/*` 与 `/api/events`
- 对错误做统一归一化
- 不依赖 Python 内部实现细节

## 3. 当前实际挂载方式

### 3.1 旧页面

- 路由：`/`
- 资源：`static/index.html`
- 状态：当前默认可用

### 3.2 新前端挂载点

- 路由：`/app`
- 目录：`frontend/dist`
- 状态：仅在 `frontend/dist` 存在时可用，否则返回 404 + hint

## 4. 当前契约能力

### 4.1 契约索引

- `GET /api/contracts`

### 4.2 前端主契约

- `GET /api/contracts/frontend-v1`
- 文件来源：`docs/contracts/frontend-api-contract.v1.json`

## 5. 推荐接入顺序

### 第一阶段：先消费稳定 API

优先接入：

- `/api/lab/status/quick`
- `/api/lab/status/deep`
- `/api/events`
- `/api/lab/training/plans`
- `/api/lab/training/runs`
- `/api/lab/training/evaluations`
- `/api/runtime_paths`
- `/api/evolution_config`
- `/api/agent_configs`
- `/api/data/status`

### 第二阶段：补模型与研究台

- `/api/investment-models`
- `/api/leaderboard`
- `/api/allocator`
- `/api/strategies`
- `/api/data/capital_flow`
- `/api/data/dragon_tiger`
- `/api/data/intraday_60m`

## 6. 当前建议

- 保留 `/` 作为现有内置控制台
- 把 `/app` 视为未来独立前端挂载点
- 所有前端类型与 client 优先由 `frontend-v1` 契约生成或校验
- 若后端字段有调整，应先更新契约，再推动前端升级
