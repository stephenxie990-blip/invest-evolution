# Findings（2026-03-10 全量审查）

## 总体评价
- 这是一个“已经完成一次较大重构收口”的项目，而不是早期原型。
- 代码中能看到明确的 V2 分层思路、导入边界约束、配置审计、训练实验产物沉淀与较广覆盖的测试体系。
- 当前最主要的风险来自“重构后兼容层和局部契约没有收尾干净”。

## 主要优点
- `app/` / `brain/` / `market_data/` / `invest/` 分层基本清晰。
- `market_data/repository.py` 建立了统一 canonical SQLite schema，扩展表也已纳入同一仓储。
- `config/services.py` 对配置变更提供审计日志和快照，审计性较强。
- `brain/runtime.py` 与 `app/llm_gateway.py` 统一了 LLM 出口和 tool-calling 框架。
- `SelectionMeeting` / `ReviewMeeting` 已形成“选股 → 复盘 → 反思”协同闭环。
- 项目自带大量结构和契约测试，说明团队在主动约束架构演化。

## 关键问题
- 根目录 `web_server.py` 只是 `from app.web_server import *`，不会导出 `_runtime`、`_event_buffer`、`_data_download_running` 等私有状态，导致兼容层不是“真实别名”。
- `app/train.py` 中 `run_training_cycle()` 直接依赖 `random_cutoff_date(min_date=..., max_date=...)` 和 `check_training_readiness()`，与旧的 monkeypatch / 适配习惯发生漂移。
- `invest/agents/hunters.py` 中 `_recover_hunter_result()` 签名收缩，但测试仍按旧签名调用，说明 refactor 没有完成契约收尾。
- `static/index.html` 当前训练中心较简化，缺少项目测试要求的 Agent 总览、时间线过滤、策略差异对比等产品化元素。
- `invest/evolution/analyzers.py` 暴露未接实网 LLM 的 mock 实现，且仓库内未见真实调用，属于残留死代码风险。

## 验证结果
- `./.venv/bin/python -m pytest -q`：失败 16 例，问题主要集中在兼容壳、接口漂移、前端语义回退。
- `./.venv/bin/python -m compileall app brain invest market_data config`：通过。
- `ruff`：虚拟环境未安装，未能执行 lint。

## 工作区备注
- 当前工作区存在一个与本次审查无关的未提交变更：`data/evolution/generations/momentum_v1_test_candidate.json` 仅时间戳变化。
