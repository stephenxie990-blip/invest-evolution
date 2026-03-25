# 技术债修复蓝图（2026-03-25）

## 目标

围绕本轮审查中识别出的四类核心技术债，完成一轮“结构可持续化”修复：

1. `training` 主链依赖动态代理与软边界
2. `train.py` 兼容 facade 过重
3. `stock_analysis.py` 职责过载
4. `web/server.py` 依赖模块级全局状态与手工线程生命周期

本轮目标不是重写系统，而是在不破坏现有 CLI / Web / contract 行为的前提下，把最高风险的结构债向显式边界和可验证模块迁移。

## 原则

- 保持对外兼容，优先内部收口
- 优先拆“软边界”，再拆“超大文件”
- 用 focused tests 锁定新边界，而不是只靠口头约定
- 每个改动都必须有对应的质量门

## 工作流

### Stream A: Training Boundary

所有者：主代理统筹，worker `training_boundary_refactor` 执行

目标：

- 减少 `application/training/execution.py` 对 `import_module/getattr` 的依赖
- 用显式 helper / interface 替代高频动态代理
- 尽量削薄 `application/train.py` 中仍留在 facade 的训练核心职责

验收：

- 不改变 `train.py` 对外行为
- focused tests 覆盖新边界
- `pyright` / `ruff` 通过

### Stream B: Stock Analysis Split

所有者：主代理统筹，worker `stock_analysis_split` 执行

目标：

- 从 `application/stock_analysis.py` 中抽离高内聚模块
- 优先抽离 contracts / dataclasses / response assembly / bridge helpers
- 保持调用方 import 和外部行为兼容

验收：

- 现有调用方无需大面积改造
- focused tests 锁定抽离后的行为
- 文件体量和职责边界明显改善

### Stream C: Web Runtime Container

所有者：主代理统筹，worker `web_runtime_container` 执行

目标：

- 收口 `interfaces/web/server.py` 的 embedded runtime state
- 减少 `globals()` 和裸模块级状态写入
- 保持现有路由和部署模式兼容

验收：

- Web 行为不变
- focused tests 覆盖 state/container 行为
- 并发和生命周期代码更可读、更可替换

## 主代理职责

- 统一蓝图与优先级
- 控制改动边界和 merge 风险
- 审阅子代理提交并做最终集成
- 运行验证闭环并出最终修复报告

## 验证闭环

至少执行以下验证：

1. focused pytest suites for changed areas
2. `uv run pyright` on touched modules
3. `uv run ruff check` on touched modules and tests
4. 必要时补跑架构守卫测试

## 本轮完成定义

满足以下条件才算完成：

- 三条工作流全部合并
- 没有引入新的 contract 回归
- 原始审查里指出的高/中风险问题均有对应修复或明确降级说明
- 最终汇报包含：
  - 修了什么
  - 如何验证
  - 仍存在哪些后续债务

## 第二轮目标

在第一轮已经完成的基础上，继续做“小步快跑但真实减债”的第二轮：

### Round 2A: Stock Analysis Orchestration Extraction

- 继续从 `application/stock_analysis.py` 中抽离高内聚模块
- 优先级：
  - `research resolution` / `display contract` 相关 dataclass 与装配逻辑
  - 不直接触碰最宽的执行主链，避免 blast radius 过大

### Round 2B: Web Runtime Dual-Path Tightening

- 保留兼容别名的前提下，进一步减少 `server.py` 对裸别名读写的直接依赖
- 尽量把读路径统一走 container-backed helpers
- 补 focused tests 验证 container 是 canonical state owner

### Round 2C: Training Compatibility Cleanup

- 在不扩大 `execution.py` 风险面的前提下，再削薄一层 facade / helper 债务
- 目标是继续从 `train.py` 或 `controller.py` 中抽离高内聚兼容对象，而不是重新大拆训练主链

## 第二轮完成定义

- 至少一块 `stock_analysis.py` 高内聚逻辑继续外提
- Web runtime dual-path 进一步收紧，container ownership 更明确
- Training surface 再完成一处低风险减债
- 第二轮 focused joint verification 通过

## 第三轮目标

第三轮开始把重点从“契约层瘦身”推进到“服务/编排层收口”，但仍坚持小步安全：

### Round 3A: Stock Analysis Service Extraction

