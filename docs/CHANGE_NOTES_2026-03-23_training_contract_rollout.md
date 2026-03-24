# Change Note: Training Contract Rollout (2026-03-23)

本次 note 对应两次连续提交：

- `18efe23` `training: tighten review digest and progress contracts`
- `a8d0036` `tests: stabilize training lab artifact assertion`

## 这次变了什么

- review digest 边界继续收紧：
  - `manager_review_report` 不再从 legacy full report 回退投影
  - review-progress SSE 改为发射 compact、contract-backed 的 decision payload，而不是透传宽松 dict
- observability / tests 同步到新的摘要合同：
  - 更新了 review-progress 相关 contract 测试与多经理迁移边界测试
- 清掉了一个全仓验证 blocker：
  - `tests/test_web_training_lab_api.py` 里的训练实验室 artifact 路径断言改成稳定、显式的 expected path，避免跨作用域变量引用带来的脆弱断言

## 为什么重要

- 训练链的 review、observability 和 persistence 边界进一步统一到“严格摘要合同”，减少 payload 漂移回裸 `dict[str, Any]`
- Web / SSE 读侧看到的是更可信、可预期的 compact payload，前后端和测试都更容易维持稳定
- 仓库验证重新回到全绿，后续继续推进 docs / architecture 对齐时不再被测试噪音阻塞

## 验证

以下命令在本轮 rollout 完成后通过：

- `uv run pyright`
- `uv run ruff check src tests`
- `uv run pytest -q`
