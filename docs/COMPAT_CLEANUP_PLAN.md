# 去兼容化归档报告

本报告归档本次“去兼容化”整改的目标、实施范围、最终结果与影响说明。

## 背景

项目在早期演进过程中保留了多类历史兼容设计，包括：

- `src/` 目录下的根模块 re-export 包装层
- 运行时动态注册的 `src.*` 别名
- 面向旧调用方式保留的属性别名与构造入口
- `SimulatedTrader` 在未设置 `TradingPlan` 时的隐式兜底逻辑
- 文档与测试中针对兼容路径的保留说明
- 双安装口径：`pyproject.toml` 与 `requirements.txt`

这些设计虽然降低了迁移阻力，但同时带来了以下问题：

- 工程结构存在双路径心智负担
- 测试与文档会持续强化历史路径
- 运行时合同不够显式
- 打包与安装路径不够单一
- 代码洁净度和维护成本不理想

## 整改目标

本次整改的目标是：

- 只保留一套真实源码入口：根目录模块
- 删除 `src.*` 相关兼容层与动态别名
- 删除旧 API、旧属性别名与旧运行时 fallback
- 统一安装与打包入口到 `pyproject.toml`
- 让测试、文档、运行时合同全部与当前真实结构一致

## 实施结果

### 1. 删除 `src/` 兼容层

已完成：

- 删除整个 `src/` 目录
- 删除其中所有 `from xxx import *` 包装文件
- 不再保留 `src.__init__` 兼容包定义

结果：

- 仓库只保留根目录模块作为唯一源码路径
- 不再存在 `src/*.py` 兼容包装文件

### 2. 删除运行时 `src.*` 动态别名

已完成：

- 删除根 `__init__.py` 中的 `install_legacy_aliases()` 与 `INVEST_ENABLE_LEGACY_SRC`
- 删除 `core.py` 中的 `_install_compat_modules()`
- 删除 `agents.py` 中的 `_install_agent_compat()`

结果：

- 运行时不再注册任何 `src.*` 动态别名
- 旧导入路径在仓库内部已被完全移除

### 3. 删除旧 API 与属性别名

已完成：

- 将 `config.llm_model` 调用全部改为 `config.llm_fast_model`
- 删除 `EvolutionConfig.llm_model` 属性别名
- 删除 `LLMRouter.from_caller()`

结果：

- LLM 配置字段命名与语义完全统一
- `LLMRouter` 仅保留 `from_config()` 的明确构造路径

### 4. 删除交易引擎隐式兜底逻辑

已完成：

- 删除 `SimulatedTrader` 在未设置 `TradingPlan` 时的内部选股 fallback
- 改为显式要求先调用 `set_trading_plan()` 再运行模拟

结果：

- `SimulatedTrader` 的输入合同更加清晰
- 运行逻辑从“隐式容错”变为“显式失败”

### 5. 更新测试与文档

已完成：

- 删除测试中对 `src.*` 兼容路径的导入验证
- 删除测试中对 `LLMRouter.from_caller()` 的验证
- 删除 trader 无 `TradingPlan` 的兼容测试
- 将相关文档改写为单路径结构说明

结果：

- 测试只验证当前真实结构
- 文档不再鼓励或解释历史兼容路径

### 6. 统一安装与打包入口

已完成：

- 删除 `requirements.txt`
- 在 `pyproject.toml` 中显式声明 `tool.setuptools.py-modules`
- 统一 README 安装口径为 `python3 -m pip install -e ".[dev]"`

结果：

- 安装方式收敛为单一来源
- 删除 `src/` 后的 setuptools 自动发现问题已解决

## 关键影响

### 正向影响

- 仓库结构显著简化
- 导入路径、文档、测试、安装方式完全一致
- 运行时合同更清晰，隐式行为更少
- 工程维护成本下降
- 后续重构与拆模块的前置障碍已经减少

### 破坏性变更

本次整改属于明确的破坏性收敛，影响包括：

- 任何外部 `src.*` 导入将不再可用
- 任何依赖 `LLMRouter.from_caller()` 的外部代码将不再可用
- 任何未先设置 `TradingPlan` 就直接运行 `SimulatedTrader` 的代码将失败
- `pip install -r requirements.txt` 不再是项目维护口径

如果外部仍有历史脚本依赖这些路径，需要单独同步修改。

## 验证结果

本次整改后已完成以下验证：

- `python -m pip install -e '.[dev]'` 通过
- `pytest -q` 全量通过

结论：

- 去兼容化后的工程结构可安装、可测试、可运行

## 相关提交

本轮去兼容化变更已提交到 Git：

- `c603ab8` — `refactor: remove legacy compatibility layers`

在此之前的仓库治理基线提交为：

- `6af1486` — `chore: establish clean project baseline`

## 当前状态

当前项目已经完成从“历史兼容并存”到“单路径、单入口、显式合同”的结构收敛。

可作为后续工作的干净基线包括：

- 超大文件拆分
- 模块边界进一步收紧
- 文档体系再整理
- Web/API/训练层职责继续解耦

## 后续建议

建议后续优先级如下：

1. 拆分超大模块，如 `evaluation.py`、`optimization.py`、`trading.py`
2. 对 `commander.py` 做运行时/CLI/状态持久化分层
3. 为 Web 层补充更细粒度的 API 测试与契约测试
4. 将归档类文档与设计类文档分层整理，减少重复描述
