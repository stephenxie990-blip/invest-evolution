# Contract Freeze Gate

## 目的
- 把 Commander 统一自然语言总线的协议冻结为可重复执行的发布门。
- 保证以下三层同时无漂移：
  - 运行协议
  - transcript / audit snapshot
  - frontend contract / schema / openapi 导出

## 推荐命令
```bash
# 快速门：契约漂移 + 协议/黄金测试
invest-freeze-gate --mode quick

# 完整门：快速门 + 全量后端回归
invest-freeze-gate --mode full

# 只看将执行什么
invest-freeze-gate --mode full --list
```

## 实际执行内容
1. `python3 scripts/generate_frontend_contract_derivatives.py --check`
2. focused protocol regression
3. full regression suite（仅 `--mode full`）

## 通过标准
- contract drift 为 0
- transcript / golden / schema / contract 测试通过
- Commander / ask_stock / runtime / web API 回归通过

## 失败处理
- 若失败在 contract drift：先执行 `invest-refresh-contracts`，确认变更是否是预期协议升级。
- 若失败在 focused protocol regression：优先检查 schema、golden、shared builder。
- 若失败在 full regression：回滚最近协议层改动，或修正对应域 workflow。
