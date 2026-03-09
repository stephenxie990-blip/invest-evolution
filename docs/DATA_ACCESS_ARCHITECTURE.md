# 数据管理与调用接口架构

## 目标

在保持现有日频训练/回测主链路稳定的前提下，把项目的数据访问能力划分为清晰的四层：

1. 日频主链路
2. 日频增强层
3. 事件查询层
4. 日内执行层

## 分层原则

### 1. `TrainingDatasetBuilder`

职责：
- 输出训练/回测主数据
- 以 `daily_bar` 为骨架
- 点时拼接 `security_master`、`financial_snapshot`、`factor_snapshot`、`security_status_daily`
- 可选拼接 `capital_flow_daily`

适用：
- 日频选股
- 回测
- 模型训练

### 2. `CapitalFlowDatasetService`

职责：
- 提供 `capital_flow_daily` 的读取
- 支持把资金流按 `trade_date` 合并回日频 frame

适用：
- 资金流因子增强
- 资金面过滤

### 3. `EventDatasetService`

职责：
- 提供 `dragon_tiger_list` 事件查询
- 保持事件数据独立，不破坏主日频 frame

适用：
- 事件研究
- 标签构建
- 风险提示与选股过滤

### 4. `IntradayDatasetBuilder`

职责：
- 提供 `intraday_bar_60m` 读取能力
- 独立承载日内执行 / 择时数据

适用：
- 60分钟择时
- 执行层研究
- T+0 / 日内策略

## 统一入口

### `DataManager`

保留为统一高层入口，但不再强制把所有数据都塞进同一张日频表：

- `load_stock_data(...)`：日频主入口，可选 `include_capital_flow=True`
- `get_status_summary(...)`：统一状态
- `get_capital_flow_data(...)`：资金流查询
- `get_dragon_tiger_events(...)`：事件查询
- `get_intraday_60m_data(...)`：60分钟线查询
- `get_market_index_frame(...)`：指数数据

## 当前实施状态

已落地：
- `intraday_bar_60m` repository / ingestion / CLI / 高层读取入口
- `capital_flow_daily` repository / ingestion / 高层查询入口
- `dragon_tiger_list` repository / ingestion / 高层查询入口
- `DataManager` 已形成分层式访问方法

## 推荐调用方式

### 日频主链路

```python
from market_data import DataManager

manager = DataManager()
stocks = manager.load_stock_data("20250115", stock_count=100, min_history_days=250)
```

### 日频 + 资金流增强

```python
stocks = manager.load_stock_data(
    "20250115",
    stock_count=100,
    min_history_days=250,
    include_capital_flow=True,
)
```

### 龙虎榜事件查询

```python
events = manager.get_dragon_tiger_events(
    start_date="20240101",
    end_date="20251231",
)
```

### 60分钟线查询

```python
bars_60m = manager.get_intraday_60m_data(
    codes=["sh.600000"],
    start_date="20240101",
    end_date="20240131",
)
```

## 部署建议

1. 主程序继续以日频能力为默认路径
2. 资金流作为可选增强打开
3. 龙虎榜作为独立事件服务使用
4. 60分钟线只在执行层 / 日内研究里使用
5. 避免把事件表和日内表直接塞进默认训练 frame

## 后续建议

1. Web API 增加扩展查询端点
2. 为 `IntradayDatasetBuilder` 增加按单日 / 单股切片助手
3. 为 `CapitalFlowDatasetService` 增加常用资金流派生指标
4. 为 `EventDatasetService` 增加事件窗口标注工具
