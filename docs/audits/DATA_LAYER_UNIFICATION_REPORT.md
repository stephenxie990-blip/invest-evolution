# 数据层统一报告

## 1. 报告结论

数据层统一工作已经从“多入口、多口径、局部脚本化”的状态，收敛到当前的单一主链：

- **单库**：`data/stock_history.db`
- **单 repository**：`MarketDataRepository`
- **单同步层**：`DataIngestionService`
- **单读侧层**：`datasets.py`
- **单训练入口**：`DataManager`

## 2. 具体落地表现

### 2.1 训练链路

训练入口不再自己散拼数据，而是通过：

- `DataManager.check_training_readiness()`
- `DataManager.load_stock_data()`

完成诊断与读取。

### 2.2 Web 链路

Web 数据接口统一改走：

- `WebDatasetService`

### 2.3 数据健康检查

已具备：

- 健康状态
- 最新日期
- 股票数 / K 线数
- 缺失项提示
- 元数据摘要

### 2.4 数据维度扩展

已纳入 canonical 体系的附加数据包括：

- financial snapshots
- capital flow
- dragon tiger events
- intraday 60m

## 3. 对当前系统的影响

统一后的直接收益是：

- 训练与 Web 看到的是同一套离线库
- readiness 诊断可直接服务训练前检查
- 新数据能力不会再变成单独脚本孤岛
- 后续做 allocator / leaderboard / lab 对比时，底层数据口径更稳定

## 4. 仍需注意的点

- 统一已完成，但“演进机制”还不算完整
- SQLite 适合当前单机模式，不代表天然适合多实例共享
- 历史辅助脚本仍需与主链区分对待
