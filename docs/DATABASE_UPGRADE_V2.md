# 数据库升级方案 V2

更新时间：2026-03-08

## 1. 背景

当前系统已经具备较完整的 A 股个股日线历史，但数据库仍偏向“价格行情仓库”，尚未形成真正支持训练、回测、风控、复盘和横截面研究的一体化研究数据库。

### 当前实测状态
- 股票主数据：5510 只
- 个股日线：9237155 行
- 财务快照：0 行
- 指数日线：本次升级前未统一入库
- 行业映射：`security_master.industry` 已广泛覆盖，但运行时行业判断仍大量依赖 `data/industry_map.json`

### 当前主要短板
- 个股行情是主干，但指数/基准没有统一入库
- 财务表已建但未接入读侧与训练链路
- 行业与板块信息没有形成统一单一事实源
- 质量审计只检查“有无数据”，缺少覆盖率、缺失率、时效性和训练可用性检查
- 没有派生特征层，导致训练和回测重复现算

## 2. 升级目标

把现有数据层从“个股日线存储”升级为“研究型数据库”，满足以下目标：

- 支撑价格驱动、价值/质量驱动、行业/风格驱动三类策略
- 支撑训练、评估、风控、复盘对同一份数据口径复用
- 支撑离线复现，不依赖运行时临时联网抓取关键基准数据
- 支撑后续因子快照、市场状态识别和横截面研究扩展

## 3. 目标分层

### L1 原始事实层
- `security_master`：股票基础信息
- `daily_bar`：个股日线行情
- `index_bar`：指数/基准日线行情
- `financial_snapshot`：财务与市值快照
- `trading_calendar`：交易日历
- `sector_membership`：行业/概念/指数成分归属
- `security_status_daily`：ST、停牌、涨跌停、新股窗口等状态

### L2 派生特征层
- `factor_snapshot`：技术因子、相对强弱、波动率、量能等
- `market_regime_daily`：市场状态标签
- `benchmark_snapshot`：基准收益、超额收益对照缓存

### L3 训练与研究层
- `training_universe_snapshot`：某日训练可用股票池快照
- `cycle_feature_audit`：每轮训练用到的因子覆盖和缺失情况
- `strategy_signal_log`：策略信号与候选记录
- `portfolio_daily`：组合日度净值与风险暴露

## 4. 表设计优先级

## P0：立即落地

### 4.1 `index_bar`
用途：统一大盘、基准和市场状态输入。

建议字段：
- `index_code` TEXT
- `trade_date` TEXT
- `open` REAL
- `high` REAL
- `low` REAL
- `close` REAL
- `volume` REAL
- `amount` REAL
- `pct_chg` REAL
- `source` TEXT
- `updated_at` TEXT

首批指数：
- `sh.000001` 上证指数
- `sz.399001` 深证成指
- `sz.399006` 创业板指
- `sh.000300` 沪深300
- 后续可扩到 `sh.000905`、`sh.000852`

### 4.2 财务快照读侧接入
目标：让 `financial_snapshot` 从“只写不读”变成训练和选股可用。

建议新增能力：
- 读取某股票在 `cutoff_date` 前最近已披露财务
- 批量读取股票池财务快照
- 支持按披露日对齐，而不是按报告期直接对齐

### 4.3 行业统一事实源
目标：消除数据库行业信息和 `industry_map.json` 的双轨漂移。

建议策略：
- 默认以 `security_master.industry` 为主
- `industry_map.json` 仅作为人工覆盖补丁
- 对运行时行业查询增加缓存层

### 4.4 数据健康审计增强
在现有 `DataQualityService` 基础上增加：
- 个股/指数覆盖范围
- 财务覆盖股票数
- 历史长度达标股票数
- 近 20/60/250 日数据缺失率
- 异常价量检查

## P1：训练链路接入

### 4.5 `financial_snapshot` 真正进入训练集
在 `TrainingDatasetBuilder` 中，对股票池拼接：
- 最新 ROE
- 净利润
- 营收
- 总资产
- 最新市值

