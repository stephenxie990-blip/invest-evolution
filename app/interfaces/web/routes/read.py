"""Route registration for readonly runtime web endpoints."""

from __future__ import annotations

from typing import Any, Callable

from flask import Flask, jsonify, request


ResponseValue = Any
RuntimeGetter = Callable[[], Any]


def _is_error_response(value: Any) -> bool:
    return isinstance(value, tuple)


def _runtime_or_not_ready(
    *,
    get_runtime: RuntimeGetter,
    runtime_not_ready_response: Callable[[], ResponseValue],
) -> Any:
    runtime = get_runtime()
    if runtime is None:
        return runtime_not_ready_response()
    return runtime


def _parse_view_or_400(request_view_arg: Callable[[], str]) -> str | ResponseValue:
    try:
        return request_view_arg()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


def _parse_limit_or_400(
    parse_limit_arg: Callable[..., int],
    *,
    default: int = 20,
    maximum: int = 200,
) -> int | ResponseValue:
    try:
        return parse_limit_arg(default=default, maximum=maximum)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


def _handle_runtime_display_list(
    *,
    get_runtime: RuntimeGetter,
    runtime_not_ready_response: Callable[[], ResponseValue],
    request_view_arg: Callable[[], str],
    parse_limit_arg: Callable[..., int],
    respond_with_display: Callable[..., ResponseValue],
    fetch: Callable[[Any, int], Any],
    default_limit: int = 20,
    maximum_limit: int = 200,
) -> ResponseValue:
    runtime = _runtime_or_not_ready(
        get_runtime=get_runtime,
        runtime_not_ready_response=runtime_not_ready_response,
    )
    if _is_error_response(runtime):
        return runtime

    view = _parse_view_or_400(request_view_arg)
    if _is_error_response(view):
        return view
    limit = _parse_limit_or_400(parse_limit_arg, default=default_limit, maximum=maximum_limit)
    if _is_error_response(limit):
        return limit
    return respond_with_display(fetch(runtime, int(limit)), view=str(view))


def _handle_runtime_display_detail(
    *,
    get_runtime: RuntimeGetter,
    runtime_not_ready_response: Callable[[], ResponseValue],
    request_view_arg: Callable[[], str],
    respond_with_display: Callable[..., ResponseValue],
    fetch: Callable[[Any], Any],
) -> ResponseValue:
    runtime = _runtime_or_not_ready(
        get_runtime=get_runtime,
        runtime_not_ready_response=runtime_not_ready_response,
    )
    if _is_error_response(runtime):
        return runtime

    view = _parse_view_or_400(request_view_arg)
    if _is_error_response(view):
        return view
    return respond_with_display(fetch(runtime), view=str(view))


