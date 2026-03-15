# 复盘会议 (Cycle #2)

**时间**: 2026-03-15 13:25

## 近期表现
- 总轮数: 1
- 胜率: 100%
- 平均收益: +5.15%

## 决策
- 优先落实tighten_risk，缩短持有期并收紧止盈保护
- 在样本不足且算法方案表现落后的情况下，降低自动化方案使用强度，优先保留高置信度机会
- 按regime、plan_source、agent、持有天数做分层复盘，确认失效环节后再扩大调整幅度

### 参数调整
- position_size: 0.06
- take_profit_pct: 0.08
- stop_loss_pct: 0.035
- cash_reserve: 0.3
- trailing_pct: 0.06

### Agent 权重调整
- trend_hunter: 0.90 ↓
- quality_agent: 0.95 ↓

**最终执行摘要**: 最终执行参数：position_size=6%，take_profit_pct=8%，stop_loss_pct=4%，cash_reserve=30%，trailing_pct=6%；最终执行权重：trend_hunter=0.90，quality_agent=0.95

**理由**: 当前仅1轮交易且校准样本4个，100%胜率不具统计显著性，应采取保守收缩而非放大风险。校准反馈明确为tighten_risk，且T+20失效偏高、方向校准较弱，因此将max_hold_days从30下调至15，并收紧take_profit_pct与trailing_pct以更早锁定收益。结合进化裁判的保守方向，position_size下调至0.06，并提高cash_reserve与signal_threshold以减少低置信度暴露。Agent层面，trend_hunter和quality_agent准确率分别为40%和50%，均缺乏足够优势，宜小幅降权而非大幅调整。整体建议以风险收缩、分层复盘、延后大改为主。