# 投资进化系统 v1.0 全量审查报告

审查日期：2026-03-10

## 1. 结论摘要

### 1.1 总体判断
这个项目已经不是“原型堆代码”，而是一个经过明显重构收口的量化训练平台：
- 入口层已经收口到 `app/`
- 运行时能力收口到 `brain/`
- 数据访问收口到 `market_data/`
- 业务协同和策略逻辑收口到 `invest/`

整体方向是正确的，代码里能看到明确的分层意识、配置治理、审计落盘、训练实验产物沉淀，以及较强的测试约束。

### 1.2 当前主要风险
当前项目的主要问题不是主链路完全失效，而是以下四类“重构后遗症”：
1. **兼容壳不是真正别名**：根目录包装模块只适合启动，不适合模块级兼容。
2. **接口契约漂移**：训练控制器与数据管理器之间的局部调用契约发生变化，影响测试与扩展。
3. **产品层回退**：前端训练中心从产品化状态退回了较简化版本。
4. **残留半废弃代码**：进化分析层仍暴露未落地的 mock LLM 实现，增加认知负担。

---

## 2. 架构审查

### 2.1 架构优点
1. **分层清晰**
   - 顶层应用入口位于 `/Users/zhangsan/Desktop/投资进化系统v1.0/app/commander.py:551`
   - Web API 真实实现位于 `/Users/zhangsan/Desktop/投资进化系统v1.0/app/web_server.py:41`
   - 训练主控位于 `/Users/zhangsan/Desktop/投资进化系统v1.0/app/train.py:222`
   - 数据 canonical 仓储位于 `/Users/zhangsan/Desktop/投资进化系统v1.0/market_data/repository.py:40`
   - 会议协同位于 `/Users/zhangsan/Desktop/投资进化系统v1.0/invest/meetings/selection.py:26` 与 `/Users/zhangsan/Desktop/投资进化系统v1.0/invest/meetings/review.py:100`

2. **有明确的导入边界守卫**
   - `tests/test_architecture_import_rules.py` 和 `tests/test_structure_guards.py` 强制限制新层之间的耦合，说明项目不是靠自觉，而是靠测试守住分层。

3. **配置治理成熟**
   - `/Users/zhangsan/Desktop/投资进化系统v1.0/config/services.py:18` 的 `EvolutionConfigService` 提供配置写入、审计日志、快照复制。
   - 这是生产化系统常见的成熟特征。

### 2.2 架构问题
1. **根目录兼容壳只实现了“启动兼容”，没有实现“模块兼容”**
   - `/Users/zhangsan/Desktop/投资进化系统v1.0/web_server.py:1`
   - `/Users/zhangsan/Desktop/投资进化系统v1.0/commander.py:1`
   - `/Users/zhangsan/Desktop/投资进化系统v1.0/train.py:1`
   - 这些文件只是 `from app.xxx import *`。
   - 这样会丢失以下能力：
     - 私有状态不会被转发
     - 对根模块属性的 monkeypatch 不会回写到真实实现模块
     - 兼容层无法作为真实实现模块的“别名”使用

2. **残留半废弃进化分析层**
   - `/Users/zhangsan/Desktop/投资进化系统v1.0/invest/evolution/analyzers.py:231`
   - `_call_llm()` 仍明确写着“这里应该调用实际的 LLM API”，实际返回 mock response。
   - 仓库内未见其被真实主链路调用，说明这是未清理的残留层，而不是正式能力。

### 2.3 架构评分
- 分层设计：8.5/10
- 兼容层收尾：5/10
- 可维护性：7.5/10

---

## 3. 功能实现审查

### 3.1 CLI / Runtime 主链路
`CommanderRuntime` 与 `InvestmentBodyService` 的设计总体合理：
- 训练任务状态、锁文件、运行摘要、训练实验产物都被统一管理
- BrainRuntime、Cron、Heartbeat、Bridge、Memory、Plugin 被集中拼装
- 整体编排中心比较明确

关键位置：
- `/Users/zhangsan/Desktop/投资进化系统v1.0/app/commander.py:551`
- `/Users/zhangsan/Desktop/投资进化系统v1.0/app/commander.py:800`
- `/Users/zhangsan/Desktop/投资进化系统v1.0/app/commander.py:1385`

### 3.2 Web API 实现
Web API 覆盖面较广：
- 状态查询
- 训练触发
- 训练 lab 计划 / 执行 / 评估
- 策略刷新
- 记忆查询
- 数据状态 / 下载
- allocator / leaderboard / investment models

这说明产品面已经不是只有“跑训练”，而是尝试形成运营控制台。

### 3.3 当前功能问题
1. **Web 兼容导入面破损**
   - 测试失败集中在 `_runtime`、`_event_buffer`、`_data_download_running` 等模块级状态丢失。
   - 真实实现存在于 `/Users/zhangsan/Desktop/投资进化系统v1.0/app/web_server.py:41-56`，但根模块 `/Users/zhangsan/Desktop/投资进化系统v1.0/web_server.py:1` 无法暴露这些状态。
   - 这会破坏测试、脚本注入以及任何依赖根模块状态的外部用法。

