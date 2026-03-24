# Release Gate And Sign-off Checklist / 发布闸门与联签清单

日期：2026-03-20  
范围：`WS1` 验证环境恢复、`WS3` runtime/web/deploy 解耦后的正式放行流程固化

## 1. 目标

这份清单用于把当前已经实测通过的验证资产固化进放行流程，避免后续继续依赖临时命令、口头对齐或会话记忆。

当前正式分层如下：

1. `Stage 0 Environment Smoke`
2. `Stage 1 Static + Contract Gate`
3. `Stage 2 Canonical Release Verification`
4. `Stage 3 Historical Compatibility Gate`
5. `Stage 4 Release Shadow Gate`
6. `Stage 5 Manual Release Sign-off`

自动化 Stage 0 -> Stage 2 聚合入口：

```bash
uv run python scripts/run_release_readiness_gate.py --include-commander-brain
```

说明：

- 该入口顺序执行 environment smoke、freeze gate quick、`p0`、`p1`、`performance-regression`，并可按需加上 `commander-brain`。
- `app.release_verification` 仍是 bundle 的 canonical 定义源。

## 2. Stage 0 Environment Smoke

必须通过：

```bash
uv run python scripts/run_verification_smoke.py
```

要求：

- `uv.lock` 与 `.venv` 一致
- `pandas`、`pytest`、`ruff`、`pyright`、`gunicorn` 可导入
- focused smoke pytest / ruff / pyright 通过

若 `.venv/bin/pytest` 或其他 console script shebang 漂移，先执行：

```bash
python3 scripts/bootstrap_env.py --reinstall
```

## 3. Stage 1 Static + Contract Gate

必须通过：

```bash
uv run python -m app.freeze_gate --mode quick
```

要求：

- runtime contract 派生物无漂移
- focused protocol / golden regression 通过
- critical `ruff` 通过
- critical `pyright` 通过

## 4. Stage 2 Canonical Release Verification

### 4.1 P0 Bundle

命令：

```bash
uv run python -m app.release_verification --bundle p0
```

覆盖：

- web/runtime split-topology 主回归
- state-backed status / events / training lab / leaderboard
- contract headers / runtime contract / control-plane API
- runtime service / gunicorn / deploy topology 资产

### 4.2 P1 Bundle

命令：

```bash
uv run python -m app.release_verification --bundle p1
```

覆盖：

- data API / memory API
- control-plane 服务层与 train bootstrap
- route-level state-backed facade
- V2 contracts
- 环境与 bootstrap 资产

### 4.3 Commander / Brain Bundle

命令：

```bash
uv run python -m app.release_verification --bundle commander-brain
```

覆盖：

- `BrainRuntime`
- brain scheduler / extensions
- commander transcript / mutating workflow / direct planner golden
- commander CLI / validation / unified entry

### 4.4 Performance Regression Bundle

命令：

```bash
uv run python -m app.release_verification --bundle performance-regression
```

覆盖：

- `src/invest_evolution/agent_runtime/memory.py` append / truncate / cached-count regression
- `invest/memory.py` lazy BM25 rebuild regression
- `compute_indicator_snapshot()` correctness regression
- `src/invest_evolution/market_data/ingestion.py` focused performance-path correctness
- release shadow gate artifact / divergence report regression

### 4.5 全量当前 Canonical Bundle

命令：

```bash
uv run python -m app.release_verification --bundle all
```

或直接使用聚合入口：

```bash
uv run python scripts/run_release_readiness_gate.py --include-commander-brain
```

要求：

- `p0`、`p1`、`performance-regression`、`commander-brain` 四层全部通过
- 不接受口头“只差少量 warning”式放行

## 5. Stage 3 Historical Compatibility Gate

必须单独执行历史兼容 lane，不得混入 canonical bundle：

- 先执行历史分区守卫：

```bash
uv run python -m pytest -q tests/test_historical_compatibility_partition.py
```

- 再执行单独 lane，并生成独立报告：

