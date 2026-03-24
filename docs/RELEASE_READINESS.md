# Release Readiness / 发布收口与放行清单

日期：2026-03-23
状态：active

## 目标

这份文档是当前唯一有效的 release readiness / manual sign-off 说明。
它把正式放行链路收口到一条主路径，避免继续依赖归档 checklist、临时命令或会话记忆。

当前正式分层如下：

1. `Stage 0 Environment Smoke`
2. `Stage 1 Freeze Gate`
3. `Stage 2 Canonical Release Verification`
4. `Stage 4 Release Shadow Gate`
5. `Stage 5 Manual Release Sign-off`

说明：

- `Stage 3 Historical Compatibility Gate` 不再混入默认 release readiness 主链。
- 若必须执行历史兼容 lane，应单独跑、单独归档、单独结论。
- 历史版本 checklist 只保留在 `docs/archive/RELEASE_SIGNOFF_CHECKLIST_20260320.md` 作为背景参考，不再作为当前放行入口。

## 统一入口

自动化 Stage 0 -> Stage 4 smoke 聚合入口：

```bash
uv run python scripts/run_release_readiness_gate.py --include-commander-brain --include-shadow-gate
```

该入口当前顺序执行：

- environment bootstrap check
- verification smoke
- freeze gate quick
- `p0` bundle
- `p1` bundle
- `performance-regression` bundle
- 可选 `commander-brain` bundle
- `Stage 4 shadow smoke`（默认 `smoke profile`，自动附加 `--llm-dry-run`）

## Stage 0 Environment Smoke

必须通过：

```bash
python3 scripts/bootstrap_env.py --check
uv run python scripts/run_verification_smoke.py
```

验收口径：

- `uv.lock` 与当前环境一致
- `pytest`、`ruff`、`pyright`、`gunicorn` 等关键依赖可导入
- focused smoke pytest / ruff / pyright 通过
- public surface 已收口到 direct `runtime-v2` contract 与 `/api/status` 主状态入口；不得回流 `/api/contracts` catalog 或 `/api/lab/status/*` alias
- deploy public surface `200/404` smoke 通过
- WSGI import smoke 与 Gunicorn `--check-config` 通过

若 `.venv` console script shebang 漂移，可先执行：

```bash
python3 scripts/bootstrap_env.py --reinstall
```

## Stage 1 Freeze Gate

必须通过：

```bash
uv run python -m invest_evolution.application.freeze_gate --mode quick
```

验收口径：

- runtime contract 派生物无漂移
- focused protocol / golden regression 通过
- critical `ruff` 通过
- critical `pyright` 通过

## Stage 2 Canonical Release Verification

### P0 Bundle

```bash
uv run python -m invest_evolution.application.release --bundle p0
```

覆盖：

- web/runtime split-topology 主回归
- status / events / training lab / governance API
- runtime service / gunicorn / deploy topology 资产

### P1 Bundle

```bash
uv run python -m invest_evolution.application.release --bundle p1
```

覆盖：

- public data surface / runtime persistence surface
- control-plane 服务层与 train bootstrap
- route-level state-backed facade
- 环境与 bootstrap 资产

### Commander / Brain Bundle

```bash
uv run python -m invest_evolution.application.release --bundle commander-brain
```

覆盖：

- commander CLI / transcript / workflow golden
- brain runtime / scheduler / extensions

### Performance Regression Bundle

```bash
uv run python -m invest_evolution.application.release --bundle performance-regression
```

覆盖：

- agent runtime memory / BM25 / indicators
- market data ingestion / unification
- release management / shadow gate 工件回归

### All Bundle

```bash
uv run python -m invest_evolution.application.release --bundle all
```

说明：

- `all` 是完整 union bundle。
- 日常 release readiness 优先走 `scripts/run_release_readiness_gate.py`，因为它同时收口了环境 smoke 与 freeze gate。

## Stage 4 Release Shadow Gate

Stage 4 现在拆成两层：

1. `shadow smoke`
2. `shadow strict`

`shadow smoke` 是自动化 release-readiness 主链的一部分，目标是验证 shadow pipeline、工件、治理统计与 summary/verify 主路径可以一键跑通，不把外部 LLM 抖动和真实策略收敛速度当成默认放行阻塞项。

`shadow strict` 保留真实质量门槛，作为 Stage 5 manual sign-off 前的正式策略资格验证，也就是当前的 `strict profile`。
如果当前目标是“先以最小迭代成本定位 strict 失败项”，允许先跑一次 `strict probe`：
保持 `strict` 的 research feedback 约束不变，只临时收窄验证样本门槛，快速暴露策略/治理问题；probe 通过不等于最终 sign-off 通过。

### Stage 4A Shadow Smoke

自动化入口默认使用 `smoke profile`：

```bash
uv run python scripts/run_release_readiness_gate.py --include-shadow-gate
```

也可手动执行：

```bash
uv run python scripts/run_release_gate_stage1.py --output <fresh-output-dir> --cycles 5 --successful-cycles-target 5 --llm-dry-run
uv run python -m invest_evolution.application.release shadow-gate --run-dir <fresh-output-dir> --profile smoke
```

