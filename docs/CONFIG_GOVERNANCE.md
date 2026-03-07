# 配置治理说明

## 当前实现

配置修改统一通过 `EvolutionConfigService` 处理，职责包括：

- patch 归一化
- 参数校验
- YAML 持久化
- 失败回滚
- 快照保存
- 审计日志追加

## 核心文件

- `config/evolution.yaml`：当前生效配置文件
- `runtime/state/config_changes.jsonl`：配置变更审计日志
- `runtime/state/config_snapshots/`：配置快照归档
- `runtime/outputs/training/cycle_*_config_snapshot.json`：训练周期冻结配置快照

## 审计字段

每次配置变更至少记录：

- 变更时间
- 变更来源
- 修改字段
- 修改前后值摘要

其中敏感字段如 `llm_api_key` 只记录脱敏值。
