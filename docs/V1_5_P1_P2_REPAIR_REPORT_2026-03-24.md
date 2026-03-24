# v1.5 P1/P2 修复交付报告

日期：2026-03-24  
范围：多经理配置、Web 运行时布尔解析与无状态注入、运行时契约引用校验、训练双快照命名收口、README CLI 同步守卫、结构守卫放宽与 freeze gate 接线

## 1. 缺陷清单与结论

本轮已完成并验证以下问题：

1. 多经理架构开关在 canonical live config 中被默认关闭，导致 runtime projection 偏离实际目标拓扑。
2. Web 配置布尔解析使用直接 truthy/falsy 转换，存在字符串值误判风险。
3. Web 运行时仍持有状态化注入默认值，不符合当前 stateless deploy surface。
4. runtime contract 对 `text/event-stream` 的 body reference 校验过严，产生误报。
5. 训练结果同时持有 `stage_snapshots` 与 `contract_stage_snapshots`，命名与内容长期漂移。
6. README 仍引用已经退役的 `strategies` CLI 命令。
7. 结构守卫对目录 Python 文件集合采用完全相等断言，对受控扩展场景误报过高。

结论：

- P1 修复项已落地并通过定向回归。
- P2 收口项已落地并接入自动化门禁。
- 当前剩余未纳入本轮范围的问题只有已明确延期的 `session-catchup.py` 失败，不属于本次 hotfix 范围。

## 2. 根因分析

### 2.1 配置与部署面

- 配置默认值仍停留在早期单经理/影子模式收缩阶段，没有随 v1.5 的多经理正式架构一并切换。
- Web 层把来自配置中心的字符串布尔值直接传入 `bool(...)`，使 `"false"`、`"no"` 这类值被误判为 `True`。
- Web 运行态默认实例没有强制保持无状态，导致接口层行为和 README 中的公开部署表述不完全一致。

### 2.2 契约与训练工件面

- runtime contract 的 reference 校验没有覆盖 SSE 响应媒体类型，造成合法 contract 被视为 unresolved。
- 训练链路同时维护 canonical 快照与对外 contract 快照，但缺少统一 attach/merge 规则，导致同名 stage 在两个视图中的字段逐步偏离。

### 2.3 文档与治理面

- README CLI 示例缺少 live parser 对照校验，文档迭代后容易保留历史命令名。
- 结构守卫没有区分 canonical owner 与受控白名单扩展，导致目录级防漂移规则过于僵硬。

## 3. 代码落点

- `src/invest_evolution/config/__init__.py`
- `src/invest_evolution/interfaces/web/server.py`
- `src/invest_evolution/application/runtime_contracts.py`
- `src/invest_evolution/application/training/execution.py`
- `src/invest_evolution/application/freeze_gate.py`
- `README.md`
- `tests/test_config_infrastructure_suite.py`
- `tests/test_web_server_runtime_and_bool.py`
- `tests/test_runtime_api_contract.py`
- `tests/test_training_end_to_end_validation_flow.py`
- `tests/test_architecture_closure_assets.py`
- `tests/test_structure_guards.py`

## 4. 测试与验证结果

### 4.1 定向回归

通过：

```bash
./.venv/bin/python -m pytest -q \
  tests/test_training_end_to_end_validation_flow.py \
  tests/test_training_persistence_boundary.py \
  tests/test_architecture_closure_assets.py \
  tests/test_structure_guards.py \
  tests/test_web_server_runtime_and_bool.py \
  tests/test_config_infrastructure_suite.py
```

结果：

- 62 passed

### 4.2 代码质量

通过：

```bash
./.venv/bin/python -m ruff check \
  src/invest_evolution/application/training/execution.py \
  src/invest_evolution/application/freeze_gate.py \
  tests/test_training_end_to_end_validation_flow.py \
  tests/test_architecture_closure_assets.py \
  tests/test_structure_guards.py \
  tests/test_web_server_runtime_and_bool.py \
  tests/test_config_infrastructure_suite.py

./.venv/bin/pyright \
  src/invest_evolution/application/training/execution.py \
  src/invest_evolution/application/freeze_gate.py \
  tests/test_training_end_to_end_validation_flow.py \
  tests/test_architecture_closure_assets.py \
  tests/test_structure_guards.py \
  tests/test_web_server_runtime_and_bool.py \
  tests/test_config_infrastructure_suite.py
```

结果：

- `ruff check` 通过
- `pyright` 0 errors / 0 warnings / 0 informations

### 4.3 发布主链验证

本轮要求执行 automated release-readiness 主链，包含：

- Stage 0 Environment Smoke
- Stage 1 Freeze Gate
- Stage 2 Canonical Release Verification
- Stage 4 Shadow Smoke

实际执行结果以本次发布命令输出为准，并应与 `docs/RELEASE_READINESS.md` 保持一致。

## 5. 性能与行为对比

本轮没有引入新的重型算法或数据路径扩展，性能影响主要体现在治理与校验层：

- 配置布尔解析从直接 truthy/falsy 判断改为显式 token 归一化，增加的是常量级字符串处理成本。
- 双快照收口在 attach 阶段做一次浅层字段合并，复杂度与 stage 数量线性相关，对单周期执行成本影响可忽略。
- README/结构守卫检查进入 freeze gate focused bundle 后，会略微增加验证时间，但换来对文档漂移与目录漂移的自动阻断。

结论：

- 没有发现需要单独回滚的性能回退信号。
- 本轮收益主要来自正确性、可审计性与发布稳定性提升。

## 6. 回滚方案

如果 1.0.1 在放行后需要回滚，按以下顺序执行：

1. 回退 `src/invest_evolution/config/__init__.py` 到上一版本默认开关配置。
2. 回退 `src/invest_evolution/interfaces/web/server.py` 的布尔解析与无状态默认值。
3. 回退 `src/invest_evolution/application/runtime_contracts.py` 的 SSE reference 例外处理。
4. 回退 `src/invest_evolution/application/training/execution.py` 的双快照合并逻辑。
5. 回退 README / freeze gate / 结构守卫相关测试与门禁接线。
6. 重新运行 `freeze_gate --mode quick` 与 `application.release --bundle p0/p1` 确认回滚后的系统仍可启动。

## 7. 发布建议

- 建议版本号：`1.0.1`
- 发布类型：patch
- 推荐 tag：`v1.0.1`
- 推荐放行路径：先跑 release-readiness 主链，再创建 release commit 与 tag
