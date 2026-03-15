# 模型优化修复蓝图与实施路径（2026-03-15）

## 1. 文档目标

本文不是新的大版本愿景文档，而是一份**面向下一阶段实际优化与修复工作的执行蓝图**。

目标只有一个：

> 把当前“已经能跑通的训练与模型切换系统”，推进为“审计完整、准入可信、候选可比较、结果可解释”的模型治理闭环。

本蓝图直接承接以下已确认事实：

- 当前系统主骨架成立，不需要推倒重写。
- 当前主要问题集中在：
  - 优化审计事件缺少 `cycle_id`
  - 路由准入只看样本数，不看性能底线
  - benchmark pass rate 偏低，freeze gate 长期不过
  - training / review / routing 三段治理标准尚未完全一致
- 当前系统更像**强研究平台**，还不是**高可信自动化模型演化系统**。

因此，接下来的优化修复应坚持“先修治理，再修选择，再修效果，最后扩能力”的顺序。

---

## 2. 下一阶段的核心目标

## 2.1 北极星目标

把系统从：

- “模型会训练、会切换、会变异”

推进到：

- “模型的每一次训练、评审、切换、变异都可追踪、可解释、可比较、可回滚”

## 2.2 四个阶段性结果

本轮优化修复完成后，至少要达成以下 4 个结果：

1. **审计闭环成立**  
   任意一条优化事件都能追溯到触发周期、触发原因、影响配置、后续结果。

2. **路由准入可信**  
   regime routing 只在“样本充足且质量达标”的模型集合中做选择。

3. **候选晋级纪律成立**  
   candidate config、runtime override、active config 三者严格区分，未经 promotion gate 不得静默接管。

4. **效果评估从“看收益”升级为“看治理后收益”**  
   优化不是只看是否有正收益，而是看是否同时改善 benchmark、Sharpe、回撤、research feedback gate。

---

## 3. 问题定义与根因拆解

## 3.1 问题一：优化链路不可完整回溯

### 当前表现

- `optimization_events.jsonl` 中的事件没有稳定的周期主键
- review、loss optimization、YAML mutation 之间无法稳定串联
- 训练结果和优化事件难以自动做因果归因

### 根因

- `OptimizationEvent` 结构不完整
- event factory 调用点没有统一注入 `cycle_id`
- 当前测试只验证 event stage，不验证 event lineage

### 影响

- 研究复盘成本高
- 自动生成优化报告困难
- promotion / freeze / rollback 的证据链不完整

## 3.2 问题二：模型路由准入过宽

### 当前表现

- 只要 `min_cycles` 和 `min_cycles_per_regime` 满足，就可能进入 routing 候选
- 即使 `score < 0`、`avg_return_pct < 0`、`benchmark_pass_rate = 0`，也可能成为某个 regime 的 leader

### 根因

- eligibility gate 只覆盖“样本量”
- allocator 的先验和 blending 逻辑没有质量底线保护
- reasoning 会复述“相对更优”，但它比较的是一组本身都不合格的候选

### 影响

- 路由解释失真
- 自动切换的可信度下降
- 模型治理基线不统一

## 3.3 问题三：训练闭环活跃，但模型晋级纪律偏弱

### 当前表现

- 系统会生成 mutation 候选、review 调参建议和 runtime overrides
- 但“生成候选”和“接管 active”之间的纪律尚不够硬
- 当前 promotion applied count 为 0，但 candidate pending 已累积，说明系统有候选积压问题

### 根因

- promotion gate 目前更多是记录和摘要，还不是强执行纪律中心
- active / candidate / override 三种状态虽然已经开始分离，但还未形成全链一致规则

### 影响

- 难以比较候选策略是否值得晋级
- 训练输出容易停留在“产生很多候选，但系统并未更稳定”

## 3.4 问题四：效果评估维度仍偏松

### 当前表现

- 最优模型已有正收益，但 benchmark pass rate 仍偏低
- final validation 中，`momentum` 仍是 best model，但 benchmark pass rate 为 0
- freeze gate 阻塞项主要在 `win_rate / avg_sharpe / benchmark_pass_rate / research_feedback_gate`

### 根因

- 当前系统能“区分模型”，但还没能“稳定筛出高质量模型”
- 训练优化链更偏“修亏损”，而不是“提升全面质量”

### 影响

- 结果能看，但不够稳
- 训练跑得动，不代表可以放心自动化升级模型