### 4.6 `trading_calendar`
解决交易日窗口、回看长度和未来窗口计算不精确的问题。

建议字段：
- `trade_date`
- `market`
- `is_open`
- `prev_trade_date`
- `next_trade_date`

### 4.7 `security_status_daily`
目标：提升可交易性过滤。

建议字段：
- `code`
- `trade_date`
- `is_st`
- `is_suspended`
- `is_limit_up`
- `is_limit_down`
- `is_new_stock_window`

## P2：研究效率层

### 4.8 `factor_snapshot`
建议首批字段：
- `ma5` `ma10` `ma20` `ma60`
- `momentum20` `momentum60`
- `volatility20`
- `volume_ratio`
- `turnover_mean20`
- `drawdown60`
- `relative_strength_hs300`
- `breakout20`
- `industry_rank`

### 4.9 `sector_membership`
建议结构：
- `code`
- `sector_type`（industry/concept/index/style）
- `sector_name`
- `effective_from`
- `effective_to`
- `source`

## 5. 与现有代码的对齐改造点

### 数据层
- `market_data/repository.py`
  - 新增 `index_bar` / 相关查询接口
  - 后续新增财务查询、交易日历查询、状态查询
- `market_data/ingestion.py`
  - 新增指数同步
  - 后续新增交易日历、股票状态同步
- `market_data/quality.py`
  - 新增指数和财务覆盖检查

### 训练/回测层
- `market_data/datasets.py`
  - 后续拼接财务快照、指数特征、交易日历
- `market_data/manager.py`
  - 训练就绪诊断增加指数/财务准备度
- `invest/trading/risk.py`
  - 优先使用统一行业数据源
  - 市场状态改为读本地指数库，而非运行时抓取
- `invest/evaluation/freeze.py`
  - 基准数据改为本地 `index_bar`

### 配置与 Web
- `app/web_server.py`
  - 数据下载接口纳入指数同步
- `/api/data/status`
  - 暴露指数覆盖和财务覆盖状态

## 6. 推荐实施顺序

### 第 1 阶段：打通基准数据闭环
- 建 `index_bar`
- 实现同步、状态查询、质量检查
- Web 下载链路加入指数同步

### 第 2 阶段：打通财务闭环
- 增加财务查询接口
- 训练集拼接财务快照
- 选股和风控接入 ROE / 市值

### 第 3 阶段：统一行业与板块
- 行业查询改成数据库优先
- 新增 `sector_membership`
- 让行业中性化和分析报告共用同一数据源

### 第 4 阶段：增加交易状态与日历
- 新增 `trading_calendar`
- 新增 `security_status_daily`
- 提升交易可执行性约束

### 第 5 阶段：建设特征层
- 新增 `factor_snapshot`
- 训练、回测、复盘统一复用同一份因子快照

## 7. 本轮已启动的改造

已落地：
- 新增 `index_bar` 表结构与仓储接口
- 新增 `sync_index_bars()`
- Web 后台下载链路加入指数同步
- 数据质量输出加入指数覆盖状态
- CLI `python -m market_data` 在 `baostock` 模式下同步指数

未落地但优先级高：
- 财务读侧
- 数据库优先的行业统一事实源
- 交易日历与状态表

## 8. 验收标准

### P0 验收
- 本地数据库存在主要指数历史
- `/api/data/status` 能返回指数状态
- Web 一键下载能同步个股 + 指数
- 质量审计能反映指数数据是否存在

### P1 验收
- 对任意 `cutoff_date` 可取到股票池的最近财务快照
- 价值/质量策略不再只依赖价格代理变量
- 市值中性化能基于数据库真实值运行

## 9. 风险与注意事项
- Tushare 财务同步速度较慢，建议支持增量与断点续跑
- 行业字段需要统一口径，避免 Baostock 行业分类与手工映射冲突
- 指数代码口径需统一为本地格式（如 `sh.000300`）
- 交易状态类数据需要明确来源和刷新频率