- 优先从 `stock_analysis.py` 中抽离一块 service/orchestration 级能力
- 候选优先级：
  - `ResearchResolutionService`
  - `BatchAnalysisViewService`
  - `StockAnalysisResearchBridgeService` 中相对独立的子段
- 目标不是重写，而是让 `stock_analysis.py` 不再同时做所有编排 owner

### Round 3B: Training Facade Decomposition Design

- 先评估 `TrainingResult` 与 `SelfLearningController` 的拆分边界
- 只有在低风险情况下才落一小步代码
- 若风险过高，输出可实施设计与下一步落点，避免硬拆

### Round 3C: Supporting Cleanup Only

- Web 仅在直接帮助 round-three 主线时才做补充收口
- 不再主动扩张 web 面改动范围

## 第三轮完成定义

- `stock_analysis.py` 至少完成一处 service/orchestration 级外提
- `train.py` 的下一阶段拆分边界被明确，必要时落一小步实现
- round-three focused verification 通过

## 第四轮目标

第四轮继续收紧 `stock_analysis.py` 的 service owner，优先处理剩余最高价值块：

### Round 4A: Research Resolution Service Extraction

- 优先把 `ResearchResolutionService` 整体外提，或在风险控制下抽出其核心 orchestration 子服务
- 目标是让 `stock_analysis.py` 不再同时拥有 resolution contracts、batch service、resolution service 三层 owner

### Round 4B: Minimal Supporting Changes Only

- 仅在 extraction 需要时对调用方或兼容导出做最小修改
- 不主动扩张到训练或 web 的新战线

## 第四轮完成定义

- `ResearchResolutionService` 或其主要 orchestration 子块完成外提
- focused verification 通过

## 第五轮目标

第五轮继续缩小 `stock_analysis.py` 的 service owner 面积，但坚持兼容优先：

### Round 5A: Research Bridge Runtime Ownership Extraction

- 将 `StockAnalysisResearchBridgeService` 的主运行路径迁移到独立模块
- 优先外提 bridge runtime context、assembly pipeline、output/finalize orchestration
- 在 `stock_analysis.py` 中保留 facade-compatible import/export，避免影响现有调用方

### Round 5B: Guardrail Hardening

- 增加结构护栏，防止 `stock_analysis.py` 在后续演进中重新吸回 research bridge / resolution owner
- focused verification 覆盖 ask-stock bridge 契约、结构守卫、架构导入规则

## 第五轮完成定义

- `StockAnalysisResearchBridgeService` 运行期 owner 完成外提
- `stock_analysis.py` 不再内嵌 research bridge service 主实现
- focused verification 通过

## 第六轮目标

第六轮把焦点转向 `stock_analysis.py` 中剩余的 duplicated legacy implementation：

### Round 6A: Legacy Resolution Retirement

- 确认 `_LegacyResearchResolutionService` 是否仍有真实运行时引用
- 若无真实依赖，则直接删除整块复制实现，而不是继续保留沉没成本
- 保持 canonical owner 仍为 `stock_analysis_research_resolution_service.py`

### Round 6B: Facade Compatibility Preservation

- 对仍有外部依赖价值的 module-level contracts/export 保持兼容导出
- 用结构护栏锁定“不能重新把 legacy resolution 实现塞回 `stock_analysis.py`”

## 第六轮完成定义

- `_LegacyResearchResolutionService` 从 `stock_analysis.py` 中退役
- canonical resolution owner 保持外提模块化形态
- facade compatibility exports 与 focused verification 通过

## 第七轮目标

第七轮转向 `ask_stock` 主链内部的 helper density，继续把 facade 压薄：

### Round 7A: Ask-Stock Assembly Extraction

- 将 ask-stock 的 stage adapter / response assembly / header-orchestration helper 从 `stock_analysis.py` 外提到独立模块
- 优先抽离高内聚的“stage contract -> protocol response”装配层，而不去打散 execution / research orchestration 本体

### Round 7B: Facade Boundary Locking

- 保持 ask-stock 对外 contract 和 payload shape 不变
- 增加结构守卫，防止 ask-stock assembly owner 回流到 `stock_analysis.py`

## 第七轮完成定义

- ask-stock assembly owner 完成外提
- `stock_analysis.py` 不再直接承载大段 ask-stock response assembly helper
- focused verification 通过

## 第八轮目标

第八轮继续处理 ask-stock 主链中剩余的 execution/research sequencing owner：

### Round 8A: Ask-Stock Execution Orchestration Extraction

