import json
from pathlib import Path

import jsonschema
import pytest
import invest_evolution.interfaces.web.server as web_server
from invest_evolution.application.runtime_contracts import (
    CONTRACT_PATH,
    OPENAPI_PATH,
    SCHEMA_PATH,
    build_contract_documents,
    check_contract_documents,
    load_contract_source,
)


def test_runtime_contract_endpoint_returns_machine_readable_contract():
    client = web_server.app.test_client()

    res = client.get("/api/contracts/runtime-v2")

    assert res.status_code == 200
    payload = res.get_json()
    assert payload["contract_id"] == "runtime-v2"
    assert payload["runtime_entrypoint"] == "/api/chat"
    assert "removed_web_shell_mount" not in payload
    assert (
        payload["components"]["schemas"]["responseFeedback"]["properties"]["summary"][
            "type"
        ]
        == "string"
    )
    assert (
        payload["components"]["schemas"]["responseNextAction"]["properties"]["kind"][
            "type"
        ]
        == "string"
    )
    assert (
        payload["components"]["schemas"]["responseEnvelope"]["properties"]["feedback"][
            "$ref"
        ]
        == "#/components/schemas/responseFeedback"
    )
    assert (
        payload["components"]["schemas"]["statusWrappedConfig"]["properties"][
            "feedback"
        ]["$ref"]
        == "#/components/schemas/responseFeedback"
    )
    assert payload["components"]["schemas"]["agentConfigList"]["properties"]["status"][
        "enum"
    ] == ["ok"]
    assert (
        payload["components"]["schemas"]["agentConfigList"]["properties"]["configs"][
            "items"
        ]["properties"]["system_prompt"]["type"]
        == "string"
    )
    assert (
        payload["components"]["schemas"]["agentConfigMutation"]["properties"][
            "restart_required"
        ]["type"]
        == "boolean"
    )
    control_plane_read_properties = payload["components"]["schemas"][
        "controlPlaneRead"
    ]["properties"]
    assert control_plane_read_properties["llm_resolution"]["type"] == "object"
    assert "config_path" not in control_plane_read_properties
    assert "local_override_path" not in control_plane_read_properties
    assert "audit_log_path" not in control_plane_read_properties
    assert "snapshot_dir" not in control_plane_read_properties
    assert payload["components"]["schemas"]["runtimePathsConfig"]["required"] == [
        "training_output_dir",
        "artifact_log_dir",
    ]
    assert (
        payload["components"]["schemas"]["chatReply"]["properties"]["next_action"][
            "$ref"
        ]
        == "#/components/schemas/responseNextAction"
    )
    assert (
        payload["components"]["schemas"]["chatReply"]["properties"]["session_key"][
            "type"
        ]
        == "string"
    )
    assert (
        payload["components"]["schemas"]["chatReply"]["properties"]["chat_id"]["type"]
        == "string"
    )
    assert (
        payload["components"]["schemas"]["chatReply"]["properties"]["request_id"][
            "type"
        ]
        == "string"
    )
    assert payload["components"]["schemas"]["chatStreamConnected"]["properties"][
        "status"
    ]["enum"] == ["connected"]
    assert (
        payload["components"]["schemas"]["chatStreamRuntimeEvent"]["properties"][
            "stream_kind"
        ]["type"]
        == "string"
    )
    assert (
        payload["components"]["schemas"]["chatStreamSummary"]["properties"][
            "event_count"
        ]["type"]
        == "integer"
    )
    assert (
        payload["components"]["schemas"]["chatStreamSummary"]["properties"][
            "highest_risk_summary"
        ]["type"]
        == "string"
    )
    assert (
        payload["components"]["schemas"]["chatStreamSummary"]["properties"][
            "confirmation_summary"
        ]["type"]
        == "string"
    )
    assert (
        payload["components"]["schemas"]["chatStreamSummary"]["properties"][
            "last_display_text"
        ]["type"]
        == "string"
    )
    assert (
        payload["components"]["schemas"]["chatStreamReply"]["properties"][
            "stream_summary"
        ]["$ref"]
        == "#/components/schemas/chatStreamSummary"
    )
    assert payload["components"]["schemas"]["chatStreamDone"]["properties"]["status"][
        "enum"
    ] == ["completed"]
    assert (
        payload["components"]["schemas"]["chatStreamError"]["properties"]["error"][
            "type"
        ]
        == "string"
    )
    assert (
        payload["transcript_snapshots"]["schema_version"] == "transcript_snapshots.v1"
    )
    assert "ask_stock" in payload["transcript_snapshots"]["examples"]
    assert (
        payload["transcript_snapshots"]["examples"]["ask_stock"]["entrypoint"]["domain"]
        == "stock"
    )
    assert any(
        endpoint["path"] == "/api/events/summary" and endpoint["method"] == "GET"
        for endpoint in payload["endpoints"]
    )
    assert any(
        endpoint["path"] == "/api/control_plane" and endpoint["method"] == "GET"
        for endpoint in payload["endpoints"]
    )
    assert any(
        endpoint["path"] == "/api/control_plane" and endpoint["method"] == "POST"
        for endpoint in payload["endpoints"]
    )
    assert any(
        endpoint["path"] == "/api/contracts/runtime-v2/schema"
        and endpoint["method"] == "GET"
        for endpoint in payload["endpoints"]
    )
    assert any(
        endpoint["path"] == "/api/contracts/runtime-v2/openapi"
        and endpoint["method"] == "GET"
        for endpoint in payload["endpoints"]
    )
    assert all(
        endpoint["path"] != "/api/lab/status/quick" for endpoint in payload["endpoints"]
    )
    assert all(
        endpoint["path"] != "/api/lab/status/deep" for endpoint in payload["endpoints"]
    )
    assert all(endpoint["path"] != "/api/train" for endpoint in payload["endpoints"])
    assert all(
        endpoint["path"] != "/api/leaderboard" for endpoint in payload["endpoints"]
    )
    assert all(
        endpoint["path"] != "/api/allocator" for endpoint in payload["endpoints"]
    )
    assert all(
        endpoint["path"] != "/api/governance/preview"
        for endpoint in payload["endpoints"]
    )
    assert all(endpoint["path"] != "/api/managers" for endpoint in payload["endpoints"])
    assert all(
        endpoint["path"] != "/api/playbooks" for endpoint in payload["endpoints"]
    )
    assert all(
        endpoint["path"] != "/api/playbooks/reload" for endpoint in payload["endpoints"]
    )
    assert all(endpoint["path"] != "/api/cron" for endpoint in payload["endpoints"])
    assert all(
        endpoint["path"] != "/api/cron/{job_id}" for endpoint in payload["endpoints"]
    )
    assert all(endpoint["path"] != "/api/memory" for endpoint in payload["endpoints"])
    assert all(
        endpoint["path"] != "/api/memory/{record_id}"
        for endpoint in payload["endpoints"]
    )
    assert all(
        endpoint["path"] != "/api/data/capital_flow"
        for endpoint in payload["endpoints"]
    )
    assert all(
        endpoint["path"] != "/api/data/dragon_tiger"
        for endpoint in payload["endpoints"]
    )
    assert all(
        endpoint["path"] != "/api/data/intraday_60m"
        for endpoint in payload["endpoints"]
    )
    assert "#/components/sse_schemas/governanceDecision" in payload["sse"]["event_refs"]
    training_plan_create_endpoint = next(
        endpoint
        for endpoint in payload["endpoints"]
        if endpoint["path"] == "/api/lab/training/plans"
        and endpoint["method"] == "POST"
    )
    assert (
        "manager_scope"
        in payload["components"]["schemas"]["trainingPlan"]["properties"]
    )
    assert (
        "model_scope"
        not in payload["components"]["schemas"]["trainingPlan"]["properties"]
    )
    assert (
        "manager_scope" in training_plan_create_endpoint["request_body"]["properties"]
    )
    assert (
        "model_scope" not in training_plan_create_endpoint["request_body"]["properties"]
    )
    chat_endpoint = next(
        endpoint
        for endpoint in payload["endpoints"]
        if endpoint["path"] == "/api/chat" and endpoint["method"] == "POST"
    )
    assert (
        chat_endpoint["request_body"]["properties"]["session_key"]["type"] == "string"
    )
    assert chat_endpoint["request_body"]["properties"]["chat_id"]["type"] == "string"
    assert chat_endpoint["request_body"]["properties"]["request_id"]["type"] == "string"
    chat_stream_endpoint = next(
        endpoint
        for endpoint in payload["endpoints"]
        if endpoint["path"] == "/api/chat/stream" and endpoint["method"] == "POST"
    )
    assert chat_stream_endpoint["success"]["content_type"] == "text/event-stream"
    assert chat_stream_endpoint["sse_event_refs"] == [
        "#/components/schemas/chatStreamConnected",
        "#/components/schemas/chatStreamRuntimeEvent",
        "#/components/schemas/chatStreamSummary",
        "#/components/schemas/chatStreamReply",
        "#/components/schemas/chatStreamDone",
        "#/components/schemas/chatStreamError",
    ]
    cycle_complete = payload["components"]["sse_schemas"]["cycleComplete"]["data"][
        "properties"
    ]
    assert "requested_data_mode" in cycle_complete
    assert "effective_data_mode" in cycle_complete
    assert "llm_mode" in cycle_complete
    agent_prompt_update = next(
        endpoint
        for endpoint in payload["endpoints"]
        if endpoint["path"] == "/api/agent_prompts" and endpoint["method"] == "POST"
    )
    assert agent_prompt_update["request_body"]["required"] == ["name", "system_prompt"]
    assert "llm_model" not in agent_prompt_update["request_body"]["properties"]
    runtime_paths_get = next(
        endpoint
        for endpoint in payload["endpoints"]
        if endpoint["path"] == "/api/runtime_paths" and endpoint["method"] == "GET"
    )
    assert runtime_paths_get["success"]["body_ref"] == "runtimePathsWrappedConfig"
    runtime_paths_update = next(
        endpoint
        for endpoint in payload["endpoints"]
        if endpoint["path"] == "/api/runtime_paths" and endpoint["method"] == "POST"
    )
    assert set(runtime_paths_update["request_body"]["properties"]) == {
        "training_output_dir",
        "artifact_log_dir",
    }
    control_plane_update = next(
        endpoint
        for endpoint in payload["endpoints"]
        if endpoint["path"] == "/api/control_plane" and endpoint["method"] == "POST"
    )
    assert set(control_plane_update["request_body"]["properties"]) == {"llm", "data"}
    assert control_plane_update["request_body"]["additionalProperties"] is False
    assert set(
        control_plane_update["request_body"]["properties"]["llm"]["properties"]
    ) == {"providers", "models", "bindings"}
    assert (
        control_plane_update["request_body"]["properties"]["llm"][
            "additionalProperties"
        ]
        is False
    )
    assert (
        control_plane_update["request_body"]["properties"]["data"]["properties"][
            "runtime_policy"
        ]["additionalProperties"]
        is False
    )
    data_download = next(
        endpoint
        for endpoint in payload["endpoints"]
        if endpoint["path"] == "/api/data/download" and endpoint["method"] == "POST"
    )
    assert data_download["request_body"]["properties"]["confirm"]["type"] == "boolean"
    assert (
        "GET/POST /api/control_plane"
        in payload["preferred_runtime_flows"]["runtime_configuration"]
    )
    runtime_not_required = {
        ("GET", "/api/status"),
        ("GET", "/api/events/summary"),
        ("GET", "/api/lab/training/plans"),
        ("GET", "/api/lab/training/plans/{plan_id}"),
        ("GET", "/api/lab/training/runs"),
        ("GET", "/api/lab/training/runs/{run_id}"),
        ("GET", "/api/lab/training/evaluations"),
        ("GET", "/api/lab/training/evaluations/{run_id}"),
        ("GET", "/api/agent_prompts"),
        ("POST", "/api/agent_prompts"),
        ("GET", "/api/runtime_paths"),
        ("POST", "/api/runtime_paths"),
        ("GET", "/api/evolution_config"),
        ("POST", "/api/evolution_config"),
        ("GET", "/api/control_plane"),
        ("POST", "/api/control_plane"),
        ("GET", "/api/data/status"),
        ("POST", "/api/data/download"),
    }
    for method, path in runtime_not_required:
        endpoint = next(
            item
            for item in payload["endpoints"]
            if item["method"] == method and item["path"] == path
        )
        assert endpoint["runtime_required"] is False
    assert payload["sse"]["path"] == "/api/events"


