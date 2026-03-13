# Findings & Decisions

## Requirements
- 用户接受五阶段顺序，希望继续细化为更务实的实施方案。
- 方案需要强调可执行性，而不是再次做宏观研究。
- 每个阶段都要说明接入点、边界、风险和验收标准。
- 方案应优先复用现有项目骨架，而不是引入大规模架构替换。

## Research Findings
- 当前系统的 LLM 组件绑定和配置解析入口在 `config/control_plane.py`，适合挂接结构化输出与 feature flags。
- 当前系统已能从 runtime contract 生成 JSON Schema / OpenAPI，入口在 `app/runtime_contract_tools.py`，适合作为结构化输出和校验规则的统一来源。
- `BrainRuntime` 已具备工具 schema 校验、tool-calling loop、risk level 判断、task bus 和 protocol envelope，主入口在 `brain/runtime.py`。
- 高风险写操作已经有 confirmation gate，如 `update_control_plane`、`update_evolution_config`、`trigger_data_download`、多轮真实训练，主要实现位于 `app/commander.py`。
- 训练计划创建与执行已是清晰的实验工件流：`create_training_plan` 写入 artifact，`execute_training_plan` 执行并记录 run/evaluation，适合承接 PySR 与未来 workflow 抽象。
- `freeze_gate` 已经有 contract drift check 与 focused/full regression 分层，适合将新能力以 gate 的形式稳态纳入。
- 当前依赖较轻，仅有 `litellm`、`flask`、数据源库等，尚未引入 `pydantic`、`instructor`、`guardrails`、`e2b`、`temporal`、`pysr` 等依赖。

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| 把 `Instructor` 当作结构化输出层，而不是替换现有 gateway | 能最小侵入地增强关键 intent 的输出可靠性 |
| 把 `Guardrails` 放在高风险写操作之前，而不是全局包裹所有请求 | 更贴合当前 confirmation gate 和 mutating workflow 设计 |
| 把 `PySR` 放进 training lab / research 支线 | 它更像研究引擎，不是 agent orchestration 组件 |
| 把 `E2B` 设计成可选执行后端 | 当前系统本质是本地单进程 runtime，隔离执行应当按需启用 |
| 把 `Temporal` 放到最后，并先抽象 workflow 接口 | 直接引入会影响运行模型，需要先确认真实痛点和边界 |

## Issues Encountered
| Issue | Resolution |
|-------|------------|
| `python` 不在 PATH，无法直接运行 skill 脚本 | 使用 `python3` 重新执行 |
| 根目录没有本轮规划文件，已有同名文件只存在于备份目录 | 在项目根目录新建本轮专用 planning files |

## Resources
- `/Users/zhangsan/Desktop/投资进化系统v1.0/config/control_plane.py`
- `/Users/zhangsan/Desktop/投资进化系统v1.0/app/runtime_contract_tools.py`
- `/Users/zhangsan/Desktop/投资进化系统v1.0/brain/runtime.py`
- `/Users/zhangsan/Desktop/投资进化系统v1.0/brain/tools.py`
- `/Users/zhangsan/Desktop/投资进化系统v1.0/app/commander.py`
- `/Users/zhangsan/Desktop/投资进化系统v1.0/app/llm_gateway.py`
- `/Users/zhangsan/Desktop/投资进化系统v1.0/app/stock_analysis.py`
- `/Users/zhangsan/Desktop/投资进化系统v1.0/app/freeze_gate.py`
- `/Users/zhangsan/Desktop/投资进化系统v1.0/pyproject.toml`
- `/Users/zhangsan/Desktop/投资进化系统v1.0/docs/plans/AGENT_FOUNDATION_PHASED_IMPLEMENTATION_PLAN_20260313.md`

## Visual/Browser Findings
- 本轮未使用浏览器或图片工具；信息来源主要是本地代码和上一轮已核实的官方资料。
