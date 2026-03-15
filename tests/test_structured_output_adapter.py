from brain.structured_output import StructuredOutputAdapter


def test_training_plan_execute_normalization_extracts_latest_result_summary():
    adapter = StructuredOutputAdapter()

    payload = adapter.normalize_payload(
        tool_name="invest_training_plan_execute",
        payload={
            "status": "completed",
            "plan_id": "plan_1",
            "training_lab": {"run": {"run_id": "run_1"}},
            "results": [
                {"status": "ok", "cycle_id": 6, "return_pct": 1.2},
                {
                    "status": "ok",
                    "cycle_id": 7,
                    "return_pct": 0.8,
                    "benchmark_passed": True,
                    "promotion_record": {"status": "candidate_generated", "gate_status": "awaiting_gate"},
                    "lineage_record": {"lineage_status": "candidate_pending"},
                },
            ],
        },
    )

    assert payload["result_overview"]["result_count"] == 2
    assert payload["result_overview"]["ok_result_count"] == 2
    assert payload["latest_result"]["cycle_id"] == 7
    assert payload["latest_result"]["promotion_record"]["gate_status"] == "awaiting_gate"
    assert payload["latest_result"]["lineage_record"]["lineage_status"] == "candidate_pending"


def test_config_update_normalization_keeps_pending_and_updated_sections_stable():
    adapter = StructuredOutputAdapter()

    payload = adapter.normalize_payload(
        tool_name="invest_control_plane_update",
        payload={
            "status": "confirmation_required",
            "pending": {"patch": {"llm": {"bindings": {"controller.main": "foo"}}}},
            "restart_required": True,
        },
    )

    assert payload["status"] == "confirmation_required"
    assert payload["pending"]["patch"]["llm"]["bindings"]["controller.main"] == "foo"
    assert payload["updated"] == []
    assert payload["control_plane"] == {}


def test_training_plan_execute_normalization_marks_fallback_for_invalid_shapes():
    adapter = StructuredOutputAdapter()

    payload = adapter.normalize_payload(
        tool_name="invest_training_plan_execute",
        payload={
            "status": 1,
            "plan_id": None,
            "run_id": None,
            "results": {"bad": "shape"},
            "summary": [],
        },
    )

    assert payload["status"] == "1"
    assert payload["plan_id"] == ""
    assert payload["run_id"] == ""
    assert payload["results"] == []
    assert payload["structured_output"]["status"] in {"repaired", "fallback"}
    assert payload["structured_output"]["repair_attempted"] is True


def test_training_read_side_normalization_briefs_latest_result_and_governance():
    adapter = StructuredOutputAdapter()

    runs = adapter.normalize_payload(
        tool_name="invest_training_runs_list",
        payload={
            "status": "ok",
            "count": "1",
            "items": [
                {
                    "run_id": "run_1",
                    "plan_id": "plan_1",
                    "status": "completed",
                    "payload": {
                        "results": [
                            {"cycle_id": 1, "status": "ok", "return_pct": 0.2},
                            {
                                "cycle_id": 2,
                                "status": "ok",
                                "return_pct": 0.6,
                                "benchmark_passed": True,
                                "promotion_record": {"gate_status": "awaiting_gate"},
                                "lineage_record": {"lineage_status": "candidate_pending"},
                            },
                        ]
                    },
                }
            ],
        },
    )
    evaluations = adapter.normalize_payload(
        tool_name="invest_training_evaluations_list",
        payload={
            "status": "ok",
            "count": "1",
            "items": [
                {
                    "run_id": "run_1",
                    "plan_id": "plan_1",
                    "status": "completed",
                    "assessment": {
                        "success_count": "2",
                        "no_data_count": "0",
                        "error_count": "0",
                        "avg_return_pct": 0.4,
                        "benchmark_pass_rate": 0.5,
                        "latest_result": {"cycle_id": 2, "status": "ok"},
                    },
                    "promotion": {
                        "verdict": "rejected",
                        "passed": False,
                        "research_feedback": {"passed": False},
                    },
                    "governance_metrics": {"candidate_pending_count": 1},
                    "realism_summary": {"avg_holding_days": 4.5},
                }
            ],
        },
    )
    summary = adapter.normalize_payload(
        tool_name="invest_training_lab_summary",
        payload={
            "status": "ok",
            "plan_count": "1",
            "run_count": "1",
            "evaluation_count": "1",
            "latest_plans": [{"plan_id": "plan_1", "status": "planned"}],
            "latest_runs": runs["items"],
            "latest_evaluations": evaluations["items"],
        },
    )

    assert runs["count"] == 1
    assert runs["items"][0]["latest_result"]["cycle_id"] == 2
    assert runs["items"][0]["latest_result"]["promotion_record"]["gate_status"] == "awaiting_gate"
    assert runs["structured_output"]["status"] == "repaired"
    assert evaluations["items"][0]["assessment"]["success_count"] == 2
    assert evaluations["items"][0]["governance_metrics"]["candidate_pending_count"] == 1
    assert evaluations["structured_output"]["status"] == "repaired"
    assert summary["plan_count"] == 1
    assert summary["latest_run_summary"]["latest_result"]["cycle_id"] == 2
    assert summary["latest_evaluation_summary"]["assessment"]["latest_result"]["cycle_id"] == 2
    assert summary["structured_output"]["status"] == "validated"


def test_config_and_agent_prompt_normalization_backfills_legacy_shapes():
    adapter = StructuredOutputAdapter()

    control_plane = adapter.normalize_payload(
        tool_name="invest_control_plane_get",
        payload={
            "config": {"llm": {"bindings": {"controller.main": "gpt-5"}}},
            "config_path": "/tmp/control_plane.json",
        },
    )
    runtime_paths = adapter.normalize_payload(
        tool_name="invest_runtime_paths_get",
        payload={
            "config": {
                "workspace": "/tmp/workspace",
                "training_output_dir": "/tmp/training",
                "runtime_loaded": True,
            }
        },
    )
    prompts = adapter.normalize_payload(
        tool_name="invest_agent_prompts_list",
        payload={
            "status": "ok",
            "items": [
                {"name": "researcher", "system_prompt": "focus on evidence"},
                {"name": "reviewer", "role": "critic", "system_prompt": "challenge assumptions"},
            ],
        },
    )
    prompt_update = adapter.normalize_payload(
        tool_name="invest_agent_prompts_update",
        payload={"status": "ok", "updated": ["researcher"], "restart_required": True},
    )

    assert control_plane["control_plane"]["llm"]["bindings"]["controller.main"] == "gpt-5"
    assert control_plane["config_path"] == "/tmp/control_plane.json"
    assert runtime_paths["paths"]["workspace"] == "/tmp/workspace"
    assert runtime_paths["runtime_loaded"] is True
    assert [item["name"] for item in prompts["configs"]] == ["researcher", "reviewer"]
    assert prompts["configs"][1]["role"] == "critic"
    assert prompt_update["updated"] == ["researcher"]
    assert prompt_update["restart_required"] is True
