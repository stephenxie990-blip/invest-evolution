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
