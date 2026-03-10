# 安全与发布前清理 Runbook

## 1. 密钥治理

### 目标

- 所有 LLM 密钥默认不再写入 `config/evolution.yaml`。
- 敏感项优先走环境变量，其次走 `config/evolution.local.yaml`。
- Web/API 只返回 `llm_api_key_masked` 与 `llm_api_key_source`，不返回明文。

### 当前约定

- 主配置：`config/evolution.yaml`
- 本地敏感覆盖层：`config/evolution.local.yaml`
- 环境变量优先级最高：`LLM_API_KEY`、`LLM_API_BASE`、`LLM_MODEL`、`LLM_DEEP_MODEL`
- 临时覆盖文件：`INVEST_CONFIG_PATH=/abs/path/to/override.yaml`

### Web API 鉴权

- 生产部署必须配置：`WEB_API_TOKEN`、`WEB_API_REQUIRE_AUTH=true`。
- 可选：`WEB_API_PUBLIC_READ_ENABLED=true` 仅放开只读状态接口，其余接口仍需鉴权。
- 支持请求头：`Authorization: Bearer <token>` 或 `X-Invest-Token: <token>`。
- `GET /healthz` 为无鉴权健康检查接口，仅返回最小存活信息。

### 发布前检查（Web API）

- [ ] 非回环部署时已设置 `WEB_API_TOKEN`。
- [ ] 非回环部署时已设置 `WEB_API_REQUIRE_AUTH=true`。
- [ ] 如需开放匿名读，仅确认 `WEB_API_PUBLIC_READ_ENABLED` 对应风险可接受。
- [ ] Gunicorn / 反向代理启动命令已验证，不直接暴露 Flask 开发服务器。


### 发布前检查

- [ ] `config/evolution.yaml` 中不包含真实密钥。
- [ ] `config/evolution.local.yaml` 已加入 `.gitignore`，且不纳入提交。
- [ ] `.env` / `.env.*` 已加入 `.gitignore`。
- [ ] 通过 `GET /api/evolution_config` 确认 `llm_api_key_source` 为 `env` 或 `local_yaml`。
- [ ] 如果密钥曾暴露在历史文件/截图/日志中，先轮换，再发布。

## 2. 配置分层

### 生效顺序

1. dataclass 默认值
2. `config/evolution.yaml`
3. `config/evolution.local.yaml`
4. `INVEST_CONFIG_PATH` 指向的覆盖文件
5. 环境变量

### 推荐分工

- `config/evolution.yaml`：团队共享、非敏感、可审阅的运行参数
- `config/evolution.local.yaml`：开发机本地密钥、灰度标志、个人覆盖项
- 环境变量：线上密钥、CI/CD、部署平台变量

### 示例

```yaml
# config/evolution.yaml
model_routing_enabled: true
model_routing_mode: rule
web_ui_shell_mode: legacy
frontend_canary_enabled: false
```

```yaml
# config/evolution.local.yaml
llm_api_key: ${ENV:LLM_API_KEY}
```

## 3. 回滚开关

### Web 壳层回滚

- `web_ui_shell_mode: legacy`
  - `/` 始终回到旧静态壳
  - `/legacy` 永久保留旧壳直达入口
  - `/app` 新前端仍可单独访问用于联调

### Web 壳层切换

- `web_ui_shell_mode: app`
  - `/` 直接进入新前端
  - `/legacy` 仍保留，作为一键回滚入口

### 灰度开关

- `frontend_canary_enabled: true`
  - 在 `web_ui_shell_mode=legacy` 时，可通过下列方式进入新前端：
    - 查询参数：`/?__frontend=app`
    - Header：`X-Invest-Frontend-Canary: app`

### 业务回滚

- 模型路由回滚：
  - `model_routing_enabled: false`
  - 或 `model_routing_mode: off`
- 训练成本回滚：
  - CLI / Web 显式启用 Smoke / Demo 模式
- 保底入口：
  - `python3 commander.py run --interactive`
  - `python3 train.py --cycles 1`

## 4. 灰度发布步骤

### Phase 0：构建验证

- [ ] `cd frontend && npm run build`
- [ ] `python3 -m pytest -q`
- [ ] `npx playwright test tests/e2e/training-lab.spec.ts`
- [ ] `GET /api/contracts/frontend-v1` 返回 200

### Phase 1：暗发布

- [ ] 保持 `web_ui_shell_mode=legacy`
- [ ] 部署新 `frontend/dist`
- [ ] 仅内部使用 `/?__frontend=app` 或 Header 访问新前端
- [ ] 观察 `/api/events`、`/api/train`、`/api/evolution_config` 的错误率

### Phase 2：小流量灰度

- [ ] 打开 `frontend_canary_enabled=true`
- [ ] 限定内部 QA / 前端团队 / 运营同学使用 canary 参数
- [ ] 重点验证页面：`/app/training-lab`、`/app/models`、`/app/settings`

### Phase 3：全量切换

- [ ] 将 `web_ui_shell_mode` 改为 `app`
- [ ] 验证 `/legacy` 仍可访问
- [ ] 保留至少一个发布窗口的回滚期

## 5. 回滚步骤

### Web UI 回滚

1. `POST /api/evolution_config`：`{"web_ui_shell_mode":"legacy","frontend_canary_enabled":false}`
2. 校验 `/` 返回旧壳、`/legacy` 可访问
3. 保留 `/app` 仅供排障，不对外宣告

### 模型路由回滚

1. `POST /api/evolution_config`：`{"model_routing_enabled":false,"model_routing_mode":"off"}`
2. 校验 `GET /api/investment-models` 中 `routing.mode=off`
3. 手工跑 1 次 `GET /api/model-routing/preview` 或训练冒烟，确认不再自动切模

## 6. 发布前最终清单

- [ ] 无真实密钥出现在 README、契约文档、示例配置、测试快照中
- [ ] `config/evolution.yaml` 只保留非敏感共享配置
- [ ] `config/evolution.local.yaml` / `.env.*` 已忽略
- [ ] `/legacy`、`/app`、`/api/contracts/frontend-v1` 三条入口均可访问
- [ ] `web_ui_shell_mode`、`frontend_canary_enabled` 已在设置页/API 可改
- [ ] 回滚命令与回滚 JSON 已写入值班手册
