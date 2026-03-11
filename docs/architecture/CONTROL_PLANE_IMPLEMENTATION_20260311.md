# 统一控制面实施板（2026-03-11）

## 背景
- 当前 LLM 配置分散在全局配置、agent JSON、commander 独立配置三处，导致模型切换语义不一致。
- 当前外部市场数据出口分散在运行时数据管理器、在线兜底加载器、数据同步服务中，内部运行环境不够干净。
- 本轮目标接受“修改配置后重启生效”，不强求运行中热更新。

## 总目标
1. 建立单一控制面，统一管理 LLM provider / model / component binding。
2. 让训练主链路与 commander 在启动时统一从控制面装配 LLM。
3. 让运行时市场数据访问默认收口为本地离线，只允许显式同步任务访问外部数据源。
4. Web/API 层提供统一的读取与写入入口，并明确 `restart_required=true`。

## 非目标
- 本轮不做运行中 LLM 热切换。
- 本轮不重写全部数据同步脚本，只收口运行时主链路。
- 本轮不移除旧配置格式；先做兼容桥接。

## 验收要求
### A. LLM 统一控制面
- 存在单一控制面配置文件，包含 providers / models / bindings。
- `SelfLearningController`、`SelectionMeeting`、`ReviewMeeting`、`LLMOptimizer`、`CommanderRuntime` 启动时都从控制面解析 LLM。
- agent 支持优先读取控制面 binding，旧 `agents_config.json.llm_model` 仅作为兼容 fallback。
- 修改控制面后重启系统，新的模型绑定能够稳定生效。

### B. 运行时外部数据收口
- 训练运行时默认只读本地数据库，不再偷偷走在线 baostock 兜底。
- 运行时若本地数据不足，应返回结构化不可用错误，而非自动联网抓取。
- 显式数据同步命令仍可访问 akshare / tushare / baostock。

### C. 配置与可观测性
- 新增统一控制面 GET/POST API。
- POST 成功响应明确包含 `restart_required=true`。
- 配置写入保留审计与快照。

### D. 验证
- 定向 pytest 覆盖：控制面解析、启动装配、运行时外部数据策略、API 持久化。
- 至少跑 1 轮真实训练或启动级验证，确认绑定解析正确且无旧 provider 漂移。

## 工作分解（Subagent Units）
### Unit 1：控制面建模
- 产出：控制面 schema / loader / resolver。
- 风险：与现有 `config`、`agents_config` 兼容。
- Done：可根据 component key 解析出最终 `model/api_key/api_base`。

### Unit 2：训练链路装配
- 产出：controller / meetings / optimizer / agents 接入统一 resolver。
- 风险：caller 生命周期与现有 dry-run、统计逻辑兼容。
- Done：训练主链路不再散落 `LLMCaller()` 默认构造。

### Unit 3：Commander 装配
- 产出：commander brain 读取统一 binding。
- 风险：保留 CLI / env override 优先级。
- Done：重启后 commander 与训练系统共用控制面语义。

### Unit 4：数据出口收口
- 产出：运行时数据策略；离线优先且禁止在线兜底。
- 风险：不能影响显式同步任务。
- Done：训练运行时外部市场数据访问被禁止或显式拒绝。

### Unit 5：配置 API 与回归
- 产出：统一控制面 API、审计、测试。
- 风险：旧前端兼容。
- Done：可读可写控制面，且 POST 明确要求重启。

## Skills 使用铺排
1. `pi-planning-with-files`
   - 用途：维护 `task_plan.md` / `findings.md` / `progress.md` 与实施板。
2. `agentic-engineering`
   - 用途：采用 eval-first 与分单元验证方式推进。
3. `backend-patterns`
   - 用途：设计控制面配置服务、统一 API 与出站边界。

## 实施顺序
1. 先落控制面 loader + resolver。
2. 再接训练链路与 commander 启动装配。
3. 然后加运行时数据出口策略。
4. 最后补 API 与定向回归。

## 回退策略
- 任何组件若未命中控制面 binding，回退到现有 `config` / `agents_config` 逻辑。
- 第一阶段保留旧接口兼容；第二、三阶段已将 `/api/evolution_config` 收口为训练参数接口、移除 `/api/agent_configs`，配置前端改走 `/api/agent_prompts` + `/api/control_plane`。
