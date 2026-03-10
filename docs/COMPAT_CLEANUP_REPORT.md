# 兼容层清理报告

## 1. 当前兼容层现状

项目已经完成大部分结构收口，但仍保留少量兼容入口，目的是：

- 不打断已有使用方式
- 让旧命令与新结构共存
- 降低文档与脚本迁移成本

## 2. 当前保留的兼容层

### 2.1 根目录兼容启动壳

- `commander.py` -> 转发到 `app.commander`
- `train.py` -> 转发到 `app.train`
- `web_server.py` -> 转发到 `app.web_server`
- `llm_gateway.py` -> 转发到 `app.llm_gateway`
- `llm_router.py` -> 转发到 `app.llm_router`

### 2.2 独立工具脚本

- `allocator.py`
- `leaderboard.py`
- `sync_data.py`

其中前两个仍有明确用途；`sync_data.py` 更偏历史辅助脚本。

## 3. 已经完成的清理收益

- 真实实现不再散落在仓库根目录
- 文档可以明确指向 `app/`、`market_data/`、`invest/`、`brain/`
- 测试能围绕主实现收敛
- Web / CLI / train 的职责边界更清楚

## 4. 当前兼容策略

### 4.1 对外命令保持稳定

用户仍可继续使用：

- `python3 commander.py ...`
- `python3 train.py ...`
- `python3 web_server.py ...`

同时推荐逐步过渡到：

- `invest-commander`
- `invest-train`
- `python3 -m market_data`

### 4.2 对内开发以新路径为准

开发时应优先引用：

- `app.commander`
- `app.train`
- `app.web_server`
- `app.llm_gateway`
- `app.llm_router`

而不是继续围绕根目录壳文件开发。

## 5. 剩余清理建议

- 若确认无外部依赖，可未来评估是否移除 `sync_data.py`
- 若 UI 完整接管某些脚本能力，可逐步弱化独立脚本入口
- 但在当前阶段，根目录兼容壳仍然是合理保留项
