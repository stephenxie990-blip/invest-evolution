# Canonical Surface

## 正式实现入口

当前正式实现统一位于：

- `src/invest_evolution/application/commander_main.py`
- `src/invest_evolution/application/config_surface.py`
- `src/invest_evolution/application/commander/bootstrap.py`
- `src/invest_evolution/application/commander/ops.py`
- `src/invest_evolution/application/commander/runtime.py`
- `src/invest_evolution/application/commander/status.py`
- `src/invest_evolution/application/commander/workflow.py`
- `src/invest_evolution/application/train.py`
- `src/invest_evolution/application/training/bootstrap.py`
- `src/invest_evolution/application/training/controller.py`
- `src/invest_evolution/application/training/execution.py`
- `src/invest_evolution/application/training/review.py`
- `src/invest_evolution/application/training/policy.py`
- `src/invest_evolution/interfaces/web/server.py`
- `src/invest_evolution/common/utils.py`

其中：

- `application/train.py` 与 `application/commander_main.py` 是稳定 facade owner
- `application/config_surface.py` 是 config public-surface 的 canonical owner；`web/*` 与 `commander/*` 共享的 config/runtime fallback helper 统一从这里消费
- `training/bootstrap.py` 承接训练入口装配层；CLI 参数解析与 dispatch 已并回 `train.py`
- `commander/bootstrap.py` / `commander/workflow.py` / `commander/ops.py` 承接 Commander 入口装配层与动作收口

仓库根目录不再保留业务入口。对外稳定入口统一由 console scripts 和项目托管环境中的 `python -m invest_evolution...` 提供，不恢复旧根入口壳或 alias 注入。

## 稳定调用方式

推荐使用：

- `invest-commander`
- `invest-train`
- `invest-runtime`
- `invest-data`
- `invest-refresh-contracts`
- `invest-freeze-gate`
- `invest-release-verify`
- `uv run python -m invest_evolution.interfaces.cli.commander`
- `uv run python -m invest_evolution.interfaces.cli.train`
- `uv run python -m invest_evolution.interfaces.cli.runtime`
- `uv run python -m invest_evolution.interfaces.cli.market_data`
- `./.venv/bin/python -m invest_evolution.interfaces.cli.commander`
- `./.venv/bin/python -m invest_evolution.interfaces.cli.train`
- `./.venv/bin/python -m invest_evolution.interfaces.cli.runtime`
- `./.venv/bin/python -m invest_evolution.interfaces.cli.market_data`

说明：

- `python -m invest_evolution...` 的稳定前提是使用项目托管 `.venv` 或已安装当前包的解释器。
- 普通系统解释器在源码 checkout 下不保证能直接发现 `src/` 包；这不属于对外兼容面。

## 入口分层

当前兼容面不是“所有入口地位相同”，而是分层约定：

- 人类主入口：
  `invest-commander`
  `uv run python -m invest_evolution.interfaces.cli.commander`
- 批处理 / 自动化兼容入口：
  `invest-train`
  `uv run python -m invest_evolution.interfaces.cli.train`
- runtime daemon 入口：
  `invest-runtime`
- stateless deploy / machine surface：
  `invest_evolution.interfaces.web.wsgi:app`
  `GET /api/status`
  `GET /api/events`
  `GET /api/events/summary`
  `POST /api/chat`
  `POST /api/chat/stream`
  `GET /api/lab/training/plans`
  `POST /api/lab/training/plans`
  `GET /api/lab/training/plans/{plan_id}`
  `POST /api/lab/training/plans/{plan_id}/execute`
  `GET /api/lab/training/runs`
  `GET /api/lab/training/runs/{run_id}`
  `GET /api/lab/training/evaluations`
  `GET /api/lab/training/evaluations/{run_id}`
  `GET /api/runtime_paths`
  `POST /api/runtime_paths`
  `GET /api/evolution_config`
  `POST /api/evolution_config`
  `GET /api/control_plane`
  `POST /api/control_plane`
  `GET /api/agent_prompts`
  `POST /api/agent_prompts`
  `GET /api/data/status`
  `POST /api/data/download`
  `GET /api/contracts/runtime-v2`
  `GET /api/contracts/runtime-v2/schema`
  `GET /api/contracts/runtime-v2/openapi`

补充约定：

