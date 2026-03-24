# v1.5 第二阶段优化实施清单

## 目标

本清单对应 v1.5 当前主链稳定后的第二阶段收口工作，范围只包含 4 项已经确认、且可以独立验收的优化：

1. `manager_config_ref` canonicalization 统一
2. review/eval boundary 的 `subject_type` 判定收口
3. isolated experiment preset / discovery 收口
4. research case store 读取与缓存优化

本轮原则：

- 不恢复已退役包结构
- 不重写现有治理主链
- 只做可验证、可回滚、可审计的增量收口

## 实施顺序

1. 统一 `manager_config_ref` 语义
2. 修正 review/eval 主语边界
3. 收口 isolated experiments
4. 优化 research case store 读取

排序原因：
先固定“主体身份”和“边界投影”的基础语义，再处理实验发现策略和 research I/O，否则后两项验证会建立在漂移语义之上。

## 优化 1：`manager_config_ref` Canonicalization 统一

### 目标

把 training、execution、review contracts、research feedback 对 `manager_config_ref` 的处理接到同一套 canonicalization 规则上，消除“写的时候一套、读的时候一套”的漂移。

### 风险

- 旧 payload 中同时存在 alias、relative path、absolute path、bare filename
- 若强行统一为单一绝对路径，可能破坏 `executed.yaml` 这类兼容语义
- manager identity 与 runtime config identity 脱钩时，会把错误 ref 透传到 review/research 阶段

### Owner 文件

- `src/invest_evolution/investment/managers/registry.py`
- `src/invest_evolution/application/training/policy.py`
- `src/invest_evolution/application/training/execution.py`
- `src/invest_evolution/application/training/review_contracts/__init__.py`
- `src/invest_evolution/investment/research/case_store.py`
- `tests/test_runtime_config_ref_semantics.py`

### 实施步骤

1. 将共享 helper 固定在 manager registry 层，统一 alias/path/bare filename 的归一化入口。
2. 让 training policy 的 `resolve_manager_config_ref()` 与 scope canonicalization 复用共享 helper。
3. 让 execution projection 与 review contracts 复用同一套 canonical ref 语义。
4. 让 research case store 在有 `manager_id` 上下文时执行 manager-aware canonicalization，避免 alias 侧读写不一致。
5. 增补 basename / alias / relative path 的回归测试。

### 验收标准

- alias 输入如 `momentum_v1` 会稳定收口到同一 runtime config ref
- bare filename 如 `executed.yaml` 保持兼容，不被错误绝对化
- training / execution / review / research 对同一 manager 的 config ref 判定一致

### 验证命令

```bash
python3 -m pytest -q tests/test_runtime_config_ref_semantics.py
```

### 回滚边界

- 只回滚共享 canonicalization helper 和其调用点
- 不回滚 research feedback 统计逻辑与 isolated experiments 逻辑
- 回滚后必须重新验证 bare filename 与 alias 兼容语义

## 优化 2：review/eval boundary `subject_type` 收口

### 目标

让 review/eval boundary 的 `subject_type` 推导完全依赖 canonical scope projection / snapshot / manager results 信号，而不是简单依赖 `portfolio_plan` 是否非空。

### 风险

- payload 被裁剪或精简后，`portfolio_plan={}` 但 execution snapshot 仍然代表组合主语
- 若这里误判为 `single_manager`，后续 compatibility fields 会降级到 legacy 分支
- review/report 与 outcome snapshot 的主语语义可能再次分叉

### Owner 文件

- `src/invest_evolution/application/training/observability.py`
- `tests/test_training_review_protocol.py`

### 实施步骤

1. 在 review/eval boundary 组装 projection snapshot 时补齐 `manager_results` / `portfolio_plan` / `dominant_manager_id` 缺失字段。
2. 先走 `_project_manager_compatibility()`，再从 projection 结果读取 `subject_type`。
3. 只把 `portfolio_plan` 非空作为兜底，不再作为首要判据。
4. 增补“`portfolio_plan` 缺失但 snapshot 仍明确为组合主语”的回归测试。

### 验收标准

