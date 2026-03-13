# 项目文件体系重组方案（2026-03-13）

## 目标

1. 让 `commander` 相关实现按“入口 / 支撑 / 域服务 / 运行态”分层。
2. 清掉已经退出主链的 Web UI 残留与临时会话草稿。
3. 降低根目录噪音，把脚本、会话文档、归档物各归各位。

## 最终目录建议

```text
app/
  commander.py                    # 唯一 commander 运行时主入口
  commander_support/             # commander 入口薄壳依赖的全部 support 模块
    ask.py
    cli.py
    config.py
    domain_catalog.py
    identity.py
    observability.py
    plugin.py
    runtime_lifecycle.py
    runtime_mutation.py
    runtime_query.py
    runtime_state.py
    services.py
    status.py
    training.py
    training_plan.py
    workflow.py
  investment_body_service.py
  runtime_contract_*.py
  stock_analysis.py
  strategy_gene_registry.py
  train.py
  web_server.py

brain/                           # agent runtime / planner / scheduler / memory / plugins
config/                          # 全局配置与控制面配置
invest/                          # 投资域模型与训练/会议/评估核心
market_data/                     # 数据读写与同步层

scripts/
  data/                          # 数据回填/修复脚本
  *.sh                           # 环境与本地清理脚本

runtime/                         # 纯运行态目录
docs/
  plans/
    session/                     # 本轮会话过程文档归档
  contracts/
  runbooks/
  architecture/
  blueprints/

历史归档区/
  20260313_root_cleanup/         # 已退出主链但暂保留追溯价值的目录
  ...
```

## 本轮已完成

- `app/commander_*` support 文件全部归并到 `app/commander_support/`
- `runtime/baostock_backfill.py` / `runtime/baostock_resume_backfill.py` 迁移到 `scripts/data/`
- 根目录会话草稿 `findings.md` / `progress.md` / `task_plan.md` 迁移到 `docs/plans/session/`
- 旧 `frontend/` / `static/` 迁移到 `历史归档区/20260313_root_cleanup/`

## 后续可选收口

1. 若确认外部没有直接依赖，可把 `allocator.py` / `leaderboard.py` 再并入 `scripts/cli/`
2. 评估是否保留根层 `llm_gateway.py` / `llm_router.py` 兼容壳
3. 为 `docs/architecture` / `docs/blueprints` 增加索引页，进一步提升文档可读性
