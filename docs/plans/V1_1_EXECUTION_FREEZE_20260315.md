# V1.1 Execution Freeze

## Goal

冻结 `v1.1` 当前可执行基线，明确 `Phase 0-5` 的目标、工作边界、验收标准与默认验证门槛，避免后续继续把范围扩张成新一轮结构性重构。

## Frozen Baseline

本次冻结基线以 `2026-03-15` 仓库状态为准，默认依赖以下稳定接缝：

- `brain/runtime.py` 作为 runtime orchestration 主入口
- `brain/presentation.py` 作为 human receipt / runtime narration presenter
- `brain/structured_output.py` 作为内建 structured-output adapter
- `brain/guardrails.py` 作为内建 mutating workflow guardrail policy engine
- `app/interfaces/web/presentation.py` 作为 web display card seam
- `app/commander_support/status.py` 作为 runtime + training lab 状态汇总 seam
- `app/lab/artifacts.py` / `app/lab/evaluation.py` 作为 training lab artifact 与 evaluation 摘要生成 seam
- `invest/contracts/agent_context.py` 作为 `confidence` 尾项 contract 收口点

## Accepted Gates

默认质量门槛分为两层：

### Quick gate

- contract drift check
- focused protocol regression
- critical ruff check
- critical pyright check

Quick gate 必须覆盖本轮核心 seam：

- `structured_output`
- `guardrails`
- `training_lab`
- `web presentation`
- `agent_context confidence contract`

### Full gate

- `ruff check .`
- `pyright .`
- `pytest -q`
- `python -m app.freeze_gate --mode quick`
- `python -m app.freeze_gate --mode full`

## Phase Plan

| Phase | Goal | Core work | Acceptance |
|---|---|---|---|
| 0 | 冻结当前基线 | 固定 seam、冻结 quick/full gate、补 execution freeze 文档 | 文档落盘；`freeze_gate` 覆盖核心 seam；后续工作不再扩张成结构重构 |
| 1 | 训练协议继续硬化 | 补 protocol / dataset / model_scope / optimization 摘要链；训练治理与现实性指标进入 evaluation / artifacts | `training_lab` run/eval artifacts 可直接读出协议摘要、promotion、治理与现实性字段 |
| 2 | 协议尾项收口 | 将 `confidence` fallback 统一收口到 contract helper；消灭散落读取 | selection / training 默认走 `AgentContext.effective_confidence()` 或 contract helper；`confidence` clamp 到 `0..1` |
| 3 | 内建 Structured Output 深化 | 扩 structured-output 到 training list/get/summary 与 config read side | `training_plan_list`、`training_runs_list`、`training_evaluations_list`、`training_lab_summary`、`control_plane_get`、`runtime_paths_get`、`agent_prompts_*` 都可稳定 normalize |
| 4 | 内建 Guardrails 策略化升级 | 扩 mutating tool guardrail 到 cutoff policy、runtime path、agent prompt 语义校验 | 非法 cutoff / 路径 / prompt 更新可在执行前被阻断并返回明确 reason code |
| 5 | 展示与运维可观测性补齐 | commander / web / brain receipt 增加 training governance 与 runtime governance 摘要 | CLI / web / receipt 三条展示链都能看到治理指标、现实性摘要与 runtime structured-output/guardrail 统计 |

## Execution Rules

- 不再引入新的架构层级或目录迁移作为 `v1.1` 前置任务
- 所有新增治理与协议字段必须能在至少一个展示入口被直接观察到
- 新增 contract/guardrail/structured-output 规则必须补 focused tests
- 所有 mutating workflow 升级都必须经过 `freeze_gate` quick 模式

## Review Checklist

在宣布 `Phase 0-5` 完成前，必须确认：

- training plan / run / evaluation / summary 四条读链字段一致
- runtime status / web display / brain receipt 三条展示链信息一致
- `guardrails.reason_codes` 具备稳定语义，不依赖 prompt 猜测
- `structured_output.status` 能区分 `validated / repaired / fallback`
- `candidate_pending / active_candidate_drift / realism` 等关键治理信号可见

## Exit Condition

满足以下条件才允许进入训练模拟与最终汇报：

- focused verification 通过
- full verification 通过
- 系统级复审确认 `Phase 0-5` 目标均有代码、测试和展示面支撑
- 20 轮训练模拟已执行并产出可审计结果
