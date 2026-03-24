from __future__ import annotations

import json

from flask import Flask

from invest_evolution.interfaces.web.presentation import (
    build_display_payload,
    build_data_source_unavailable_response,
    build_json_error_response,
    build_json_status_error_response,
    build_not_found_response,
    display_limit_response_or_400,
    display_list_response_or_400,
    display_response_or_400,
    display_response_or_404,
    parse_bool_field_or_400,
    parse_detail_or_400,
    parse_int_field_or_400,
    parse_json_object_or_400,
    parse_limit_or_400,
    parse_optional_query_int_or_400,
    parse_query_bool_or_400,
    parse_query_int_or_400,
    parse_required_str_field_or_400,
    parse_str_list_field_or_400,
    parse_value_or_400,
    parse_view_limit_or_400,
    parse_view_or_400,
    parsed_request_response_or_400,
    read_object_field,
    read_query_str,
    read_query_str_list,
    read_str_field,
    runtime_display_response_or_400,
    runtime_items_response_or_400,
    runtime_optional_detail_response_or_404,
    runtime_or_fallback_display_response_or_400,
    runtime_or_fallback_payload_response,
)
from invest_evolution.market_data import DataSourceUnavailableError


def test_json_error_helpers_share_response_templates():
    app = Flask(__name__)

    with app.app_context():
        error_response = build_json_error_response("boom", 500, request_id="req_1")
        status_error_response = build_json_status_error_response(
            "bad input",
            400,
            field="mock",
        )
        missing_response = build_not_found_response(
            FileNotFoundError("artifact missing")
        )

    assert error_response.status_code == 500
    assert error_response.get_json() == {"error": "boom", "request_id": "req_1"}
    assert status_error_response.status_code == 400
    assert status_error_response.get_json() == {"status": "error", "error": "bad input", "field": "mock"}
    assert missing_response.status_code == 404
    assert missing_response.get_json() == {"error": "artifact missing"}


def test_data_source_unavailable_helper_returns_standard_payload():
    app = Flask(__name__)
    error = DataSourceUnavailableError(
        "market data offline",
        cutoff_date="20260321",
        stock_count=25,
        min_history_days=120,
        requested_data_mode="live",
        available_sources={"offline": False, "online": False, "mock": True},
        offline_diagnostics={"source": "akshare"},
        suggestions=["switch to mock mode"],
        allow_mock_fallback=True,
    )

    with app.app_context():
        canonical_response = build_data_source_unavailable_response(error)

    assert canonical_response.status_code == 503
    assert canonical_response.get_json()["error"] == "market data offline"
    assert canonical_response.get_json()["offline_diagnostics"]["source"] == "akshare"


def test_parse_helpers_wrap_value_errors_as_400():
    app = Flask(__name__)

    with app.app_context():
        parsed_value = parse_value_or_400(lambda: 7)
        invalid_view = parse_view_or_400(lambda: (_ for _ in ()).throw(ValueError("view must be one of: json, human")))
        invalid_limit = parse_limit_or_400(
            lambda **kwargs: (_ for _ in ()).throw(ValueError(f"limit invalid for {kwargs['maximum']}")),
            default=20,
            maximum=200,
        )

    assert parsed_value == 7
    assert not isinstance(invalid_view, str)
    assert invalid_view.status_code == 400
    assert invalid_view.get_json()["error"] == "view must be one of: json, human"
    assert not isinstance(invalid_limit, int)
    assert invalid_limit.status_code == 400
    assert invalid_limit.get_json()["error"] == "limit invalid for 200"


def test_parse_detail_helper_uses_canonical_strict_signature():
    app = Flask(__name__)

    def strict_detail_parser(value, *, default="fast", field_name="detail", strict=False):
        del default, strict
        if value not in {"fast", "slow"}:
            raise ValueError(f"{field_name} must be one of: fast, slow")
        return value

    with app.app_context():
        strict_value = parse_detail_or_400(strict_detail_parser, raw_value="slow")
        invalid_value = parse_detail_or_400(strict_detail_parser, raw_value="weird")

    assert strict_value == "slow"
    assert not isinstance(invalid_value, str)
    assert invalid_value.status_code == 400
    assert invalid_value.get_json()["error"] == "detail must be one of: fast, slow"


