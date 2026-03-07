# Task Plan: 投资进化系统模块运转审查

## Goal
基于仓库代码，审查 `market_data/`、`invest/`、`brain/`、`commander.py`、`web_server.py` 的职责边界、调用链、数据流和依赖关系，并给出结构化结论。

## Current Phase
Phase 1

## Phases
### Phase 1: Requirements & Discovery
- [x] Understand user intent
- [x] Identify constraints and requirements
- [ ] Document findings in findings.md
- **Status:** in_progress

### Phase 2: Entry Points & Structure
- [ ] Identify startup entrypoints
- [ ] Map core packages and files
- [ ] Document initial dependencies
- **Status:** pending

### Phase 3: Call Graph & Data Flow
- [ ] Trace runtime assembly path
- [ ] Trace business workflows and data handoff
- [ ] Trace API/UI exposure path
- **Status:** pending

### Phase 4: Synthesis & Verification
- [ ] Summarize architecture and dependency directions
- [ ] Validate conclusions against code references
- [ ] Note risks / coupling points
- **Status:** pending

### Phase 5: Delivery
- [ ] Deliver concise review to user
- **Status:** pending

## Key Questions
1. 系统从启动到对外服务的主路径是什么？
2. `brain/` 与 `invest/` 是如何装配成统一 runtime 的？
3. `market_data/` 的事实数据如何进入投资决策链路？
4. API/UI 调用最终落到哪些核心对象与方法？
5. 当前模块依赖是单向分层还是存在回流耦合？

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| 使用文件化审查记录 | 任务涉及多目录多入口，便于跨多次查看保持上下文 |
| 以入口文件为主线追踪 | 先抓 runtime 装配和服务暴露，再下钻到业务流最稳妥 |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| `python` command not found when running skill helper | 1 | Switch to `python3` or shell-native file creation |