def test_runtime_contract_schema_endpoint_returns_json_schema():
    client = web_server.app.test_client()

    res = client.get("/api/contracts/runtime-v2/schema")

    assert res.status_code == 200
    payload = res.get_json()
    assert payload["$schema"].startswith("https://json-schema.org/")
    assert payload["title"] == "Runtime API Contract V2"


def test_runtime_contract_openapi_endpoint_returns_openapi_document():
    client = web_server.app.test_client()

    res = client.get("/api/contracts/runtime-v2/openapi")

    assert res.status_code == 200
    payload = res.get_json()
    assert payload["openapi"] == "3.1.0"
    assert "/api/events" in payload["paths"]
    assert "/api/events/summary" in payload["paths"]
    assert "/api/chat/stream" in payload["paths"]
    assert "/api/control_plane" in payload["paths"]
    assert "/api/contracts/runtime-v2/schema" in payload["paths"]
    assert "/api/contracts/runtime-v2/openapi" in payload["paths"]
    assert "/api/contracts" not in payload["paths"]
    assert "/api/lab/status/quick" not in payload["paths"]
    assert "/api/lab/status/deep" not in payload["paths"]
    assert (
        payload["x-transcript-snapshots"]["schema_version"] == "transcript_snapshots.v1"
    )
    assert "runtime_status" in payload["x-transcript-snapshots"]["examples"]
    assert "/api/train" not in payload["paths"]
    assert "/api/leaderboard" not in payload["paths"]
    assert "/api/allocator" not in payload["paths"]
    assert "/api/governance/preview" not in payload["paths"]
    assert "/api/managers" not in payload["paths"]
    assert "/api/playbooks" not in payload["paths"]
    assert "/api/playbooks/reload" not in payload["paths"]
    assert "/api/cron" not in payload["paths"]
    assert "/api/cron/{job_id}" not in payload["paths"]
    assert "/api/memory" not in payload["paths"]
    assert "/api/memory/{record_id}" not in payload["paths"]
    assert "/api/data/capital_flow" not in payload["paths"]
    assert "/api/data/dragon_tiger" not in payload["paths"]
    assert "/api/data/intraday_60m" not in payload["paths"]
    training_plan_post_properties = payload["paths"]["/api/lab/training/plans"]["post"][
        "requestBody"
    ]["content"]["application/json"]["schema"]["properties"]
    assert "manager_scope" in training_plan_post_properties
    assert "model_scope" not in training_plan_post_properties
    assert payload["paths"]["/api/chat/stream"]["post"]["x-sse-event-refs"] == [
        "#/components/schemas/chatStreamConnected",
        "#/components/schemas/chatStreamRuntimeEvent",
        "#/components/schemas/chatStreamSummary",
        "#/components/schemas/chatStreamReply",
        "#/components/schemas/chatStreamDone",
        "#/components/schemas/chatStreamError",
    ]
    agent_prompt_request = payload["paths"]["/api/agent_prompts"]["post"][
        "requestBody"
    ]["content"]["application/json"]["schema"]
    assert agent_prompt_request["required"] == ["name", "system_prompt"]
    assert "llm_model" not in agent_prompt_request["properties"]
    runtime_paths_request = payload["paths"]["/api/runtime_paths"]["post"][
        "requestBody"
    ]["content"]["application/json"]["schema"]
    assert set(runtime_paths_request["properties"]) == {
        "training_output_dir",
        "artifact_log_dir",
    }
    control_plane_request = payload["paths"]["/api/control_plane"]["post"][
        "requestBody"
    ]["content"]["application/json"]["schema"]
    assert set(control_plane_request["properties"]) == {"llm", "data"}
    assert control_plane_request["additionalProperties"] is False
    assert set(control_plane_request["properties"]["llm"]["properties"]) == {
        "providers",
        "models",
        "bindings",
    }
    assert control_plane_request["properties"]["llm"]["additionalProperties"] is False
    assert (
        control_plane_request["properties"]["data"]["properties"]["runtime_policy"][
            "additionalProperties"
        ]
        is False
    )
    data_download_request = payload["paths"]["/api/data/download"]["post"][
        "requestBody"
    ]["content"]["application/json"]["schema"]
    assert data_download_request["properties"]["confirm"]["type"] == "boolean"
    assert (
        payload["paths"]["/api/control_plane"]["get"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]["$ref"]
        == "#/components/schemas/controlPlaneRead"
    )
    assert payload["paths"]["/api/chat"]["post"]["x-runtime-required"] is True
    assert payload["paths"]["/api/chat/stream"]["post"]["x-runtime-required"] is True
    assert (
        payload["paths"]["/api/lab/training/plans"]["post"]["x-runtime-required"]
        is True
    )
    assert (
        payload["paths"]["/api/lab/training/plans/{plan_id}/execute"]["post"][
            "x-runtime-required"
        ]
        is True
    )
    assert payload["paths"]["/api/status"]["get"]["x-runtime-required"] is False
    assert payload["paths"]["/api/events/summary"]["get"]["x-runtime-required"] is False
    assert (
        payload["paths"]["/api/lab/training/plans"]["get"]["x-runtime-required"]
        is False
    )
    assert (
        payload["paths"]["/api/lab/training/plans/{plan_id}"]["get"][
            "x-runtime-required"
        ]
        is False
    )
    assert (
        payload["paths"]["/api/lab/training/runs"]["get"]["x-runtime-required"] is False
    )
    assert (
        payload["paths"]["/api/lab/training/runs/{run_id}"]["get"]["x-runtime-required"]
        is False
    )
    assert (
        payload["paths"]["/api/lab/training/evaluations"]["get"]["x-runtime-required"]
        is False
    )
    assert (
        payload["paths"]["/api/lab/training/evaluations/{run_id}"]["get"][
            "x-runtime-required"
        ]
        is False
    )
    assert payload["paths"]["/api/agent_prompts"]["get"]["x-runtime-required"] is False
    assert payload["paths"]["/api/agent_prompts"]["post"]["x-runtime-required"] is False
    assert payload["paths"]["/api/runtime_paths"]["get"]["x-runtime-required"] is False
    assert payload["paths"]["/api/runtime_paths"]["post"]["x-runtime-required"] is False
    assert (
        payload["paths"]["/api/evolution_config"]["get"]["x-runtime-required"] is False
    )
    assert (
        payload["paths"]["/api/evolution_config"]["post"]["x-runtime-required"] is False
    )
    assert payload["paths"]["/api/control_plane"]["get"]["x-runtime-required"] is False
    assert payload["paths"]["/api/control_plane"]["post"]["x-runtime-required"] is False
    assert payload["paths"]["/api/data/status"]["get"]["x-runtime-required"] is False
    assert payload["paths"]["/api/data/download"]["post"]["x-runtime-required"] is False


