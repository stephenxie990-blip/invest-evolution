# Standalone Frontend Workspace

本目录已经进入“契约驱动 + 独立演进”阶段，不再只是预留目录。

## 当前状态
- 挂载路径：`/app`
- 技术栈：React + TypeScript + Vite + React Router + TanStack Query + Zod + ECharts
- 运行模式：前端独立开发，后端继续提供 `/api/*` 与 `/api/events`
- 旧页面 `/`：仅保留过渡壳层和命令触发
- 训练默认模式：真实数据 / 离线库优先，`mock` 仅作显式 smoke/demo 选择

## 已落地骨架
- 路由壳：`src/app/layout/AppShell.tsx`
- 页面：`src/pages/dashboard`、`src/pages/training-lab`、`src/pages/models`、`src/pages/data`、`src/pages/settings`
- API 层：`src/shared/api/*`
- 类型与契约：`src/shared/contracts/types.ts`、`src/shared/api/contracts.ts`
- SSE：`src/shared/realtime/events.ts`

## 契约入口
- 目录索引：`GET /api/contracts`
- 主契约：`GET /api/contracts/frontend-v1`
- JSON Schema：`GET /api/contracts/frontend-v1/schema`
- OpenAPI：`GET /api/contracts/frontend-v1/openapi`
- 机器可读：`docs/contracts/frontend-api-contract.v1.json`
- 人类可读：`docs/contracts/frontend-interface-ledger.v1.md`

## 开发命令
```bash
cd frontend
npm run dev
npm run build
```

## 协作约束
- 所有页面必须通过 `apiRequest()` 消费后端接口。
- 所有接口响应必须先经过 Zod schema 校验。
- 新增页面/能力时，优先更新契约和接口台账，再落页面。
- 构建产物输出到 `frontend/dist`，由 Flask 的 `/app` 托管。

## E2E 冒烟测试
```bash
cd frontend
npm run test:e2e
```

当前已包含一条基于 Playwright 的训练中心冒烟测试，使用网络拦截模拟 `/api/*` 返回，不依赖真实后端数据。
