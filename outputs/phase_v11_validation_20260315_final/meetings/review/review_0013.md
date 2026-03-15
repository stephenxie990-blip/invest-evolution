# 复盘会议 (Cycle #13)

**时间**: 2026-03-15 01:48

## 近期表现
- 总轮数: 1
- 胜率: 100%
- 平均收益: +6.67%

## 决策
- 下调置信度并收紧触发阈值
- 问股校准显示概率偏乐观，先收紧触发条件与风险暴露
- 基于 ask 侧归因样本给训练侧的建议：recalibrate_probability

### 参数调整
- stop_loss_pct: 0.03
- position_size: 0.15

### Agent 权重调整
- trend_hunter: 0.95 ↓
- contrarian: 0.67 ↓
- defensive_agent: 0.83 ↓

**最终执行摘要**: 最终执行参数：stop_loss_pct=3%，position_size=15%；最终执行权重：trend_hunter=0.95，contrarian=0.67，defensive_agent=0.83

**理由**: 复盘综合: 方向=conservative