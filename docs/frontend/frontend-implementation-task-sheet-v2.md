# Frontend 团队实施任务单 V2

> 目标：以前后端契约为唯一依赖，独立升级 `/app` 新前端；旧壳 `/legacy` 继续保留，后端 CLI 与 API 无感运行。
>
> 契约入口：`GET /api/contracts/frontend-v1`
> OpenAPI：`GET /api/contracts/frontend-v1/openapi`
> JSON Schema：`GET /api/contracts/frontend-v1/schema`

## 一、工作边界

- [ ] `FEV2-00` 仅消费 HTTP + SSE 契约，不直接读取 Python 内部实现。
- [ ] `FEV2-01` 新前端固定挂载在 `/app`，旧页面保留在 `/legacy`。
- [ ] `FEV2-02` 所有页面状态统一分为 `loading / success / empty / degraded / error`。
- [ ] `FEV2-03` 页面内任何“训练已完成/失败/降级”判断都必须来自接口字段，不允许硬编码推断。
- [ ] `FEV2-04` SSE 事件处理统一走 `useEventStream()`，禁止页面自行散落创建多个 `EventSource`。

## 二、按页面拆分 Checklist

### A. Dashboard 页面

- [ ] `FEV2-DASH-01` 接入 `GET /api/lab/status/quick`，展示 runtime、training_lab、data、strategies 的摘要卡片。
- [ ] `FEV2-DASH-02` 支持 `detail_mode=fast` 默认展示，慢查询能力延后，不阻塞首页加载。
- [ ] `FEV2-DASH-03` 当 `training_lab.plan_count/run_count/evaluation_count` 缺失时，退化显示 `--`，不报前端异常。
- [ ] `FEV2-DASH-04` Dashboard 卡片中的“进入训练实验室”“查看配置”“查看数据状态”跳转到对应页面。
- [ ] `FEV2-DASH-05` 验收：刷新页面后 2 秒内完成首屏渲染，无未捕获 Promise 错误。

### B. Training Lab 页面

- [ ] `FEV2-TRAIN-01` 用 `GET /api/lab/training/plans?limit=10` 渲染计划列表。
- [ ] `FEV2-TRAIN-02` 用 `GET /api/lab/training/plans/{plan_id}` 渲染计划详情、目标、spec、artifacts。
- [ ] `FEV2-TRAIN-03` 用 `GET /api/lab/training/runs?limit=10` 渲染训练运行列表。
- [ ] `FEV2-TRAIN-04` 用 `GET /api/lab/training/runs/{run_id}` 渲染运行详情与 `results[]` 周期结果。
- [ ] `FEV2-TRAIN-05` 用 `GET /api/lab/training/evaluations?limit=10` 渲染评估列表。
- [ ] `FEV2-TRAIN-06` 用 `GET /api/lab/training/evaluations/{run_id}` 渲染 promotion / assessment / artifacts。
- [ ] `FEV2-TRAIN-07` 用 `POST /api/train` 启动训练，默认 `mock=false`，仅在用户显式勾选 Smoke / Demo 时传 `mock=true`。
- [ ] `FEV2-TRAIN-08` 用 `GET /api/model-routing/preview` 展示截断日、市场状态、选中模型、切模原因。
- [ ] `FEV2-TRAIN-09` 用 `GET /api/investment-models` 展示 `routing.enabled`、`routing.mode`、`routing.allowed_models`、`routing.last_decision`。
- [ ] `FEV2-TRAIN-10` 补齐训练中心六个产品卡片：路由卡、Agent 总览、时间线筛选、发言卡、策略差异卡、训练结果卡。
- [ ] `FEV2-TRAIN-11` `results[].routing_decision` 必须以结构化视图展示，不退化成原始 JSON 文本。
- [ ] `FEV2-TRAIN-12` `optimization_events[]` 必须支持按 trigger/stage 分组展示。
- [ ] `FEV2-TRAIN-13` `degraded=true` 时显示“降级运行”横幅，并透出 `degrade_reason`。
- [ ] `FEV2-TRAIN-14` `status=no_data`、`status=error`、`status=ok` 三种周期结果分别使用不同视觉态。
- [ ] `FEV2-TRAIN-15` 验收：本页通过 Playwright 用例，覆盖“真实训练错误 + SSE 实时面板 + 策略差异”。

