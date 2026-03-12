# 后端清理总清单（2026-03-12）

> 原则：本轮以后端收口为主，不再为历史兼容单独保留代码。  
> 但仍保留系统鲁棒性所需的 deterministic fallback / offline fallback，不把“容错”误删成“兼容”。

## 当前阶段判断

后端已经进入收口后半程，但还没达到“彻底干净、可封箱”的状态。  
现在的主要残留已经不是训练/问股主链本身，而是：

1. `brain/` 与 `app/commander.py` 中的 tool alias / compat 文案
2. `app/train.py` 中已经失效的历史兼容调用桥
3. `app/stock_analysis.py` / `invest/research/*` 中的 `legacy dashboard` / `legacy_signals` 桥接层
4. `app/web_server.py` 中尚未彻底退出的 legacy shell / 双轨 rollout 语义
5. `config/control_plane.py` / `config/control_plane.yaml` 中带 `legacy_*` 命名的默认绑定

## P0：立即可删

### 1. Commander tool alias
- [x] 删除 `invest_status`
- [x] 删除 alias 说明常量与测试
- [x] 更新 Commander prompt，不再提 backward alias

### 2. ReviewMeeting prompt alias
- [x] 删除 `_REVIEW_COMMANDER_SYSTEM`
- [x] 统一改用 `_REVIEW_DECISION_SYSTEM`
- [x] 更新相关测试

### 3. Train compat shim
- [x] 删除 `app/train.py::_call_with_compatible_signature`
- [x] 直接调用统一后的 `DataManager` 接口
- [x] 用训练/数据相关测试兜底

## P1：后端主链继续清仓

### 4. `ask_stock` legacy dashboard
- [x] 删除 `legacy_yaml_dashboard` 命名
- [x] 删除 `_build_dashboard_fallback_projection()` 对 legacy dashboard builder 的依赖
- [x] 统一改为 canonical fallback renderer
- [x] 更新 `tests/test_ask_stock_model_bridge.py`

### 5. `legacy_signals` compat 镜像
- [x] 删除 `snapshot.feature_snapshot["legacy_signals"]`
- [x] hypothesis / renderer / ask payload 仅消费 canonical `metadata` / `factor_values`
- [x] 删除 compat 相关测试断言

### 6. Web legacy shell / rollout
- [x] 删除 `web_ui_shell_mode=legacy` 并行壳语义
- [x] 删除 `frontend_canary_enabled` / query-param canary 开关
- [x] 删除人类前端 UI 资产与前端工作区
- [x] `/legacy` 与 `/app` 不再提供 UI，仅保留 410 tombstone 提示，引导到 `/api/chat` / `/api/status` / `/api/events`

## P2：后端结构整理

### 7. `web_server.py` 瘦身
- [x] 抽取 `status` / `lab status` / `runtime not ready` 统一 responder
- [x] 抽取 detail-mode 解析 helper
- [ ] 将 `web_server.py` 压缩为 thin adapter
  - 已新增共享 `app/runtime_artifact_reader.py`，把 artifact 路径解析与安全读盘从 `web_server.py` / `commander_observability.py` 中抽离
  - `web_server.py` 当前剩余的主要“厚度”已集中在 memory detail 展开与 web 壳双轨切换，不再是状态类路由的重复胶水代码

### 8. `commander.py` 瘦身
- [ ] 盘点超大函数与 prompt 拼装块
- [ ] 把 training lab / runtime inspection / stock analysis 相关响应拼装拆到 service/helper
- [ ] 删除历史兼容提示语

### 9. Control plane legacy naming
- [x] 将 `legacy_default` / `legacy_fast` / `legacy_deep` 重命名为中性命名
- [x] 删除 `legacy_model_setting_from_control_plane(...)`
- [x] 更新示例配置与测试

## P3：死代码与废弃面

### 10. 死代码扫描
- [ ] 扫描只在测试中被引用、运行时已无入口的 helper / 常量 / route
- [ ] 删除只为兼容保留的注释、说明、空壳分支

### 11. 边界守卫
- [ ] 继续确保 `历史归档区/`、`项目收口备份0312/` 不进入运行时 import 面
- [ ] 补强结构守卫，防止归档/备份代码重新被引用

## 退出门

满足以下条件后，可切到前端彻底重构：

- [x] `legacy dashboard` / `legacy_signals` 已从问股主链删除
- [x] `invest_status` 等 backward alias 已删除
- [x] `train.py` compat shim 已删除
- [x] `web_server.py` 不再承担前后端双轨切换兼容逻辑
- [x] 全量 `pytest` 通过
- [x] 真实训练与真实问股各复跑一次通过