def test_request_body_helper_normalizes_empty_and_rejects_non_object_json():
    app = Flask(__name__)

    with app.test_request_context(
        "/api/train",
        method="POST",
        data="[]",
        content_type="application/json",
    ):
        invalid_body = parse_json_object_or_400(force=True)

    with app.test_request_context("/api/data/download", method="POST"):
        empty_body = parse_json_object_or_400(silent=True)

    with app.test_request_context(
        "/api/chat",
        method="POST",
        data="{",
        content_type="application/json",
    ):
        invalid_json = parse_json_object_or_400(force=True)

    assert not isinstance(invalid_body, dict)
    assert invalid_body.status_code == 400
    assert invalid_body.get_json()["error"] == "request body must be a JSON object, got list"
    assert not isinstance(invalid_json, dict)
    assert invalid_json.status_code == 400
    assert empty_body == {}


def test_body_field_helpers_normalize_common_request_shapes():
    app = Flask(__name__)

    def parse_bool(value, field_name):
        if isinstance(value, bool):
            return value
        raise ValueError(f"{field_name} must be a boolean")

    payload = {
        "message": "  hello world  ",
        "rounds": "500",
        "mock": True,
        "tags": "growth, quality,  ",
        "protocol": {"mode": "strict"},
    }

    with app.app_context():
        message = parse_required_str_field_or_400(payload, "message")
        rounds = parse_int_field_or_400(payload, "rounds", default=1, minimum=1, maximum=100)
        mock = parse_bool_field_or_400(payload, "mock", parse_bool, default=False)
        tags = parse_str_list_field_or_400(payload, "tags", default=[])

    assert message == "hello world"
    assert rounds == 100
    assert mock is True
    assert tags == ["growth", "quality"]
    assert read_str_field(payload, "message", strip=False) == "  hello world  "
    assert read_object_field(payload, "protocol") == {"mode": "strict"}


def test_query_helpers_normalize_strings_lists_and_optional_ints():
    app = Flask(__name__)

    with app.test_request_context(
        "/api/governance/preview?cutoff_date=20260321&allowed_manager_ids=momentum&allowed_manager_ids=mean_reversion&stock_count=25"
    ):
        cutoff_date = read_query_str("cutoff_date", empty_as_none=True)
        manager_ids = read_query_str_list("allowed_manager_ids")
        stock_count = parse_optional_query_int_or_400("stock_count")

    with app.test_request_context("/api/data/capital_flow?codes=sh.600001, sz.000001"):
        codes = read_query_str_list("codes")

    with app.test_request_context("/api/governance/preview?stock_count=bad"):
        invalid_stock_count = parse_optional_query_int_or_400("stock_count")

    assert cutoff_date == "20260321"
    assert manager_ids == ["momentum", "mean_reversion"]
    assert stock_count == 25
    assert codes == ["sh.600001", "sz.000001"]
    assert invalid_stock_count is not None
    assert not isinstance(invalid_stock_count, int)
    assert invalid_stock_count.status_code == 400
    assert invalid_stock_count.get_json()["error"] == "stock_count must be an integer"


def test_parse_view_limit_helper_composes_shared_display_query_contract():
    app = Flask(__name__)

    with app.test_request_context("/api/events/summary?view=human&limit=55"):
        parsed_display = parse_view_limit_or_400(
            lambda: "human",
            lambda **kwargs: 55,
            default_limit=50,
            maximum_limit=200,
        )

    with app.test_request_context("/api/events/summary?view=xml"):
        invalid_display = parse_view_limit_or_400(
            lambda: (_ for _ in ()).throw(ValueError("view must be one of: json, human")),
            lambda **kwargs: kwargs["default"],
            default_limit=50,
            maximum_limit=200,
        )

    assert parsed_display == ("human", 55)
    assert not isinstance(invalid_display, tuple)
    assert invalid_display.status_code == 400
    assert invalid_display.get_json()["error"] == "view must be one of: json, human"


def test_parsed_request_response_helper_unifies_dict_parse_and_route_error_paths():
    app = Flask(__name__)

    with app.app_context():
        success_response = parsed_request_response_or_400(
            parse_request=lambda: {"rounds": 3, "mock": False},
            respond=lambda parsed_request: build_json_error_response(
                f"parsed:{parsed_request['rounds']}",
                202,
            ),
        )
        invalid_response = parsed_request_response_or_400(
            parse_request=lambda: build_json_error_response("rounds must be an integer", 400),
            respond=lambda parsed_request: build_json_error_response(
                f"parsed:{parsed_request['rounds']}",
                202,
            ),
        )

    assert success_response.status_code == 202
    assert success_response.get_json() == {"error": "parsed:3"}
    assert invalid_response.status_code == 400
    assert invalid_response.get_json() == {"error": "rounds must be an integer"}


