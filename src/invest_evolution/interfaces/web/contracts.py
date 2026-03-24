"""Shared helpers for serving runtime contract documents through the interface layer."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from invest_evolution.interfaces.web.presentation import (
    ResponseValue,
    build_contract_payload_response,
    build_json_error_response,
)
from invest_evolution.application.runtime_contracts import (
    RUNTIME_CONTRACT_DOCUMENTS_BY_ID,
    RuntimeContractDocument,
    load_runtime_contract_document,
)

RUNTIME_CONTRACT_ROUTE_SPECS: tuple[tuple[str, str, str], ...] = (
    ("/api/contracts/runtime-v2", "api_contract_runtime_v2", "runtime-v2"),
    ("/api/contracts/runtime-v2/schema", "api_contract_runtime_v2_schema", "runtime-v2-schema"),
    ("/api/contracts/runtime-v2/openapi", "api_contract_runtime_v2_openapi", "runtime-v2-openapi"),
)


def serve_runtime_contract_document(
    document_id: str,
    *,
    logger: Any,
    load_document: Callable[
        [RuntimeContractDocument],
        dict[str, Any],
    ] = load_runtime_contract_document,
) -> ResponseValue:
    document = RUNTIME_CONTRACT_DOCUMENTS_BY_ID[document_id]
    try:
        return build_contract_payload_response(load_document(document))
    except FileNotFoundError:
        return build_json_error_response(document.not_found_error, 404)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        logger.exception("%s (%s)", document.load_error_log, document.id)
        return build_json_error_response(str(exc), 500)
