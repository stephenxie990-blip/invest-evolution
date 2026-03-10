# 前端重构执行计划（基于当前实现）

## 1. 当前起点

当前仓库的前端现状是：

- 旧控制台：`static/index.html`
- 新前端挂载点：`/app`
- 契约输出：`/api/contracts/frontend-v1`
- 后端主链：仍以 Commander Runtime + Flask API 为中心

因此前端升级应该遵循：

- **先独立工程化**
- **先契约驱动**
- **先并行，不替换**

## 2. 执行目标

1. 让新前端在 `frontend/` 中独立开发
2. 使用 `frontend-v1` 契约消费后端能力
3. 优先承接训练实验室、仪表盘、配置治理与数据控制台
4. 在 `/app` 并行运行稳定后，再评估是否替换旧页面

## 3. 推荐里程碑

### Milestone A：前端工程初始化

建议交付：

- `frontend/package.json`
- `frontend/vite.config.ts`
- `frontend/src/main.tsx`
- `frontend/src/app/router.tsx`
- `frontend/src/app/layout/*`

要求：

- 本地 dev server 能代理 Flask `/api/*`
- 构建产物输出到 `frontend/dist`
- 访问 `/app` 时可由 Flask 托管 SPA

### Milestone B：契约驱动 SDK

建议交付：

- API client
- 类型层 / schema 校验层
- SSE client
- 错误归一化层

优先覆盖：

- status
- training lab
- config
- events
- data status

### Milestone C：训练中心优先落地

优先原因：当前后端最完整、最有闭环价值的前端能力就是 Training Lab。

建议页面：

- 训练计划列表 / 创建页
- 训练运行详情页
- 训练评估详情页
- 实时事件时间线

### Milestone D：仪表盘与配置中心

建议页面：

- runtime 总览
- training lab 摘要
- runtime paths 配置
- evolution config 配置
- agent configs 配置

### Milestone E：数据控制台与模型研究台

建议页面：

- 数据状态页
- 资金流 / 龙虎榜 / 60m 查询页
- 模型清单页
- leaderboard 页
- allocator 页
- 策略基因页

## 4. 当前不建议做的事

- 不要把训练逻辑搬到前端
- 不要让新前端依赖 Python 内部对象状态
- 不要在早期直接删除 `static/index.html`
- 不要绕过契约直接散写 `fetch()`

## 5. 当前最佳实施路径

### Sprint 1

- 初始化 `frontend/`
- 建立契约驱动 SDK
- 跑通 `/app` 托管与本地代理

### Sprint 2

- 完成训练中心主链
- 接入 `/api/events`
- 跑通“创建计划 -> 执行 -> 看结果 -> 看评估”

### Sprint 3

- 完成仪表盘与配置中心
- 形成独立可演示的新前端主壳

### Sprint 4

- 补数据控制台、模型研究台
- 评估 `/app` 对旧页面的替代度