2. **前端训练中心产品语义明显回退**
   - `tests/test_train_ui_semantics.py` 期待以下元素：
     - `agent-collapse-btn`
     - `timeline-filter-type`
     - `timeline-filter-keyword`
     - `agent-overview`
     - `策略差异对比`
   - 当前 `static/index.html` 中均不存在。
   - 训练中心目前仅保留“轮次 + mock + 开始训练 + 简单结果展示”，见 `/Users/zhangsan/Desktop/投资进化系统v1.0/static/index.html:721`。
   - 这不是小样式问题，而是功能级产品化能力缺失。

### 3.4 功能评分
- CLI / Runtime：8/10
- Web API：7.5/10
- 前端产品完成度：5.5/10

---

## 4. 数据链路审查

### 4.1 数据层优点
1. **canonical schema 统一程度高**
   - `/Users/zhangsan/Desktop/投资进化系统v1.0/market_data/repository.py:58`
   - 已统一纳入：`security_master`、`daily_bar`、`index_bar`、`financial_snapshot`、`trading_calendar`、`security_status_daily`、`factor_snapshot`、`capital_flow_daily`、`dragon_tiger_list`、`intraday_bar_60m`、`ingestion_meta`

2. **读写链路职责分离较好**
   - Repository 负责落库和查询
   - IngestionService 负责数据获取与写入
   - DatasetBuilder 负责训练 / Web / T0 的视图构建
   - DataQualityService 负责健康检查

3. **训练前 readiness 检查比较成熟**
   - `/Users/zhangsan/Desktop/投资进化系统v1.0/market_data/manager.py:369`
   - 会给出 `eligible_stock_count`、issues、suggestions、date_range、quality_checks。
   - 这比“数据不够直接报错”好很多。

### 4.2 数据链路问题
1. **训练控制器对 DataManager 的契约变更没有收尾**
   - `/Users/zhangsan/Desktop/投资进化系统v1.0/app/train.py:776`
   - 现在控制器直接调用：
     - `random_cutoff_date(min_date=..., max_date=...)`
     - `check_training_readiness(...)`
   - 但测试和旧习惯仍在 patch：
     - 零参数 `random_cutoff_date`
     - `diagnose_training_data`
   - 这说明接口升级没有做平滑兼容。
   - 对生产默认实现影响不大，但对扩展实现、测试替身和外部集成是不稳定因素。

2. **readiness 方法名存在双轨**
   - `check_training_readiness()` 与 `diagnose_training_data()` 同时存在，职责重叠。
   - 这是典型“重构没完全收口”的信号，后续容易再次分叉。

### 4.3 数据层评分
- 数据模型：9/10
- 链路清晰度：8.5/10
- 对外契约稳定性：6.5/10

---

## 5. Agent 协同审查

### 5.1 协同设计优点
1. **角色边界定义明确**
   - `agent_settings/agents_config.json` 对 `MarketRegime`、`TrendHunter`、`Commander`、`EvoJudge`、`ReviewDecision` 都写了明确职责和负例约束。
   - 这是好的 agent governance。

2. **会议编排有真实闭环**
   - `SelectionMeeting`：按模型路由不同 specialist agent
   - `ReviewMeeting`：聚合事实 → Strategist → EvoJudge → ReviewDecision → 触发 reflection
   - 关键代码：
     - `/Users/zhangsan/Desktop/投资进化系统v1.0/invest/meetings/selection.py:173`
     - `/Users/zhangsan/Desktop/投资进化系统v1.0/invest/meetings/review.py:243`
     - `/Users/zhangsan/Desktop/投资进化系统v1.0/invest/meetings/review.py:269`

3. **BrainRuntime 不是 prompt 拼接器，而是真正 tool-calling runtime**
   - `/Users/zhangsan/Desktop/投资进化系统v1.0/brain/runtime.py:270`
   - 有明确的工具定义、参数校验、tool loop、session memory。

4. **自治能力不是口号**
   - File bridge：`/Users/zhangsan/Desktop/投资进化系统v1.0/brain/bridge.py:30`
   - Cron：`/Users/zhangsan/Desktop/投资进化系统v1.0/brain/scheduler.py:24`
   - Heartbeat：`/Users/zhangsan/Desktop/投资进化系统v1.0/brain/scheduler.py:189`
   - Persistent memory：`/Users/zhangsan/Desktop/投资进化系统v1.0/brain/memory.py:24`

### 5.2 协同问题
1. **Hunter 恢复函数契约漂移**
   - `/Users/zhangsan/Desktop/投资进化系统v1.0/invest/agents/hunters.py:61`
   - `_recover_hunter_result()` 现在只接收 `raw` 和 `valid_codes`。
   - 但测试仍按旧签名传 `stop_loss_pct` / `take_profit_pct` 默认值。
   - 这说明 refactor 已经改变返回语义，但周边调用和测试没有彻底收尾。

