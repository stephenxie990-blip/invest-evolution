---
id: momentum_trend
name: Momentum Trend Gene
enabled: true
priority: 80
description: Focus on strong trend continuation with volume confirmation.
---

# Momentum Trend Gene

Entry:
- MA5 > MA20 > MA60
- RSI in [45, 78]
- volume_ratio >= 1.5

Exit:
- hard_stop: 5%
- take_profit: 15%
- trailing_drawdown: 8%
