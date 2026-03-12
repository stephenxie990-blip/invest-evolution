# Findings & Decisions

## Requirements
- 基于真实仓库分析“训练链”和“问股链”当前割裂点
- 给出统一研究引擎的研究方案，而非泛泛概念图
- 方案必须满足：同一语义、同一因果、同一验证闭环
- 方案需要兼顾短期可落地与长期架构收敛

## Research Findings
- 训练主链核心在 `app/train.py` 的 `SelfLearningController`，负责数据加载、模型处理、选股会议、模拟交易、评估、优化与冻结。
- 问股主链核心在 `app/stock_analysis.py` 的 `StockAnalysisService`，由 `app/commander.py` 的 `ask_stock()` 直接透传调用。
- 目前运行时层已有统一意识，但偏“工件与目录统一”，例如 `docs/RUNTIME_STATE_DESIGN.md` 主要统一了 `runtime/` 输出、锁、工件与状态文件。
- 数据层统一已基本完成，`docs/DATA_LAYER_UNIFICATION_REPORT.md` 明确训练与 Web 已共享同一离线库、同一 `DataManager` / repository 口径。
- 因此当前真正未统一的重点，不在数据源，而在“研究语义层 / 状态层 / 归因闭环”。

## Decisions
| Decision | Rationale |
|----------|-----------|
| 先验证现有代码中是否已有近似状态对象 | 可能存在可复用基础，不必从零命名/建模 |
| 重点梳理执行链，而不是只看目录名 | 真正割裂点通常在调用路径和状态读写处 |
| 将已有文档作为旁证，不直接等同于现状事实 | 文档可能超前或滞后于代码 |

## Issues Encountered
| Issue | Resolution |
|-------|------------|
| 环境无 `python` | 使用 shell 原生命令与 `python3` 作为后备 |
| 大文件一次性读取被截断 | 改为按关键方法与行段分块读取 |

## Resources
- `/Users/zhangsan/.agents/skills/agentic-engineering/SKILL.md`
- `/Users/zhangsan/.agents/skills/pi-planning-with-files/SKILL.md`
- `/Users/zhangsan/Desktop/投资进化系统v1.0/app/train.py`
- `/Users/zhangsan/Desktop/投资进化系统v1.0/app/stock_analysis.py`
- `/Users/zhangsan/Desktop/投资进化系统v1.0/docs/RUNTIME_STATE_DESIGN.md`
- `/Users/zhangsan/Desktop/投资进化系统v1.0/docs/DATA_LAYER_UNIFICATION_REPORT.md`

## Visual/Browser Findings
- 当前尚未使用浏览器/图片工具