def register_runtime_read_routes(
    app: Flask,
    *,
    get_runtime: RuntimeGetter,
    parse_detail_mode: Callable[..., str],
    status_response: Callable[..., ResponseValue],
    runtime_not_ready_response: Callable[[], ResponseValue],
    request_view_arg: Callable[[], str],
    parse_limit_arg: Callable[..., int],
    respond_with_display: Callable[..., ResponseValue],
) -> None:
    @app.route("/api/status")
    def api_status():
        detail_mode = parse_detail_mode(request.args.get("detail", "fast"))
        return status_response(detail_mode=detail_mode)

    @app.route("/api/lab/status/quick")
    def api_lab_status_quick():
        return status_response(detail_mode="fast", route_mode="quick")

    @app.route("/api/lab/status/deep")
    def api_lab_status_deep():
        return status_response(detail_mode="slow", route_mode="deep")

    @app.route("/api/events/summary")
    def api_events_summary():
        return _handle_runtime_display_list(
            get_runtime=get_runtime,
            runtime_not_ready_response=runtime_not_ready_response,
            request_view_arg=request_view_arg,
            parse_limit_arg=parse_limit_arg,
            respond_with_display=respond_with_display,
            fetch=lambda runtime, limit: runtime.get_events_summary(limit=limit),
            default_limit=50,
            maximum_limit=200,
        )

    @app.route("/api/lab/training/plans")
    def api_training_plan_list():
        return _handle_runtime_display_list(
            get_runtime=get_runtime,
            runtime_not_ready_response=runtime_not_ready_response,
            request_view_arg=request_view_arg,
            parse_limit_arg=parse_limit_arg,
            respond_with_display=respond_with_display,
            fetch=lambda runtime, limit: runtime.list_training_plans(limit=limit),
        )

    @app.route("/api/lab/training/plans/<plan_id>")
    def api_training_plan_get(plan_id: str):
        runtime = _runtime_or_not_ready(
            get_runtime=get_runtime,
            runtime_not_ready_response=runtime_not_ready_response,
        )
        if _is_error_response(runtime):
            return runtime
        view = _parse_view_or_400(request_view_arg)
        if _is_error_response(view):
            return view
        try:
            return respond_with_display(runtime.get_training_plan(plan_id), view=str(view))
        except FileNotFoundError as exc:
            return jsonify({"error": str(exc)}), 404

    @app.route("/api/lab/training/runs")
    def api_training_run_list():
        return _handle_runtime_display_list(
            get_runtime=get_runtime,
            runtime_not_ready_response=runtime_not_ready_response,
            request_view_arg=request_view_arg,
            parse_limit_arg=parse_limit_arg,
            respond_with_display=respond_with_display,
            fetch=lambda runtime, limit: runtime.list_training_runs(limit=limit),
        )

    @app.route("/api/lab/training/runs/<run_id>")
    def api_training_run_get(run_id: str):
        runtime = _runtime_or_not_ready(
            get_runtime=get_runtime,
            runtime_not_ready_response=runtime_not_ready_response,
        )
        if _is_error_response(runtime):
            return runtime
        view = _parse_view_or_400(request_view_arg)
        if _is_error_response(view):
            return view
        try:
            return respond_with_display(runtime.get_training_run(run_id), view=str(view))
        except FileNotFoundError as exc:
            return jsonify({"error": str(exc)}), 404

    @app.route("/api/lab/training/evaluations")
    def api_training_evaluation_list():
        return _handle_runtime_display_list(
            get_runtime=get_runtime,
            runtime_not_ready_response=runtime_not_ready_response,
            request_view_arg=request_view_arg,
            parse_limit_arg=parse_limit_arg,
            respond_with_display=respond_with_display,
            fetch=lambda runtime, limit: runtime.list_training_evaluations(limit=limit),
        )

    @app.route("/api/lab/training/evaluations/<run_id>")
    def api_training_evaluation_get(run_id: str):
        runtime = _runtime_or_not_ready(
            get_runtime=get_runtime,
            runtime_not_ready_response=runtime_not_ready_response,
        )
        if _is_error_response(runtime):
            return runtime
        view = _parse_view_or_400(request_view_arg)
        if _is_error_response(view):
            return view
        try:
            return respond_with_display(runtime.get_training_evaluation(run_id), view=str(view))
        except FileNotFoundError as exc:
            return jsonify({"error": str(exc)}), 404

    @app.route("/api/investment-models")
    def api_investment_models():
        return _handle_runtime_display_detail(
            get_runtime=get_runtime,
            runtime_not_ready_response=runtime_not_ready_response,
            request_view_arg=request_view_arg,
            respond_with_display=respond_with_display,
            fetch=lambda runtime: runtime.get_investment_models(),
        )

    @app.route("/api/leaderboard")
    def api_leaderboard():
        return _handle_runtime_display_detail(
            get_runtime=get_runtime,
            runtime_not_ready_response=runtime_not_ready_response,
            request_view_arg=request_view_arg,
            respond_with_display=respond_with_display,
            fetch=lambda runtime: runtime.get_leaderboard(),
        )