def test_query_bool_and_bounded_int_helpers_preserve_400_semantics():
    app = Flask(__name__)

    def parse_bool(value, field_name):
        normalized = str(value).strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no", ""}:
            return False
        raise ValueError(f"{field_name} must be a boolean")

    def parse_int(value, field_name, minimum=None, maximum=None):
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field_name} must be an integer") from exc
        if minimum is not None:
            parsed = max(minimum, parsed)
        if maximum is not None:
            parsed = min(maximum, parsed)
        return parsed

    with app.test_request_context("/api/allocator?top_n=8&refresh=true"):
        top_n = parse_query_int_or_400("top_n", parse_int, default=3, minimum=1, maximum=4)
        refresh = parse_query_bool_or_400("refresh", parse_bool, default=False)

    with app.test_request_context("/api/allocator?top_n=bad&refresh=nope"):
        invalid_top_n = parse_query_int_or_400("top_n", parse_int, default=3, minimum=1, maximum=4)
        invalid_refresh = parse_query_bool_or_400("refresh", parse_bool, default=False)

    assert top_n == 4
    assert refresh is True
    assert not isinstance(invalid_top_n, int)
    assert invalid_top_n.status_code == 400
    assert invalid_top_n.get_json()["error"] == "top_n must be an integer"
    assert not isinstance(invalid_refresh, bool)
    assert invalid_refresh.status_code == 400
    assert invalid_refresh.get_json()["error"] == "refresh must be a boolean"


def test_shared_display_responder_helpers_unify_runtime_and_fallback_templates():
    app = Flask(__name__)

    def respond_with_display(payload, *, status_code=200, view="json"):
        response = build_json_error_response("unexpected", 500)
        response.set_data(b"")
        response = app.response_class(
            response=build_json_error_response("placeholder", 500).get_data(),
            status=status_code,
            mimetype="application/json",
        )
        del response
        from flask import jsonify

        result = jsonify({"payload": payload, "view": view})
        result.status_code = status_code
        return result

    with app.test_request_context("/api/demo?view=human"):
        runtime_detail = runtime_display_response_or_400(
            load_runtime=lambda: {"runtime": True},
            request_view_arg=lambda: "human",
            respond_with_display=respond_with_display,
            fetch=lambda runtime: {"kind": "detail", "runtime": runtime["runtime"]},
        )

    with app.test_request_context("/api/demo?view=json"):
        runtime_items = runtime_items_response_or_400(
            load_runtime=lambda: {"runtime": True},
            request_view_arg=lambda: "json",
            respond_with_display=respond_with_display,
            fetch_items=lambda runtime: [{"runtime": runtime["runtime"]}],
        )

    with app.test_request_context("/api/demo?view=json"):
        missing_detail = runtime_optional_detail_response_or_404(
            load_runtime=lambda: {"runtime": True},
            request_view_arg=lambda: "json",
            respond_with_display=respond_with_display,
            fetch=lambda runtime: None,
            not_found_message="memory record not found",
        )

    with app.test_request_context("/api/demo?view=json"):
        fallback_payload = runtime_or_fallback_display_response_or_400(
            get_runtime=lambda: None,
            request_view_arg=lambda: "json",
            respond_with_display=respond_with_display,
            runtime_fetch=lambda runtime: runtime,
            fallback_fetch=lambda: {"kind": "fallback"},
        )

    with app.test_request_context("/api/demo?view=human&limit=3"):
        facade_limit = display_limit_response_or_400(
            request_view_arg=lambda: "human",
            parse_limit_arg=lambda **kwargs: 7,
            respond_with_display=respond_with_display,
            fetch=lambda limit: {"limit": limit},
        )
        facade_list = display_list_response_or_400(
            request_view_arg=lambda: "human",
            parse_limit_arg=lambda **kwargs: 3,
            respond_with_display=respond_with_display,
            fetch=lambda limit: {"count": limit},
        )
        facade_detail = display_response_or_400(
            request_view_arg=lambda: "human",
            respond_with_display=respond_with_display,
            fetch=lambda: {"kind": "facade"},
        )

    with app.test_request_context("/api/demo?view=json"):
        facade_missing = display_response_or_404(
            request_view_arg=lambda: "json",
            respond_with_display=respond_with_display,
            fetch=lambda: (_ for _ in ()).throw(FileNotFoundError("artifact missing")),
        )

    assert runtime_detail.status_code == 200
    assert runtime_detail.get_json() == {"payload": {"kind": "detail", "runtime": True}, "view": "human"}
    assert runtime_items.get_json() == {"payload": {"count": 1, "items": [{"runtime": True}]}, "view": "json"}
    assert missing_detail.status_code == 404
    assert missing_detail.get_json()["error"] == "memory record not found"
    assert fallback_payload.get_json() == {"payload": {"kind": "fallback"}, "view": "json"}
    assert facade_limit.get_json() == {"payload": {"limit": 7}, "view": "human"}
    assert facade_list.get_json() == {"payload": {"count": 3}, "view": "human"}
    assert facade_detail.get_json() == {"payload": {"kind": "facade"}, "view": "human"}
    assert facade_missing.status_code == 404
    assert facade_missing.get_json()["error"] == "artifact missing"