def test_runtime_contract_endpoint_returns_404_when_document_missing(monkeypatch):
    client = web_server.app.test_client()

    def fake_load(document):
        raise FileNotFoundError(document.source_path)

    monkeypatch.setattr(web_server, "load_runtime_contract_document", fake_load)

    res = client.get("/api/contracts/runtime-v2/schema")

    assert res.status_code == 404
    assert res.get_json()["error"] == "runtime contract schema not found"


def test_runtime_contract_endpoint_returns_500_for_invalid_document(monkeypatch):
    client = web_server.app.test_client()

    def fake_load(document):
        raise ValueError("broken contract payload")

    monkeypatch.setattr(web_server, "load_runtime_contract_document", fake_load)

    res = client.get("/api/contracts/runtime-v2/openapi")

    assert res.status_code == 500
    assert res.get_json()["error"] == "broken contract payload"


def test_generated_contract_derivatives_validate_against_main_contract():
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    contract_schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    openapi = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))

    jsonschema.validate(contract, contract_schema)

    assert openapi["info"]["version"] == contract["version"]
    assert (
        openapi["paths"]["/api/events"]["get"]["x-sse-event-refs"]
        == contract["sse"]["event_refs"]
    )
    chat_stream_endpoint = next(
        endpoint
        for endpoint in contract["endpoints"]
        if endpoint["path"] == "/api/chat/stream" and endpoint["method"] == "POST"
    )
    assert (
        openapi["paths"]["/api/chat/stream"]["post"]["x-sse-event-refs"]
        == chat_stream_endpoint["sse_event_refs"]
    )

    for endpoint in contract["endpoints"]:
        path_item = openapi["paths"][endpoint["path"]]
        assert endpoint["method"].lower() in path_item


