# Training Center Backend Checklist

## Page Surfaces

- [x] `train-shell-contract-card`
- [x] `train-routing-card`
- [x] `train-agent-overview-card`
- [x] `train-timeline-card`
- [x] `train-speech-card-panel`
- [x] `train-strategy-diff-card`
- [x] `train-result-card`

## Core APIs

- [x] `POST /api/train`
- [x] `GET /api/investment-models`
- [x] `GET /api/model-routing/preview`
- [x] `GET /api/contracts/frontend-v1`
- [x] `GET /api/contracts/frontend-v1/openapi`
- [x] `GET /api/events`

## Key Fields for Frontend

- `routing.enabled`
- `routing.mode`
- `routing.allowed_models`
- `routing.last_decision`
- `results[].routing_decision`
- `results[].model_name`
- `results[].requested_data_mode`
- `results[].effective_data_mode`
- `results[].llm_mode`

## SSE Events

- [x] `agent_status`
- [x] `module_log`
- [x] `meeting_speech`
- [x] `cycle_complete`
- [x] `routing_decided`
- [x] `model_switch_applied`
- [x] `model_switch_blocked`

## Error Flows

- [x] runtime unavailable → `503`
- [x] data source unavailable → `503 dataSourceUnavailableError`
- [x] invalid query/body → `400 flatError`
