# Findings

## 当前发现
- `DataManager / datasets` 当前是日频中心化设计，适合承载 `daily_bar + financial + factor + status + index`
- `capital_flow_daily` 与日频粒度一致，适合作为可选增强层，而不是默认主骨架
- `dragon_tiger_list` 是事件型稀疏表，更适合独立事件服务
- `intraday_bar_60m` 是日内执行层数据，更适合独立 builder，不应直接塞进默认日频 frame

## 本次实施
- 重组了 `market_data/datasets.py` 的高层服务分层
- 为 `DataManager` 增加了统一访问扩展接口
- 为 `load_stock_data()` 增加了 `include_capital_flow` 可选增强参数
- 输出了架构文档 `docs/DATA_ACCESS_ARCHITECTURE.md`