---

## 4. 优化修复的设计原则

下一阶段所有改动都应遵守以下原则：

## 原则 A：先修治理，再修收益

在没有稳定审计链和准入纪律之前，任何“提升收益”的改动都可能只是让系统更难解释。

## 原则 B：先收紧自动权力，再扩展自动能力

系统当前最需要的不是更多自动化动作，而是更明确的自动化边界。

## 原则 C：所有候选都必须可比较

如果一个 candidate config 无法回答“从哪来、和谁比、为何未晋级、是否提升”，它就不应进入长期积累面。

## 原则 D：同一条质量标准要贯穿 review、optimization、routing、promotion

不能出现：

- review 说不合格
- routing 仍把它作为候选
- promotion 又缺少明确淘汰规则

## 原则 E：效果优化以质量门槛为中心，不以单次正收益为中心

本轮目标不是把某一轮收益做高，而是让系统在滚动窗口中更可靠。

---

## 5. 蓝图总览

本轮优化修复分为 6 个工作流：

| 工作流 | 目标 | 优先级 |
| --- | --- | --- |
| A. 优化审计修复 | 建立完整 lineage | P0 |
| B. 路由准入收紧 | 让 allocator 只在合格候选中选择 | P0 |
| C. 候选晋级纪律 | 固化 active/candidate/override 规则 | P0 |
| D. 训练质量门矩阵 | 统一 review/optimization/routing/promotion 标准 | P1 |
| E. 效果提升回路 | 以 benchmark/Sharpe/回撤为目标调优 | P1 |
| F. Agent 证据化与测试加固 | 让判断更结构化、更可验证 | P2 |

---

## 6. 工作流 A：优化审计修复

## 6.1 目标

把优化事件从“时间序列日志”升级为“可追踪的周期级治理事件”。

## 6.2 主要改动

### 数据结构

- 为 `OptimizationEvent` 增加：
  - `cycle_id`
  - `model_name`
  - `config_name`
  - `source_stage`
  - `event_id`
  - `parent_event_id`（可选）

### 调用链

- 所有 event factory 调用点统一传入 `cycle_id`
- `review_decision`、`research_feedback`、`llm_analysis`、`evolution_engine`、`yaml_mutation`、`optimization_error` 的 event shape 统一

### 落盘与聚合

- `optimization_events.jsonl` 统一写完整 event schema
- `cycle_*.json` 中保留该周期的 event 摘要
- `training_run / evaluation` 中增加 optimization lineage summary

## 6.3 建议涉及文件

- `app/train.py`
- `app/training/optimization.py`
- `app/training/review_stage_services.py`
- `app/training/outcome_services.py`
- `app/training/promotion_services.py`
- `app/training/lineage_services.py`

## 6.4 必补测试

- `tests/test_training_optimization.py`
- 新增 `tests/test_optimization_event_lineage.py`
- 新增 `tests/test_cycle_artifact_optimization_audit.py`

## 6.5 验收标准

- 任意 event 都有 `cycle_id`
- 任意 `yaml_mutation` 都能追到来源周期
- 任意周期结果都能反查当轮 optimization timeline

---

## 7. 工作流 B：路由准入收紧

## 7.1 目标

让 allocator 从“有样本就可参与”升级为“样本够且质量过线才可参与”。

## 7.2 新增质量门

建议在 leaderboard eligibility 中引入两层 gate：

### Gate 1：样本门

- `min_cycles`
- `min_cycles_per_regime`

### Gate 2：质量门

- `min_score`
- `min_avg_return_pct`
- `min_benchmark_pass_rate`
- `min_avg_sharpe_ratio`

建议默认先启用以下保守门槛：

- `score > 0`
- `avg_return_pct >= 0`
- `benchmark_pass_rate >= 0.05`
- `avg_sharpe_ratio >= 0`

## 7.3 allocator 改动

- `_eligible_entries()` 同时尊重样本门和质量门
- 对某 regime 若无合格候选：
  - 返回“维持当前 active”
  - 或降级为 `unknown` regime 的均衡配置
- reasoning 不能再说“其历史表现更优”，除非它真的过了质量门

## 7.4 建议涉及文件

- `invest/leaderboard/engine.py`
- `invest/allocator/engine.py`
- `invest/router/engine.py`
- `config/*.yaml` 中的 routing / leaderboard policy

## 7.5 必补测试

