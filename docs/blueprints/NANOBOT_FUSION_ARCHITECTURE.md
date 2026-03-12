# Commander 融合架构说明

当前系统的“融合”不是概念层面的，而是代码层面的：`CommanderRuntime` 把本地 agent runtime 与投资训练主体装在同一个进程里。

## 1. 融合对象

### 1.1 Brain

`brain/` 提供：

- 多轮 session
- tool calling
- cron / heartbeat
- bridge
- memory
- plugin

### 1.2 Body

`InvestmentBodyService` + `SelfLearningController` 提供：

- 训练执行
- 周期结果统计
- 模型切换
- 会议、模拟交易、评估、优化
- 周期工件落盘

## 2. 核心装配点

装配入口：`app/commander.py`

关键对象：

- `CommanderConfig`
- `CommanderRuntime`
- `BrainRuntime`
- `InvestmentBodyService`
- `TrainingLabArtifactStore`
- `StrategyGeneRegistry`
- `MemoryStore`
- `CronService`
- `HeartbeatService`
- `BridgeHub`

## 3. 为什么说是融合运行时

### 3.1 一个状态源

`CommanderRuntime.status()` 会一次性返回：

- runtime 状态
- brain 工具与 session 统计
- body 训练状态
- memory 状态
- bridge 状态
- 插件列表
- 策略基因
- 配置摘要
- 数据状态
- training lab 摘要

### 3.2 一个动作面

Brain 通过 `brain/tools.py` 注册的工具，可以直接驱动：

- 查询系统状态
- 生成训练计划
- 执行训练计划
- 单轮训练
- 列出/重载策略
- 管理 cron
- 搜索记忆
- 重载插件

### 3.3 一个落盘现场

融合后的动作不会分别落到多套日志系统，而是统一进入 `runtime/`：

- 状态快照
- memory
- training lab
- 会议记录
- 训练结果
- 配置快照

## 4. 工具面设计

当前内置工具包括：

- `invest_status`
- `invest_quick_status`
- `invest_deep_status`
- `invest_train`
- `invest_quick_test`
- `invest_list_strategies`
- `invest_training_plan_create`
- `invest_training_plan_list`
- `invest_training_plan_execute`
- `invest_reload_strategies`
- `invest_cron_add`
- `invest_cron_list`
- `invest_cron_remove`
- `invest_memory_search`
- `invest_plugin_reload`

## 5. Bridge / Cron / Heartbeat 的位置

### 5.1 Bridge

- 文件通道在 `runtime/sessions/inbox` 与 `runtime/sessions/outbox`
- 用于外部系统以文件形式向 Commander 投递消息

### 5.2 Cron

- `CronService` 支持 interval 型任务
- 任务内容本质上仍是“向 Commander 发送一条消息”

### 5.3 Heartbeat

- 心跳任务用于定期触发自检或常规维持动作
- Web 模式默认关闭 heartbeat

## 6. 策略基因如何参与融合

`StrategyGeneRegistry` 会把 `strategies/` 中的可插拔策略文件整理为摘要，并写入：

- `runtime/workspace/SOUL.md`
- `runtime/workspace/HEARTBEAT.md`

这使得 Brain 在进行工具决策前，能感知当前激活的策略 DNA。

## 7. 当前设计的优势

- 指挥与执行之间没有 RPC 边界，链路更短
- 所有状态可聚合成统一快照
- Web / CLI / tool calling 使用同一套真实能力
- 训练实验室、策略基因、记忆搜索都能被自然语言工作流直接驱动

## 8. 当前设计的注意点

- 仍然是单进程、单实例优先架构
- 训练执行依赖互斥锁，避免并发污染
- 长时间运行时，要重点关注 `runtime/state/*.lock` 与训练输出目录增长
