# 去兼容化迁移计划

目标：移除项目中的历史兼容层、旧导入路径、旧接口兜底与重复入口，收敛到单一、明确、可维护的工程结构。

## 当前执行状态

- Phase 1：已完成
- Phase 2：已完成
- Phase 3：已完成
- Phase 4：已完成
- Phase 5：已完成
- Phase 6：已完成

## 迁移原则

- 只保留一套真实源码入口：根目录模块。
- 删除 `src/` 兼容包装层及所有 `src.*` 别名。
- 删除仅为旧代码保留的 API、属性别名和运行时 fallback。
- 文档、测试、安装方式统一到当前真实结构。
- 每一阶段都保证测试可运行、Git 可回滚。

## 当前兼容面清单

### A. `src/` 目录兼容层
- `src/*.py` 仅做 `from xxx import *` re-export
- `src/__init__.py` 标记为 legacy compatibility package

### B. 运行时 `src.*` 别名
- `__init__.py` 中的 `install_legacy_aliases()` + `INVEST_ENABLE_LEGACY_SRC`
- `core.py` 中的 `_install_compat_modules()`
- `agents.py` 中的 `_install_agent_compat()`

### C. 旧 API / 旧语义兼容点
- `config.EvolutionConfig.llm_model` 属性别名
- `llm_router.LLMRouter.from_caller()`
- `SimulatedTrader` 未设置 `TradingPlan` 时的内部选股 fallback

### D. 测试兼容点
- `tests/test_structure_guards.py` 针对 `src/` 的兼容断言
- `tests/test_all_modules.py` 中 `src.*` 导入测试
- `tests/test_llm_router.py` 中 `from_caller()` 测试
- `tests/test_all_modules.py` 中 trader 无 plan 向后兼容测试

### E. 文档兼容点
- `docs/MAIN_FLOW.md` 当前仍解释 `src/` 兼容层
- 其他文档中残余“兼容/旧逻辑/保留”措辞需收口

## 分阶段实施

### Phase 1：删除文档与测试兼容表述

范围：
- 更新 `README.md`
- 更新 `docs/MAIN_FLOW.md`
- 改写测试，不再验证 `src.*` 与旧兼容接口

验收标准：
- 测试只验证真实根模块结构
- 文档不再宣称保留 `src/` 兼容层

### Phase 2：移除配置与路由兼容 API

范围：
- 将 `config.llm_model` 调用全部改为 `config.llm_fast_model`
- 删除 `EvolutionConfig.llm_model` 属性
- 删除 `LLMRouter.from_caller()` 及相关测试

验收标准：
- 全仓无 `config.llm_model` 调用
- `LLMRouter` 只保留 `from_config()` 构造路径

### Phase 3：移除交易引擎旧兜底逻辑

范围：
- 删除 `SimulatedTrader` 无 `TradingPlan` 时的内部选股逻辑
- 改为显式要求 `set_trading_plan()` 后才能运行模拟
- 更新相应测试

验收标准：
- `SimulatedTrader` 的输入合同清晰明确
- 没有 “无 plan 也能跑” 的隐式行为

### Phase 4：移除运行时 `src.*` 别名

范围：
- 删除 `__init__.py` 中 legacy alias 安装逻辑
- 删除 `core.py` 的 `_install_compat_modules()`
- 删除 `agents.py` 的 `_install_agent_compat()`

验收标准：
- 代码中不再注册任何 `src.*` 动态别名
- 测试与文档全部使用根模块路径

### Phase 5：删除 `src/` 目录

范围：
- 删除 `src/` 下所有兼容包装文件
- 清理引用与构建残留

验收标准：
- 仓库中不存在 `src/*.py` 兼容包装
- 全量测试通过

### Phase 6：收敛安装与对外接口

已完成：
- 删除 `requirements.txt`，只保留 `pyproject.toml` 作为依赖来源
- 删除未被仓库内部使用的根 `__init__.py`

验收结果：
- 安装方式单一、清晰
- 仓库不再保留无实际调用的历史聚合入口

## 推荐执行顺序

1. Phase 1
2. Phase 2
3. Phase 3
4. Phase 4
5. Phase 5
6. Phase 6

## 风险提示

- Phase 3 可能影响少量研究脚本；需要用测试覆盖实际调用链。
- Phase 4/5 会直接破坏任何外部 `src.*` 导入；应在删除前确认本仓库内部已完全收敛。
- Phase 6 牵涉开发习惯，不建议与核心代码迁移混在同一提交里。

## 当前建议

先做 Phase 1 + Phase 2。它们收益高、风险低，能快速把兼容面砍掉一半以上。