def test_runtime_display_response_short_circuits_route_error_before_view_parse():
    route_error = ("runtime not ready", 503)

    result = runtime_display_response_or_400(
        load_runtime=lambda: route_error,
        request_view_arg=lambda: (_ for _ in ()).throw(AssertionError("view should not be parsed")),
        respond_with_display=lambda payload, *, view="json": {"payload": payload, "view": view},
        fetch=lambda runtime: runtime,
    )

    assert result == route_error


def test_build_display_payload_flattens_legacy_snapshot_wrapper_to_canonical_surface():
    app = Flask(__name__)

    with app.app_context():
        payload = build_display_payload(
            {
                "mode": "quick",
                "snapshot": {
                    "status": "ok",
                    "detail_mode": "fast",
                    "protocol": {"schema_version": "bounded.v1", "domain": "runtime", "operation": "status"},
                    "task_bus": {"schema_version": "taskbus.v1"},
                },
            }
        )

    assert payload["mode"] == "quick"
    assert payload["status"] == "ok"
    assert payload["detail_mode"] == "fast"
    assert payload["protocol"]["domain"] == "runtime"
    assert payload["task_bus"]["schema_version"] == "taskbus.v1"
    assert "snapshot" not in payload


def test_build_display_payload_reads_nested_latest_result_and_runtime_governance_fallback():
    app = Flask(__name__)

    with app.app_context():
        payload = build_display_payload(
            {
                "payload": {
                    "results": [
                        {
                            "cycle_id": 7,
                            "status": "ok",
                            "realism_metrics": {
                                "trade_record_count": 2,
                                "avg_trade_amount": 12000,
                                "avg_turnover_rate": 0.35,
                                "avg_holding_days": 4.5,
                                "selection_mode": "top_k",
                            },
                        }
                    ]
                },
                "governance_metrics": {
                    "runtime": {
                        "structured_output": {"validated_count": 4, "repaired_count": 1, "fallback_count": 2},
                        "guardrails": {"block_count": 1, "last_reason_codes": ["schema_mismatch"]},
                    }
                },
            }
        )

    cards = {card["id"]: card for card in payload["display"]["cards"]}
    realism_rows = {row["label"]: row["value"] for row in cards["execution_realism"]["rows"]}
    runtime_rows = {row["label"]: row["value"] for row in cards["runtime_governance"]["rows"]}

    assert cards["execution_realism"]["summary"] == "2 条交易记录"
    assert realism_rows["avg_holding_days"] == "4.5"
    assert cards["runtime_governance"]["tone"] == "warning"
    assert runtime_rows["guardrail_blocks"] == "1"
    assert runtime_rows["fallback"] == "2"
    assert cards["runtime_governance"]["badges"] == ["schema_mismatch"]


def test_runtime_or_fallback_payload_response_unifies_contract_and_json_payload_paths():
    app = Flask(__name__)

    def build_contract_payload_response(payload, **kwargs):
        return app.response_class(
            response=json.dumps(
                {"kind": "contract", "payload": payload, "extra": kwargs}
            ),
            status=kwargs.get("status_code", 200),
            mimetype="application/json",
        )

    with app.test_request_context("/api/data/demo"):
        runtime_payload = runtime_or_fallback_payload_response(
            get_runtime=lambda: {"runtime": True},
            build_contract_payload_response=build_contract_payload_response,
            runtime_fetch=lambda runtime: {"source": "runtime", "flag": runtime["runtime"]},
            fallback_fetch=lambda: {"source": "fallback"},
        )

    with app.test_request_context("/api/data/demo"):
        fallback_payload = runtime_or_fallback_payload_response(
            get_runtime=lambda: None,
            build_contract_payload_response=build_contract_payload_response,
            runtime_fetch=lambda runtime: {"source": "runtime", "flag": runtime["runtime"]},
            fallback_fetch=lambda: {"source": "fallback"},
        )

    assert runtime_payload.status_code == 200
    assert runtime_payload.get_json() == {
        "kind": "contract",
        "payload": {"source": "runtime", "flag": True},
        "extra": {},
    }
    assert fallback_payload.status_code == 200
    assert fallback_payload.get_json() == {
        "kind": "contract",
        "payload": {"source": "fallback"},
        "extra": {},
    }
