# 后端目录职责图（2026-03-13）

本文记录**当前收口后的后端目录职责**，用于继续清理、扩展 `commander` 入口，以及后续做前端彻底退出后的纯 agent 化协作。

## 1. 当前后端总分层

```text
root shell
  └─> app/               # 统一入口、Web/CLI 适配、运行时编排
       ├─> brain/        # agent runtime、task bus、自然语言调度
       ├─> invest/       # 投资业务域能力
       ├─> market_data/  # 数据读写、质量审计、训练数据构造
       └─> config/       # 控制面、运行路径、训练配置
```

## 2. 目录职责

### 2.1 `app/`

`app/` 现在只承接**入口层与薄服务层**，不再承载大块业务细节。

- `app/commander.py`：统一 CLI / runtime 入口，组织 training、config、memory、analytics 等 runtime method
- `app/web_server.py`：Flask 主骨架，只负责 app 初始化、runtime bootstrap、auth/rate-limit、SSE、模块注册
- `app/web_read_routes.py`：只读查询路由
- `app/web_ops_routes.py`：运维、配置、记忆类路由
- `app/web_data_routes.py`：数据查询与下载路由
- `app/web_command_routes.py`：命令型入口，例如 `chat`、`train`、training execute
- `app/runtime_contract_*`：runtime contract 源与派生文档生成工具
- `app/commander_support/`：Commander 的辅助层，包括展示、status、workflow、services、runtime_query 等

### 2.2 `brain/`

`brain/` 是**自然语言编排与 agent 运行时层**，已经是项目的核心交互中枢。

- `brain/runtime.py`：自然语言入口、tool loop、human receipt 组装
- `brain/task_bus.py`：bounded workflow / gate / confirmation / risk policy
- `brain/tools.py`：把投资域、数据域、配置域能力包装成工具
- `brain/transcript_snapshot.py`：运行转录快照
- `brain/schema_contract.py`：风险、确认、task bus 等 schema 常量

### 2.3 `invest/`

`invest/` 是**投资业务域**，不应再被入口层直接揉进大量细节。

- `invest/models/`：模型实现与配置
- `invest/allocator/`：分配策略
- `invest/leaderboard/`：训练结果聚合
- `invest/meetings/`：selection / review / recorder
- `invest/evolution/`：优化与进化链路
- `invest/shared/`：共享指标、调用器、摘要工具

### 2.4 `market_data/`

`market_data/` 是**标准数据层**，承担读写与质量审计。

- `repository.py`：canonical SQLite schema 与查询
- `ingestion.py`：数据同步写路径
- `datasets.py`：Web/训练/分析读路径
- `manager.py`：兼容 façade
- `quality.py`：健康检查与诊断

### 2.5 `config/`

`config/` 是**控制面与运行参数层**。

- `config/evolution.yaml`：训练与策略主配置
- `config/control_plane/`：LLM 控制面配置
- `config/services.py`：Evolution / RuntimePath 配置服务

## 3. Web 收口后的职责边界

Web API 现在已经拆成四个注册模块：

- `app/web_read_routes.py`：read-only
- `app/web_ops_routes.py`：ops/config/memory
- `app/web_data_routes.py`：data query/download
- `app/web_command_routes.py`：chat/train/execute

`app/web_server.py` 不再承担每条路由的具体业务逻辑，而只保留：

- Flask app 创建
- runtime bootstrap / shutdown
- auth / public-read / rate limit
- SSE 事件流
- 路由模块注册
- `main()`

这意味着后续如果继续增强 API，应该优先改对应 route module，而不是重新把逻辑堆回 `web_server.py`。

## 4. 当前推荐依赖方向

```text
route modules -> CommanderRuntime / commander_support
CommanderRuntime -> brain + invest + market_data + config
brain -> invest + market_data + config
invest -> config
market_data -> config
```

避免反向依赖：

- 不要让 `invest/` 依赖 `app/web_*`
- 不要让 `market_data/` 依赖 `brain/runtime`
- 不要在 `web_server.py` 重新写 route-specific 业务逻辑

## 5. 继续清理时的建议顺序

### 5.1 优先继续清理

- `app/commander.py` 中仍然偏厚的 runtime method 聚合面
- `app/commander_support/` 内部可再细分的 presentation / workflow / services 边界
- `brain/runtime.py` 中 receipt、intent、tool loop 的局部职责重叠

### 5.2 暂不建议动

- `brain/task_bus.py` 的 schema 与 gate 结构
- `docs/contracts/runtime-api-contract.v1.json` 的核心路径与头部契约
- SSE 总线、事件历史、监控相关协议

## 6. 对下一阶段 commander 增强的意义

后端目录收口完成后，`commander` 可以继续专注三件事：

1. 自然语言调度更稳定
2. 事件解释更贴近人类理解
3. 回执文案更适合作为“人类可直接阅读的系统说明”

这也是后续应该继续投入的主线。