- `manager_portfolio` 不会因为 `portfolio_plan={}` 被误判成 `single_manager`
- `compatibility_fields.derived/source` 与最终 `subject_type` 对齐
- review/eval boundary 与 outcome execution boundary 的主语语义一致

### 验证命令

```bash
python3 -m pytest -q tests/test_training_review_protocol.py
```

### 回滚边界

- 只回滚 review/eval boundary 的 subject_type 推导
- 不回滚 execution 主链 projection 与 case store
- 回滚后必须确保单经理路径仍可正常构建 review input

## 优化 3：isolated experiment preset / discovery 收口

### 目标

消除 isolated experiment 的 preset 双份维护，并改进 regime discovery 采样策略，避免固定 30 天步进漏掉短暂 regime 窗口。

### 风险

- 新增 preset 时，核心模块有但 CLI 不可用
- 短暂 bear / oscillation 窗口会被 coarse sampling 直接跳过
- discovery 结果不足时，isolated strict-training 会被误判成“无可用 cutoff dates”

### Owner 文件

- `src/invest_evolution/application/training/isolated_experiments.py`
- `scripts/run_isolated_regime_manager_experiment.py`
- `tests/test_isolated_regime_manager_experiments.py`

### 实施步骤

1. 从核心 preset 注册表导出 `list_isolated_experiment_preset_names()`。
2. CLI `--preset` 直接复用注册表，不再手写 choices。
3. 把 discovery 从固定 coarse scan 扩展为 `coarse + dense` 的探测日程。
4. 在测试中覆盖 preset 导出与“短暂 regime 被 dense scan 命中”的场景。

### 验收标准

- CLI preset choices 与核心注册表完全同步
- `step_days=30` 时仍可发现 7 天级别的短暂 regime 窗口
- discovery 输出包含明确的 `discovery_strategy` 诊断字段

### 验证命令

```bash
python3 -m pytest -q tests/test_isolated_regime_manager_experiments.py
```

### 回滚边界

- 只回滚 isolated_experiments / CLI / 对应测试
- 不回滚 training scope、research feedback 与 runtime config
- 回滚后需恢复现有 preset 行为，不得影响非 isolated 主链

## 优化 4：research case store 读取与缓存优化

### 目标

在不改变外部行为的前提下，为 `list_cases()`、`list_attributions()` 和 `_iter_case_attribution_records()` 加入低风险缓存/失效机制，降低长期 strict/shadow 训练下的重复扫盘与 JSON 读取成本。

### 风险

- 缓存若直接暴露内部对象，调用方修改返回值会污染后续读取
- 仅处理 save 路径、不处理目录签名变化，会漏掉外部写入导致的缓存失效
- iter 级缓存若 key 设计不完整，会把不同过滤条件的结果串用

### Owner 文件

- `src/invest_evolution/investment/research/case_store.py`
- `tests/test_research_feedback_windowing.py`
- `tests/test_research_training_feedback.py`
- `tests/test_research_feedback_gate.py`

### 实施步骤

1. 为 case / attribution 目录建立基于文件列表、mtime、size 的签名缓存。
2. 当签名变化或 save 操作发生时，清空对应列表缓存与 iter record 缓存。
3. 为 `_iter_case_attribution_records()` 增加按过滤条件缓存的计算层。
4. 返回值统一做 defensive copy，保证调用方不能污染内部缓存。
5. 对缓存命中与失效行为补回归测试。

### 验收标准

- 重复 `list_cases()` / `list_attributions()` 不会重复读同一批 JSON
- 新写入 case / attribution 后，缓存会自动失效并返回新数据
- `_iter_case_attribution_records()` 在相同筛选条件下可命中缓存，不同条件不会串用

### 验证命令

```bash
python3 -m pytest -q tests/test_research_feedback_windowing.py
```

### 回滚边界

- 只回滚 case store 缓存层与相关测试
- 不回滚 research feedback gate 判定逻辑
- 回滚后仍需保持 case / attribution 的时序排序语义

## 最小验证闭环

完成以上 4 项后，至少执行以下验证：

```bash
python3 scripts/generate_runtime_contract_derivatives.py --check
uv run python -m invest_evolution.application.freeze_gate --mode quick
python3 -m pytest -q \
  tests/test_runtime_config_ref_semantics.py \
  tests/test_training_review_protocol.py \
  tests/test_isolated_regime_manager_experiments.py \
  tests/test_research_feedback_windowing.py
```