def test_runtime_contract_removes_legacy_frontend_keys():
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

    assert "frontend_shell_mount" not in contract
    assert "legacy_shell_mount" not in contract
    assert "removed_web_shell_mount" not in contract
    assert "frontend_preferred_flows" not in contract
    assert all(
        "frontend_preferred" not in endpoint for endpoint in contract["endpoints"]
    )


def test_removed_runtime_v1_contract_endpoints_return_404():
    client = web_server.app.test_client()

    for path in (
        "/api/contracts",
        "/api/contracts/runtime-v1",
        "/api/contracts/runtime-v1/schema",
        "/api/contracts/runtime-v1/openapi",
    ):
        res = client.get(path)
        assert res.status_code == 404


def test_runtime_train_policy_load_rejects_external_paths(tmp_path: Path):
    external = tmp_path / "external_config.yaml"
    external.write_text("train: {}", encoding="utf-8")

    from invest_evolution.investment.governance.engine import (
        _load_train_policy_from_runtime_config_ref,
    )

    assert _load_train_policy_from_runtime_config_ref(str(external)) is None


def test_runtime_train_policy_load_logs_warning_on_invalid_yaml(
    tmp_path: Path, monkeypatch, caplog
):
    from invest_evolution.investment.governance import engine as governance_engine

    monkeypatch.setattr(governance_engine, "PROJECT_ROOT", tmp_path)
    runtime_file = tmp_path / "runtime_bad.yaml"
    runtime_file.write_text("train: [", encoding="utf-8")

    with caplog.at_level("WARNING"):
        result = governance_engine._load_train_policy_from_runtime_config_ref(
            str(runtime_file)
        )

    assert result is None
    assert "Failed to load train policy" in caplog.text


