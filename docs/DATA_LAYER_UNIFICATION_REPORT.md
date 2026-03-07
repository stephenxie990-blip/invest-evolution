# 数据层统一重构归档报告

日期：2026-03-07

## 结论

数据层双轨设计已收口为一套 canonical schema，训练、Web、T0 三条读取链路已统一到同一仓储层；旧双表读取/写入逻辑已从业务实现中移除，只保留极薄 façade 名称用于外部调用稳定。

## 已完成范围

### Phase 0：基线锁定
- 增加了数据层行为测试，覆盖 legacy → canonical 迁移、`DataManager.load_stock_data()`、`T0DataLoader.load_data_at_t0()`、Web `/api/data/*`
- 明确字段映射：
  - `stock_info` → `security_master`
  - `daily_kline` / `stock_daily` → `daily_bar`
  - `financial_data` → `financial_snapshot`
  - `metadata` → `ingestion_meta`

### Phase 1：统一仓储层
- 新增 `market_data/repository.py`
- 建立 canonical schema：
  - `security_master`
  - `daily_bar`
  - `financial_snapshot`
  - `ingestion_meta`
- 新增旧表导入和旧表清理能力

### Phase 2：统一写入入口
- 新增 `market_data/ingestion.py`
- `DataCache.download_stock_info()` → `DataIngestionService.sync_security_master()`
- `DataCache.download_daily_kline()` → `DataIngestionService.sync_daily_bars()`
- `DataDownloader.download_all()` → `DataIngestionService.sync_daily_bars_from_tushare()`
- Web `/api/data/download` 已切换到统一写入服务

### Phase 3：统一读取入口
- 新增 `market_data/datasets.py`
- `OfflineDataLoader` 已退化为 `TrainingDatasetBuilder` façade
- `DataManager` 读取链路统一走 canonical schema
- Web `/api/data/status` 已切换到 `WebDatasetService`

### Phase 4：迁移 T0 研究链路
- `HistoricalStockPool` / `T0DataLoader` 已统一走 `T0DatasetBuilder`
- T0 股票池基于 `security_master.list_date` / `delist_date`
- 幸存者偏差修正逻辑集中到 canonical 读取层

### Phase 5：删除旧双轨逻辑
- 业务代码已不再直接读写 `stock_daily` / `daily_kline` / `stock_info` / `metadata`
- `market_data/manager.py` 已收敛为干净主入口，仅保留 `DataManager` / `MockDataProvider` / `EvolutionDataLoader`
- 旧类 façade 与 legacy 表清理辅助代码已删除
- 项目运行时只认 canonical schema

## 新的模块边界

- `market_data/repository.py`：唯一数据仓储与 schema 管理者
- `market_data/ingestion.py`：唯一写入入口
- `market_data/datasets.py`：训练/T0/Web 三套读取构造器
- `market_data/quality.py`：结构化健康巡检
- `market_data/manager.py`：向外暴露兼容 façade、mock 数据和在线兜底

## 保留与删除原则

### 已删除的内容
- 旧双表的业务读写实现
- Web 和训练分别使用不同表模型的路径
- T0 依赖在线股票池即时构造的分散逻辑

### 已彻底删除的旧名称
- `DataCache`
- `OfflineDataLoader`
- `DataDownloader`
- `HistoricalStockPool`
- `T0DataLoader`

对应调用点已切换到 `DataIngestionService`、`TrainingDatasetBuilder`、`T0DatasetBuilder`。

## Agent 参与边界

- 不参与：数据下载、标准化、落库、cutoff 裁切、T0 规则执行
- 可参与：`DataQualityService` 输出后的解释、诊断和运维问答

## 迁移后的操作建议

1. 首次同步 canonical 数据：`python -m market_data --source baostock --start 20180101`
2. 确认状态：访问 Web `/api/data/status` 或调用 `DataQualityService.audit()`
3. 旧表已执行物理清理；后续仅维护 canonical schema