2. **fallback 很强，但部分可观测性仍不足**
   - 好处：系统比较抗 LLM 不可用
   - 问题：当 fallback 触发时，虽然有日志，但在产品面与实验面上还不够统一地显式暴露“本轮到底是 LLM 决策还是算法兜底”。
   - 代码里已有 `selection_mode` / `meeting_fallback` 线索，但 UI 与实验汇总还可以进一步强化。

### 5.3 Agent 协同评分
- 角色治理：8.5/10
- 编排闭环：8/10
- 契约稳定性：6.5/10

---

## 6. 验证结果

### 6.1 已执行验证
1. `./.venv/bin/python -m pytest -q`
2. `./.venv/bin/python -m compileall app brain invest market_data config`

### 6.2 结果
- `pytest`：出现 **16 个失败点**，主要分布如下：
  1. `web_server` 根模块兼容壳导致的状态不可见
  2. `run_training_cycle()` 与 `DataManager` monkeypatch / 旧契约不兼容
  3. `_recover_hunter_result()` 签名漂移
  4. `static/index.html` 缺少训练中心产品化控件
- `compileall`：通过
- `ruff`：当前虚拟环境未安装，未完成 lint 校验

### 6.3 失败分组解释
#### A. 兼容壳问题（影响最大）
关联失败：
- `tests/test_web_server_runtime_and_bool.py`
- `tests/test_web_training_lab_api.py`
- `tests/test_v2_web_models_api.py`
- `tests/test_web_server_memory_api.py`
- `tests/test_train_event_stream.py`
- `tests/test_data_unification.py`

根因：
- `/Users/zhangsan/Desktop/投资进化系统v1.0/web_server.py:1` 不是模块别名，只是名字拷贝。

#### B. 训练控制器接口漂移
关联失败：
- `tests/test_train_cycle.py`
- `tests/test_train_event_stream.py`

根因：
- `/Users/zhangsan/Desktop/投资进化系统v1.0/app/train.py:776`
- 调用签名和方法名都变了，但测试与替身接口没同步收口。

#### C. Hunter 辅助函数漂移
关联失败：
- `tests/test_hunter_code_normalization.py`

根因：
- `/Users/zhangsan/Desktop/投资进化系统v1.0/invest/agents/hunters.py:61`

#### D. 前端语义回退
关联失败：
- `tests/test_train_ui_semantics.py`

根因：
- `/Users/zhangsan/Desktop/投资进化系统v1.0/static/index.html:721`
- 训练中心已经简化，未满足仓库对“产品化训练中心”的自测语义。

---

## 7. 优先级问题清单

### P0
1. **修复根目录兼容壳的模块级兼容性**
   - 否则 Web 相关测试、脚本注入、兼容导入都会持续不稳定。

### P1
2. **统一 `SelfLearningController` 与 `DataManager` 的 readiness / cutoff 契约**
   - 明确一个稳定方法名
   - 对历史调用保留兼容层，或统一升级所有替身与测试

3. **修复 Hunter 恢复函数的契约收尾**
   - 决定保留旧签名兼容，还是统一更新测试与所有调用约定

4. **恢复训练中心的产品化前端语义，或同步下调测试标准**
   - 当前代码与测试对产品能力的认知已经分裂

### P2
5. **清理 `invest/evolution/analyzers.py` 这类未落地主链路的残留层**
   - 要么删掉，要么接成真实实现，不要长期保持 mock 暴露状态

6. **进一步显式化 fallback 可观测性**
   - 在 API / UI / 实验产物中统一标记：本轮是 LLM / fallback / algorithm 哪一种来源

---

## 8. 建议的修复顺序

### 第一阶段：先恢复工程稳定性
1. 修 `web_server.py` 兼容层
2. 修 `run_training_cycle()` 与 `DataManager` 契约
3. 修 Hunter 恢复签名
4. 重新跑 `pytest`

### 第二阶段：恢复产品完成度
5. 决定训练中心前端是恢复旧功能，还是重写测试以匹配新产品策略
6. 完善 timeline / agent overview / 策略差异对比等视图

### 第三阶段：做收尾清理
7. 删除或接通 `invest/evolution/analyzers.py`
8. 统一命名与兼容层策略，减少“双入口 + 双契约”现象

---

## 9. 最终评价

如果以“重构方向是否正确”来评价，这个项目是 **明显朝正确方向前进的**。
如果以“当前是否已经进入稳定可演进状态”来评价，我的结论是：

**核心架构已成型，但收尾质量还不够。**

更具体地说：
- **架构：好**
- **数据层：很好**
- **Agent 编排：有体系**
- **兼容层与产品层：当前最弱**
- **工程稳定性：在测试层面仍需一轮集中收尾**

综合评分：**7.6 / 10**
