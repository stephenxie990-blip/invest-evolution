# Contributing / 贡献指南

感谢你关注 `Invest Evolution / 投资进化系统`。

这个项目当前处于一个很明确的阶段：

- 它已经不是原型脚本集合
- 但它也还不是生产级自动交易系统
- 当前最重要的目标是：**把 Agent-first 协作、训练闭环和治理边界做得更可信**

所以，我们最欢迎的贡献，不是盲目扩功能，而是帮助系统变得更清楚、更稳定、更可验证。

## What We Welcome / 当前欢迎的贡献

- 修复与补强训练、治理、路由、晋级、冻结相关逻辑
- 补充 focused tests、契约测试、回归测试
- 收紧类型边界、减少隐式 `dict` 传递
- 完善文档、README、架构说明、运行手册
- 改善 GitHub 开源体验与社区协作材料

## What We Are Not Looking For First / 当前不优先的贡献

- 直接面向实盘托管资金的自动化执行能力
- 不受治理约束的“更强自治”
- 只增加角色数量、但不提升证据链和质量门的 Agent 扩展
- 没有验证支持的大规模重构

## Contribution Principles / 贡献原则

1. **Keep the mainline trustworthy**  
   优先保证主链可信，而不是堆叠新能力。

2. **Small, reviewable changes**  
   尽量提交小而清晰、便于评审的改动。

3. **Code + tests + docs move together**  
   涉及行为变化时，优先同时补测试与文档。

4. **Respect governance semantics**  
   不要随意破坏 `promotion`、`lineage`、`deployment_stage`、`quality_gate_matrix` 等治理语义。

## Suggested Workflow / 建议协作流程

1. 先阅读：
   - `README.md`
   - `docs/README.md`
   - `docs/audits/PROJECT_INTERPRETATION_REPORT_20260315.md`
   - `docs/MAIN_FLOW.md`
2. 明确本次改动影响的是：
   - 训练协议
   - 治理逻辑
   - 数据底座
   - Agent 协作
   - 文档 / GitHub 入口
3. 先做 focused validation，再考虑全量验证。

## Validation / 验证建议

在提交重要改动前，优先运行与你修改范围最接近的测试。

常见验证包括：

```bash
pytest -q
```

如果你修改的是治理 / 训练协议 / allocator / routing，建议优先看这些测试附近的模式：

- `tests/test_training_experiment_protocol.py`
- `tests/test_training_optimization.py`
- `tests/test_training_promotion_lineage.py`
- `tests/test_model_governance.py`
- `tests/test_allocator.py`
- `tests/test_leaderboard.py`
- `tests/test_model_routing.py`

## Docs Matter / 文档同样重要

对于这个项目来说，文档不是装饰。

如果你的改动改变了以下任一项，请同步更新文档：

- 系统定位
- 正式入口
- 训练协议
- Agent 角色边界
- 治理规则
- GitHub 对外表述

## Final Note / 最后说明

这个项目当前最重要的不是“看起来更炫”，而是“变得更可信”。

如果你的改动让系统：

- 更可解释
- 更可验证
- 更可治理
- 更容易被外部人理解和参与

那就是非常有价值的贡献。
