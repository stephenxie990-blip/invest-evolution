# 复盘会议 (Cycle #6)

**时间**: 2026-03-15 01:23

## 近期表现
- 总轮数: 1
- 胜率: 0%
- 平均收益: -2.58%

## 决策
- 收紧选股标准
- 先收紧入场条件与风险暴露
- 按市场状态拆分止损和仓位阈值
- 问股校准显示近期命中偏弱，先收紧止损与仓位
- 基于 ask 侧归因样本给训练侧的建议：tighten_risk

### 参数调整
- stop_loss_pct: 0.03
- position_size: 0.15

### Agent 权重调整
- trend_hunter: 0.50 ↓
- contrarian: 0.86 ↓
- defensive_agent: 1.00 →

**最终执行摘要**: 最终执行参数：stop_loss_pct=3%，position_size=15%；最终执行权重：trend_hunter=0.50，contrarian=0.86，defensive_agent=1.00

**理由**: 复盘综合: 方向=conservative