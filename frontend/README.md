# Standalone Frontend Workspace

本目录预留给新的独立前端工程，当前阶段只冻结契约，不提交页面实现。

## 目标
- 新前端挂载到 `/app`
- 后端 API 通过 `/api/*` 提供
- 旧页面 `/` 保留，直到迁移完成

## 后端契约入口
- 目录索引：`GET /api/contracts`
- 主契约：`GET /api/contracts/frontend-v1`
- 本地文件：`docs/contracts/frontend-api-contract.v1.json`

## 推荐技术栈
- React
- TypeScript
- Vite
- React Router
- TanStack Query
- Zod
- ECharts

## 推荐开发模式
- 前端 dev server 代理到 Flask
- 所有接口类型以 `frontend-api-contract.v1.json` 为准
- 前端客户端统一归一化后端错误体
- 构建产物输出到 `frontend/dist`

## 挂载规则
- Flask 已预留 `/app`
- 若 `frontend/dist` 不存在，访问 `/app` 会返回明确的 404 提示
