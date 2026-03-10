# 数据层统一计划（当前状态）

## 1. 状态结论

截至当前实现，数据层统一的核心计划已经完成，本文档现作为**收尾说明 + 剩余 backlog** 使用。

## 2. 已完成项

### 2.1 canonical schema 收口

已统一到 `MarketDataRepository` 管理的 SQLite schema，覆盖：

- 主数据
- 日线
- 指数
- 财务
- 交易日历
- 状态
- 因子
- 资金流
- 龙虎榜
- 60 分钟线

### 2.2 read-side builder 收口

当前已具备：

- 训练用读取构造器
- Web 状态与查询构造器
- 资金流 / 事件 / 盘中读取构造器
- T0 读取构造器

### 2.3 兼容 façade 收口

`DataManager` 已成为训练主链统一入口，负责：

- readiness 诊断
- stock data 加载
- 离线优先 / 在线兜底 / mock 兜底

### 2.4 Web API 收口

当前 `/api/data/*` 已使用统一 WebDatasetService，而不是散乱的历史读法。

### 2.5 质量审计收口

`DataQualityService` 已完成结构化健康检查，支持：

- summary
- checks
- issues
- meta snapshot

## 3. 已通过的实现性验证

从当前代码与测试覆盖看，统一计划至少已经在以下方面落地：

- 数据层结构守卫
- canonical repository 读写
- 训练路径可用
- Web 数据 API 可用
- 配置覆盖与数据状态摘要可用

## 4. 剩余 backlog

### 4.1 更细粒度的 schema 演进机制

当前仍由代码初始化 schema，后续可考虑：

- 更明确的 schema version
- 升级脚本
- 向后兼容说明

### 4.2 更多 point-in-time 因子

训练 builder 当前已支持财务、状态、因子、资金流拼接，后续可继续扩充更丰富的横截面特征。

### 4.3 更明确的数据保鲜策略

后续可在状态摘要中增加：

- 按表粒度的 freshness
- source freshness SLA
- 最近同步失败信息

## 5. 当前执行建议

后续新增任何数据能力，都遵循：

1. 先扩 `repository.py` schema / query
2. 再扩 `ingestion.py` 写路径
3. 再扩 `datasets.py` 的读侧 builder
4. 最后由 `DataManager` 或 Web API 暴露
