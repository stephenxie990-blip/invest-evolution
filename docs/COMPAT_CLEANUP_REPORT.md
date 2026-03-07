# 去兼容化归档报告

归档时间：2026-03-07
状态：已完成

本报告归档本次“去兼容化 + 工程收口”整改的目标、实施范围、最终结果与影响说明。

## 背景

项目在早期演进过程中保留了多类历史兼容设计，包括：

- 根目录兼容启动壳与真实实现并存
- 旧文档对历史单文件布局的延续描述
- 打包配置落后于当前分包结构
- 训练输出、会议记录、配置快照等运行副产物存在默认全局路径耦合
- 若干过程性分析文档停留在“执行中”状态

这些设计虽然降低了迁移阻力，但也带来了结构认知负担、文档漂移与治理边界不清的问题。

## 本轮整改目标

- 统一真实入口认知，明确 `app/` 为真实应用层，根目录入口为兼容壳
- 统一文档、代码、测试、打包配置的口径
- 统一 `Web / Commander / Train` 三个入口的运行路径配置
- 将过程性文档收口为归档文档，避免继续以临时工作台状态残留

## 已完成事项

### 1. 文档对齐

已完成：

- 更新 `README.md` 的主链路说明与分包布局
- 更新 `docs/MAIN_FLOW.md` 的模块映射与结构约束
- 更新 `docs/NANOBOT_FUSION_ARCHITECTURE.md` 的 Brain / Body / 训练主体描述

结果：

- 文档描述已与当前仓库真实结构一致
- 不再以历史单文件布局作为主叙事

### 2. 打包修复

已完成：

- 修正 `pyproject.toml`
- 保留根目录兼容入口为 `py-modules`
- 将业务包改为自动发现子包

结果：

- `invest.*`、`brain.*`、`market_data.*`、`config.*` 等子包可被正确打包
- 安装方式与实际源码结构一致

### 3. 训练副产物路径治理

已完成：

- `CommanderConfig` 新增并统一管理以下路径：
  - `training_output_dir`
  - `meeting_log_dir`
  - `config_audit_log_path`
  - `config_snapshot_dir`
- `SelfLearningController` 支持上述路径显式注入
- `MeetingRecorder`、配置快照/审计日志落点已接入统一配置

结果：

- 训练链不再强耦合固定全局默认路径
- 运行态目录更容易迁移、隔离与测试

### 4. Web / Commander / Train 运行路径统一

已完成：

- 新增 `RuntimePathConfigService`
- 新增 Web 接口 `/api/runtime_paths`
- 前端配置面板新增运行路径配置区
- `Commander` 启动时读取统一运行路径配置
- `Train` CLI 在未显式传参时读取同一配置源
- Web 运行时保存后会立即同步当前 `CommanderRuntime` 与训练控制器

结果：

- `Web / Commander / Train` 三个入口已共享同一套路径配置口径
- 运行路径治理从“代码内分散默认值”提升为“可视化、可持久化、可复用的统一配置”

### 5. 过程文档收口

已完成：

- `docs/COMPAT_CLEANUP_PLAN.md` 不再保留
- `findings.md`、`progress.md`、`task_plan.md` 已转为归档状态

结果：

- 根目录不再继续保留“执行中”语义的遗留工作文档
- 当前文档状态更适合作为稳定基线继续演进

## 验证结果

本轮整改后已完成以下验证：

- `pytest -q` 全量通过
- `python -m pip install -e '.[dev]'` 通过

结论：

- 本轮文档、打包与路径治理整改后，项目仍保持可安装、可测试、可运行

## 相关文档

- 项目全面审计：`docs/项目审计0307.md`
- 主流程说明：`docs/MAIN_FLOW.md`
- 融合架构说明：`docs/NANOBOT_FUSION_ARCHITECTURE.md`

## 当前状态

当前项目已经完成从“兼容遗留较多、过程文档散落”到“入口统一、路径统一、文档收口”的进一步治理。

可作为后续工作的更干净基线包括：

- 进一步拆分 `app/train.py`
- 进一步收紧 `app/commander.py`
- 将更多临时分析材料迁入正式文档体系或清理出仓库根目录