- `tests/test_leaderboard.py`
- `tests/test_allocator.py`
- `tests/test_model_routing.py`
- `tests/test_web_model_routing_api.py`
- 新增 `tests/test_routing_quality_gate.py`

## 7.6 验收标准

- 负分模型不能进入 routing eligible 集合
- 零 benchmark pass 的模型不能被写成 regime leader，除非全体候选都不合格且系统明确走 provisional/fallback 路径
- routing explanation 与真实 gating 结果一致

---

## 8. 工作流 C：候选晋级纪律

## 8.1 目标

把 active config、candidate config、runtime overrides 三种状态彻底区分，并建立清晰晋级路径。

## 8.2 新规则

### Rule 1：生成候选不等于采纳候选

- `yaml_mutation` 只能生成 candidate
- 不能隐式改写 active config

### Rule 2：runtime override 只服务于单次或短期实验

- override 必须记录来源与过期边界
- 不得长期悬挂在 active 上伪装为模型本体能力

### Rule 3：candidate 晋级必须经过 promotion gate

promotion gate 至少比较：

- rolling win rate
- avg return
- avg Sharpe
- max drawdown
- benchmark pass rate
- research feedback gate
- realism sanity checks

### Rule 4：candidate 要有明确淘汰规则

若连续 N 个窗口未晋级，应：

- 归档
- 标记淘汰原因
- 不再参与 active drift 统计

## 8.3 建议涉及文件

- `app/training/experiment_protocol.py`
- `app/training/promotion_services.py`
- `app/training/lineage_services.py`
- `app/training/controller_services.py`
- `app/training/outcome_services.py`
- `app/lab/evaluation.py`

## 8.4 必补测试

- `tests/test_training_promotion_lineage.py`
- `tests/test_lab_artifacts.py`
- 新增 `tests/test_candidate_promotion_discipline.py`

## 8.5 验收标准

- 任意时刻能回答：
  - 当前 active 是谁
  - 当前 candidate 是谁
  - override 来源是什么
  - candidate 为什么没晋级

---

## 9. 工作流 D：训练质量门矩阵

## 9.1 目标

把 training、review、optimization、routing、promotion 的质量判断统一为一套 matrix，而不是五套松散口径。

## 9.2 建议建立统一质量矩阵

| 指标 | Review | Optimization | Routing | Promotion | Freeze |
| --- | --- | --- | --- | --- | --- |
| win_rate | 观察 | 触发修复 | 参考 | 必需 | 必需 |
| avg_return_pct | 观察 | 触发修复 | 必需 | 必需 | 必需 |
| avg_sharpe_ratio | 观察 | 触发修复 | 必需 | 必需 | 必需 |
| max_drawdown | 观察 | 触发修复 | 必需 | 必需 | 必需 |
| benchmark_pass_rate | 观察 | 必需 | 必需 | 必需 | 必需 |
| research_feedback_gate | 观察 | 必需 | 可选 | 必需 | 必需 |
| realism_checks | 观察 | 可选 | 可选 | 必需 | 可选 |

## 9.3 建议动作

- 将当前分散在多个 service 中的阈值统一映射到一份 policy
- 在 artifact 中显式写出每一轮各 gate 的 pass/fail 原因
- 让 routing / promotion / freeze 读取同一份标准，而不是各自硬编码阈值

## 9.4 验收标准

- 同一模型在 review、routing、promotion 上不会出现互相冲突的结论
- 任意一次 gate 失败，都能说明失败在哪些指标

---

## 10. 工作流 E：效果提升回路

## 10.1 目标

在治理基线修复完成后，才开始做真正的效果优化。

## 10.2 优化方向

### 方向 A：benchmark pass 优先

当前最佳模型收益为正，但 benchmark pass rate 仍低。这说明系统可能“会赚钱”，但不够稳定或不够符合设定目标。下一轮优化不应优先追求更高收益，而应优先提高：

- benchmark pass rate
- avg Sharpe
- 胜率稳定性

### 方向 B：regime 分桶评估更严格

对 `bull / bear / oscillation` 分别统计：

- 样本数
- 胜率
- avg return
- Sharpe
- drawdown
- benchmark pass rate

不要让某个模型只凭少量 regime 偶然表现就保住长期准入。

### 方向 C：优化触发从“连续亏损”拓展为“质量退化”

建议新增第二类触发器：

- 连续 benchmark fail
- rolling Sharpe 持续为负
- research feedback gate 连续未通过