- Commander 是唯一推荐的人类入口。
- `invest-train` 保留为协议化训练/调试入口，不重新包装为新的产品面。
- `invest-data` 保留为数据底座维护入口，不承接 Commander 的控制面职责。
- Web/API 只保留可视化、状态读取、SSE 与 API 命令路由，不再承担新的产品主入口语义。
- Web/API 是无状态 deploy surface，不再承担默认嵌入 runtime 的产品壳职责。
- 正式部署拓扑固定为 `invest-evolution-runtime.service` + `invest-evolution.service` + Nginx。
- `invest_evolution.interfaces.web.wsgi:app` 是唯一 canonical WSGI 入口。
- `--embedded-runtime` 仅允许 `compat/dev` 场景，不回流为生产默认。
- `/api/agent_prompts` 的 public write contract 已收窄为 `name + system_prompt`；模型绑定统一经 `/api/control_plane`。
- `/api/runtime_paths` 的 public read/write contract 仅保留 `training_output_dir` 与 `artifact_log_dir`；配置审计与快照路径属于内部 runtime/data layer 细节，runtime 是否可用由 endpoint contract 表达，不再通过 config payload 暴露额外布尔位。
- `/api/evolution_config` 的 public GET/POST response 仅保留训练与 Web 运行参数；配置层叠路径、runtime override 路径、审计/快照位置、compat/runtime 版本信号与 auth secret source 不属于 public metadata。
- `/api/control_plane` 的 public GET 只保留 masked binding/config 与 `llm_resolution` 治理诊断；本地配置/审计/快照路径不属于 public metadata。
- `/api/control_plane` 的 public POST 只接受 `llm.providers` / `llm.models` / `llm.bindings` 与 `data.runtime_policy`；其它 config metadata、审计路径与 runtime-only 字段不属于 public write surface。
- `/api/status`、`/api/events/summary`、training lab 只读接口以及 config/data fallback 接口在 stateless deploy 下仍可工作；`runtime_required` 只保留给真正依赖 live runtime/loop 的对话或执行入口，例如 `POST /api/chat`、`POST /api/chat/stream`、`POST /api/lab/training/plans`、`POST /api/lab/training/plans/{plan_id}/execute`。
- 以下旧公共路由已退役，不再属于 canonical contract：`/api/train`、`/api/leaderboard`、`/api/allocator`、`/api/governance/preview`、`/api/managers`、`/api/playbooks`、`/api/playbooks/reload`、`/api/cron`、`/api/cron/{job_id}`、`/api/memory`、`/api/memory/{record_id}`、`/api/data/capital_flow`、`/api/data/dragon_tiger`、`/api/data/intraday_60m`、`/api/lab/status/quick`、`/api/lab/status/deep`、`/api/contracts`。

## Contract 分层

当前仓库需要明确区分两类 contract：

- `Public contract`
  - 面向 deploy、CLI、外部自动化与兼容承诺。
  - 具体承载物是 console scripts、`invest_evolution.interfaces.web.wsgi:app`、`/api/*` 路由，以及 `docs/contracts/runtime-api-contract.v2.json` / schema / openapi。
- `Internal runtime/agent contract`
  - 面向仓库内部运行时编排，不等价于 public Web/API surface。
  - 具体承载物包括 `bounded_workflow.v2`、`task_bus.v2`、`task_coverage.v2`、`artifact_taxonomy.v2`、tool registry、transcript snapshots、agent role baseline 与内部 orchestration payload。

收口规则：

- 如果改动影响 `Public contract`，必须同步更新 runtime-v2 contract、`COMPATIBILITY_SURFACE.md`，并通过 public surface 精确守卫与 deploy smoke。
- 如果改动只影响 `Internal runtime/agent contract`，应更新相应实现文档，例如 `AGENT_INTERACTION.md`、`MAIN_FLOW.md` 或训练/运行时设计文档，但不要顺手把内部能力提升成新的 `/api/*` 入口。
- `agent_runtime`、`application/commander/*` 中出现的内部能力、工具名或 task bus 字段，不自动构成外部兼容承诺。
- `application/commander_main.py` 是稳定 facade owner，但 `application/commander/*` 内部模块不得反向把它当成依赖总线；内部协作应直接依赖 `bootstrap / ops / runtime / status / workflow / research_services` 等 canonical owner。
- `interfaces/web/server.py` 不直接装载 `commander_main` 类型；默认 embedded runtime 类型解析收口到 `interfaces/web/runtime.py`，避免 HTTP 入口层重新变成 Commander facade 的旁路 owner。
- `interfaces/web/routes.py` 不再拥有 config fallback helper 的 canonical 实现；这部分逻辑统一收口到 `application/config_surface.py`。
- `interfaces/web/routes.py` 对 config surface 只保留 HTTP adapter 职责；surface route registry、public patch validator 与 fallback/update spec 统一收口到 `application/config_surface.py`。
- Web response 中透出的 `task_bus`、`feedback`、`next_action` 与 `X-Bounded-Workflow-Schema` / `X-Task-Bus-Schema` 等 headers，只说明 public envelope 携带了内部协议版本信号，不表示 deploy 兼容面会为 planner / audit / artifact 全字段逐项兜底。
- `/api/agent_prompts` 仍属于 public config surface，但它表达的是 role prompt / role baseline 管理，不是 `agent_runtime` capability registry。
- contract 公开面已经收口到 `runtime-v2` 单一版本；不再额外保留 `/api/contracts` catalog 枚举入口。
- 对外只有显式列入 runtime-v2 contract 和本文件的入口，才算 public surface。

## 平台核心 vs 投资域核心

- 平台核心：`interfaces/`、`application/`、`agent_runtime/`、`config/`、`common/`
- 投资域核心：`investment/`、`market_data/`
- 平台核心负责入口、编排、运行态桥接、工具协议与配置治理；投资域核心负责经理体系、治理、研究、进化与数据事实。

## 仓库边界

- 所有产品源码位于 `src/invest_evolution/`
- 根目录 Python 文件只保留部署相关的 `gunicorn.conf.py`
- `outputs/`、agent planning 文件、`.Codex/`、`.workspace/` 均视为本地态，不属于版本化公共面
- `agent_settings/agents_config.json` 维护仓库内可复现 baseline；运行时对该文件的本地修改若需保留，应显式提交
- 历史设计与执行材料进入 `docs/archive/`

## 维护规则

- 新功能一律进入 `src/invest_evolution/` 或 `scripts/`
- 不再新增根目录启动壳、旧命名空间别名或临时兼容导出
- 若未来再发生结构收口，应直接更新 canonical 文档与测试，不恢复旧入口或历史兼容壳
