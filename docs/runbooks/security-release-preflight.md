# 安全与发布前清理 Runbook

## 目标

- 保持系统 **CLI-first / API-first / agent-first**。
- 确保事件流、监控、自然语言交互、配置控制面在发布前都可验证。
- 禁止把密钥、旧 UI 假设或无效入口带进生产。

## 1. 密钥治理

- 所有 LLM 密钥默认不写入 `config/evolution.yaml`。
- 敏感项优先走环境变量，其次走 `config/evolution.local.yaml`。
- `/api/control_plane` 只返回掩码后的 provider 配置；`/api/evolution_config` 不暴露密钥。

### 发布前检查

- [ ] `config/evolution.yaml` 中不包含真实密钥。
- [ ] `config/evolution.local.yaml` 已加入 `.gitignore`，且不纳入提交。
- [ ] `.env` / `.env.*` 已加入 `.gitignore`。
- [ ] `GET /api/control_plane` 中 `api_key` 为掩码值。
- [ ] 如果密钥曾出现在历史文件、日志或截图中，先轮换再发布。

## 2. API 鉴权与限流

- 生产部署必须配置：`WEB_API_TOKEN`、`WEB_API_REQUIRE_AUTH=true`。
- 如需开放匿名只读访问，只允许 `WEB_API_PUBLIC_READ_ENABLED=true` 放开状态类接口。
- 支持请求头：`Authorization: Bearer <token>` 或 `X-Invest-Token: <token>`。
- 应用内置简单限流，可通过 `WEB_RATE_LIMIT_*` 环境变量调整。
- `GET /healthz` 为无鉴权最小存活检查。

### 发布前检查

- [ ] 非回环部署时已设置 `WEB_API_TOKEN`。
- [ ] 非回环部署时已设置 `WEB_API_REQUIRE_AUTH=true`。
- [ ] 已确认 `WEB_API_PUBLIC_READ_ENABLED` 是否符合暴露策略。
- [ ] Gunicorn / 反向代理启动命令已验证，不直接暴露 Flask 开发服务器。
- [ ] `deploy/nginx/invest-evolution.conf`、`deploy/systemd/invest-evolution.service` 已按服务器路径调整。

## 3. 配置分层

### 生效顺序

1. dataclass 默认值
2. `config/evolution.yaml`
3. `config/evolution.local.yaml`
4. `INVEST_CONFIG_PATH` 指向的覆盖文件
5. 环境变量

### 推荐分工

- `config/evolution.yaml`：团队共享、非敏感、可审阅的运行参数
- `config/evolution.local.yaml`：开发机本地密钥、个人覆盖项
- 环境变量：线上密钥、CI/CD、部署平台变量

## 4. 交互与观测发布门

### 必须保留的入口

- `POST /api/chat`：自然语言交互入口
- `GET /api/status`：运行状态总览
- `GET /api/events`：SSE 事件流
- `GET /api/contracts/runtime-v1`：当前机器可读契约文档
- `python3 commander.py ...`：CLI / 调度主入口

### 发布前检查

- [ ] `POST /api/chat` 可正常返回结构化回复。
- [ ] `GET /api/status`、`GET /api/lab/status/quick`、`GET /api/lab/status/deep` 返回正常。
- [ ] `GET /api/events` 可建立 SSE 连接。
- [ ] `GET /api/contracts/runtime-v1` 返回 200。
- [ ] 全量 `./.venv/bin/python -m pytest -q` 通过。

## 5. 回滚开关

### 业务回滚

- 模型路由回滚：
  - `model_routing_enabled: false`
  - 或 `model_routing_mode: off`
- 训练成本回滚：
  - CLI / API 显式启用 smoke/demo/mock
- 保底入口：
  - `python3 commander.py run --interactive`
  - `python3 train.py --cycles 1`

## 6. 最终清单

- [ ] 无真实密钥出现在 README、契约文档、示例配置、测试快照中
- [ ] `config/evolution.yaml` 只保留非敏感共享配置
- [ ] Web UI 已删除，不依赖 `/app` 或 `/legacy` 提供任何人类界面
- [ ] 事件流、状态接口、自然语言交互和 CLI 入口均可用
- [ ] 回滚命令与值班手册已更新为 API / CLI 语义