`smoke profile` 验收口径：

- shadow run 正常结束，`run_status` 必须是 `completed` 或 `completed_with_skips`
- `successful_cycles >= 1`
- `unexpected_reject_count = 0`
- `governance_blocked_count = 0`
- `artifact_completeness = 1.0`
- runtime mutation 工件只允许落在 fresh output dir 下的 `runtime_generations/`

说明：

- `smoke profile` 不要求 `validation_pass_count` / `promote_count` 达到真实策略阈值。
- `smoke profile` 的目标是验证管线与工件，不代替真实策略质量结论。

### Stage 4B Shadow Strict

必须使用 fresh output dir，并满足：

- `successful_cycles >= 30`
- `unexpected_reject_count = 0`
- `governance_blocked_count = 0`
- `validation_pass_count >= 2`
- `promote_count >= 1`
- `candidate_missing_rate <= 0.50`
- `needs_more_optimization_rate <= 0.70`
- `artifact_completeness = 1.0`
- `run_report.freeze_gate_evaluation.research_feedback_gate` 必须满足 contract-ready：
  - 要么 `active = true`
  - 要么 `passed = true` 且 `reason = requested_regime_feedback_unavailable`
- `run_report.freeze_gate_evaluation.research_feedback_gate.passed = true`

推荐命令：

```bash
uv run python scripts/run_release_gate_stage1.py --output <fresh-output-dir>
uv run python -m invest_evolution.application.release shadow-gate --run-dir <fresh-output-dir> --profile strict
```

低成本 strict probe：

```bash
uv run python scripts/run_release_readiness_gate.py \
  --include-shadow-gate \
  --shadow-profile strict \
  --shadow-cycles 8 \
  --shadow-successful-cycles-target 5 \
  --shadow-verify-successful-cycles-min 5 \
  --shadow-verify-validation-pass-count-min 1 \
  --shadow-verify-promote-count-min 0
```

或直接只跑 Stage 4：

```bash
uv run python scripts/run_release_gate_stage1.py \
  --output <fresh-output-dir> \
  --cycles 8 \
  --successful-cycles-target 5 \
  --force-full-cycles
uv run python -m invest_evolution.application.release shadow-gate \
  --run-dir <fresh-output-dir> \
  --profile strict \
  --successful-cycles-min 5 \
  --validation-pass-count-min 1 \
  --promote-count-min 0
```

必须归档的最小工件：

- `run_report.json`
- `release_gate_divergence_report.json`
- `release_gate_divergence_report.md`
- `<fresh-output-dir>/runtime_generations/`

说明：

- 历史 `data/evolution/generations/*` 已从仓库移除，不再作为 runtime 演化产物的落点或归档面。
- strict sign-off 只接受 fresh output dir 中的 run-local 工件，不接受仓库内 data layer 回写。
- 这意味着 strict gate 现在不仅验证 shadow pipeline 的治理统计，还要求 ask/research 反馈门满足 contract-ready 并且结论通过；`insufficient_samples` 不再算 strict 通过，只有显式 `requested_regime_feedback_unavailable` 才可作为“未跨 regime fallback”的可接受收口态。
- probe 只允许临时覆盖 `successful_cycles` / `validation_pass_count` / `promote_count` 这类样本量相关门槛；`research_feedback_gate.active/passed` 仍必须保持 strict 口径。

第三阶段后，manual sign-off 建议额外检查：

- `run_report.research_feedback_coverage`
  - requested regime gap 是否仍明显偏大
  - `next_target_regimes` 是否指向当前 strict 失败的主要 regime
- training evaluation 内的 `assessment.manager_regime_breakdown`
  - 失败是否集中在单一 manager / regime 组合
- `promotion.manager_regime_validation`
  - 若本轮显式开启，应确认失败项确实来自二维质量缺口，而不是低样本误伤

## Stage 5 Manual Release Sign-off

Stage 5 Manual Release Sign-off 只在前面自动化闸门全部通过后执行。

放行前至少确认：

- `scripts/run_release_readiness_gate.py --include-commander-brain` 已通过
- `shadow smoke` 已通过，且 `scripts/run_release_readiness_gate.py --include-commander-brain --include-shadow-gate` 可复现
- `shadow strict` 使用 fresh output dir 且阈值全部通过
- `git status --short` 中不存在 `data/evolution/generations/*` 这类 tracked runtime artifact 漂移
- canonical public contract 已与实际 routes、active docs、deploy smoke 保持一致
- 当前 release 涉及的文档、测试、脚本入口已经同步
- 若有人工 override，必须留下可追溯说明与工件链接

## 不再使用的旧入口

以下内容仍可能出现在历史文档或旧会话中，但不再是当前正式入口：

- `docs/archive/RELEASE_SIGNOFF_CHECKLIST_20260320.md` 作为 active checklist
- `app.release_verification`
- `app.release_shadow_gate`

当前应统一使用：

- `scripts/run_release_readiness_gate.py`
- `invest_evolution.application.release`
- 本文档