如 focused 套件通过，再视时间追加全量：

```bash
python3 -m pytest -q
```

## 完成判定

第二阶段优化可判定完成，必须同时满足：

- 4 项优化均有明确 owner 文件和回滚边界
- 每项至少有 1 条新增或更新的针对性测试
- focused 验证命令可稳定通过
- freeze gate 与 runtime contract derivative 检查不回退

---

## Live 隔离实验驱动的第二阶段实施清单

本节补充 2026-03-24 两组 live 隔离实验后的直接实施项：

- `defensive_low_vol @ bear`
- `mean_reversion @ oscillation`

前提事实：

- 两组 `realization_summary` 都满足 `drift_count = 0`
- 所有 cycle 都满足 `manager_match = true`
- 所有 cycle 都满足 `regime_match = true`

因此以下结论按“真实策略 / 工程瓶颈”处理，而不是实验隔离失真处理。

### 实施项 A：Cycle 内 runtime 调整延迟生效

#### 审查结论

- `apply_runtime_adjustments_boundary()` 在 cycle 锁定期间直接修改 `session_current_params`
- `finalize_cycle_runtime_window()` 随后又将这些变化识别为非法 mutation 并回滚
- 这会污染 review/finalize 对“本周期真实参数”的观察，并制造伪 warning

#### 实施动作

1. 在 cycle 锁定期间把常规 runtime 调整写入 deferred buffer
2. 在 cycle finalize 解锁后一次性落地 deferred adjustments
3. 在 runtime summary 中显式记录 deferred adjustment keys

#### 验收口径

- review 阶段不再被 future-cycle 参数污染
- `illegal runtime mutation` 不再由正常 optimization/review 动作触发
- 下一周期启动前 deferred adjustments 已进入 session params

### 实施项 B：`mean_reversion @ oscillation` 入场信号去掉 falling-knife 偏置

#### 审查结论

- live run: `avg_return=-1.0889%`，`avg_sharpe=-1.7706`，`benchmark_pass_rate=0.0`
- research gate 样本 `24` 已足够 active，不是样本缺失
- `T+5/T+10/T+20/T+60` 几乎整排失败，优先说明入口选股失真

#### 实施动作

1. 提高 oscillation 场景下的 `min_reversion_score`
2. 为超深跌幅、空头趋势但 RSI 不够极端的标的增加硬过滤
3. 将 oscillation 的持仓上限收缩到更保守水平

#### 验收口径

- oscillation 下不再默认接纳典型 falling knife
- signal packet 在 oscillation 下体现更低暴露、更高现金

### 实施项 C：`mean_reversion @ oscillation` 退出与风险覆盖收紧

#### 审查结论

- live run 中出现连续亏损、`single_stock_crash -> tighten_stop`、`all_positions_red`
- research gate recommendation 仍为 `tighten_risk`
- 说明退出与风险覆盖也需要同步收紧，而不仅是改入口

#### 实施动作

1. 下调 oscillation regime 下的 `stop_loss_pct`
2. 下调 oscillation regime 下的 `take_profit_pct` 与 `trailing_pct`
3. 让风险参数收紧与更高现金底线绑定生效

#### 验收口径

- oscillation 下输出的风险参数比 base runtime 更保守
- 不放松 strict gate 的前提下，风险信号应更一致

### 实施项 D：`defensive_low_vol @ bear` 的信号质量与基准对齐修复

#### 审查结论

- live run: `avg_return=+0.2814%`，但 `avg_sharpe=-0.3116`，`benchmark_pass_rate=0.125`
- research gate 样本 `89`，并持续给出 `tighten_risk`
- 这条线不是 regime 问题，而是 bear 中候选质量与风险一致性不足

#### 实施动作

1. 提高 bear regime 下的 `min_defensive_score`
2. 过滤 bear 下高波动、弱 20 日趋势、空头候选
3. 收缩 bear regime 下的持仓数、现金暴露与风控参数

#### 验收口径

- bear 下 defensive runtime 默认只保留更干净的防御候选
- signal packet 在 bear 下体现更高质量、低暴露输出
