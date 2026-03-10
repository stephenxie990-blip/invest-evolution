# Mock Policy

## Policy

- mock 仅用于 smoke/demo/test。
- 正式训练默认走真实数据链路。
- 当离线与在线数据源都不可用时，系统默认报错，不再隐式回退 mock。

## Explicit Entry Points

- `python3 train.py --mock`
- `python3 commander.py train-once --mock`
- `POST /api/train { "mock": true }`
- `POST /api/lab/training/plans { "mock": true }`

## Audit Expectations

训练结果必须可见：

- `requested_data_mode`
- `effective_data_mode`
- `llm_mode`
- `degraded`
- `degrade_reason`