### 方向 D：现实性指标进入优化 loop

当前训练结果里已有 realism metrics。建议把以下指标纳入候选晋级判断：

- 平均单笔交易金额是否异常
- 平均持仓天数是否偏离策略预期
- turnover / concentration 是否异常

## 10.3 验收标准

- 新一轮 20-cycle rerun 中：
  - benchmark pass rate 明显高于当前基线
  - 负分模型不再进入 regime routing 候选
  - promotion candidate 的堆积显著下降

---

## 11. 工作流 F：Agent 证据化与测试加固

## 11.1 目标

让 Agent 的判断不只停留在 reasoning 文本，而能沉淀结构化证据。

## 11.2 优化点

- MarketRegime 结果输出市场证据摘要
- ReviewDecision 输出建议来源指标
- EvoJudge 输出参数调整依据
- Routing decision 输出实际 gating 命中详情

## 11.3 建议测试

- `tests/test_agent_roster.py`
- `tests/test_review_meeting_v2.py`
- `tests/test_agent_observability_contract.py`
- 新增 `tests/test_routing_reasoning_alignment.py`

## 11.4 验收标准

- 关键 Agent 的结论可以被结构化字段解释
- Web / commander / training artifact 中可见一致摘要

---

## 12. 分阶段实施路径

## Phase 0：冻结基线与指标面

### 目标

在开始修复前冻结基线，避免“边修边漂”。

### 动作

1. 固定基线输出目录与对比指标
2. 记录当前 leaderboard、freeze gate、promotion stats、realism summary
3. 固定本轮回归测试集合

### 输出

- baseline metrics snapshot
- regression suite list

## Phase 1：先修 P0 治理

### 目标

完成工作流 A、B、C。

### 产出

- 审计完整
- 路由准入收紧
- candidate 晋级纪律建立

### 预计顺序

1. 修 `OptimizationEvent`
2. 修 leaderboard / allocator quality gate
3. 修 promotion / lineage / candidate discipline

## Phase 2：统一质量门矩阵

### 目标

完成工作流 D。

### 产出

- review、optimization、routing、promotion、freeze 共用质量标准

## Phase 3：效果优化

### 目标

完成工作流 E。

### 产出

- 新的 rerun 输出
- benchmark / Sharpe / gate 指标对比

## Phase 4：证据化与展示收尾

### 目标

完成工作流 F。

### 产出

- 更可信的 reasoning
- Web / runtime / artifacts 的统一治理摘要

---

## 13. 推荐执行顺序与人日估算

| 阶段 | 内容 | 预估工作量 |
| --- | --- | --- |
| Phase 0 | 冻结基线、补快照、确认回归集 | 0.5 - 1 天 |
| Phase 1 | 审计修复 + routing gate + candidate discipline | 2 - 4 天 |
| Phase 2 | 统一质量矩阵与 gate 对齐 | 1 - 2 天 |
| Phase 3 | rerun、分析、参数/触发器调优 | 2 - 4 天 |
| Phase 4 | Agent 证据化、展示与文档收口 | 1 - 2 天 |

总计建议：**6.5 - 13 天**

如果要压缩为最小可交付版本，建议先只做：

1. `OptimizationEvent` 修复
2. routing quality gate
3. candidate promotion discipline
4. rerun 验证

这四项能最快提升系统可信度。

---

## 14. 第一批建议立即执行的改动

如果下一步立刻进入代码修改，我建议按下面顺序开始：

1. `OptimizationEvent` 增加 `cycle_id`，补事件 lineage 测试
2. `leaderboard` 增加质量门，`allocator` 改为只使用合格候选
3. `routing reasoning` 增加 gating explain，避免“负分也更优”的表述
4. `promotion_services` 增加 candidate 淘汰规则
5. 重新跑一次 focused tests
6. 重新跑一次 20-cycle 训练验证

---

## 15. 最终判断

当前阶段最优策略不是继续扩模型种类，也不是增加更多 Agent，而是把**模型治理闭环**做硬。

这轮优化修复真正要解决的不是“模型不够聪明”，而是以下问题：

- 模型是否可信
- 模型是否可追踪
- 模型是否只在合格范围内被切换
- 模型候选是否有明确晋级与淘汰规则

只要这条线做好，后续无论是继续提升 `momentum`、修复 `mean_reversion`，还是引入新模型，系统都会更稳，也更值得持续投入。
