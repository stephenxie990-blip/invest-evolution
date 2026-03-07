# Runtime 目录说明

`runtime/` 是项目的运行时产物根目录，存放程序执行过程中自动生成、会持续变化的本地文件。

## 目录用途

- `runtime/outputs/`：训练结果、评估结果、状态快照等输出
- `runtime/logs/`：运行日志
- `runtime/memory/`：持久记忆文件，例如 `commander_memory.jsonl`
- `runtime/sessions/`：Bridge 收件箱/发件箱
- `runtime/workspace/`：Commander 工作区与自动生成的运行辅助文件

## 管理原则

- 该目录默认不提交运行内容到 Git
- 可以安全清理大部分内容，但清理前应确认是否需要保留当前运行现场
- 如需保留目录用途说明，仅追踪本 README