- 将 ask-stock 的 execution stage 与 research-resolution sequencing 迁移到独立模块
- 保持 `stock_analysis.py` 作为组合根与 facade，不让其继续直接承载 sequencing 细节

### Round 8B: Monkeypatch Compatibility Preservation

- 对测试与现有调用习惯中依赖的 monkeypatch 路径保持兼容
- 避免因为初始化时冻结绑定而破坏 `_run_react_executor`、`_build_research_bridge` 或模块级 `build_dashboard_projection` 的替换能力

## 第八轮完成定义

- ask-stock execution orchestration owner 完成外提
- facade 兼容行为保持稳定
- focused verification 通过

## 第九轮目标

第九轮继续把 ask-stock 主链压薄到 request shaping 层：

### Round 9A: Ask-Stock Request-Context Extraction

- 将 ask-stock 的 request-context 构造与上下文预处理迁移到独立模块
- 保持该层与 execution owner、assembly owner 分离，形成清晰的三段式 pipeline

### Round 9B: Boundary Locking

- 保留 facade forwarding，避免不必要的调用方回归
- 增加结构守卫，锁定 request-context owner 的导入与组合根位置

## 第九轮完成定义

- ask-stock request-context owner 完成外提
- ask-stock pipeline 分层更清晰
- focused verification 通过

## 第十轮目标

第十轮把重心从 ask-stock 主链切回 stock-analysis 的共享通用 mechanics：

### Round 10A: Shared Tool-Response Extraction

- 将多个公共工具接口复用的 response builder helpers 迁移到独立模块
- 保持 public tool entrypoints 留在 `StockAnalysisService`，只下沉共享 mechanics

### Round 10B: Non-Ask-Stock Boundary Locking

- 用结构守卫锁定 shared tool-response builder 已外提的事实
- 避免公共工具的 payload shaping 重新在 facade 文件中膨胀

## 第十轮完成定义

- shared tool-response builder owner 完成外提
- 非 ask-stock 公共 mechanics 继续收口
- focused verification 通过

## 第十一轮目标

第十一轮继续处理非 ask-stock 工具入口共用的 runtime support：

### Round 11A: Shared Query/Window Runtime Extraction

- 将 query context、price-window、frame tail/date window 等共享 helper 迁移到独立模块
- 保持 `StockAnalysisService` 中 public tool entrypoints 不变，仅把复用的 runtime support 下沉为 owner service

### Round 11B: Facade And Guardrail Tightening

- 在 `stock_analysis.py` 中保留 facade wrapper，避免影响既有调用路径
- 增加结构守卫与直接 helper 单测，锁定 shared runtime support 已外提

## 第十一轮完成定义

- shared query/window runtime owner 完成外提
- facade 转发与行为兼容保持稳定
- focused verification 通过

## 第十二轮目标

第十二轮继续处理非 ask-stock 工具入口共用的 projection mechanics：

### Round 12A: Shared Snapshot/Indicator Projection Extraction

- 将 snapshot projection、indicator projection 相关共享 helper 迁移到独立模块
- 保持 `_project_snapshot_fields` 仍留在既有 `batch service` owner，不重复切分已稳定的边界
- 保持 public tool entrypoints 留在 `StockAnalysisService`

### Round 12B: Facade And Compatibility Preservation

- 在 `stock_analysis.py` 中保留 `_build_snapshot_projection` 与 `_build_indicator_projection` facade wrapper
- 对 `_build_batch_analysis_context` / `_view_from_snapshot` 采用运行时转发，避免破坏 monkeypatch 兼容性

## 第十二轮完成定义

- shared snapshot/indicator projection owner 完成外提
- facade 转发与 monkeypatch 兼容保持稳定
- focused verification 通过

## 第十三轮目标

第十三轮继续处理 execution trace 与工具结果摘要共用的 presentation mechanics：

### Round 13A: Shared Observation Formatting Extraction

- 将 observation envelope、section projection、tool observation summary 等共享 helper 迁移到独立模块
- 保持 public tool entrypoints 与 execution 主链不变，只下沉共享 observation formatting mechanics

### Round 13B: Facade And Trace Compatibility Preservation

- 在 `stock_analysis.py` 中保留 `_observation_envelope`、`_observation_section`、`_project_tool_observation`、`_summarize_observation` facade wrapper
- 保持 execution trace payload shape 与现有 tests 兼容

## 第十三轮完成定义

- shared observation formatting owner 完成外提
- facade 转发与 execution trace 兼容保持稳定
- focused verification 通过