```bash
uv run python -m pytest -q -m historical_compatibility \
  --junitxml=outputs/release_readiness/historical_compatibility.junit.xml
```

- `historical_compatibility` 测试单独跑
- 单独出报告
- 结果单独归档

当前说明：

- 本清单只固化 canonical lane
- historical lane 仍需按既有兼容策略单列执行

## 6. Stage 4 Release Shadow Gate

必须满足：

- 使用 fresh output dir
- 工件完整率 `100%`
- `successful_cycles >= 30`
- `unexpected_reject_count = 0`
- `governance_blocked_count = 0`
- `validation_pass_count >= 2`
- `promote_count >= 1`
- `candidate_missing_rate <= 0.50`
- `needs_more_optimization_rate <= 0.70`

推荐入口：

```bash
uv run python scripts/run_release_gate_stage1.py --output <fresh-output-dir>
uv run python -m app.release_shadow_gate --run-dir <fresh-output-dir>
```

必须归档：

- `run_report.json`
- release gate JSON report
- release gate Markdown report
- shadow gate 指标摘要

当前 blocker 说明：

- `outputs/release_shadow_gate_20260320_114407_formal/` 是正式失败样本，不得视为通过。
- 当前失败事实包括：
  - 缺失 `run_report.json`
  - 缺失 JSON / Markdown divergence reports
  - `cycle_23.json` 工件异常膨胀
- `outputs/release_shadow_gate_bounded_smoke_v2/` 只证明工件链路已恢复，不可替代 fresh-output formal pass 样本。
- `outputs/release_shadow_gate_20260321_012500_formal_rerun_v12/` 是新的 authoritative fresh-output formal pass sample。
- 该样本已满足：
  - `successful_cycles = 30`
  - `unexpected_reject_count = 0`
  - `governance_blocked_count = 0`
  - `validation_pass_count = 14`
  - `promote_count = 8`
  - `candidate_missing_rate = 0.0`
  - `needs_more_optimization_rate = 0.0667`
  - `artifact_completeness = 1.0`

## 7. Stage 5 Manual Release Sign-off

当前 Stage 0 到 Stage 4 已具备通过证据，且五个固定签署项已在本次最终收口中完成记录，因此当前状态可表述为“已完成人工联签并已生产放行”。

固定签署项：

- [x] architecture review
- [x] security review
- [x] verification review
- [x] operations review
- [x] release manager sign-off

记录口径：

- signer: `codex`
- signed_at: `2026-03-21 14:08 +08:00`
- conclusion: `通过`

## 8. 必须附带的证据包

- [ ] `scripts/run_verification_smoke.py` 执行结果
- [ ] `app.freeze_gate --mode quick` 执行结果
- [ ] `app.release_verification --bundle p0` 执行结果
- [ ] `app.release_verification --bundle p1` 执行结果
- [ ] `app.release_verification --bundle performance-regression` 执行结果
- [ ] `app.release_verification --bundle commander-brain` 执行结果
- [ ] historical compatibility lane 报告
- [ ] release shadow gate 报告与工件目录
- [ ] deploy topology 文档一致性证据
- [ ] runtime clean-boot / restart / stale-lock 操作记录

## 9. 当前阶段结论

截至 2026-03-20，以下项目已具备正式流程入口：

- `WS1`：环境恢复、bootstrap、smoke gate
- `WS3`：web/runtime/deploy split-topology 主回归
- `WS6`：性能与工件 focused regression bundle
- `commander / brain`：上层 canonical 集成 bundle

因此，后续放行应直接复用本清单与对应脚本，而不是重新手写命令集合。

## 10. 建议的统一放行编排命令

执行 Stage 0 到 Stage 3：

```bash
uv run python scripts/run_release_readiness_gate.py --include-commander-brain
```

执行 Stage 0 到 Stage 4：

```bash
uv run python scripts/run_release_readiness_gate.py \
  --include-commander-brain \
  --include-shadow-gate \
  --shadow-output-dir <fresh-output-dir>
```