### C. Models 页面

- [ ] `FEV2-MODEL-01` 接入 `GET /api/investment-models`，展示模型清单、当前激活模型、当前配置文件。
- [ ] `FEV2-MODEL-02` 接入 `GET /api/leaderboard`，展示总榜和 regime 维度榜单。
- [ ] `FEV2-MODEL-03` 接入 `GET /api/allocator`，展示 allocation、active_models、model_weights、reasoning。
- [ ] `FEV2-MODEL-04` 当 `routing.mode=off` 时，页面必须明确提示“固定模型模式”。
- [ ] `FEV2-MODEL-05` 当 `routing.last_decision` 缺失时，显示“尚未产生路由决策”，不得展示过期缓存内容。
- [ ] `FEV2-MODEL-06` 验收：从榜单页跳入训练页时，可带上默认模型筛选或预览参数。

### D. Settings 页面

- [ ] `FEV2-SET-01` 接入 `GET /api/evolution_config` 展示当前参数、掩码后的密钥状态、配置层路径。
- [ ] `FEV2-SET-02` 接入 `POST /api/evolution_config` 更新训练参数、模型路由参数、Web 壳发布开关。
- [ ] `FEV2-SET-03` 接入 `GET /api/runtime_paths` / `POST /api/runtime_paths` 管理输出目录、会议日志、配置快照目录。
- [ ] `FEV2-SET-04` 设置页必须可编辑以下新增字段：`web_ui_shell_mode`、`frontend_canary_enabled`。
- [ ] `FEV2-SET-05` 设置页必须只展示 `llm_api_key_masked` 和 `llm_api_key_source`，禁止显示明文密钥。
- [ ] `FEV2-SET-06` 当 `llm_api_key_source=yaml` 时，给出“迁移到环境变量或 local override”的安全提示。
- [ ] `FEV2-SET-07` 验收：提交布尔字段时兼容 `true/false`，提交后页面能重新拉取最新配置并刷新显示。

### E. Data 页面

- [ ] `FEV2-DATA-01` 接入 `GET /api/data/status` 展示离线库规模、最新日期、质量摘要。
- [ ] `FEV2-DATA-02` 接入深查接口：`/api/data/capital_flow`、`/api/data/dragon_tiger`、`/api/data/intraday_60m`。
- [ ] `FEV2-DATA-03` 对慢查询统一提供 skeleton + empty + retry，而不是阻塞整页。
- [ ] `FEV2-DATA-04` 对 `503 data_source_unavailable` 按结构化错误卡渲染建议动作。
- [ ] `FEV2-DATA-05` 验收：在离线库可用但在线不可用的情况下，页面仍能稳定展示离线状态和诊断信息。

## 三、按接口拆分 Checklist

### 核心训练接口

- [ ] `FEV2-API-TRAIN-01` `POST /api/train` 请求体：`rounds`、`mock`。
- [ ] `FEV2-API-TRAIN-02` `POST /api/train` 响应需消费：`results[]`、`summary`、`results[].routing_decision`、`results[].error_payload`。
- [ ] `FEV2-API-TRAIN-03` `GET /api/model-routing/preview` 查询参数：`cutoff_date`、`stock_count`、`allowed_models`。
- [ ] `FEV2-API-TRAIN-04` `GET /api/investment-models` 除模型列表外，必须消费 `routing.*` 元信息。

### 实验室接口

- [ ] `FEV2-API-LAB-01` 列表页统一消费 `count + items[]` 契约。
- [ ] `FEV2-API-LAB-02` 详情页统一以 `plan_id/run_id` 为主键，禁止拼接路径猜测文件路径。
- [ ] `FEV2-API-LAB-03` `artifacts` 中任意路径字段只能作为“链接展示值”，不得直接信任用于文件系统访问。

### 配置接口

- [ ] `FEV2-API-CONFIG-01` `GET /api/evolution_config` 消费 `config_layers`、`local_override_path`、`llm_api_key_source`。
- [ ] `FEV2-API-CONFIG-02` `POST /api/evolution_config` 只提交发生变更的字段。
- [ ] `FEV2-API-CONFIG-03` 若接口返回 `updated=[]`，前端显示“无变更”而不是“保存失败”。

## 四、按错误流拆分 Checklist

### HTTP 级错误

