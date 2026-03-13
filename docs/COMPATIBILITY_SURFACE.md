# Compatibility Surface

## 正式实现入口

当前真实实现统一位于：

- `app/commander.py`
- `app/train.py`
- `app/web_server.py`
- `app/llm_gateway.py`
- `app/llm_router.py`

开发、重构、排障和新增功能应优先围绕这些模块进行，而不是围绕根目录壳文件扩展。

## 根目录兼容入口

以下文件当前仍保留，但它们的职责是“兼容启动/兼容导入”，不是新的业务实现层：

- `commander.py`
- `train.py`
- `web_server.py`
- `llm_gateway.py`
- `llm_router.py`

此外，以下两个工具型脚本仍是有意保留的独立入口：

- `scripts/cli/allocator.py`
- `scripts/cli/leaderboard.py`

部署相关入口保留为：

- `wsgi.py`
- `gunicorn.conf.py`

## 当前策略

- 对外：兼容旧命令，避免打断已有调用方式。
- 对内：所有新增实现一律放在包内目录或 `scripts/`，不再向仓库根目录增加业务模块。
- 对测：允许测试通过根目录兼容壳做导入验证，但业务测试应优先覆盖真实实现模块。

## 迁移建议

优先使用：

- `invest-commander`
- `invest-train`
- `python3 -m market_data`

保留兼容用法：

- `python3 commander.py ...`
- `python3 train.py ...`
- `python3 web_server.py ...`

## 第二波清理约束

从本轮开始，根目录 Python 文件集合被视为受控边界；若后续新增新的根目录业务脚本，应先说明为什么不能放入 `app/`、`invest/`、`market_data/`、`brain/` 或 `scripts/`。

## 本轮评估结论

- `allocator.py`、`leaderboard.py` 已完成下沉，正式位置为 `scripts/cli/`
- `llm_gateway.py`、`llm_router.py` 暂保留为根层兼容壳，直到测试与潜在外部调用一起切换完成
