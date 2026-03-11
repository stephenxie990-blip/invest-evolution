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


## 统一控制面专项（2026-03-11）

### 目标
- 建立统一 LLM 控制面与运行时外部数据出口策略。
- 确保修改配置后重启系统即可全局生效。
- 收口训练与 commander 的 LLM 装配语义。

### 阶段
- [x] 建立实施板与兼容边界
- [x] 新增控制面 loader / resolver
- [x] 接入训练链路与 commander 启动装配
- [x] 增加运行时数据出口策略
- [x] 暴露统一控制面 API 并补回归

### 验收
- 统一控制面可表达 provider / model / binding。
- 训练与 commander 启动时统一从控制面解析 LLM。
- 运行时训练默认不再在线抓市场数据。
- 配置变更 API 明确返回 `restart_required=true`。


## 统一控制面专项（2026-03-11）

### 第二阶段
- [x] 将 `/api/evolution_config` 收口为兼容壳
- [x] 将 `/api/agent_configs` 收口为兼容壳
- [x] 新增 `market_data_gateway` 统一外部数据出站层
- [x] 接入 CLI / Web 下载 / 运行时训练链路
- [x] 完成真实训练回归验证


- [x] 第一波：迁移旧前端 Agent 配置到 `/api/agent_prompts` + `/api/control_plane`
- [x] 第一波：删除 `/api/agent_configs` 兼容壳


- [x] 第二波：`/api/evolution_config` 下线 LLM 字段，仅保留训练参数
- [x] 第三波：删除 evolution 兼容翻译层、旧说明与旧契约测试


- [x] 底层瘦身：`EvolutionConfigService` 去除 LLM 输出/编辑职责
- [x] 合约产物与设置页契约改为 control plane 安全面板

## Commander 统一入口升级总方案（2026-03-11）

### 目标
- 将 Commander 升级为唯一人类入口与统一控制平面代理。
- 停止前端继续承担关键控制职责，后续只保留可选展示壳。
- 分阶段补齐 Commander 在配置域、数据域、分析查询域、观测域的覆盖能力。

### 阶段
- [x] 完成功能盘点与差距分析
- [x] 输出总方案、技术路径图、subagent 拆分与验收标准
- [ ] Phase 1：补齐 Commander 管理能力缺口
- [ ] Phase 2：补统一观测面
- [ ] Phase 3：构建自然语言任务层与风险门控
- [ ] Phase 4：前端降级为可选视图
- [ ] Phase 5：问股 / 策略 DSL 增强

### 立即实施建议
- 先做分析查询域 + 配置域 + Lab 列表能力接入 Commander。
- 再做数据域与统一观测层。
- 最后把高频操作提升为自然语言任务模板。



- [x] 启动层瘦身：训练 / commander / LLMCaller 优先使用 control plane 默认绑定

## Commander 统一入口升级实施收官（2026-03-11）

### Phase 0~5 完成情况
- [x] Phase 0：完成现状审计、能力矩阵与升级蓝图
- [x] Phase 1：补齐 Commander 对配置域、训练实验室、模型路由、数据状态的管理能力
- [x] Phase 2：补齐统一观测面（events / diagnostics / memory / runtime summary）
- [x] Phase 3：补齐自然语言入口与风险门控（tool routing / confirm gate / no-LLM fallback）
- [x] Phase 4：Web 降级为兼容壳并复用共享 service，Commander 成为推荐主入口
- [x] Phase 5：补齐问股能力、策略目录与本地 stock analysis 工作流

### 最终验收
- [x] Commander 可覆盖核心训练、配置、观测、数据查询、实验室查询、问股能力
- [x] Web/API 与 Commander 复用共享 service，避免能力分叉
- [x] 全量 pytest 通过
- [x] 通过 Commander `ask` 入口完成 mock 训练
- [x] 通过 Commander `ask` 入口完成真实训练