- [ ] `FEV2-ERR-01` `400`：表单校验错误，展示字段级错误或 toast，不做全页 fatal。
- [ ] `FEV2-ERR-02` `404`：`/app` 产物缺失时，前端运维提示指向 `frontend/dist` 构建。
- [ ] `FEV2-ERR-03` `500`：显示请求 ID / 错误摘要 / 重试按钮，不显示 Python traceback。
- [ ] `FEV2-ERR-04` `503`：对训练数据不可用、runtime 未就绪采用专门错误卡，而不是空白页。

### 训练结果级错误

- [ ] `FEV2-ERR-10` 消费 `results[].error_payload.error_code`，至少支持 `data_source_unavailable` 的专门展示。
- [ ] `FEV2-ERR-11` 消费 `results[].error_payload.available_sources`，展示 offline / online / mock 可用性。
- [ ] `FEV2-ERR-12` 消费 `results[].error_payload.suggestions[]`，以 CTA 列表展示下一步动作。
- [ ] `FEV2-ERR-13` 当 `allow_mock_fallback=false` 时，禁止前端自动重试为 mock 模式。

### 降级流

- [ ] `FEV2-ERR-20` 当 `degraded=true` 时，必须展示 `requested_data_mode` 与 `effective_data_mode` 的差异。
- [ ] `FEV2-ERR-21` 当 `effective_data_mode=offline` 且 `requested_data_mode=live` 时，展示“已从在线退化到离线缓存”。
- [ ] `FEV2-ERR-22` 当 `llm_mode=dry_run` 时，页面必须显式标识为低成本演练，不可与正式训练混淆。

## 五、按 SSE 事件拆分 Checklist

- [ ] `FEV2-SSE-01` 统一订阅 `/api/events`。
- [ ] `FEV2-SSE-02` 支持 `connected` 事件的连接态显示。
- [ ] `FEV2-SSE-03` 支持 `cycle_start`、`cycle_complete` 更新训练时间线与结果卡。
- [ ] `FEV2-SSE-04` 支持 `agent_status` 更新 Agent 总览状态。
- [ ] `FEV2-SSE-05` 支持 `module_log`，并从 `kind=routing_decision` 中提取路由摘要。
- [ ] `FEV2-SSE-06` 支持 `meeting_speech`，更新发言卡与会议时间线。
- [ ] `FEV2-SSE-07` 支持 `routing_decided`，刷新“市场状态 / 选中模型 / 置信度 / 原因”。
- [ ] `FEV2-SSE-08` 事件契约不匹配时写控制台 debug + 页面非阻断提示，不能让整个页面崩溃。
- [ ] `FEV2-SSE-09` SSE 断线后显示“自动重连中”，保留最近 100 条事件缓存。

## 六、验收标准 Checklist

### 功能验收

- [ ] `FEV2-ACC-01` `/app` 可独立运行，`/legacy` 可回滚访问。
- [ ] `FEV2-ACC-02` 训练中心完整恢复路由卡、Agent 总览、时间线筛选、发言卡、策略差异卡。
- [ ] `FEV2-ACC-03` 前端只依赖契约文档与 TS schema，不依赖 Python 文件结构。
- [ ] `FEV2-ACC-04` 配置页可安全展示密钥来源与配置层，不暴露密钥明文。

### 测试验收

- [ ] `FEV2-ACC-10` 单页至少覆盖 loading / success / empty / error 四态。
- [ ] `FEV2-ACC-11` `Training Lab` 通过 Playwright E2E：列表、详情、结构化错误、SSE 面板、策略差异。
- [ ] `FEV2-ACC-12` 新增页面交互必须通过契约 mock 测试，不依赖真实训练运行。
- [ ] `FEV2-ACC-13` 所有 API 失败必须可重试，且不会污染 React Query cache。

### 发布验收

- [ ] `FEV2-ACC-20` 灰度期间 `web_ui_shell_mode=legacy`，仅通过 `?__frontend=app` 或 Header 进入新前端。
- [ ] `FEV2-ACC-21` 全量切换前完成 `/api/train`、`/api/model-routing/preview`、`/api/evolution_config` 三条关键链路人工冒烟。
- [ ] `FEV2-ACC-22` 回滚时仅调整 `web_ui_shell_mode` 或关闭 canary，不需要改后端业务逻辑。
