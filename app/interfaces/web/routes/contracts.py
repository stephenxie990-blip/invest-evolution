"""Runtime contract route registration."""

from __future__ import annotations

from typing import Any, Callable

from flask import Flask


def register_runtime_contract_routes(
    app: Flask,
    *,
    build_contracts_payload: Callable[[], dict[str, Any]],
    serve_contract_document: Callable[[str], Any],
) -> None:
    @app.route("/api/contracts")
    def api_contracts():
        return build_contracts_payload()

    @app.route("/api/contracts/runtime-v1")
    def api_contract_runtime_v1():
        return serve_contract_document("runtime-v1")

    @app.route("/api/contracts/runtime-v1/schema")
    def api_contract_runtime_v1_schema():
        return serve_contract_document("runtime-v1-schema")

    @app.route("/api/contracts/runtime-v1/openapi")
    def api_contract_runtime_v1_openapi():
        return serve_contract_document("runtime-v1-openapi")
