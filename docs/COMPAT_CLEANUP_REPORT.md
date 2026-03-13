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

### 2.2 独立工具脚本

- `scripts/cli/allocator.py`
- `scripts/cli/leaderboard.py`

二者仍有明确用途。

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
- `scripts/cli/allocator.py`
- `scripts/cli/leaderboard.py`

而不是继续围绕根目录壳文件开发。

## 5. 剩余清理建议

- `allocator` / `leaderboard` 已并入 `scripts/cli/`，后续如有更多独立工具脚本，也应优先进入该目录
- 根层 `llm_gateway.py` / `llm_router.py` 已移除，后续若新增 LLM 相关能力，应继续只维护 `app/` 内正式实现
