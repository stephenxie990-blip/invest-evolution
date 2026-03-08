# Findings

## 2026-03-08 实际操作观察
- 已使用真实浏览器自动化按用户路径操作：打开首页 -> 进入“训练追踪” -> 勾选 Mock -> 点击“开始训练” -> 切到“工作台”观察时间线。
- 页面存在持续连接（SSE），自动化不能使用 `networkidle`，需要用 `load`/`domcontentloaded`。

## 复现到的真实问题
1. `MarketRegime.reason()` 返回 dict，但前端追踪事件按字符串切片，导致历史错误 `slice(None, 200, None)`。
2. 训练在 `no_data` 提前结束时，没有明确终态事件，前端会出现“上面仍在运行、下面却已出结果”的状态错位。
3. 前端把 `status=no_data` 直接当正常训练结果汇总，导致 `+0.00%` / `0 笔交易` 的误导性摘要。
4. Mock 模式默认历史窗口与项目配置 `min_history_days=1095` 不匹配，导致 Web 演示下经常直接 `no_data`。
5. Mock 模式只把主 `llm_caller` 设为 `dry_run`，但各 Agent 仍可能实际调用 LLM，不符合“Mock 演示”预期。
6. 模拟交易阶段追踪日志引用了不存在的 `trader.capital` 属性，导致训练在进入模拟交易时异常。
7. 周期结果写 JSON 时，`numpy.bool_` 落入 `audit_tags` / `optimization_events`，导致持久化失败。

## 本轮修复后观察
- 训练追踪界面现在能正确区分三类终态：完成 / 跳过 / 失败。
- 工作台时间线现在能看到 `cycle_start`、各 Agent 进度、`cycle_skipped` 或 `cycle_complete` 的真实闭环。
- Mock 训练已经能稳定跑通完整一轮，不再默认早早 `no_data`。
- 真实浏览器自动化最终观察到一轮成功完成：
  - `cycle_complete` 已出现
  - 结果摘要显示 `请求轮次=1 / 有效完成=1 / 跳过=0 / 失败=0`
  - 工作台状态回到“空闲”
  - 示例收益率：`+4.25%`