## 第十四轮目标

第十四轮继续处理 LLM react 相关的 prompt/presentation mechanics：

### Round 14A: Shared Prompt And Tool-Presentation Extraction

- 将 tool definition filtering、default thought、LLM assistant/tool message builder、system/user prompt 等共享 helper 迁移到独立模块
- 保持 react orchestration、strategy store 与 public tool entrypoints 不变，只下沉共享 prompt/presentation mechanics

### Round 14B: Facade And React Compatibility Preservation

- 在 `stock_analysis.py` 中保留 `_stock_tool_definitions`、`_default_thought`、`_build_llm_assistant_tool_message`、`_build_llm_tool_result_message`、`_stock_system_prompt`、`_build_stock_user_prompt` facade wrapper
- 保持 react tool-call payload shape 与现有 tests 兼容

## 第十四轮完成定义

- shared prompt/presentation owner 完成外提
- facade 转发与 react payload 兼容保持稳定
- focused verification 通过

## 第十五轮目标

第十五轮继续处理 planning/react path 共用的 parsing/rendering mechanics：

### Round 15A: Shared Parsing And Template Rendering Extraction

- 将 tool-arg parsing 与 template-rendering helper 迁移到独立模块
- 保持 `_build_plan`、LLM react execution loop 与 public tool entrypoints 不变，只下沉共享 parsing/rendering mechanics

### Round 15B: Facade And Behavior Preservation

- 在 `stock_analysis.py` 中保留 `_render_template_args`、`_parse_tool_args` facade wrapper
- 保持模板替换、数字字符串转整型、空输入回退与异常行为兼容

## 第十五轮完成定义

- shared parsing/rendering owner 完成外提
- facade 转发与 planning/react helper 行为兼容保持稳定
- focused verification 通过

## 第十六轮目标

第十六轮开始处理 `StockAnalysisService._init_research_services` 的组合根初始化密度：

### Round 16A: Support-Service Composition Extraction

- 先只提取非 research owner 的 support-service wiring bundle
- 保持 `ResearchResolutionService`、`StockAnalysisResearchBridgeService`、`AskStockExecutionOrchestrationService` 仍在 `stock_analysis.py` 组合根内构造

### Round 16B: Dynamic Provider Preservation

- 对 request-context、prompt、observation、projection、runtime support 等已提取 owner 继续使用 runtime-forwarded provider/lambda
- 避免在组合根抽离时冻结 monkeypatch-sensitive path

## 第十六轮完成定义

- support-service wiring bundle 完成外提
- `_init_research_services` 明显瘦身且 research-side runtime behavior 保持稳定
- focused verification 通过

## 第十七轮目标

第十七轮继续处理 `StockAnalysisService._init_research_services` 中剩余的 research-side 组合根密度：

### Round 17A: Research-Service Composition Extraction

- 提取 `ResearchResolutionService` 与 `StockAnalysisResearchBridgeService` 的构造到独立 composition bundle
- 保持 `AskStockExecutionOrchestrationService(...)` 仍在 `stock_analysis.py` 中内联构造

### Round 17B: Runtime Callback Boundary Preservation

- 不改变 `_run_react_executor`、`_build_research_bridge`、`build_dashboard_projection` 的运行期动态转发方式
- 避免把 ask-stock execution orchestration 的 monkeypatch-sensitive callback 在 bundle 中提前冻结

## 第十七轮完成定义

- research-side owner composition bundle 完成外提
- `_init_research_services` 继续瘦身且 execution behavior 保持稳定
- focused verification 通过

## 第六轮目标

第六轮处理 stock-analysis 最后一个明显的 service-level 过渡残留：

### Round 6A: Legacy Resolution Retirement

- 评估 `_LegacyResearchResolutionService` 是否仍有运行时或测试期真实入口
- 若无真实入口，则直接删除该 legacy class，而不是继续保留过渡壳
- 保持 `ResearchResolutionService` facade export 指向提取模块不变

### Round 6B: Guardrail Tightening

- 结构守卫明确禁止 `stock_analysis.py` 再次出现 `_LegacyResearchResolutionService`
- focused verification 继续覆盖 ask-stock 契约、架构守卫、类型与 lint

## 第六轮完成定义

- `_LegacyResearchResolutionService` 被安全移除
- `stock_analysis.py` 不再保留 legacy resolution 过渡块
- focused verification 通过
