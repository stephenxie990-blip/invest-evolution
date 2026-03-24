# Onboarding And Handoff / 入门与交接清单

日期：2026-03-23
状态：active

## 目标

这份文档是仓库当前最小 onboarding / handoff kit。
它不记录一次性会话细节，而是固定新人上手、工程接力、阶段收口时必须共享的最小事实。

## 第一小时阅读路径

如果你是第一次进入仓库，建议按这个顺序阅读：

1. `README.md`
2. `docs/README.md`
3. `docs/MAIN_FLOW.md`
4. `docs/TRAINING_FLOW.md`
5. `docs/COMPATIBILITY_SURFACE.md`
6. `docs/CONFIG_GOVERNANCE.md`
7. `docs/DATA_ACCESS_ARCHITECTURE.md`

读完之后，应当能回答四个问题：

- 当前唯一推荐的人类入口是什么
- 当前对外只讲哪三件事
- 训练 / Web / release gate 的正式主链分别是什么
- 数据层与配置层的稳定边界在哪里

## 当前对外只讲三件事

从产品和对外沟通角度，当前仓库只压缩成三条主叙事：

1. `Commander control surface`
   唯一推荐的人类入口，承接状态查询、训练执行、训练实验室、配置管理和运行诊断。
2. `Training Lab + governance loop`
   训练计划、训练运行、训练评估、晋级与复盘工件构成当前最完整的能力闭环。
3. `Stateless Web/API deploy surface`
   Web/API 是部署与机器读写界面，不承担新的产品主入口职责。

其余入口、辅助脚本和兼容 facade 都应围绕这三件事服务，而不是重新长出第四种产品口径。

## 验证阶梯

进入改动前或交接前，至少明确自己需要跑到哪一层：

### Level 0: 环境一致性

```bash
python3 scripts/bootstrap_env.py --check
uv run python scripts/run_verification_smoke.py
```

### Level 1: 聚焦回归

针对你修改的模块跑 focused pytest，例如：

```bash
uv run python -m pytest -q tests/test_release_management_suite.py
```

### Level 2: 主链 bundle

```bash
uv run python -m invest_evolution.application.release --bundle p0
uv run python -m invest_evolution.application.release --bundle p1
```

### Level 3: 放行链路

```bash
uv run python scripts/run_release_readiness_gate.py --include-commander-brain
```

## Ownership Map / 当前 owner 视角

- `Commander / control surface`
  - `src/invest_evolution/application/commander_main.py`
  - `src/invest_evolution/application/commander/`
- `Training loop / governance`
  - `src/invest_evolution/application/training/`
  - `src/invest_evolution/investment/`
- `Stateless Web/API surface`
  - `src/invest_evolution/interfaces/web/`
  - `gunicorn.conf.py`
- `Data foundation`
  - `src/invest_evolution/market_data/repository.py`
  - `src/invest_evolution/market_data/manager.py`
  - `src/invest_evolution/market_data/datasets.py`
- `Compatibility + docs + release gate`
  - `docs/`
  - `scripts/run_release_readiness_gate.py`
  - `src/invest_evolution/application/release.py`

## Handoff Checklist / 交接清单

交接时至少说明以下内容：

1. 本次改动收口到哪一条主链
2. 改动影响的是哪一类 public surface
3. 已运行哪些验证，哪些未运行
4. 是否改动了配置面、数据边界或 release gate
5. 是否同步更新了 README / docs / tests

推荐 handoff 模板：

```text
Scope:
- 本次只改动哪条主链

Surface:
- 影响的人类入口 / Web API / 数据层 / release gate

Verification:
- 已跑：
- 未跑：

Open Risks:
- 仍需人工确认或后续补强的点
```

## 不应该出现在 handoff 里的内容

- 纯会话噪音
- 没有落到文件和验证上的口头判断
- 只说“应该没问题”但没有测试或工件支撑的结论
