# invest/ legacy 清零进度

- 已完成 `shared/` 真实实现迁移，并删除 `invest/shared/legacy.py`。
- 已完成 `agents/`、`meetings/` 真实实现迁移，并删除对应 `legacy.py`。
- 已完成 `trading/`、`evaluation/` 真实实现迁移，并删除对应 `legacy.py`。
- 已完成 `selection/`、`evolution/` 真实实现迁移，并删除 `invest/_optimization_legacy.py`。
- 已修复迁移过程中暴露的少量导入/装饰器缺失问题。
- 已完成全量 `pytest` 回归，当前通过。