def test_runtime_train_policy_load_logs_warning_on_non_mapping_train(
    tmp_path: Path, monkeypatch, caplog
):
    from invest_evolution.investment.governance import engine as governance_engine

    monkeypatch.setattr(governance_engine, "PROJECT_ROOT", tmp_path)
    runtime_file = tmp_path / "runtime_bad_train.yaml"
    runtime_file.write_text("train: [1,2,3]", encoding="utf-8")

    with caplog.at_level("WARNING"):
        result = governance_engine._load_train_policy_from_runtime_config_ref(
            str(runtime_file)
        )

    assert result == {}
    assert "Runtime train policy section must be a mapping" in caplog.text


def test_generated_runtime_contract_artifacts_have_no_drift():
    assert check_contract_documents() == []


def test_generated_runtime_contract_documents_match_repo_files():
    generated = build_contract_documents()
    current = {
        CONTRACT_PATH: json.loads(Path(CONTRACT_PATH).read_text(encoding="utf-8")),
        SCHEMA_PATH: json.loads(Path(SCHEMA_PATH).read_text(encoding="utf-8")),
        OPENAPI_PATH: json.loads(Path(OPENAPI_PATH).read_text(encoding="utf-8")),
    }

    assert generated == current


def test_build_contract_documents_rejects_unresolved_body_refs():
    contract = load_contract_source()
    contract["endpoints"] = [
        *list(contract["endpoints"]),
        {
            "id": "test.invalid_ref",
            "group": "test",
            "method": "GET",
            "path": "/api/test-invalid-ref",
            "summary": "test",
            "runtime_required": False,
            "runtime_preferred": False,
            "replacement": None,
            "query_params": [],
            "path_params": [],
            "request_body": None,
            "success": {"http_status": 200, "body_ref": "missingSchema"},
            "errors": [],
            "latency": "sync",
            "pagination": "none",
            "realtime": False,
            "notes": [],
            "sse_event_refs": [],
        },
    ]

    with pytest.raises(ValueError, match="missingSchema"):
        build_contract_documents(contract)


def test_contract_catalog_endpoint_is_removed():
    client = web_server.app.test_client()

    res = client.get("/api/contracts")

    assert res.status_code == 404


def test_removed_training_lab_status_shortcuts_return_404():
    client = web_server.app.test_client()

    for path in ("/api/lab/status/quick", "/api/lab/status/deep"):
        res = client.get(path)
        assert res.status_code == 404
