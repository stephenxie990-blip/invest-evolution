# P0 修复计划（2026-03-10）

## 目标
- 修复兼容壳、训练契约和 Hunter 恢复签名漂移。
- 将旧训练页正式降级为过渡壳层，并暴露新前端契约入口。
- 跑完 P0 定向回归与全量回归。

## 阶段
- [x] 建立实施控制板与验收口径
- [x] 修复根模块兼容壳
- [x] 收口训练数据契约回退
- [x] 兼容 Hunter 恢复签名
- [x] 调整旧页测试职责并补壳层入口
- [x] 跑回归并复盘余项

## 验收
- `import web_server` 支持 monkeypatch 私有状态。
- 训练相关旧 monkeypatch 仍然可用。
- 旧页只承担壳层职责，新前端入口与契约链接可见。
- 全量 pytest 通过或余项已明确归档。

## 最新验证
- `./.venv/bin/python -m pytest tests/test_web_server_runtime_and_bool.py tests/test_train_cycle.py tests/test_train_event_stream.py tests/test_hunter_code_normalization.py tests/test_train_ui_semantics.py -q` 通过。
- `./.venv/bin/python -m pytest -q` 全量通过。
- `./.venv/bin/python -m compileall app brain invest market_data config web_server.py train.py commander.py` 通过。

## Wave 3
- [x] 生成前端契约派生物（JSON Schema / OpenAPI）
- [x] 暴露契约派生端点并纳入目录索引
- [x] 将 Agent 观测语义迁移为 API 契约测试
- [x] 在前端事件流层增加契约校验
- [x] 跑全量回归与构建验证

## 训练数据加载性能专项（2026-03-11）
- [x] 复现真实库数据加载基线并拆分阶段耗时
- [x] 对比多个候选方案（跳过补数 / 窗口裁剪 / 按股切片 / 向量化增强）
- [x] 落地最优方案并补回归测试
- [x] 在真实数据库与单周期 dry-run 上复测确认
