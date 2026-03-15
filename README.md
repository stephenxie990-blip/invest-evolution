# Invest Evolution / 投资进化系统

> Agent-first collaboration substrate for controlled decision systems, with investment as the current proving ground.  
> 一个面向 Agent-first 协作的可治理决策底座，投资是当前最完整的验证场。

`Invest Evolution` 不是一个单纯的量化脚本仓库。  
它更像一套让 Agent 在共享数据底座、明确协议和治理边界中参与训练、研究、复盘与运行的协作系统。

## 项目定位 / Positioning

- **Agent-first**：系统首先为 Agent 提供稳定上下文、结构化输入、有限行动空间和可审计输出。
- **Governance-first**：系统把 `routing`、`promotion`、`deployment stage`、`freeze gate` 等治理边界放在能力扩张之前。
- **Investment as a proving ground**：投资是当前最完整的高价值样板场景，但不是这套底层协作范式的全部定义。

## 为什么做这个项目 / Why This Exists

这个项目真正想解决的，不只是“怎么做投资决策”，而是更底层的几个问题：

- 如何让 Agent 成为可控工具，而不是不可预测的参与者
- 如何让人和 Agent 的协作关系可审计、可约束、可回放
- 如何让多个 Agent 围绕同一个事实数据底座形成稳定协作
- 如何让系统持续进化，同时不失去边界、纪律和解释能力

这里的人类更像治理者、授权者、观察者和纠偏者。  
Agent 才是系统里的第一执行单元。

## 这是什么 / What This Is

- 一个面向投资场景的 Agent-first 协作系统
- 一个把训练、研究、运行和治理收进同一条闭环的平台
- 一个持续沉淀实验工件、会议记录、候选晋级与治理判断的运行环境

## 这不是什么 / What This Is Not

- 不是只靠 buzzword 拼起来的 “Agent 外壳”
- 不是已经完成效果收敛的自动赚钱机器
- 不是可以直接放心放权的实盘托管引擎
- 不是以人类点击 UI 为核心交互范式的软件

## 当前能力 / What Works Today

- **训练闭环**：支持数据加载、模型执行、Agent 协作、模拟交易、评估、复盘、优化与治理判断
- **多 Agent 协作**：支持市场判断、选股角色、复盘分析与统一指挥协作
- **统一运行时**：支持 `Commander`、Web/API、事件流、实验记录与状态沉淀
- **治理语义**：支持模型路由、候选晋级、部署阶段区分和冻结门控

## 为什么值得看 / Why It Matters

这个项目最值得关注的地方，不只是“投资结果”，而是它在回答一个更底层的问题：

> 如何让 Agent 在真实、高复杂度、高不确定性的环境里，成为可控、可审计、可进化的工具。

如果这件事成立，那么这套协作方式不只适用于投资，也可以迁移到更多决策辅助场景。

## 5 分钟上手 / Try It Quickly

推荐使用 Python 3.11+ 与虚拟环境：

```bash
git clone https://github.com/stephenxie990-blip/invest-evolution.git invest-evolution
cd invest-evolution
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -e ".[dev]"
```

先用最小链路快速体验：

```bash
# 训练 smoke / demo
python3 commander.py train-once --rounds 1 --mock

# 进入 Commander 交互入口
python3 commander.py run --interactive

# 直接跑一轮 mock 训练
python3 train.py --cycles 1 --mock
```

如果你想继续走真实数据、训练计划、Web/API、配置治理和运行工件，请从下面这些文档继续读。

## 推荐阅读 / Read Next

- `docs/MAIN_FLOW.md`：系统主链路与整体形态
- `docs/AGENT_INTERACTION.md`：为什么第一用户是 Agent，以及角色如何协作
- `docs/TRAINING_FLOW.md`：训练协议、周期工件与治理对象
- `docs/DATA_ACCESS_ARCHITECTURE.md`：事实数据底座与统一访问方式
- `docs/CONFIG_GOVERNANCE.md`：配置边界、控制面与风险约束
- `docs/RUNTIME_STATE_DESIGN.md`：运行态、状态文件与实验沉淀
- `docs/COMPATIBILITY_SURFACE.md`：兼容入口与正式实现边界
- `docs/README.md`：完整文档索引

## 社区与安全 / Community

- `CONTRIBUTING.md`：贡献方式与协作约定
- `SECURITY.md`：安全边界与漏洞反馈方式
